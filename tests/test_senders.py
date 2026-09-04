"""Sender statistics and the pointless ranking (#21)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from den_mail import senders
from den_mail.classify.rules import H_LIST_UNSUBSCRIBE, NEWSLETTERS
from den_mail.jmap.types import ROLE_INBOX, ROLE_TRASH
from den_mail.store import actions
from den_mail.store.sync import mailbox_query_spec

from .test_engine import engine, pump, server  # noqa: F401 - fixtures
from .test_views import TRASH_JUNK, db, email  # noqa: F401 - fixture and helper

UNSUB = {H_LIST_UNSUBSCRIBE: "<mailto:leave@lists.example>"}


def test_stats_count_volume_unread_replies_and_headers(db):  # noqa: F811
    db.upsert_emails([
        email("n1", "Digest #1", "digest@lists.example", "Weekly Digest", when="2026-09-01T10:00:00Z", size=10, **UNSUB),
        email("n2", "Digest #2", "digest@lists.example", "Weekly Digest", when="2026-09-03T10:00:00Z", size=20, **UNSUB),
        email("n3", "Digest #0", "digest@lists.example", "Weekly Digest", mailboxes=("mb-trash",), **UNSUB),
        email("a1", "Lunch?", "anna@example.net", "Anna", when="2026-09-02T10:00:00Z", seen=True),
        email("a2", "Re: Lunch?", "anna@example.net", "Anna", when="2026-09-02T12:00:00Z"),
        email("s1", "Re: Lunch?", "me@example.com", "Me", to="anna@example.net", mailboxes=("mb-sent",), seen=True),
        email("o1", "Note to self", "me@example.com", "Me", seen=True),
        email("w1", "Hi", "someone@example.org", "", seen=True),
    ])
    db.record_deletions([email("x", "Old digest", "digest@lists.example"), email("y", "Old digest", "digest@lists.example", seen=True)])
    stats = senders.sender_stats(db, TRASH_JUNK)
    assert [s.email for s in stats] == ["digest@lists.example", "anna@example.net"]   # own addresses left out
    digest, anna = stats
    assert (digest.count, digest.unread, digest.size, digest.unsubscribe, digest.replied) == (2, 2, 30, True, False)
    assert (digest.deleted, digest.deleted_unread) == (2, 1)
    assert digest.first_at.startswith("2026-09-01") and digest.last_at.startswith("2026-09-03")
    assert digest.newest_subject == "Digest #2" and digest.category == NEWSLETTERS and digest.name == "Weekly Digest"
    assert "never opened" in digest.score_text and "1 deleted unread" in digest.score_text
    assert (anna.count, anna.unread, anna.replied, anna.unsubscribe) == (2, 1, True, False)
    assert anna.score < 0 < digest.score and anna.score_text == "you wrote back"
    assert senders.sender_stats(db, TRASH_JUNK, NEWSLETTERS) and senders.sender_stats(db, TRASH_JUNK, "security") == []
    assert [m["subject"] for m in senders.messages_of(db, "digest@lists.example", TRASH_JUNK)] == ["Digest #2", "Digest #1"]
    assert senders.newest_list_mail(db, "digest@lists.example", TRASH_JUNK)["id"] == "n2"
    assert senders.newest_list_mail(db, "anna@example.net", TRASH_JUNK) is None
    assert senders.sender_stats(db, TRASH_JUNK, limit=1)[0] is not None and len(senders.sender_stats(db, TRASH_JUNK, limit=1)) == 1


def test_score_orders_the_usual_suspects_first(db):  # noqa: F811
    def sender(addr: str, n: int, unread: int, unsub: bool = False) -> None:
        db.upsert_emails([email(f"{addr}-{i}", "Hi", addr, when=f"2026-08-{i + 1:02d}T10:00:00Z",
                                seen=i >= unread, **(UNSUB if unsub else {})) for i in range(n)])

    sender("loud@shop.example", 12, 12, unsub=True)     # never opened, lots of it, can leave
    sender("quiet@shop.example", 12, 12)                # never opened, no unsubscribe
    sender("mixed@shop.example", 12, 4)                 # mostly read
    sender("rare@shop.example", 2, 2, unsub=True)       # never opened, but hardly any
    sender("friend@example.net", 12, 12, unsub=True)
    db.upsert_emails([email("s", "Hi", "me@example.com", to="friend@example.net", mailboxes=("mb-sent",), seen=True)])
    order = [s.email for s in senders.sender_stats(db, TRASH_JUNK)]
    assert order == ["loud@shop.example", "quiet@shop.example", "rare@shop.example", "mixed@shop.example",
                     "friend@example.net"]
    loud = senders.sender_stats(db, TRASH_JUNK)[0]
    db.record_deletions([email(f"del{i}", "Hi", "mixed@shop.example") for i in range(6)])
    mixed = next(s for s in senders.sender_stats(db, TRASH_JUNK) if s.email == "mixed@shop.example")
    assert mixed.score > senders.SenderStats(**{**mixed.__dict__, "deleted": 0, "deleted_unread": 0}).score
    assert loud.score > mixed.score


def test_engine_counts_what_the_user_throws_away(engine, server):  # noqa: F811
    inbox = engine.roles[ROLE_INBOX]
    key = engine.load_query(mailbox_query_spec(inbox))
    pump(lambda: any(a[0] == key for a in engine.events.get("query-updated", [])))
    digest = "digest@lists.example.com"
    unread = [e["id"] for e in engine.db.get_emails(engine.db.get_query(key)["ids"]).values()
              if e["from"][0]["email"] == digest and not e["keywords"].get("$seen")]
    read = [e["id"] for e in engine.db.get_emails(engine.db.get_query(key)["ids"]).values()
            if e["from"][0]["email"] == digest and e["keywords"].get("$seen")]
    assert unread and read
    done = []
    engine.perform(actions.trash([unread[0], read[0]], engine.roles), lambda rec: done.append(rec))
    pump(lambda: done)
    stats = {s.email: s for s in senders.sender_stats(engine.db, [])}
    assert (stats[digest].deleted, stats[digest].deleted_unread) == (2, 1)
    # trashing from Trash again (a permanent delete) is not counted twice
    done.clear()
    engine.perform(actions.destroy([unread[0]]), lambda rec: done.append(rec))
    pump(lambda: done)
    stats = {s.email: s for s in senders.sender_stats(engine.db, [])}
    assert (stats[digest].deleted, stats[digest].deleted_unread) == (2, 1)
    assert engine.roles[ROLE_TRASH] in server.data.emails[read[0]]["mailboxIds"]
