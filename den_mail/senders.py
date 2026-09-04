"""Sender statistics from the cache (#21): who sends how much, how much of it is
read, and a "pointless" ranking for the cleanup dialog.

Everything comes from the local cache, so the numbers cover the mail that has
been listed since the app was installed, not the whole account.  Signals:

- volume and unread ratio per sender (the ``emails`` table),
- whether the user ever wrote to the sender (``correspondents``, seeded from
  the Sent folder by the categoriser),
- how much of the sender's mail the user threw away unread (``sender_deletions``,
  counted by the engine as actions happen),
- whether the mail carries a List-Unsubscribe header.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from .classify.rules import PRIMARY
from .store.db import Database

MAX_SENDERS = 400


@dataclass
class SenderStats:
    email: str
    name: str
    count: int
    unread: int
    first_at: str
    last_at: str
    size: int
    unsubscribe: bool         # some message carries List-Unsubscribe
    replied: bool             # the user has written to this address
    deleted: int              # messages the user trashed or destroyed
    deleted_unread: int       # ... of which unread at the time
    newest_id: str
    newest_subject: str
    category: str = PRIMARY   # of the newest message

    @property
    def unread_ratio(self) -> float:
        return self.unread / self.count if self.count else 0.0

    @property
    def score(self) -> float:
        """How pointless the sender's mail looks: high volume the user never opens
        or throws away unread, from a list they never write to.  Someone the user
        has replied to scores below zero whatever else they send."""
        volume = math.log2(self.count + 1)
        if self.replied:
            return round(-volume, 2)
        seen = self.count + self.deleted
        unread_ratio = (self.unread + self.deleted_unread) / seen if seen else 0.0
        deleted_ratio = self.deleted / seen if seen else 0.0
        return round(volume * (1 + 3 * unread_ratio) + 3 * deleted_ratio + (1.5 if self.unsubscribe else 0.0), 2)

    @property
    def score_text(self) -> str:
        if self.replied:
            return "you wrote back"
        parts = []
        if self.count and self.unread == self.count:
            parts.append("never opened")
        elif self.unread_ratio >= 0.7:
            parts.append("mostly unread")
        if self.deleted_unread:
            parts.append(f"{self.deleted_unread} deleted unread")
        if self.unsubscribe:
            parts.append("unsubscribe available")
        return ", ".join(parts) or "read"


def _where(trash_junk: list[str], category: str | None) -> tuple[str, list]:
    conds = ["e.from_email != ''"]
    params: list = []
    if trash_junk:
        marks = ",".join("?" * len(trash_junk))
        # only "?" placeholders are interpolated (Bandit B608)
        conds.append("NOT EXISTS (SELECT 1 FROM email_mailboxes x WHERE x.email_id = e.id"  # nosec B608
                     f" AND x.mailbox_id IN ({marks}))")
        params += trash_junk
    if category:
        conds.append("e.id IN (SELECT email_id FROM classification WHERE category = ?)")
        params.append(category)
    return " AND ".join(conds), params


def sender_stats(db: Database, trash_junk: list[str], category: str | None = None,
                 limit: int = MAX_SENDERS) -> list[SenderStats]:
    """One entry per sender of cached mail outside Trash and Spam (in `category`
    when given), the most pointless first; the user's own addresses are left out."""
    where, params = _where(trash_junk, category)
    own, domains = db.own_addresses()
    # only "?" placeholders and the literal clauses above are interpolated (Bandit B608)
    sql = (
        "SELECT e.from_email AS email, COUNT(*) AS count, SUM(e.seen = 0) AS unread,"
        " MIN(e.received_at) AS first_at, MAX(e.received_at) AS last_at, SUM(e.size) AS size,"
        " MAX(e.has_unsubscribe) AS unsubscribe,"
        " (SELECT n.id FROM emails n WHERE n.from_email = e.from_email ORDER BY n.received_at DESC LIMIT 1) AS newest_id,"
        " (SELECT a.name FROM addresses a WHERE a.email = e.from_email) AS name,"
        " EXISTS (SELECT 1 FROM correspondents c WHERE c.email = e.from_email) AS replied,"
        " COALESCE((SELECT d.deleted FROM sender_deletions d WHERE d.email = e.from_email), 0) AS deleted,"
        " COALESCE((SELECT d.deleted_unread FROM sender_deletions d WHERE d.email = e.from_email), 0) AS deleted_unread"
        f" FROM emails e WHERE {where} GROUP BY e.from_email"  # nosec B608
    )
    rows = db.conn().execute(sql, params).fetchall()
    out: list[SenderStats] = []
    for r in rows:
        addr = r["email"]
        if addr in own or any(addr.endswith("@" + d) for d in domains):
            continue
        out.append(SenderStats(addr, (r["name"] or "").strip() or addr, int(r["count"]), int(r["unread"] or 0),
                               r["first_at"] or "", r["last_at"] or "", int(r["size"] or 0), bool(r["unsubscribe"]),
                               bool(r["replied"]), int(r["deleted"]), int(r["deleted_unread"]), r["newest_id"] or "", ""))
    newest = db.get_emails([s.newest_id for s in out if s.newest_id])
    categories = db.get_categories(list(newest))
    for s in out:
        e = newest.get(s.newest_id)
        if e:
            s.newest_subject = e.get("subject") or ""
            s.category = categories.get(s.newest_id, PRIMARY)
    out.sort(key=lambda s: (-s.score, -s.count, s.name.lower()))
    return out[:limit]


def messages_of(db: Database, email: str, trash_junk: list[str], limit: int = 8) -> list[dict]:
    """The sender's newest cached messages outside Trash and Spam."""
    where, params = _where(trash_junk, None)
    sql = (f"SELECT e.id, e.thread_id, e.subject, e.received_at, e.seen FROM emails e WHERE {where}"  # nosec B608
           " AND e.from_email = ? ORDER BY e.received_at DESC LIMIT ?")
    return [dict(r) for r in db.conn().execute(sql, [*params, email.strip().lower(), limit])]


def newest_list_mail(db: Database, email: str, trash_junk: list[str]) -> dict | None:
    """The sender's newest cached message that carries List-Unsubscribe, for the unsubscribe plan."""
    where, params = _where(trash_junk, None)
    row = db.conn().execute(
        f"SELECT e.json FROM emails e WHERE {where} AND e.from_email = ? AND e.has_unsubscribe = 1"  # nosec B608
        " ORDER BY e.received_at DESC LIMIT 1", [*params, email.strip().lower()]).fetchone()
    return json.loads(row["json"]) if row else None
