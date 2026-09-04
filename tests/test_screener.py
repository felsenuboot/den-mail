"""The screener for first-time senders (#24)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from den_mail import rules, views
from den_mail.jmap.types import ROLE_ARCHIVE, ROLE_INBOX
from den_mail.models.thread import ThreadListModel
from den_mail.store.sync import mailbox_query_spec

from .test_engine import engine, pump, server  # noqa: F401 - fixtures
from .test_views import TRASH_JUNK, db, email  # noqa: F401 - fixture and helper


def test_cache_knows_senders_and_keeps_decisions(db):  # noqa: F811
    db.upsert_emails([email("a", "Hi", "anna@example.net", "Anna", to="me@example.com"),
                      email("s", "Re", "me@example.com", to="paul@example.net", mailboxes=("mb-sent",), seen=True)])
    assert db.knows_sender("Anna@Example.net")          # cached as a sender
    assert not db.knows_sender("anna@example.net", {"a"})   # ... but not by the batch being judged (#42)
    assert db.knows_sender("paul@example.net")          # written to
    assert not db.knows_sender("me2@example.net")       # a recipient of cached mail is not a known sender
    assert db.knows_sender("me@example.com") and db.knows_sender("x@example.org")   # own addresses never screen
    assert not db.knows_sender("new@shop.example")
    db.screener_set(["New@Shop.example"], "pending")
    assert db.knows_sender("new@shop.example") and db.screener_decision("new@shop.example") == "pending"
    assert db.screener_pending() == {"new@shop.example"}
    db.screener_set({"new@shop.example"}, "allow")
    assert db.screener_pending() == set() and db.screener_decision("new@shop.example") == "allow"
    assert db.screener_decision("nobody@example.net") is None
    db.clear_all()
    assert db.screener_decision("new@shop.example") is None


def test_inbox_badge_leaves_out_held_mail(db):  # noqa: F811
    from den_mail.models.mailbox import MailboxObject

    db.upsert_emails([
        email("h1", "Hello", "new@shop.example", "Shop"),
        email("h2", "Hello again", "new@shop.example", "Shop", thread="T-h1"),
        email("h3", "Read one", "new@shop.example", "Shop", seen=True),
        email("h4", "Elsewhere", "new@shop.example", "Shop", mailboxes=("mb-work",)),
        email("k1", "Known", "anna@example.net", "Anna"),
    ])
    assert db.screener_held_unread("mb-inbox") == 0
    db.screener_set(["new@shop.example"], "pending")
    assert db.screener_held_unread("mb-inbox") == 2          # unread, in the Inbox, still pending
    assert db.screener_held_unread("mb-work") == 1
    inbox = MailboxObject({"id": "mb-inbox", "name": "Inbox", "role": "inbox", "unreadEmails": 3, "totalEmails": 5})
    assert inbox.unread == 3
    inbox.set_held(db.screener_held_unread("mb-inbox"))
    assert inbox.unread == 1 and inbox.total == 5
    inbox.update({"id": "mb-inbox", "name": "Inbox", "role": "inbox", "unreadEmails": 4, "totalEmails": 6})
    assert inbox.unread == 2                                  # a server update keeps the deduction
    inbox.set_held(10)
    assert inbox.unread == 0                                  # never below zero
    db.screener_set(["new@shop.example"], "allow")
    inbox.set_held(db.screener_held_unread("mb-inbox"))
    assert inbox.unread == 4


def test_screener_view_lists_pending_senders_and_the_inbox_hides_them(db):  # noqa: F811
    db.upsert_emails([
        email("a", "Hello", "new@shop.example", "Shop", when="2026-09-03T10:00:00Z"),
        email("a2", "Hello again", "new@shop.example", "Shop", when="2026-09-04T10:00:00Z", thread="T-a"),
        email("b", "Lunch?", "anna@example.net", "Anna", when="2026-09-02T10:00:00Z"),
        email("t", "Old", "new@shop.example", "Shop", mailboxes=("mb-trash",)),
    ])
    screener = views.get_view(views.SCREENER)
    assert views.list_ids(db, screener, TRASH_JUNK) == []
    db.screener_set(["new@shop.example"], "pending")
    assert views.list_ids(db, screener, TRASH_JUNK) == ["a2"]
    assert views.counts(db, screener, TRASH_JUNK) == (1, 1)
    assert views.SCREENER not in {v.id for v in views.sidebar_views(False)}
    assert next(v.id for v in views.sidebar_views(True)) == views.SCREENER
    model = ThreadListModel(db)
    model.mailbox_id = "mb-inbox"
    model.set_email_ids(["a2", "b"], 2, True)
    assert [t.thread_id for t in model.items] == ["T-a", "T-b"]
    model.set_screened({"New@Shop.example"})
    assert [t.thread_id for t in model.items] == ["T-b"] and model.hidden_by_screener == 1
    model.set_email_ids(["a2", "b"], 2, True)   # a refresh keeps the filter
    assert [t.thread_id for t in model.items] == ["T-b"]
    model.set_screened(set())
    assert [t.thread_id for t in model.items] == ["T-a", "T-b"] and model.hidden_by_screener == 0


def test_engine_screens_first_time_senders_only_while_enabled(engine, server):  # noqa: F811
    pump(lambda: engine.push_connected, timeout=10)
    inbox = engine.roles[ROLE_INBOX]
    key = engine.load_query(mailbox_query_spec(inbox))
    pump(lambda: any(a[0] == key for a in engine.events.get("query-updated", [])))
    # off: nothing is screened
    off = server.deliver("Hi", frm={"name": "Stranger", "email": "stranger@example.net"})
    pump(lambda: engine.db.get_email(off) is not None, timeout=15)
    assert engine.db.screener_decision("stranger@example.net") is None
    engine.config.set("screener", True)
    engine.events.pop("new-mail", None)
    new = server.deliver("Offer", frm={"name": "Shop", "email": "new@shop.example"})
    known = server.deliver("Hello", frm={"name": "Anna", "email": "anna@example.net"})
    again = server.deliver("Hi again", frm={"name": "Stranger", "email": "stranger@example.net"})
    # the rows may land through the running sync's thread step; the decision comes with the next one (#42)
    pump(lambda: engine.db.screener_decision("new@shop.example") == "pending", timeout=15)
    pump(lambda: all(engine.db.get_email(i) is not None for i in (new, known, again)), timeout=15)
    assert engine.db.screener_decision("anna@example.net") is None      # seen before
    assert engine.db.screener_decision("stranger@example.net") is None  # cached before the switch
    def announced() -> set[str]:   # each delivery is its own push, sync and announcement
        return {e["id"] for args in engine.events.get("new-mail", []) for e in args[0]}

    pump(lambda: {known, again} <= announced(), timeout=10)
    assert new not in announced()
    assert views.list_ids(engine.db, views.get_view(views.SCREENER), engine.trash_junk_ids()) == [new]
    # a second message from the pending sender stays pending, a decision sticks
    engine.db.screener_set(["new@shop.example"], "allow")
    more = server.deliver("Offer 2", frm={"name": "Shop", "email": "new@shop.example"})
    pump(lambda: engine.db.get_email(more) is not None, timeout=15)
    assert engine.db.screener_decision("new@shop.example") == "allow"
    assert views.list_ids(engine.db, views.get_view(views.SCREENER), engine.trash_junk_ids()) == []


def test_screening_out_archives_now_and_by_rule(engine, server):  # noqa: F811
    pump(lambda: engine.push_connected, timeout=10)
    inbox, archive = engine.roles[ROLE_INBOX], engine.roles[ROLE_ARCHIVE]
    engine.config.set("screener", True)
    first = server.deliver("Offer", frm={"name": "Shop", "email": "new@shop.example"})
    pump(lambda: engine.db.screener_decision("new@shop.example") == "pending", timeout=15)
    # what the window does on "Screen out"
    engine.db.screener_set(["new@shop.example"], "block")
    rule = rules.add_rule(engine.config, rules.Rule("sender", "new@shop.example", "archive"))
    done = []
    engine.act_on_sender("new@shop.example", lambda ids: rules.combine(ids, [rule], engine.roles), lambda rec: done.append(rec))
    pump(lambda: done, timeout=10)
    assert inbox not in server.data.emails[first]["mailboxIds"] and archive in server.data.emails[first]["mailboxIds"]
    second = server.deliver("Offer 2", frm={"name": "Shop", "email": "new@shop.example"})
    pump(lambda: engine.db.get_email(second) is not None and inbox not in engine.db.get_email(second)["mailboxIds"], timeout=15)
    assert engine.db.screener_decision("new@shop.example") == "block"
