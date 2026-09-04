"""The offline outbox (#8): changes and messages queue while the server is unreachable and go out after."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from den_mail.jmap.types import ROLE_ARCHIVE, ROLE_INBOX, ROLE_SENT
from den_mail.store import actions
from den_mail.store.sync import mailbox_query_spec

from .fake_server import FakeJMAPServer
from .test_engine import engine, pump, server  # noqa: F401 - fixtures


def test_changes_and_sends_queue_offline_and_replay_when_back(engine, server):  # noqa: F811
    inbox, archive, sent = engine.roles[ROLE_INBOX], engine.roles[ROLE_ARCHIVE], engine.roles[ROLE_SENT]
    key = engine.load_query(mailbox_query_spec(inbox))
    pump(lambda: any(a[0] == key for a in engine.events.get("query-updated", [])))
    email_id = engine.db.get_query(key)["ids"][0]
    port = server.server_port
    server.stop()
    # an action while offline: applied locally, queued, the engine knows it is offline
    done = []
    engine.perform(actions.archive([email_id], engine.roles), lambda rec: done.append(rec))
    pump(lambda: done, timeout=15)
    assert done[0] is not None and email_id in done[0].originals      # still undoable
    assert inbox not in engine.db.get_email(email_id)["mailboxIds"]
    pump(lambda: engine.db.outbox_count() == 1 and not engine.online, timeout=5)
    # a message while offline: queued with an empty id
    identity = engine.db.get_identities()[0]["id"]
    draft = {"from": [{"email": "felix@example.com"}], "to": [{"email": "anna@example.net"}], "subject": "From the train",
             "textBody": [{"partId": "t", "type": "text/plain"}], "bodyValues": {"t": {"value": "No signal here."}}}
    sent_ids = []
    engine.send_email(draft, identity, None, lambda new_id: sent_ids.append(new_id), lambda m: sent_ids.append(("error", m)))
    pump(lambda: sent_ids, timeout=15)
    assert sent_ids == [""] and engine.db.outbox_count() == 2
    assert [r["kind"] for r in engine.db.outbox_list()] == ["update", "send"]
    # the server comes back (the fixture is deterministic, so the same ids exist); the next sync replays
    again = FakeJMAPServer(port=port).start()
    try:
        engine.events.pop("outbox-changed", None)
        engine.sync_now()
        pump(lambda: engine.db.outbox_count() == 0, timeout=20)
        assert engine.online
        assert inbox not in again.data.emails[email_id]["mailboxIds"] and archive in again.data.emails[email_id]["mailboxIds"]
        new = [e for e in again.data.emails.values() if e["subject"] == "From the train"]
        assert len(new) == 1 and sent in new[0]["mailboxIds"]
        pump(lambda: any(e["subject"] == "From the train" for e in engine.db.get_emails(
            [i for i in again.data.emails]).values()), timeout=10)
    finally:
        again.stop()


def _draft(subject: str, text: str) -> dict:
    return {"from": [{"email": "felix@example.com"}], "to": [{"email": "anna@example.net"}], "subject": subject,
            "textBody": [{"partId": "t", "type": "text/plain"}], "bodyValues": {"t": {"value": text}}}


def test_drafts_saved_offline_are_listed_queued_and_created_on_replay(engine, server):  # noqa: F811
    from den_mail.jmap.types import ROLE_DRAFTS

    drafts = engine.roles[ROLE_DRAFTS]
    created_ids = []
    engine.connect("draft-created", lambda _e, local, new: created_ids.append((local, new)))
    key = engine.load_query(mailbox_query_spec(drafts))
    pump(lambda: any(a[0] == key for a in engine.events.get("query-updated", [])))
    before = list(engine.db.get_query(key)["ids"])
    port = server.server_port
    server.stop()
    saved = []
    engine.save_draft(_draft("Notes", "first words"), None, saved.append, lambda m: saved.append(("error", m)))
    pump(lambda: saved, timeout=15)
    local_id = saved[0]
    assert engine.is_local(local_id) and not engine.online
    # in the cache, with its body, in the Drafts list first, and queued once
    e = engine.db.get_email(local_id)
    assert e["subject"] == "Notes" and drafts in e["mailboxIds"] and e["keywords"].get("$draft")
    assert engine.db.get_email_body(local_id)["bodyValues"]["t"]["value"] == "first words"
    assert engine.db.get_query(key)["ids"] == [local_id, *before]
    assert [r["kind"] for r in engine.db.outbox_list()] == ["draft"]
    # a later save while still offline replaces the queued row and the placeholder, no chain
    engine.save_draft(_draft("Notes, more", "more words"), local_id, saved.append, lambda m: saved.append(("error", m)))
    pump(lambda: len(saved) == 2, timeout=15)
    assert saved[1] == local_id
    rows = engine.db.outbox_list()
    assert len(rows) == 1 and rows[0]["payload"]["draft"]["subject"] == "Notes, more"
    assert engine.db.get_email(local_id)["subject"] == "Notes, more"
    assert engine.db.get_query(key)["ids"].count(local_id) == 1
    # the server comes back: the draft is created there, the placeholder goes, the window learns the id
    again = FakeJMAPServer(port=port).start()
    try:
        engine.sync_now()
        pump(lambda: engine.db.outbox_count() == 0, timeout=20)
        created = [e for e in again.data.emails.values() if e["subject"] == "Notes, more"]
        assert len(created) == 1 and drafts in created[0]["mailboxIds"]
        assert engine.db.get_email(local_id) is None
        assert local_id not in engine.db.get_query(key)["ids"]
        pump(lambda: created_ids, timeout=10)
        assert created_ids[-1] == (local_id, created[0]["id"])
        # saving again with the server id replaces the server draft the usual way
        engine.save_draft(_draft("Notes, final", "final"), created[0]["id"], saved.append, lambda m: saved.append(("error", m)))
        pump(lambda: len(saved) == 3, timeout=15)
        assert not engine.is_local(saved[2]) and created[0]["id"] not in again.data.emails
    finally:
        again.stop()


def test_sending_a_draft_saved_offline_supersedes_it(engine, server):  # noqa: F811
    identity = engine.db.get_identities()[0]["id"]
    port = server.server_port
    server.stop()
    saved, sent_ids = [], []
    engine.save_draft(_draft("On the road", "draft text"), None, saved.append, lambda m: saved.append(("error", m)))
    pump(lambda: saved, timeout=15)
    local_id = saved[0]
    engine.send_email(_draft("On the road", "sent text"), identity, local_id, sent_ids.append,
                      lambda m: sent_ids.append(("error", m)))
    pump(lambda: sent_ids, timeout=15)
    assert sent_ids == [""]
    assert [r["kind"] for r in engine.db.outbox_list()] == ["send"]      # the draft row is gone
    assert engine.db.get_email(local_id) is None
    assert engine.db.outbox_list()[0]["payload"]["replace_id"] is None   # nothing on the server to destroy
    again = FakeJMAPServer(port=port).start()
    try:
        engine.sync_now()
        pump(lambda: engine.db.outbox_count() == 0, timeout=20)
        assert [e for e in again.data.emails.values() if e["subject"] == "On the road"]
    finally:
        again.stop()


def test_discarding_a_local_draft_drops_it(engine, server):  # noqa: F811
    server.stop()
    saved = []
    engine.save_draft(_draft("Never mind", "x"), None, saved.append, lambda m: saved.append(("error", m)))
    pump(lambda: saved, timeout=15)
    engine.discard_draft(saved[0])
    pump(lambda: engine.db.outbox_count() == 0 and engine.db.get_email(saved[0]) is None, timeout=10)


def test_a_queued_change_the_server_rejects_is_reported_and_dropped(engine, server):  # noqa: F811
    port = server.server_port
    server.stop()
    done = []
    engine.perform(actions.mark_read(["M9999"], True), lambda rec: done.append(rec))   # unknown message: no patch, nothing queued
    pump(lambda: done, timeout=15)
    assert engine.db.outbox_count() == 0
    engine.db.outbox_add("update", {"patches": {"M9999": {"keywords/$seen": True}}, "description": "Marked as read"})
    again = FakeJMAPServer(port=port).start()
    try:
        engine.events.pop("action-failed", None)
        engine.sync_now()
        pump(lambda: engine.db.outbox_count() == 0, timeout=20)
        pump(lambda: engine.events.get("action-failed"), timeout=10)
        assert "Marked as read" in engine.events["action-failed"][0][0]
    finally:
        again.stop()
