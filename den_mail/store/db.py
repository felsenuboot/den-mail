"""SQLite cache: the local source of truth the UI reads from.

One connection per thread (SQLite connections are not thread-safe); WAL mode
so the UI thread can read while the sync thread writes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..jmap.types import KW_DRAFT, KW_FLAGGED, KW_SEEN, address_display

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS mailboxes (
    id TEXT PRIMARY KEY, name TEXT, parent_id TEXT, role TEXT, sort_order INTEGER DEFAULT 0,
    total_emails INTEGER DEFAULT 0, unread_emails INTEGER DEFAULT 0,
    total_threads INTEGER DEFAULT 0, unread_threads INTEGER DEFAULT 0,
    is_subscribed INTEGER DEFAULT 1, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY, thread_id TEXT, received_at TEXT, subject TEXT, preview TEXT,
    keywords TEXT NOT NULL, mailbox_ids TEXT NOT NULL, has_attachment INTEGER DEFAULT 0,
    json TEXT NOT NULL, body_json TEXT, body_fetched_at REAL);
CREATE INDEX IF NOT EXISTS emails_thread ON emails(thread_id);
CREATE INDEX IF NOT EXISTS emails_received ON emails(received_at);
CREATE TABLE IF NOT EXISTS email_mailboxes (email_id TEXT, mailbox_id TEXT, PRIMARY KEY (email_id, mailbox_id));
CREATE INDEX IF NOT EXISTS email_mailboxes_mb ON email_mailboxes(mailbox_id);
CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, email_ids TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS identities (id TEXT PRIMARY KEY, email TEXT, name TEXT, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS masked_emails (id TEXT PRIMARY KEY, email TEXT, state TEXT, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS query_cache (
    key TEXT PRIMARY KEY, spec TEXT NOT NULL, ids TEXT NOT NULL, total INTEGER, query_state TEXT,
    can_calculate_changes INTEGER DEFAULT 0, fetched_at REAL, complete INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS addresses (email TEXT PRIMARY KEY, name TEXT, last_seen TEXT, count INTEGER DEFAULT 1);
"""


