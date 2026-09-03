"""End-to-end tests of client + cache + sync engine against the fake JMAP server."""

from __future__ import annotations

import time

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from den_mail.config import Config  # noqa: E402
from den_mail.jmap.client import JMAPClient  # noqa: E402
from den_mail.jmap.types import KW_SEEN, ROLE_ARCHIVE, ROLE_INBOX, ROLE_TRASH  # noqa: E402
from den_mail.models.thread import ThreadListModel  # noqa: E402
from den_mail.store import actions  # noqa: E402
from den_mail.store.db import Database  # noqa: E402
from den_mail.store.sync import SyncEngine, mailbox_query_spec, search_query_spec  # noqa: E402

from .fake_server import FakeJMAPServer  # noqa: E402


def pump(condition, timeout: float = 10.0) -> None:
    """Iterate the GLib main context until condition() is true."""
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not condition():
        while ctx.pending():
            ctx.iteration(False)
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met")
        time.sleep(0.01)


@pytest.fixture
def server():
    srv = FakeJMAPServer().start()
    yield srv
    srv.stop()


@pytest.fixture
def engine(server, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    client = JMAPClient(server.token, server.session_url)
    client.fetch_session()
    db = Database(tmp_path / "test.sqlite3")
    eng = SyncEngine(client, db, Config())
    events: dict[str, list] = {}

    def record(name):
        def cb(_engine, *args):
            events.setdefault(name, []).append(args)

        return cb

    for sig in ("mailboxes-changed", "emails-changed", "query-updated", "body-ready", "new-mail", "action-failed",
                "identities-changed", "masked-changed", "push-status", "emails-destroyed"):
        eng.connect(sig, record(sig))
    eng.events = events
    eng.start()
    pump(lambda: "mailboxes-changed" in events)
    yield eng
    eng.stop()


def test_bootstrap_loads_mailboxes_and_identities(engine):
    mbs = engine.db.get_mailboxes()
    roles = {m["role"] for m in mbs if m.get("role")}
    assert {"inbox", "drafts", "sent", "archive", "junk", "trash"} <= roles
    assert any(m["name"] == "Projects" and m["parentId"] for m in mbs)
    assert len(engine.db.get_identities()) == 3
    assert any(i["email"].startswith("*@") for i in engine.db.get_identities())
    assert len(engine.db.get_masked_emails()) == 4
    assert engine.db.get_state("Email") is not None


def test_query_and_thread_model(engine):
    inbox = engine.roles[ROLE_INBOX]
    key = engine.load_query(mailbox_query_spec(inbox))
    pump(lambda: any(a[0] == key for a in engine.events.get("query-updated", [])))
    q = engine.db.get_query(key)
    assert q["total"] >= 15 and q["ids"]
    model = ThreadListModel(engine.db)
    model.mailbox_id = inbox
    model.set_email_ids(q["ids"], q["total"], q["complete"])
    assert model.get_n_items() == len(q["ids"])
    # the 4-message meetup conversation shows count 3 in inbox (one was sent by me)
    meetup = next(o for o in model.items if "GTK meetup" in o.subject)
    assert meetup.count == 3
    assert meetup.unread is True
    assert "Anna Berger" in meetup.participants
    # newest first
    dates = [o.received_at for o in model.items]
    assert dates == sorted(dates, reverse=True)


def test_search_spec_parsing(engine):
    spec = search_query_spec("from:anna is:unread has:attachment meetup", None, ["t", "j"])
    conds = spec["filter"]["conditions"]
    assert {"from": "anna"} in conds
    assert {"notKeyword": KW_SEEN} in conds
    assert {"hasAttachment": True} in conds
    assert {"text": "meetup"} in conds
    assert {"inMailboxOtherThan": ["t", "j"]} in conds
    key = engine.load_query(search_query_spec("ticket", None, engine.trash_junk_ids()))
    pump(lambda: any(a[0] == key for a in engine.events.get("query-updated", [])))
    ids = engine.db.get_query(key)["ids"]
    assert ids
    subjects = [engine.db.get_email(i)["subject"] for i in ids]
    assert any("ticket" in s.lower() for s in subjects)


def test_optimistic_action_and_undo(engine, server):
    inbox = engine.roles[ROLE_INBOX]
    key = engine.load_query(mailbox_query_spec(inbox))
    pump(lambda: any(a[0] == key for a in engine.events.get("query-updated", [])))
    email_id = engine.db.get_query(key)["ids"][0]
    before_unread = engine.db.get_mailbox(inbox)["unreadEmails"]
    done = []
    engine.perform(actions.archive([email_id], engine.roles), lambda rec: done.append(rec))
    pump(lambda: done)
    record = done[0]
    assert record is not None and email_id in record.originals
    local = engine.db.get_email(email_id)
    assert inbox not in local["mailboxIds"] and engine.roles[ROLE_ARCHIVE] in local["mailboxIds"]
    remote = server.data.emails[email_id]
    assert inbox not in remote["mailboxIds"]
    assert engine.db.get_mailbox(inbox)["totalEmails"] < server.data.mailboxes[inbox]["totalEmails"] + 1
    # undo restores both sides
    done.clear()
    engine.perform(record.to_action(), lambda rec: done.append(rec))
    pump(lambda: done)
    assert inbox in engine.db.get_email(email_id)["mailboxIds"]
    assert inbox in server.data.emails[email_id]["mailboxIds"]
    pump(lambda: engine.db.get_mailbox(inbox)["unreadEmails"] == before_unread, timeout=5)


def test_trash_and_failure_rollback(engine, server):
    inbox = engine.roles[ROLE_INBOX]
    key = engine.load_query(mailbox_query_spec(inbox))
    pump(lambda: any(a[0] == key for a in engine.events.get("query-updated", [])))
    email_id = engine.db.get_query(key)["ids"][1]
    done = []
    engine.perform(actions.trash([email_id], engine.roles), lambda rec: done.append(rec))
    pump(lambda: done)
    assert set(server.data.emails[email_id]["mailboxIds"]) == {engine.roles[ROLE_TRASH]}
    # a patch the server rejects (unknown mailbox) rolls back locally
    done.clear()
    engine.perform(actions.move([email_id], "nope", "Nope"), lambda rec: done.append(rec))
    pump(lambda: done)
    pump(lambda: engine.events.get("action-failed"))
    assert set(engine.db.get_email(email_id)["mailboxIds"]) == {engine.roles[ROLE_TRASH]}


def test_body_fetch_and_blob(engine, server):
    ticket = next(e for e in server.data.emails.values() if "ticket" in e["subject"].lower())
    engine.fetch_body(ticket["id"])
    pump(lambda: any(a[0] == ticket["id"] for a in engine.events.get("body-ready", [])))
    body = engine.db.get_email_body(ticket["id"])
    assert body["htmlBody"] and "cid:logo@fake" in body["bodyValues"]["2"]["value"]
    assert body["header:Delivered-To:asText"] == "shop@example.com"
    att = next(a for a in body["attachments"] if a["disposition"] == "attachment")
    got = []
    engine.fetch_blob(att["blobId"], att["name"], att["type"], lambda p: got.append(p))
    pump(lambda: got)
    assert got[0].read_bytes().startswith(b"%PDF")


def test_push_delivers_new_mail(engine, server):
    pump(lambda: engine.push_connected, timeout=10)
    inbox = engine.roles[ROLE_INBOX]
    key = engine.load_query(mailbox_query_spec(inbox))
    pump(lambda: any(a[0] == key for a in engine.events.get("query-updated", [])))
    n_before = len(engine.db.get_query(key)["ids"])
    new_id = server.deliver("Push test subject")
    pump(lambda: engine.events.get("new-mail"), timeout=15)
    assert engine.events["new-mail"][0][0][0]["id"] == new_id
    pump(lambda: len(engine.db.get_query(key)["ids"]) == n_before + 1, timeout=10)
    assert engine.db.get_query(key)["ids"][0] == new_id


def test_send_email_moves_to_sent(engine, server):
    identity = engine.db.get_identities()[0]
    done, errors = [], []
    draft = {"from": [{"name": identity["name"], "email": identity["email"]}],
             "to": [{"name": None, "email": "anna@example.net"}], "subject": "Test send",
             "textBody": [{"partId": "t", "type": "text/plain"}], "bodyValues": {"t": {"value": "Hello!"}}}
    engine.send_email(draft, identity["id"], None, lambda i: done.append(i), lambda m: errors.append(m))
    pump(lambda: done or errors)
    assert not errors, errors
    sent = engine.roles["sent"]
    remote = server.data.emails[done[0]]
    assert set(remote["mailboxIds"]) == {sent}
    assert "$draft" not in remote["keywords"]


def test_mailbox_create_rename_delete(engine, server):
    done, errors = [], []
    engine.mailbox_set(create={"new": {"name": "Travel", "parentId": None, "isSubscribed": True}},
                       on_done=lambda r: done.append(r), on_error=lambda m: errors.append(m))
    pump(lambda: done or errors)
    assert not errors
    new_id = done[0]["created"]["new"]["id"]
    pump(lambda: engine.db.get_mailbox(new_id) is not None)
    done.clear()
    engine.mailbox_set(update={new_id: {"name": "Trips"}}, on_done=lambda r: done.append(r))
    pump(lambda: done)
    pump(lambda: engine.db.get_mailbox(new_id)["name"] == "Trips")
    done.clear()
    engine.mailbox_set(destroy=[new_id], on_done=lambda r: done.append(r))
    pump(lambda: done)
    pump(lambda: engine.db.get_mailbox(new_id) is None)


def test_masked_email_create_and_disable(engine, server):
    done, errors = [], []
    engine.masked_set(create={"forDomain": "https://example.shop", "description": "Shop", "emailPrefix": "shop",
                              "state": "enabled"}, on_done=lambda r: done.append(r), on_error=lambda m: errors.append(m))
    pump(lambda: done or errors)
    assert not errors
    assert done[0]["email"].startswith("shop.")
    mid = done[0]["id"]
    done.clear()
    engine.masked_set(update={mid: {"state": "disabled"}}, on_done=lambda r: done.append(r))
    pump(lambda: done)
    assert next(m for m in engine.db.get_masked_emails() if m["id"] == mid)["state"] == "disabled"


def test_identity_update(engine, server):
    ident = engine.db.get_identities()[0]
    done = []
    engine.identity_update(ident["id"], {"name": "Felix Renamed"}, on_done=lambda: done.append(1))
    pump(lambda: done)
    assert server.data.identities[ident["id"]]["name"] == "Felix Renamed"
    assert any(i["name"] == "Felix Renamed" for i in engine.db.get_identities())


def test_body_fetch_falls_back_when_server_rejects_header_property(engine, server):
    """Fastmail rejects header:List-Unsubscribe:asText; the engine must drop it and retry."""
    from den_mail.store.sync import SyncEngine

    SyncEngine._body_properties = [p for p in SyncEngine._body_properties if not p.startswith("header:List")] + [
        "header:List-Unsubscribe:asText"]
    ticket = next(e for e in server.data.emails.values() if "ticket" in e["subject"].lower())
    engine.fetch_body(ticket["id"], force=True)
    pump(lambda: any(a[0] == ticket["id"] for a in engine.events.get("body-ready", [])))
    assert "header:List-Unsubscribe:asText" not in SyncEngine._body_properties
    assert engine.db.get_email_body(ticket["id"])["header:Delivered-To:asText"] == "shop@example.com"


def test_sort_options_round_trip_and_server_sorting(engine, server):
    from den_mail.store.sync import build_sort, parse_sort

    for key in ("newest", "oldest", "sender", "subject", "size"):
        for flagged in (False, True):
            for unread in (False, True):
                assert parse_sort(build_sort(key, flagged, unread)) == (key, flagged, unread)
    assert parse_sort([{"property": "receivedAt", "isAscending": False}]) == ("newest", False, False)
    inbox = engine.roles[ROLE_INBOX]
    key = engine.load_query(mailbox_query_spec(inbox, build_sort("oldest")))
    pump(lambda: any(a[0] == key for a in engine.events.get("query-updated", [])))
    ids = engine.db.get_query(key)["ids"]
    dates = [engine.db.get_email(i)["receivedAt"] for i in ids]
    assert dates == sorted(dates)
    key = engine.load_query(mailbox_query_spec(inbox, build_sort("newest", flagged_first=True)))
    pump(lambda: any(a[0] == key for a in engine.events.get("query-updated", [])))
    first = engine.db.get_email(engine.db.get_query(key)["ids"][0])
    assert first["keywords"].get("$flagged")
    key = engine.load_query(mailbox_query_spec(inbox, build_sort("sender")))
    pump(lambda: any(a[0] == key for a in engine.events.get("query-updated", [])))
    senders = [(engine.db.get_email(i)["from"][0].get("name") or "").lower() for i in engine.db.get_query(key)["ids"]]
    assert senders == sorted(senders)


def test_sender_grouping_rows(engine):
    from den_mail.models.thread import SenderGroup
    from den_mail.store.sync import build_sort
    inbox = engine.roles[ROLE_INBOX]
    key = engine.load_query(mailbox_query_spec(inbox, build_sort("newest")))
    pump(lambda: any(a[0] == key for a in engine.events.get("query-updated", [])))
    q = engine.db.get_query(key)
    model = ThreadListModel(engine.db)
    model.mailbox_id = inbox
    model.set_email_ids(q["ids"], q["total"], q["complete"])
    n = len(model.threads)
    assert model.get_n_items() == n                       # ungrouped: threads only
    model.set_grouped(True)
    groups = [i for i in model.items if isinstance(i, SenderGroup)]
    assert len(groups) > 1 and model.get_n_items() == n + len(groups)
    assert len(groups) == len({o.sender_key for o in model.threads})   # one group per sender
    # groups follow the active sort: each sits where its newest thread was
    first_seen = list(dict.fromkeys(o.sender_key for o in model.threads))
    assert [g.key for g in groups] == first_seen
    for g in groups:
        pos = model.items.index(g)
        assert [model.items[pos + 1 + k] for k in range(g.count)] == g.threads
        assert {t.sender_key for t in g.threads} == {g.key}
        assert g.threads == [t for t in model.threads if t.sender_key == g.key]
    # folding a group hides its threads but keeps the row; identity survives
    g = max(groups, key=lambda g: g.count)
    model.toggle_collapsed(g.key)
    assert g.collapsed and g in model.items and model.get_n_items() == n + len(groups) - g.count
    assert model.index_of(g.threads[0].thread_id) == -1
    model.reveal(g.threads[0].thread_id)
    assert not g.collapsed and model.index_of(g.threads[0].thread_id) == model.items.index(g) + 1
    # a reload keeps the fold state and the group objects
    model.collapsed.add(g.key)
    model.set_email_ids(q["ids"], q["total"], q["complete"])
    assert model.groups[g.key] is g and g.collapsed
    model.set_grouped(False)
    assert model.get_n_items() == n
    # an empty all-mail search lists everything outside trash/junk
    spec = search_query_spec("", None, ["t", "j"])
    assert spec["filter"] == {"inMailboxOtherThan": ["t", "j"]}
