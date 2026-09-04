"""Sidebar views (#19): lists answered from the local cache, not JMAP mailboxes.

A view is a SQL query over the cached messages outside Trash and Spam: the
category views (Newsletters, Transactions, Security, Updates) read the
categoriser's table (#18), "Never read" finds senders whose mail the user
has never opened, "Big attachments" the messages worth deleting for space.
The list shows one row per thread, like a JMAP query with collapseThreads,
sorted with the same choices as a mailbox; a search typed while a view is
shown filters the view here, with the same operators the search box knows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .classify.rules import NEWSLETTERS, SECURITY, TRANSACTIONS, UPDATES
from .store.db import Database
from .store.sync import resolve_mailbox, search_date, search_tokens

VIEW_PREFIX = "view:"
NEVER_READ = "view:never-read"
BIG_ATTACHMENTS = "view:big-attachments"

# "Never read": a sender counts once at least this many messages are cached, none of
# them opened, and the oldest of them has been waiting this long.
NEVER_READ_MIN_MESSAGES = 2
NEVER_READ_MIN_AGE_DAYS = 60
# "Big attachments": messages with an attachment that are at least this large.
BIG_ATTACHMENT_BYTES = 5_000_000


@dataclass(frozen=True)
class View:
    id: str
    name: str
    icon: str
    empty: str              # the empty list's explanation
    category: str | None = None
    default_sort: str = "newest"


VIEWS: tuple[View, ...] = (
    View("view:newsletters", "Newsletters", "fm-newsletter-symbolic",
         "No cached message was sorted into Newsletters.", category=NEWSLETTERS),
    View("view:transactions", "Transactions", "fm-receipt-symbolic",
         "No cached message was sorted into Transactions.", category=TRANSACTIONS),
    View("view:security", "Security", "fm-shield-symbolic",
         "No cached message was sorted into Security.", category=SECURITY),
    View("view:updates", "Updates", "fm-bell-symbolic",
         "No cached message was sorted into Updates.", category=UPDATES),
    View(NEVER_READ, "Never read", "fm-unopened-symbolic",
         f"Senders with {NEVER_READ_MIN_MESSAGES} or more cached messages, none of them opened, "
         f"the oldest at least {NEVER_READ_MIN_AGE_DAYS} days old."),
    View(BIG_ATTACHMENTS, "Big attachments", "fm-attachment-symbolic",
         f"No cached message with an attachment is {BIG_ATTACHMENT_BYTES // 1_000_000} MB or more.",
         default_sort="size"),
)
_BY_ID = {v.id: v for v in VIEWS}


def is_view_id(mailbox_id: str | None) -> bool:
    return bool(mailbox_id) and mailbox_id.startswith(VIEW_PREFIX)


def get_view(view_id: str) -> View | None:
    return _BY_ID.get(view_id)


# ------------------------------------------------------------------ SQL

_ORDER = {
    "newest": "e.received_at DESC",
    "oldest": "e.received_at ASC",
    "sender": "e.from_sort ASC, e.received_at DESC",
    "subject": "LOWER(COALESCE(e.subject, '')) ASC, e.received_at DESC",
    "size": "e.size DESC, e.received_at DESC",
}


def _order(key: str, flagged_first: bool, unread_first: bool) -> str:
    parts = []
    if flagged_first:
        parts.append("e.flagged DESC")
    if unread_first:
        parts.append("e.seen ASC")
    parts.append(_ORDER.get(key, _ORDER["newest"]))
    return ", ".join(parts)


def _escape(value: str) -> str:
    """`value` lowercased with LIKE's wildcards escaped (the clauses say ESCAPE '\\')."""
    return value.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like(value: str) -> str:
    """A LIKE pattern matching `value` anywhere."""
    return "%" + _escape(value) + "%"


def _not_in_mailboxes(alias: str, mailbox_ids: list[str]) -> tuple[str, list]:
    marks = ",".join("?" * len(mailbox_ids))
    # only the table alias and "?" placeholders are interpolated (Bandit B608)
    return (f"NOT EXISTS (SELECT 1 FROM email_mailboxes x WHERE x.email_id = {alias}.id"  # nosec B608
            f" AND x.mailbox_id IN ({marks}))", list(mailbox_ids))


