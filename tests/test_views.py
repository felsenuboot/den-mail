"""Sidebar views (#19): the cache queries behind them, the tree section and the toggle."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import gi
import pytest

gi.require_version("Gtk", "4.0")

from den_mail import views
from den_mail.classify.rules import H_LIST_UNSUBSCRIBE
from den_mail.jmap.types import ROLE_INBOX
from den_mail.models.mailbox import MailboxObject, MailboxTree
from den_mail.store.db import Database
from den_mail.store.sync import mailbox_query_spec, parse_sort

from .test_engine import engine, pump, server  # noqa: F401 - fixtures

TRASH_JUNK = ["mb-trash", "mb-junk"]
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def view(view_id: str) -> views.View:
    v = views.get_view(view_id)
    assert v is not None
    return v


NEWSLETTERS = view("view:newsletters")
TRANSACTIONS = view("view:transactions")
NEVER_READ = view(views.NEVER_READ)
BIG = view(views.BIG_ATTACHMENTS)
UNSUB = {H_LIST_UNSUBSCRIBE: "<mailto:leave@lists.example>"}


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.sqlite3")
    d.upsert_mailboxes([{"id": "mb-inbox", "name": "Inbox", "role": "inbox"}, {"id": "mb-sent", "name": "Sent", "role": "sent"},
                        {"id": "mb-trash", "name": "Trash", "role": "trash"}, {"id": "mb-junk", "name": "Spam", "role": "junk"},
                        {"id": "mb-work", "name": "Work"}])
    d.set_identities([{"id": "i1", "email": "me@example.com", "name": "Me"}, {"id": "i2", "email": "*@example.org"}])
    yield d
    d.close()


def email(eid: str, subject: str, frm: str, name: str = "", when: str = "2026-09-01T10:00:00Z", thread: str | None = None,
          mailboxes: tuple[str, ...] = ("mb-inbox",), seen: bool = False, flagged: bool = False, size: int = 1000,
          attachment: bool = False, preview: str = "", to: str = "me@example.com", **headers) -> dict:
    keywords = {}
    if seen:
        keywords["$seen"] = True
    if flagged:
        keywords["$flagged"] = True
    return {"id": eid, "threadId": thread or f"T-{eid}", "subject": subject, "preview": preview,
            "from": [{"email": frm, "name": name}], "to": [{"email": to}], "receivedAt": when, "keywords": keywords,
            "mailboxIds": dict.fromkeys(mailboxes, True), "size": size, "hasAttachment": attachment, **headers}


def ids(v: views.View, db: Database, **kw) -> list[str]:
    kw.setdefault("now", NOW)
    return views.list_ids(db, v, TRASH_JUNK, **kw)


# ------------------------------------------------------------ category views


def test_category_views_list_one_row_per_thread_outside_trash(db):
    db.upsert_emails([
        email("a", "Digest #1", "digest@lists.example", "Weekly Digest", when="2026-09-01T10:00:00Z", **UNSUB),
        email("a2", "Digest #2", "digest@lists.example", "Weekly Digest", when="2026-09-03T10:00:00Z", thread="T-a", seen=True, **UNSUB),
        email("b", "Lunch?", "anna@example.net", "Anna"),
        email("c", "Digest #0", "digest@lists.example", "Weekly Digest", mailboxes=("mb-trash",), **UNSUB),
        email("d", "Your order has shipped", "noreply@shop.example", when="2026-09-02T10:00:00Z"),
        email("j", "Digest #9", "digest@lists.example", "Weekly Digest", mailboxes=("mb-junk",), **UNSUB),
    ])
    assert ids(NEWSLETTERS, db) == ["a2"]                       # the thread's newest matching message
    assert ids(NEWSLETTERS, db, sort="oldest") == ["a"]
    assert ids(NEWSLETTERS, db, collapse=False) == ["a2", "a"]
    assert ids(NEWSLETTERS, db, unread_only=True) == ["a"]
    assert ids(TRANSACTIONS, db) == ["d"]
    assert views.counts(db, NEWSLETTERS, TRASH_JUNK) == (1, 1)  # one thread, with an unread message
    assert views.counts(db, TRANSACTIONS, TRASH_JUNK) == (1, 1)
    # Trash and Spam only stay out because the caller names them
    assert views.list_ids(db, NEWSLETTERS, []) == ["a2", "c", "j"]
    totals = views.all_counts(db, TRASH_JUNK)
    assert set(totals) == {v.id for v in views.VIEWS}
    assert totals["view:security"] == (0, 0)
    # a reclassification moves a thread between views at once
    db.set_category(["a2", "a"], "transactions")
    assert ids(NEWSLETTERS, db) == [] and ids(TRANSACTIONS, db) == ["a2", "d"]
    db.delete_emails(["a2", "a"])
    assert ids(TRANSACTIONS, db) == ["d"]


def test_sort_choices_match_the_mailbox_list(db):
    db.upsert_emails([
        email("a", "Bravo", "z@lists.example", "Alpha Letter", when="2026-09-01T10:00:00Z", size=30, **UNSUB),
        email("b", "alpha", "b@lists.example", "", when="2026-09-02T10:00:00Z", size=20, seen=True, **UNSUB),
        email("c", "Charlie", "c@lists.example", "Charlie News", when="2026-09-03T10:00:00Z", size=10, flagged=True, **UNSUB),
    ])
    assert ids(NEWSLETTERS, db) == ["c", "b", "a"]
    assert ids(NEWSLETTERS, db, sort="oldest") == ["a", "b", "c"]
    assert ids(NEWSLETTERS, db, sort="sender") == ["a", "b", "c"]     # the name when there is one, else the address
    assert ids(NEWSLETTERS, db, sort="subject") == ["b", "a", "c"]    # case does not matter
    assert ids(NEWSLETTERS, db, sort="size") == ["a", "b", "c"]
    assert ids(NEWSLETTERS, db, sort="oldest", flagged_first=True) == ["c", "a", "b"]
    assert ids(NEWSLETTERS, db, sort="oldest", unread_first=True) == ["a", "c", "b"]
    assert ids(NEWSLETTERS, db, sort="nonsense") == ["c", "b", "a"]


# ------------------------------------------------------------ never read


def test_never_read_lists_senders_whose_mail_was_never_opened(db):
    old, older, recent = "2026-06-01T10:00:00Z", "2026-05-01T10:00:00Z", "2026-08-20T10:00:00Z"
    db.upsert_emails([
        # x: three unread messages, the oldest well over the age limit
        email("x1", "Offer", "x@shop.example", when=older), email("x2", "Offer", "x@shop.example", when=old),
        email("x3", "Offer", "x@shop.example", when="2026-09-03T10:00:00Z"),
        # y: one of the two was read
        email("y1", "Hi", "y@shop.example", when=older), email("y2", "Hi", "y@shop.example", when=old, seen=True),
        # z: a single message
        email("z1", "Hi", "z@shop.example", when=older),
        # w: two unread, but the oldest is recent
        email("w1", "Hi", "w@shop.example", when=recent), email("w2", "Hi", "w@shop.example", when="2026-09-01T10:00:00Z"),
        # v: two old unread, but the user has written to v
        email("v1", "Hi", "v@example.net", when=older), email("v2", "Hi", "v@example.net", when=old),
        email("s1", "Re: Hi", "me@example.com", to="v@example.net", mailboxes=("mb-sent",), seen=True),
        # o: the user's own alias domain
        email("o1", "Hi", "someone@example.org", when=older), email("o2", "Hi", "someone@example.org", when=old),
        # t: two old unread, both in Trash
        email("t1", "Hi", "t@shop.example", when=older, mailboxes=("mb-trash",)),
        email("t2", "Hi", "t@shop.example", when=old, mailboxes=("mb-trash",)),
    ])
    assert ids(NEVER_READ, db) == ["x3", "x2", "x1"]
    assert views.counts(db, NEVER_READ, TRASH_JUNK, now=NOW) == (3, 3)
    # opening one message of the sender takes the sender out of the view
    db.patch_email("x2", keywords={"$seen": True})
    assert ids(NEVER_READ, db) == []
    # the age limit is measured from `now`
    assert ids(NEVER_READ, db, now=datetime(2026, 12, 1, tzinfo=UTC)) == ["w2", "w1"]


def test_big_attachments_are_large_and_have_one(db):
    db.upsert_emails([
        email("a", "Photos", "a@example.net", size=6_000_000, attachment=True, when="2026-09-01T10:00:00Z"),
        email("b", "Video", "b@example.net", size=9_000_000, attachment=True, when="2026-08-01T10:00:00Z"),
        email("c", "Long mail", "c@example.net", size=9_000_000, attachment=False),
        email("d", "Tiny", "d@example.net", size=100, attachment=True),
    ])
    assert ids(BIG, db, sort="size") == ["b", "a"]
    assert ids(BIG, db) == ["a", "b"]
    assert parse_sort(MailboxObject.for_view(BIG).data["sort"])[0] == "size"   # the view's default sort


# ------------------------------------------------------------ local search


def test_search_within_a_view_uses_the_search_box_operators(db):
    db.upsert_emails([
        email("a", "Digest #1", "digest@lists.example", "Weekly Digest", when="2026-09-01T10:00:00Z", preview="Issue one", **UNSUB),
        email("b", "Digest #2", "digest@lists.example", "Weekly Digest", when="2026-09-03T10:00:00Z", seen=True,
              attachment=True, mailboxes=("mb-inbox", "mb-work"), **UNSUB),
        email("c", "100% Arch news", "news@archlinux.org", "Arch Linux", when="2026-09-02T10:00:00Z", **UNSUB),
    ])
    db.set_category(["c"], "newsletters")   # "100%" reads as a promotion to the rules
    search = lambda text: ids(NEWSLETTERS, db, search=text)  # noqa: E731
    assert search("digest") == ["b", "a"]
    assert search("issue") == ["a"]                          # the preview counts
    assert search("from:weekly") == ["b", "a"]               # the display name
    assert search("from:archlinux.org") == ["c"]             # the address
    assert search('subject:"Digest #2"') == ["b"]
    assert search("is:unread") == ["c", "a"] and search("is:read") == ["b"]
    assert search("has:attachment") == ["b"]
    assert search("before:2026-09-02") == ["a"] and search("after:2026-09-02") == ["b", "c"]
    assert search("label:work") == ["b"] and search("in:inbox") == ["b", "c", "a"]
    assert search("label:nothing") == []
    assert search("100%") == ["c"]                           # LIKE wildcards are literal
    assert search("_") == []
    assert search("digest is:read") == ["b"]                 # operators combine


# ------------------------------------------------------------ cache upgrade


def test_old_caches_get_the_view_columns(tmp_path):
    path = tmp_path / "old.sqlite3"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE emails (
            id TEXT PRIMARY KEY, thread_id TEXT, received_at TEXT, subject TEXT, preview TEXT,
            keywords TEXT NOT NULL, mailbox_ids TEXT NOT NULL, has_attachment INTEGER DEFAULT 0,
            json TEXT NOT NULL, body_json TEXT, body_fetched_at REAL);
        CREATE TABLE email_mailboxes (email_id TEXT, mailbox_id TEXT, PRIMARY KEY (email_id, mailbox_id));
    """)
    e = email("a", "Photos", "Anna@Example.net", "Anna", size=7_000_000, attachment=True, seen=True, flagged=True)
    c.execute("INSERT INTO emails(id, thread_id, received_at, subject, preview, keywords, mailbox_ids, has_attachment, json)"
              " VALUES (?,?,?,?,?,?,?,?,?)", ("a", "T-a", e["receivedAt"], "Photos", "", json.dumps(e["keywords"]),
                                              json.dumps(e["mailboxIds"]), 1, json.dumps(e)))
    c.execute("INSERT INTO email_mailboxes VALUES ('a', 'mb-inbox')")
    c.commit()
    c.close()
    db = Database(path)
    row = db.conn().execute("SELECT size, from_email, from_sort, seen, flagged FROM emails WHERE id='a'").fetchone()
    assert tuple(row) == (7_000_000, "anna@example.net", "anna", 1, 1)
    assert views.list_ids(db, BIG, []) == ["a"]
    db.close()
    Database(path).close()   # a second start finds the columns and leaves them alone


