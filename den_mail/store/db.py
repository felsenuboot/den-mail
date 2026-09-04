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

from ..classify import bayes
from ..classify.rules import (
    CLASSIFY_HEADERS,
    H_LIST_UNSUBSCRIBE,
    PRIMARY,
    SOURCE_BAYES,
    SOURCE_RULES,
    SOURCE_USER,
    SURE,
    classify,
)
from ..jmap.types import (
    KW_DRAFT,
    KW_FLAGGED,
    KW_SEEN,
    address_display,
    contact_emails,
    contact_name,
    contact_photo,
)

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
    json TEXT NOT NULL, body_json TEXT, body_fetched_at REAL,
    size INTEGER DEFAULT 0, from_email TEXT DEFAULT '', from_sort TEXT DEFAULT '',
    seen INTEGER DEFAULT 0, flagged INTEGER DEFAULT 0, has_unsubscribe INTEGER DEFAULT 0);
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
CREATE TABLE IF NOT EXISTS classification (
    email_id TEXT PRIMARY KEY, category TEXT NOT NULL, source TEXT NOT NULL, confidence REAL, ts REAL, reason TEXT);
CREATE INDEX IF NOT EXISTS classification_category ON classification(category);
CREATE TABLE IF NOT EXISTS correspondents (email TEXT PRIMARY KEY, last_written TEXT);
CREATE TABLE IF NOT EXISTS sender_deletions (
    email TEXT PRIMARY KEY, deleted INTEGER DEFAULT 0, deleted_unread INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS screener (email TEXT PRIMARY KEY, decision TEXT NOT NULL, ts REAL);
CREATE TABLE IF NOT EXISTS submissions (email_id TEXT PRIMARY KEY, submission_id TEXT NOT NULL, send_at TEXT);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL, attempts INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS contacts (id TEXT PRIMARY KEY, name TEXT, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contact_emails (email TEXT PRIMARY KEY, contact_id TEXT, name TEXT);
CREATE TABLE IF NOT EXISTS bayes_docs (category TEXT PRIMARY KEY, docs INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS bayes_tokens (category TEXT, token TEXT, count INTEGER NOT NULL, PRIMARY KEY (category, token));
"""

# Columns the sidebar views (#19) filter and sort on, added to caches from before
# they existed and filled from the stored JSON once; the indexes come after that.
VIEW_COLUMNS = (
    ("size", "INTEGER DEFAULT 0"),
    ("from_email", "TEXT DEFAULT ''"),
    ("from_sort", "TEXT DEFAULT ''"),
    ("seen", "INTEGER DEFAULT 0"),
    ("flagged", "INTEGER DEFAULT 0"),
    ("has_unsubscribe", "INTEGER DEFAULT 0"),   # a List-Unsubscribe header (the cleanup ranking, #21)
)
VIEW_INDEXES = """
CREATE INDEX IF NOT EXISTS emails_from ON emails(from_email);
CREATE INDEX IF NOT EXISTS emails_size ON emails(size);
"""


def view_columns(e: dict) -> tuple[int, str, str, int, int, int]:
    """(size, from_email, from_sort, seen, flagged, has_unsubscribe) of a list-property
    Email; from_sort is what the JMAP "from" comparator orders by, the display name
    else the address, lowercased (see models.thread.sender_group_key)."""
    frm = e.get("from") or []
    first = frm[0] if frm and isinstance(frm[0], dict) else {}
    addr = (first.get("email") or "").strip().lower()
    name = (first.get("name") or "").strip().lower()
    kws = e.get("keywords") or {}
    unsub = e.get(H_LIST_UNSUBSCRIBE)
    return (int(e.get("size") or 0), addr, name or addr, 1 if kws.get(KW_SEEN) else 0,
            1 if kws.get(KW_FLAGGED) else 0, 1 if isinstance(unsub, str) and unsub.strip() else 0)


def sender_of(e: dict) -> str:
    return view_columns(e)[1]


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
    category: str = PRIMARY  # of the latest message (#18)


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._local = threading.local()
        self._write_lock = threading.RLock()
        with self.conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.executescript(SCHEMA)
            self._add_view_columns(c)
            c.executescript(VIEW_INDEXES)
            c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        # Who the user is and where sent mail lives: the "written to" signal of the categoriser.
        self._identity_emails: set[str] = set()
        self._identity_domains: set[str] = set()
        self._sent_mailbox_id: str | None = None
        self._load_identity_cache()
        self._load_sent_mailbox()
        self._bayes: bayes.BayesModel = self._load_bayes()

    @staticmethod
    def _add_view_columns(c: sqlite3.Connection) -> None:
        """A cache from before the sidebar views gets their columns, filled from the JSON."""
        have = {row["name"] for row in c.execute("PRAGMA table_info(emails)")}
        missing = [(name, decl) for name, decl in VIEW_COLUMNS if name not in have]
        if not missing:
            return
        c.execute("BEGIN")
        try:
            for name, decl in missing:
                c.execute(f"ALTER TABLE emails ADD COLUMN {name} {decl}")  # nosec B608 - names from VIEW_COLUMNS
            rows = c.execute("SELECT id, json FROM emails").fetchall()
            c.executemany("UPDATE emails SET size=?, from_email=?, from_sort=?, seen=?, flagged=?, has_unsubscribe=?"
                          " WHERE id=?", [(*view_columns(json.loads(r["json"])), r["id"]) for r in rows])
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise

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
                          "query_cache", "addresses", "classification", "correspondents", "sender_deletions",
                          "screener", "bayes_docs", "bayes_tokens", "submissions", "contacts", "contact_emails",
                          "outbox"):
                # table names come from the literal tuple above (Bandit B608)
                c.execute(f"DELETE FROM {table}")  # nosec B608
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
            self._load_sent_mailbox()

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
        """Insert or merge list-property Email objects (body cache is preserved);
        every stored message gets its category from the rules (#18)."""
        with self._write_lock:
            c = self.conn()
            # Sent mail first, so a reply in the same batch counts for its sender's category.
            new_correspondents: set[str] = set()
            for e in emails:
                if self._is_own(e):
                    new_correspondents |= self._record_correspondents(c, e)
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
                    " has_attachment, json, body_json, body_fetched_at, size, from_email, from_sort, seen, flagged,"
                    " has_unsubscribe) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        merged["id"], merged.get("threadId"), merged.get("receivedAt"), merged.get("subject"),
                        merged.get("preview"), json.dumps(keywords), json.dumps(mailbox_ids),
                        1 if merged.get("hasAttachment") else 0, json.dumps(merged), body_json,
                        existing["body_fetched_at"] if existing and body_json else None,
                        *view_columns(merged),
                    ),
                )
                c.execute("DELETE FROM email_mailboxes WHERE email_id=?", (merged["id"],))
                c.executemany("INSERT OR IGNORE INTO email_mailboxes(email_id, mailbox_id) VALUES (?,?)",
                              [(merged["id"], mid) for mid, on in mailbox_ids.items() if on])
                self._record_addresses(c, merged)
                self._classify(c, merged)
            if new_correspondents:
                self._reclassify_from(c, new_correspondents)

    # -------------------------------------------------------- categories

    def _classify(self, c: sqlite3.Connection, e: dict) -> None:
        """Store the rules' verdict, or the learned model's where the rules were unsure
        and the model is confident (#23); a category the user chose (source "user") stays."""
        verdict = classify(e, self.is_correspondent, self.is_own_address)
        category, source, confidence, reason = verdict.category, SOURCE_RULES, verdict.confidence, verdict.reason
        model = self._bayes
        if verdict.confidence < SURE and model.ready:
            pred = model.predict(bayes.tokens(e, self.sender_behaviour(sender_of(e), c)))
            if pred is not None and pred.probability >= bayes.MIN_PROBABILITY and pred.category != verdict.category:
                category, source, confidence, reason = pred.category, SOURCE_BAYES, round(pred.probability, 3), pred.reason
        c.execute(
            "INSERT INTO classification(email_id, category, source, confidence, ts, reason) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(email_id) DO UPDATE SET category=excluded.category, source=excluded.source,"
            " confidence=excluded.confidence, ts=excluded.ts, reason=excluded.reason"
            " WHERE classification.source != ?",
            (e["id"], category, source, confidence, time.time(), reason, SOURCE_USER),
        )

    def sender_behaviour(self, addr: str, c: sqlite3.Connection | None = None) -> dict | None:
        """What the user does with a sender's mail, for the learned layer's tokens."""
        addr = (addr or "").strip().lower()
        if not addr:
            return None
        c = c or self.conn()
        row = c.execute("SELECT COUNT(*) AS count, SUM(seen = 0) AS unread FROM emails WHERE from_email=?", (addr,)).fetchone()
        deleted = c.execute("SELECT deleted_unread FROM sender_deletions WHERE email=?", (addr,)).fetchone()
        replied = c.execute("SELECT 1 FROM correspondents WHERE email=?", (addr,)).fetchone() is not None
        return {"count": int(row["count"] or 0), "unread": int(row["unread"] or 0),
                "deleted_unread": int(deleted["deleted_unread"]) if deleted else 0, "replied": replied}

    # ------------------------------------------------------- learned layer

    def _load_bayes(self) -> bayes.BayesModel:
        c = self.conn()
        docs = {r["category"]: int(r["docs"]) for r in c.execute("SELECT category, docs FROM bayes_docs")}
        rows = [(r["category"], r["token"], int(r["count"])) for r in c.execute("SELECT category, token, count FROM bayes_tokens")]
        corrections = int(self.get_meta("bayes_corrections") or 0)
        return bayes.BayesModel.from_rows(docs, rows, corrections)

    def corrections_count(self) -> int:
        row = self.conn().execute("SELECT COUNT(*) AS n FROM classification WHERE source=?", (SOURCE_USER,)).fetchone()
        return int(row["n"] or 0)

    def retrain_bayes(self, limit: int = 5000) -> tuple[int, int]:
        """Rebuild the model from the user's corrections (weighted) and the rules' sure
        verdicts (the newest `limit`), store it, and use it from now on.  Returns
        (training documents, corrections)."""
        with self._write_lock:
            c = self.conn()
            model = bayes.BayesModel()
            corrections = 0
            for source, weight, cond in ((SOURCE_USER, bayes.CORRECTION_WEIGHT, ""),
                                         (SOURCE_RULES, 1, " AND cl.confidence >= ?")):
                params: list = [source]
                if cond:
                    params.append(SURE)
                params.append(limit)
                # only "?" placeholders and the literal condition above are interpolated (Bandit B608)
                rows = c.execute(
                    "SELECT e.json, cl.category FROM classification cl JOIN emails e ON e.id = cl.email_id"
                    f" WHERE cl.source = ?{cond} ORDER BY e.received_at DESC LIMIT ?", params).fetchall()  # nosec B608
                for r in rows:
                    e = json.loads(r["json"])
                    model.add(r["category"], bayes.tokens(e, self.sender_behaviour(sender_of(e), c)), weight)
                if source == SOURCE_USER:
                    corrections = len(rows)
            model.corrections = corrections
            c.execute("BEGIN")
            try:
                c.execute("DELETE FROM bayes_docs")
                c.execute("DELETE FROM bayes_tokens")
                c.executemany("INSERT INTO bayes_docs(category, docs) VALUES (?,?)", list(model.docs.items()))
                c.executemany("INSERT INTO bayes_tokens(category, token, count) VALUES (?,?,?)", model.rows())
                c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('bayes_corrections', ?)", (str(corrections),))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            self._bayes = model
            return model.size, corrections

    @property
    def bayes_ready(self) -> bool:
        return self._bayes.ready

    def unsure_ids(self, limit: int = 2000) -> list[str]:
        """Messages the rules were unsure about and the user has not decided: the ones
        a freshly trained model may change."""
        rows = self.conn().execute(
            "SELECT email_id FROM classification cl JOIN emails e ON e.id = cl.email_id"
            " WHERE cl.source != ? AND cl.confidence < ? ORDER BY e.received_at DESC LIMIT ?",
            (SOURCE_USER, SURE, limit)).fetchall()
        return [r["email_id"] for r in rows]

    def get_categories(self, ids: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        c = self.conn()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            # only "?" placeholders are interpolated (Bandit B608)
            q = f"SELECT email_id, category FROM classification WHERE email_id IN ({','.join('?' * len(chunk))})"  # nosec B608
            for row in c.execute(q, chunk):
                out[row["email_id"]] = row["category"]
        return out

    def get_classification(self, email_id: str) -> dict | None:
        row = self.conn().execute("SELECT * FROM classification WHERE email_id=?", (email_id,)).fetchone()
        return dict(row) if row else None

    def set_category(self, email_ids: list[str], category: str, source: str = SOURCE_USER) -> None:
        """A correction by the user that the rules and the learned layer will not overwrite."""
        with self._write_lock:
            self.conn().executemany(
                "INSERT OR REPLACE INTO classification(email_id, category, source, confidence, ts, reason)"
                " VALUES (?,?,?,?,?,?)",
                [(eid, category, source, 1.0, time.time(), "your choice" if source == SOURCE_USER else "")
                 for eid in email_ids])

    def reclassify(self, ids: list[str] | None = None) -> list[str]:
        """Run the rules again over the given (else all) cached messages; returns their ids."""
        with self._write_lock:
            c = self.conn()
            emails = list(self.get_emails(ids).values()) if ids is not None else [
                json.loads(r["json"]) for r in c.execute("SELECT json FROM emails").fetchall()]
            for e in emails:
                self._classify(c, e)
            return [e["id"] for e in emails]

    def _reclassify_from(self, c: sqlite3.Connection, senders: set[str]) -> list[str]:
        """Mail already cached from people the user has now written to; returns its ids."""
        done: list[str] = []
        for addr in senders:
            rows = c.execute("SELECT json FROM emails WHERE LOWER(json) LIKE ?",
                             (f'%"email": "{addr.lower()}"%',)).fetchall()
            for r in rows:
                e = json.loads(r["json"])
                if any((a.get("email") or "").strip().lower() == addr for a in e.get("from") or []):
                    self._classify(c, e)
                    done.append(e["id"])
        return done

    def emails_missing_headers(self, limit: int = 500) -> list[str]:
        """Cached before the categoriser's headers were part of the list fetch (#18)."""
        marker = f'"{CLASSIFY_HEADERS[0]}"'
        rows = self.conn().execute("SELECT id FROM emails WHERE instr(json, ?) = 0 ORDER BY received_at DESC LIMIT ?",
                                   (marker, limit)).fetchall()
        return [r["id"] for r in rows]

    def merge_headers(self, ids: list[str], fetched: list[dict]) -> None:
        """Add the categoriser's headers to cached messages and classify them again;
        ids the server no longer knows are marked as fetched (with no headers) so
        the backfill moves on."""
        by_id = {e["id"]: e for e in fetched}
        with self._write_lock:
            c = self.conn()
            for eid in ids:
                row = c.execute("SELECT json, body_json FROM emails WHERE id=?", (eid,)).fetchone()
                if not row:
                    continue
                got = by_id.get(eid, {})
                headers = {h: got.get(h) for h in CLASSIFY_HEADERS}
                e = json.loads(row["json"])
                e.update(headers)
                body_json = row["body_json"]
                if body_json:
                    body = json.loads(body_json)
                    body.update(headers)
                    body_json = json.dumps(body)
                c.execute("UPDATE emails SET json=?, body_json=? WHERE id=?", (json.dumps(e), body_json, eid))
                self._classify(c, e)

    # ----------------------------------------------------- correspondents

    def _load_identity_cache(self) -> None:
        emails, domains = set(), set()
        for i in self.get_identities():
            addr = (i.get("email") or "").strip().lower()
            if addr.startswith("*@"):
                domains.add(addr[2:])
            elif addr:
                emails.add(addr)
        self._identity_emails, self._identity_domains = emails, domains

    def _load_sent_mailbox(self) -> None:
        row = self.conn().execute("SELECT id FROM mailboxes WHERE role='sent'").fetchone()
        self._sent_mailbox_id = row["id"] if row else None

    def own_addresses(self) -> tuple[set[str], set[str]]:
        """The user's identity addresses and wildcard domains (`*@example.org`)."""
        return set(self._identity_emails), set(self._identity_domains)

    def is_own_address(self, addr: str) -> bool:
        addr = (addr or "").strip().lower()
        return addr in self._identity_emails or (
            "@" in addr and addr.rsplit("@", 1)[1] in self._identity_domains)

    def _is_own(self, e: dict) -> bool:
        """Sent by the user: in the Sent mailbox, or from one of their identities."""
        if self._sent_mailbox_id and (e.get("mailboxIds") or {}).get(self._sent_mailbox_id):
            return True
        return any(self.is_own_address(a.get("email") or "") for a in e.get("from") or [])

    def record_correspondents(self, sent: list[dict]) -> list[str]:
        """Remember the recipients of sent mail (the Sent folder seed, #18) and put
        cached mail from anyone new through the rules again; returns the ids it reclassified."""
        with self._write_lock:
            c = self.conn()
            new: set[str] = set()
            for e in sent:
                new |= self._record_correspondents(c, e)
            return self._reclassify_from(c, new) if new else []

    def _record_correspondents(self, c: sqlite3.Connection, e: dict) -> set[str]:
        """Remember who the user wrote to; returns the addresses that were new."""
        new: set[str] = set()
        when = e.get("receivedAt") or ""
        for key in ("to", "cc", "bcc"):
            for a in e.get(key) or []:
                addr = (a.get("email") or "").strip().lower()
                if not addr or "@" not in addr or self.is_own_address(addr):
                    continue
                known = c.execute("SELECT 1 FROM correspondents WHERE email=?", (addr,)).fetchone()
                c.execute("INSERT INTO correspondents(email, last_written) VALUES (?,?)"
                          " ON CONFLICT(email) DO UPDATE SET last_written=MAX(last_written, excluded.last_written)",
                          (addr, when))
                if not known:
                    new.add(addr)
        return new

    def record_deletions(self, emails: list[dict]) -> None:
        """The user threw these away (Trash or gone): counted per sender, unread ones
        apart, as the "deleted unread" signal of the cleanup ranking (#21)."""
        rows: dict[str, list[int]] = {}
        for e in emails:
            addr = view_columns(e)[1]
            if not addr:
                continue
            t = rows.setdefault(addr, [0, 0])
            t[0] += 1
            if not (e.get("keywords") or {}).get(KW_SEEN):
                t[1] += 1
        if not rows:
            return
        with self._write_lock:
            self.conn().executemany(
                "INSERT INTO sender_deletions(email, deleted, deleted_unread) VALUES (?,?,?)"
                " ON CONFLICT(email) DO UPDATE SET deleted=deleted+excluded.deleted,"
                " deleted_unread=deleted_unread+excluded.deleted_unread",
                [(addr, d, u) for addr, (d, u) in rows.items()])

    # ------------------------------------------------------------ screener

    def knows_sender(self, addr: str, except_ids: list[str] | set[str] = ()) -> bool:
        """Has this address sent cached mail other than `except_ids`, been written
        to, or been screened already?  The screener (#24) treats anyone else as a
        first-time sender.  `except_ids` is the batch being judged: a sync can
        cache a message through the thread step before Email/changes lists it as
        created, and that copy must not make its own sender known (#42)."""
        addr = (addr or "").strip().lower()
        if not addr or self.is_own_address(addr):
            return True
        c = self.conn()
        if (c.execute("SELECT 1 FROM correspondents WHERE email=?", (addr,)).fetchone() is not None
                or c.execute("SELECT 1 FROM screener WHERE email=?", (addr,)).fetchone() is not None):
            return True
        for row in c.execute("SELECT id FROM emails WHERE from_email=?", (addr,)):
            if row["id"] not in except_ids:
                return True
        return False

    def screener_set(self, addrs: list[str] | set[str], decision: str) -> None:
        """decision: "pending" (in the Screener), "allow" (reaches the Inbox) or "block"."""
        rows = [((a or "").strip().lower(), decision, time.time()) for a in addrs if (a or "").strip()]
        if rows:
            with self._write_lock:
                self.conn().executemany("INSERT OR REPLACE INTO screener(email, decision, ts) VALUES (?,?,?)", rows)

    def screener_decision(self, addr: str) -> str | None:
        row = self.conn().execute("SELECT decision FROM screener WHERE email=?", ((addr or "").strip().lower(),)).fetchone()
        return row["decision"] if row else None

    def screener_pending(self) -> set[str]:
        return {r["email"] for r in self.conn().execute("SELECT email FROM screener WHERE decision='pending'")}

    def is_correspondent(self, addr: str) -> bool:
        """Has the user ever sent mail to this address?"""
        addr = (addr or "").strip().lower()
        if not addr:
            return False
        return self.conn().execute("SELECT 1 FROM correspondents WHERE email=?", (addr,)).fetchone() is not None

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
            c.executemany("DELETE FROM classification WHERE email_id=?", [(i,) for i in ids])

    def get_email(self, email_id: str) -> dict | None:
        row = self.conn().execute("SELECT json FROM emails WHERE id=?", (email_id,)).fetchone()
        return json.loads(row["json"]) if row else None

    def get_emails(self, ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        c = self.conn()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            # only "?" placeholders are interpolated (Bandit B608)
            q = f"SELECT json FROM emails WHERE id IN ({','.join('?' * len(chunk))})"  # nosec B608
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
        category = self.get_categories([latest["id"]]).get(latest["id"], PRIMARY)
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
            category=category,
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

    # ---------------------------------------------------------------- outbox

    def outbox_add(self, kind: str, payload: dict) -> int:
        """Queue a change or a message the server could not be reached for (#8)."""
        with self._write_lock:
            cur = self.conn().execute("INSERT INTO outbox(kind, payload, created) VALUES (?,?,?)",
                                      (kind, json.dumps(payload), time.time()))
            return int(cur.lastrowid)

    def outbox_list(self) -> list[dict]:
        rows = self.conn().execute("SELECT id, kind, payload, created, attempts FROM outbox ORDER BY id").fetchall()
        return [{"id": r["id"], "kind": r["kind"], "payload": json.loads(r["payload"]), "created": r["created"],
                 "attempts": r["attempts"]} for r in rows]

    def outbox_count(self) -> int:
        return int(self.conn().execute("SELECT COUNT(*) AS n FROM outbox").fetchone()["n"] or 0)

    def outbox_delete(self, row_id: int) -> None:
        with self._write_lock:
            self.conn().execute("DELETE FROM outbox WHERE id=?", (row_id,))

    def outbox_bump(self, row_id: int) -> None:
        with self._write_lock:
            self.conn().execute("UPDATE outbox SET attempts = attempts + 1 WHERE id=?", (row_id,))

    # ----------------------------------------------------------- submissions

    def set_submission(self, email_id: str, submission_id: str, send_at: str | None) -> None:
        """Remember the EmailSubmission of a message sent later (#6), so it can be cancelled."""
        with self._write_lock:
            self.conn().execute("INSERT OR REPLACE INTO submissions(email_id, submission_id, send_at) VALUES (?,?,?)",
                                (email_id, submission_id, send_at))

    def get_submission(self, email_id: str) -> dict | None:
        row = self.conn().execute("SELECT * FROM submissions WHERE email_id=?", (email_id,)).fetchone()
        return dict(row) if row else None

    def delete_submission(self, email_id: str) -> None:
        with self._write_lock:
            self.conn().execute("DELETE FROM submissions WHERE email_id=?", (email_id,))

    # ------------------------------------------------------------ identities

    def set_identities(self, identities: list[dict]) -> None:
        with self._write_lock:
            c = self.conn()
            c.execute("DELETE FROM identities")
            c.executemany("INSERT INTO identities(id, email, name, json) VALUES (?,?,?,?)",
                          [(i["id"], i.get("email"), i.get("name"), json.dumps(i)) for i in identities])
            self._load_identity_cache()

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

    # -------------------------------------------------------------- contacts

    def set_contacts(self, cards: list[dict]) -> None:
        """Replace the address book (a full ContactCard/get, #4)."""
        with self._write_lock:
            c = self.conn()
            c.execute("DELETE FROM contacts")
            c.execute("DELETE FROM contact_emails")
            self._insert_contacts(c, cards)

    def upsert_contacts(self, cards: list[dict]) -> None:
        with self._write_lock:
            c = self.conn()
            for card in cards:
                c.execute("DELETE FROM contact_emails WHERE contact_id=?", (card["id"],))
            self._insert_contacts(c, cards)

    @staticmethod
    def _insert_contacts(c: sqlite3.Connection, cards: list[dict]) -> None:
        for card in cards:
            name = contact_name(card)
            c.execute("INSERT OR REPLACE INTO contacts(id, name, json) VALUES (?,?,?)", (card["id"], name, json.dumps(card)))
            c.executemany("INSERT OR REPLACE INTO contact_emails(email, contact_id, name) VALUES (?,?,?)",
                          [(addr, card["id"], name) for addr in contact_emails(card)])

    def delete_contacts(self, ids: list[str]) -> None:
        with self._write_lock:
            c = self.conn()
            c.executemany("DELETE FROM contacts WHERE id=?", [(i,) for i in ids])
            c.executemany("DELETE FROM contact_emails WHERE contact_id=?", [(i,) for i in ids])

    def contact_count(self) -> int:
        return int(self.conn().execute("SELECT COUNT(*) AS n FROM contacts").fetchone()["n"] or 0)

    def contact_for(self, email: str | None) -> dict | None:
        """The ContactCard of an address, if the address book has it."""
        addr = (email or "").strip().lower()
        if not addr:
            return None
        row = self.conn().execute("SELECT c.json FROM contact_emails ce JOIN contacts c ON c.id = ce.contact_id"
                                  " WHERE ce.email=?", (addr,)).fetchone()
        return json.loads(row["json"]) if row else None

    def contact_photo_for(self, email: str | None) -> tuple[str, str, str] | None:
        """(contact id, blobId, mediaType) of the photo of the contact behind an address."""
        card = self.contact_for(email)
        if not card:
            return None
        photo = contact_photo(card)
        return (card["id"], photo[0], photo[1]) if photo else None

    # ------------------------------------------------------------- addresses

    def search_addresses(self, prefix: str, limit: int = 8) -> list[dict]:
        """Completion candidates: the address book first, then addresses seen in cached mail."""
        like = f"%{prefix.lower()}%"
        c = self.conn()
        out: list[dict] = []
        seen: set[str] = set()
        for r in c.execute("SELECT email, name FROM contact_emails WHERE email LIKE ? OR LOWER(name) LIKE ?"
                           " ORDER BY name, email LIMIT ?", (like, like, limit)):
            out.append({"email": r["email"], "name": r["name"] or None})
            seen.add(r["email"])
        rows = c.execute(
            "SELECT email, name FROM addresses WHERE email LIKE ? OR LOWER(name) LIKE ?"
            " ORDER BY count DESC, last_seen DESC LIMIT ?",
            (like, like, limit),
        ).fetchall()
        for r in rows:
            if r["email"] not in seen and len(out) < limit:
                out.append({"email": r["email"], "name": r["name"] or None})
        return out