def _never_read_senders(db: Database, trash_junk: list[str], now: datetime) -> tuple[str, list]:
    """Subquery listing the senders whose cached mail the user has never opened."""
    conds = ["s.from_email != ''", "s.from_email NOT IN (SELECT email FROM correspondents)"]
    params: list = []
    if trash_junk:
        sql, p = _not_in_mailboxes("s", trash_junk)
        conds.append(sql)
        params += p
    own, domains = db.own_addresses()
    if own:
        conds.append(f"s.from_email NOT IN ({','.join('?' * len(own))})")
        params += sorted(own)
    for domain in sorted(domains):
        conds.append("s.from_email NOT LIKE ? ESCAPE '\\'")
        params.append("%@" + _escape(domain))
    cutoff = (now - timedelta(days=NEVER_READ_MIN_AGE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # the clauses are the literals above with "?" placeholders (Bandit B608)
    sql = (f"SELECT s.from_email FROM emails s WHERE {' AND '.join(conds)} GROUP BY s.from_email"  # nosec B608
           " HAVING SUM(s.seen) = 0 AND COUNT(*) >= ? AND MIN(s.received_at) <= ?")
    params += [NEVER_READ_MIN_MESSAGES, cutoff]
    return sql, params


def _search_conditions(db: Database, text: str, now: datetime) -> tuple[list[str], list]:
    """The search box grammar (store.sync.search_query_spec) applied to the cache columns."""
    conds: list[str] = []
    params: list = []
    mailboxes: list[dict] | None = None
    for key, value in search_tokens(text):
        if key in ("from", "sender"):
            conds.append("(e.from_sort LIKE ? ESCAPE '\\' OR e.from_email LIKE ? ESCAPE '\\')")
            params += [_like(value)] * 2
        elif key == "subject":
            conds.append("e.subject LIKE ? ESCAPE '\\'")
            params.append(_like(value))
        elif key in ("to", "cc"):
            conds.append("e.json LIKE ? ESCAPE '\\'")
            params.append(_like(value))
        elif key == "is" and value in ("unread", "read", "flagged", "starred", "unflagged"):
            conds.append({"unread": "e.seen = 0", "read": "e.seen = 1", "flagged": "e.flagged = 1",
                          "starred": "e.flagged = 1", "unflagged": "e.flagged = 0"}[value])
        elif key == "has" and value in ("attachment", "attachments"):
            conds.append("e.has_attachment = 1")
        elif key in ("before", "after", "older_than", "newer_than", "older", "newer") and (when := search_date(value, now)):
            conds.append("e.received_at < ?" if key.startswith(("before", "older")) else "e.received_at >= ?")
            params.append(when)
        elif key in ("label", "in"):
            if mailboxes is None:
                mailboxes = db.get_mailboxes()
            mid = resolve_mailbox(value, mailboxes)
            conds.append("EXISTS (SELECT 1 FROM email_mailboxes x WHERE x.email_id = e.id AND x.mailbox_id = ?)")
            params.append(mid or "")
        else:
            word = f"{key}:{value}" if key else value
            conds.append("(e.subject LIKE ? ESCAPE '\\' OR e.preview LIKE ? ESCAPE '\\'"
                         " OR e.from_sort LIKE ? ESCAPE '\\' OR e.from_email LIKE ? ESCAPE '\\')")
            params += [_like(word)] * 4
    return conds, params


def _where(db: Database, view: View, trash_junk: list[str], unread_only: bool, search: str,
           now: datetime) -> tuple[str, list]:
    conds: list[str] = []
    params: list = []
    if trash_junk:
        sql, p = _not_in_mailboxes("e", trash_junk)
        conds.append(sql)
        params += p
    if view.category:
        conds.append("e.id IN (SELECT email_id FROM classification WHERE category = ?)")
        params.append(view.category)
    elif view.id == NEVER_READ:
        sql, p = _never_read_senders(db, trash_junk, now)
        conds.append(f"e.seen = 0 AND e.from_email IN ({sql})")
        params += p
    elif view.id == BIG_ATTACHMENTS:
        conds.append("e.has_attachment = 1 AND e.size >= ?")
        params.append(BIG_ATTACHMENT_BYTES)
    if unread_only:
        conds.append("e.seen = 0")
    if search.strip():
        more, p = _search_conditions(db, search, now)
        conds += more
        params += p
    return " AND ".join(conds) or "1", params


def list_ids(db: Database, view: View, trash_junk: list[str], sort: str = "newest",
             flagged_first: bool = False, unread_first: bool = False, unread_only: bool = False,
             search: str = "", collapse: bool = True, now: datetime | None = None) -> list[str]:
    """The ids the view lists, in order: with `collapse`, one message per thread
    (the first of the thread's matching messages in the sort order), the way
    Email/query answers with collapseThreads; else every matching message."""
    now = now or datetime.now(UTC)
    where, params = _where(db, view, trash_junk, unread_only, search, now)
    order = _order(sort, flagged_first, unread_first)
    if collapse:
        # only "?" placeholders and the sort clauses above are interpolated (Bandit B608)
        sql = (f"SELECT id FROM (SELECT e.*, ROW_NUMBER() OVER (PARTITION BY e.thread_id ORDER BY {order}) AS rn"
               f" FROM emails e WHERE {where}) e WHERE rn = 1 ORDER BY {order}")  # nosec B608
    else:
        sql = f"SELECT id FROM emails e WHERE {where} ORDER BY {order}"  # nosec B608
    return [row["id"] for row in db.conn().execute(sql, params)]


def counts(db: Database, view: View, trash_junk: list[str], now: datetime | None = None) -> tuple[int, int]:
    """(threads, threads with an unread message) the view would list."""
    where, params = _where(db, view, trash_junk, False, "", now or datetime.now(UTC))
    row = db.conn().execute(
        "SELECT COUNT(DISTINCT e.thread_id) AS total,"
        " COUNT(DISTINCT CASE WHEN e.seen = 0 THEN e.thread_id END) AS unread"
        f" FROM emails e WHERE {where}", params).fetchone()  # nosec B608 - see list_ids
    return int(row["total"] or 0), int(row["unread"] or 0)


def all_counts(db: Database, trash_junk: list[str], now: datetime | None = None) -> dict[str, tuple[int, int]]:
    now = now or datetime.now(UTC)
    return {v.id: counts(db, v, trash_junk, now) for v in VIEWS}
