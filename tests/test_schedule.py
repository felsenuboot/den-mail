"""Send later (#6): the presets, and a scheduled submission end to end."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import gi

gi.require_version("Gtk", "4.0")

from den_mail import schedule
from den_mail.jmap.types import KW_DRAFT, ROLE_DRAFTS, ROLE_SCHEDULED, ROLE_SENT

from .test_engine import engine, pump, server  # noqa: F401 - fixtures

TZ = timezone(timedelta(hours=2))


def test_presets_are_ahead_and_in_order():
    now = datetime(2026, 9, 4, 9, 30, tzinfo=TZ)   # a Friday morning
    labels = [label for label, _ in schedule.presets(now)]
    assert labels == ["This afternoon", "This evening", "Tomorrow morning", "Tomorrow afternoon", "Monday morning"]
    times = dict(schedule.presets(now))
    assert times["This afternoon"] == datetime(2026, 9, 4, 14, 0, tzinfo=TZ)
    assert times["Tomorrow morning"] == datetime(2026, 9, 5, 8, 0, tzinfo=TZ)
    assert times["Monday morning"] == datetime(2026, 9, 7, 8, 0, tzinfo=TZ)
    assert all(t > now for t in times.values())
    evening = datetime(2026, 9, 4, 19, 0, tzinfo=TZ)
    assert [label for label, _ in schedule.presets(evening)] == ["Tomorrow morning", "Tomorrow afternoon", "Monday morning"]
    monday = datetime(2026, 9, 7, 6, 0, tzinfo=TZ)
    assert dict(schedule.presets(monday))["Monday morning"] == datetime(2026, 9, 14, 8, 0, tzinfo=TZ)
    assert schedule.to_utc(datetime(2026, 9, 4, 14, 0, tzinfo=TZ)) == "2026-09-04T12:00:00Z"
    assert schedule.describe("2026-09-04T12:00:00Z").startswith("Fri 4 Sep")
    assert schedule.describe("nonsense") == "nonsense"


def test_scheduled_message_waits_and_can_be_cancelled(engine, server):  # noqa: F811
    identity = engine.db.get_identities()[0]["id"]
    draft = {"from": [{"email": "felix@example.com"}], "to": [{"email": "anna@example.net"}], "subject": "Later",
             "textBody": [{"partId": "t", "type": "text/plain"}], "bodyValues": {"t": {"value": "See you Monday."}}}
    when = schedule.to_utc(datetime.now(TZ) + timedelta(days=2))
    sent: list = []
    engine.send_email(draft, identity, None, lambda new_id: sent.append(new_id), lambda m: sent.append(("error", m)), send_at=when)
    pump(lambda: sent, timeout=10)
    new_id = sent[0]
    assert isinstance(new_id, str)
    remote = server.data.emails[new_id]
    assert engine.roles[ROLE_SCHEDULED] in remote["mailboxIds"] and engine.roles[ROLE_SENT] not in remote["mailboxIds"]
    assert engine.roles[ROLE_DRAFTS] not in remote["mailboxIds"] and not remote["keywords"].get(KW_DRAFT)
    sub = engine.db.get_submission(new_id)
    assert sub and sub["send_at"] == when
    assert server.data.submissions[sub["submission_id"]]["undoStatus"] == "pending"
    # cancelling puts it back in Drafts
    done: list = []
    engine.cancel_scheduled(new_id, lambda: done.append("ok"), lambda m: done.append(m))
    pump(lambda: done, timeout=10)
    assert done == ["ok"]
    remote = server.data.emails[new_id]
    assert engine.roles[ROLE_DRAFTS] in remote["mailboxIds"] and engine.roles[ROLE_SCHEDULED] not in remote["mailboxIds"]
    assert remote["keywords"].get(KW_DRAFT) and engine.db.get_submission(new_id) is None
    assert server.data.submissions[sub["submission_id"]]["undoStatus"] == "canceled"
    # a second cancel has nothing to cancel
    done.clear()
    engine.cancel_scheduled(new_id, lambda: done.append("ok"), lambda m: done.append(m))
    pump(lambda: done, timeout=10)
    assert done != ["ok"]
    # sending now, as before, lands in Sent with no submission kept
    sent.clear()
    engine.send_email(draft, identity, None, lambda new_id: sent.append(new_id), lambda m: sent.append(("error", m)))
    pump(lambda: sent, timeout=10)
    assert engine.roles[ROLE_SENT] in server.data.emails[sent[0]]["mailboxIds"] and engine.db.get_submission(sent[0]) is None