# ------------------------------------------------------------ the tree


def test_views_sit_between_folders_and_labels_when_enabled():
    tree = MailboxTree()
    tree.set_views([MailboxObject.for_view(v) for v in views.VIEWS])
    mailboxes = [{"id": "mb-inbox", "name": "Inbox", "role": "inbox"}, {"id": "mb-work", "name": "Work"}]
    tree.update(mailboxes)
    names = lambda: [tree.root.get_item(i).name for i in range(tree.root.get_n_items())]  # noqa: E731
    assert names() == ["Inbox", "Labels", "Work"]
    assert tree.get("view:newsletters") is not None      # addressable while hidden, so the toggle can restore it
    tree.show_views = True
    tree.refresh()
    assert names() == ["Inbox", "Views", *(v.name for v in views.VIEWS), "Labels", "Work"]
    assert [m.name for m in tree.labels()] == ["Work"] and [m.name for m in tree.all()] == ["Inbox", "Work"]
    obj = tree.get("view:never-read")
    assert obj.is_view and not obj.is_system and not obj.may("mayAddItems") and obj.icon_name == "fm-unopened-symbolic"
    tree.update(mailboxes)                                # a sync keeps the views and their counts
    obj.total, obj.unread = 5, 2
    assert tree.get("view:never-read") is obj and names()[1] == "Views"
    tree.show_views = False
    tree.refresh()
    assert names() == ["Inbox", "Labels", "Work"]


# ------------------------------------------------------------ with the engine


def test_views_answer_from_what_the_engine_cached(engine):  # noqa: F811
    key = engine.load_query(mailbox_query_spec(engine.roles[ROLE_INBOX]))
    pump(lambda: any(a[0] == key for a in engine.events.get("query-updated", [])))
    listed = views.list_ids(engine.db, NEWSLETTERS, engine.trash_junk_ids())
    subjects = {engine.db.get_email(i)["subject"] for i in listed}
    assert "Digest #40" in subjects and "Arch Linux news: kernel 7.2 and Plasma 7" in subjects
    assert "Digest #39" not in subjects                 # in Trash
    assert len(listed) == len({engine.db.get_email(i)["threadId"] for i in listed})
    total, unread = views.counts(engine.db, NEWSLETTERS, engine.trash_junk_ids())
    assert total == len(listed) and 0 < unread <= total
