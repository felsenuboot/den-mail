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