@dataclass
class ThreadSummary:
    """Aggregate of the cached emails of one thread, as shown in the list."""

    thread_id: str
    email_id: str  # representative (latest) email
    subject: str
    preview: str
    received_at: str
    participants: list[str]
    count: int
    unread: bool
    flagged: bool
    has_attachment: bool
    is_draft: bool
    mailbox_ids: set[str] = field(default_factory=set)
    email_ids: list[str] = field(default_factory=list)
    from_addresses: list[dict] = field(default_factory=list)


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._local = threading.local()
        self._write_lock = threading.RLock()
        with self.conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.executescript(SCHEMA)
            c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.conn = c
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    def clear_all(self) -> None:
        with self._write_lock:
            c = self.conn()
            for table in ("mailboxes", "emails", "email_mailboxes", "threads", "identities", "masked_emails",
                          "query_cache", "addresses"):
                c.execute(f"DELETE FROM {table}")
            c.execute("DELETE FROM meta WHERE key LIKE 'state:%'")

    # ------------------------------------------------------------------ meta

    def get_meta(self, key: str) -> str | None:
        row = self.conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str | None) -> None:
        with self._write_lock:
            if value is None:
                self.conn().execute("DELETE FROM meta WHERE key=?", (key,))
            else:
                self.conn().execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    def get_state(self, kind: str) -> str | None:
        return self.get_meta(f"state:{kind}")

    def set_state(self, kind: str, state: str | None) -> None:
        self.set_meta(f"state:{kind}", state)

    def get_session(self) -> dict | None:
        raw = self.get_meta("session")
        return json.loads(raw) if raw else None

    def set_session(self, session: dict) -> None:
        self.set_meta("session", json.dumps(session))

    # ------------------------------------------------------------- mailboxes

    def upsert_mailboxes(self, mailboxes: list[dict]) -> None:
        with self._write_lock:
            c = self.conn()
            c.executemany(
                "INSERT OR REPLACE INTO mailboxes(id, name, parent_id, role, sort_order, total_emails, unread_emails,"
                " total_threads, unread_threads, is_subscribed, json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        m["id"], m.get("name", ""), m.get("parentId"), m.get("role"), m.get("sortOrder", 0) or 0,
                        m.get("totalEmails", 0), m.get("unreadEmails", 0), m.get("totalThreads", 0),
                        m.get("unreadThreads", 0), 1 if m.get("isSubscribed", True) else 0, json.dumps(m),
                    )
                    for m in mailboxes
                ],
            )

    def delete_mailboxes(self, ids: list[str]) -> None:
        with self._write_lock:
            self.conn().executemany("DELETE FROM mailboxes WHERE id=?", [(i,) for i in ids])

    def get_mailboxes(self) -> list[dict]:
        rows = self.conn().execute("SELECT json FROM mailboxes").fetchall()
        return [json.loads(r["json"]) for r in rows]

    def get_mailbox(self, mailbox_id: str) -> dict | None:
        row = self.conn().execute("SELECT json FROM mailboxes WHERE id=?", (mailbox_id,)).fetchone()
        return json.loads(row["json"]) if row else None

    def mailbox_by_role(self, role: str) -> dict | None:
        row = self.conn().execute("SELECT json FROM mailboxes WHERE role=?", (role,)).fetchone()
        return json.loads(row["json"]) if row else None

    def adjust_mailbox_counts(self, deltas: dict[str, tuple[int, int]]) -> None:
        """Optimistically adjust (total_emails, unread_emails) per mailbox id."""
        with self._write_lock:
            c = self.conn()
            for mid, (d_total, d_unread) in deltas.items():
                row = c.execute("SELECT json FROM mailboxes WHERE id=?", (mid,)).fetchone()
                if not row:
                    continue
                m = json.loads(row["json"])
                m["totalEmails"] = max(0, (m.get("totalEmails") or 0) + d_total)
                m["unreadEmails"] = max(0, (m.get("unreadEmails") or 0) + d_unread)
                c.execute("UPDATE mailboxes SET total_emails=?, unread_emails=?, json=? WHERE id=?",
                          (m["totalEmails"], m["unreadEmails"], json.dumps(m), mid))

    # ---------------------------------------------------------------- emails

    def upsert_emails(self, emails: list[dict]) -> None:
        """Insert or merge list-property Email objects (body cache is preserved)."""
        with self._write_lock:
            c = self.conn()
            for e in emails:
                existing = c.execute("SELECT json, body_json, body_fetched_at FROM emails WHERE id=?",
                                     (e["id"],)).fetchone()
                merged = e
                body_json = None
                if existing:
                    merged = {**json.loads(existing["json"]), **e}
                    body_json = existing["body_json"]
                    if body_json:
                        body = json.loads(body_json)
                        body.update(e)
                        body_json = json.dumps(body)
                keywords = merged.get("keywords") or {}
                mailbox_ids = merged.get("mailboxIds") or {}
                c.execute(
                    "INSERT OR REPLACE INTO emails(id, thread_id, received_at, subject, preview, keywords, mailbox_ids,"
                    " has_attachment, json, body_json, body_fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        merged["id"], merged.get("threadId"), merged.get("receivedAt"), merged.get("subject"),
                        merged.get("preview"), json.dumps(keywords), json.dumps(mailbox_ids),
                        1 if merged.get("hasAttachment") else 0, json.dumps(merged), body_json,
                        existing["body_fetched_at"] if existing and body_json else None,
                    ),
                )
                c.execute("DELETE FROM email_mailboxes WHERE email_id=?", (merged["id"],))
                c.executemany("INSERT OR IGNORE INTO email_mailboxes(email_id, mailbox_id) VALUES (?,?)",
                              [(merged["id"], mid) for mid, on in mailbox_ids.items() if on])
                self._record_addresses(c, merged)

    def _record_addresses(self, c: sqlite3.Connection, e: dict) -> None:
        seen = e.get("receivedAt") or ""
        for key in ("from", "to", "cc", "replyTo"):
            for a in e.get(key) or []:
                addr = (a.get("email") or "").strip().lower()
                if not addr or "@" not in addr:
                    continue
                c.execute(
                    "INSERT INTO addresses(email, name, last_seen, count) VALUES (?,?,?,1)"
                    " ON CONFLICT(email) DO UPDATE SET count=count+1,"
                    " name=CASE WHEN excluded.name != '' THEN excluded.name ELSE addresses.name END,"
                    " last_seen=MAX(last_seen, excluded.last_seen)",
                    (addr, (a.get("name") or "").strip(), seen),
                )

    def delete_emails(self, ids: list[str]) -> None:
        with self._write_lock:
            c = self.conn()
            c.executemany("DELETE FROM emails WHERE id=?", [(i,) for i in ids])
            c.executemany("DELETE FROM email_mailboxes WHERE email_id=?", [(i,) for i in ids])

    def get_email(self, email_id: str) -> dict | None:
        row = self.conn().execute("SELECT json FROM emails WHERE id=?", (email_id,)).fetchone()
        return json.loads(row["json"]) if row else None

    def get_emails(self, ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        c = self.conn()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"SELECT json FROM emails WHERE id IN ({','.join('?' * len(chunk))})"
            for row in c.execute(q, chunk):
                e = json.loads(row["json"])
                out[e["id"]] = e
        return out

    def missing_email_ids(self, ids: list[str]) -> list[str]:
        have = self.get_emails(ids)
        return [i for i in ids if i not in have]

    def get_email_body(self, email_id: str) -> dict | None:
        row = self.conn().execute("SELECT body_json FROM emails WHERE id=?", (email_id,)).fetchone()
        return json.loads(row["body_json"]) if row and row["body_json"] else None

    def merge_body_headers(self, email_id: str, headers: dict) -> None:
        with self._write_lock:
            c = self.conn()
            row = c.execute("SELECT body_json FROM emails WHERE id=?", (email_id,)).fetchone()
            if row and row["body_json"]:
                body = json.loads(row["body_json"])
                body.update(headers)
                c.execute("UPDATE emails SET body_json=? WHERE id=?", (json.dumps(body), email_id))
                c.commit()

    def set_email_body(self, email: dict) -> None:
        with self._write_lock:
            c = self.conn()
            row = c.execute("SELECT id FROM emails WHERE id=?", (email["id"],)).fetchone()
            if not row:
                self.upsert_emails([{k: v for k, v in email.items() if k not in ("bodyValues", "bodyStructure")}])
            c.execute("UPDATE emails SET body_json=?, body_fetched_at=? WHERE id=?",
                      (json.dumps(email), time.time(), email["id"]))

    def patch_email(self, email_id: str, keywords: dict | None = None, mailbox_ids: dict | None = None) -> dict | None:
        """Apply a local (optimistic) change; returns the updated email."""
        with self._write_lock:
            e = self.get_email(email_id)
            if not e:
                return None
            if keywords is not None:
                e["keywords"] = keywords
            if mailbox_ids is not None:
                e["mailboxIds"] = mailbox_ids
            self.upsert_emails([e])
            return e

    def thread_email_ids(self, thread_id: str) -> list[str]:
        row = self.conn().execute("SELECT email_ids FROM threads WHERE id=?", (thread_id,)).fetchone()
        if row:
            return json.loads(row["email_ids"])
        rows = self.conn().execute("SELECT id FROM emails WHERE thread_id=? ORDER BY received_at", (thread_id,))
        return [r["id"] for r in rows]

    def thread_emails(self, thread_id: str) -> list[dict]:
        ids = self.thread_email_ids(thread_id)
        have = self.get_emails(ids)
        emails = [have[i] for i in ids if i in have]
        emails.sort(key=lambda e: e.get("receivedAt") or "")
        return emails

    def thread_summary(self, thread_id: str, mailbox_id: str | None, trash_junk: set[str]) -> ThreadSummary | None:
        emails = self.thread_emails(thread_id)
        if not emails:
            return None
        if mailbox_id:
            scoped = [e for e in emails if (e.get("mailboxIds") or {}).get(mailbox_id)]
        else:
            scoped = [e for e in emails if not set(e.get("mailboxIds") or {}) & trash_junk]
        if not scoped:
            scoped = emails
        latest = scoped[-1]
        participants: list[str] = []
        from_addresses: list[dict] = []
        for e in reversed(scoped):
            for a in e.get("from") or []:
                name = address_display(a)
                if name and name not in participants:
                    participants.append(name)
                    from_addresses.append(a)
        mailbox_ids: set[str] = set()
        for e in scoped:
            mailbox_ids |= {m for m, on in (e.get("mailboxIds") or {}).items() if on}
        kws = [e.get("keywords") or {} for e in scoped]
        return ThreadSummary(
            thread_id=thread_id,
            email_id=latest["id"],
            subject=latest.get("subject") or "",
            preview=latest.get("preview") or "",
            received_at=latest.get("receivedAt") or "",
            participants=participants,
            count=len(scoped),
            unread=any(not k.get(KW_SEEN) for k in kws),
            flagged=any(k.get(KW_FLAGGED) for k in kws),
            has_attachment=any(e.get("hasAttachment") for e in scoped),
            is_draft=any(k.get(KW_DRAFT) for k in kws),
            mailbox_ids=mailbox_ids,
            email_ids=[e["id"] for e in scoped],
            from_addresses=from_addresses,
        )

    # --------------------------------------------------------------- threads

    def upsert_threads(self, threads: list[dict]) -> None:
        with self._write_lock:
            self.conn().executemany("INSERT OR REPLACE INTO threads(id, email_ids) VALUES (?,?)",
                                    [(t["id"], json.dumps(t.get("emailIds") or [])) for t in threads])

    def delete_threads(self, ids: list[str]) -> None:
        with self._write_lock:
            self.conn().executemany("DELETE FROM threads WHERE id=?", [(i,) for i in ids])

    def thread_of_email(self, email_id: str) -> str | None:
        row = self.conn().execute("SELECT thread_id FROM emails WHERE id=?", (email_id,)).fetchone()
        return row["thread_id"] if row else None

    # ------------------------------------------------------------ identities

    def set_identities(self, identities: list[dict]) -> None:
        with self._write_lock:
            c = self.conn()
            c.execute("DELETE FROM identities")
            c.executemany("INSERT INTO identities(id, email, name, json) VALUES (?,?,?,?)",
                          [(i["id"], i.get("email"), i.get("name"), json.dumps(i)) for i in identities])

    def get_identities(self) -> list[dict]:
        rows = self.conn().execute("SELECT json FROM identities ORDER BY email").fetchall()
        return [json.loads(r["json"]) for r in rows]

    # ---------------------------------------------------------- masked email

    def set_masked_emails(self, items: list[dict]) -> None:
        with self._write_lock:
            c = self.conn()
            c.execute("DELETE FROM masked_emails")
            c.executemany("INSERT INTO masked_emails(id, email, state, json) VALUES (?,?,?,?)",
                          [(i["id"], i.get("email"), i.get("state"), json.dumps(i)) for i in items])

    def upsert_masked_emails(self, items: list[dict]) -> None:
        with self._write_lock:
            self.conn().executemany("INSERT OR REPLACE INTO masked_emails(id, email, state, json) VALUES (?,?,?,?)",
                                    [(i["id"], i.get("email"), i.get("state"), json.dumps(i)) for i in items])

    def get_masked_emails(self) -> list[dict]:
        rows = self.conn().execute("SELECT json FROM masked_emails").fetchall()
        items = [json.loads(r["json"]) for r in rows]
        items.sort(key=lambda m: m.get("createdAt") or "", reverse=True)
        return items

    # ----------------------------------------------------------- query cache

    def get_query(self, key: str) -> dict | None:
        row = self.conn().execute("SELECT * FROM query_cache WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        return {
            "key": key, "spec": json.loads(row["spec"]), "ids": json.loads(row["ids"]), "total": row["total"],
            "query_state": row["query_state"], "can_calculate_changes": bool(row["can_calculate_changes"]),
            "fetched_at": row["fetched_at"], "complete": bool(row["complete"]),
        }

    def set_query(self, key: str, spec: dict, ids: list[str], total: int | None, query_state: str | None,
                  can_calculate_changes: bool, complete: bool) -> None:
        with self._write_lock:
            self.conn().execute(
                "INSERT OR REPLACE INTO query_cache(key, spec, ids, total, query_state, can_calculate_changes,"
                " fetched_at, complete) VALUES (?,?,?,?,?,?,?,?)",
                (key, json.dumps(spec), json.dumps(ids), total, query_state, 1 if can_calculate_changes else 0,
                 time.time(), 1 if complete else 0),
            )

    def drop_queries(self) -> None:
        with self._write_lock:
            self.conn().execute("DELETE FROM query_cache")

    # ------------------------------------------------------------- addresses

    def search_addresses(self, prefix: str, limit: int = 8) -> list[dict]:
        like = f"%{prefix.lower()}%"
        rows = self.conn().execute(
            "SELECT email, name FROM addresses WHERE email LIKE ? OR LOWER(name) LIKE ?"
            " ORDER BY count DESC, last_seen DESC LIMIT ?",
            (like, like, limit),
        ).fetchall()
        return [{"email": r["email"], "name": r["name"] or None} for r in rows]
