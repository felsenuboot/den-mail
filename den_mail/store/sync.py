"""The sync engine: one worker thread that owns all network I/O and DB writes.

The UI never talks to the server.  It asks the engine for things (load a
query, fetch a body, perform an action) and listens to GObject signals that
are always emitted on the main thread.
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from gi.repository import GLib, GObject

from .. import rules as client_rules
from ..classify.rules import CLASSIFY_HEADERS, RULES_VERSION
from ..config import Config, attachments_dir
from ..jmap.client import (
    AuthError,
    JMAPClient,
    JMAPError,
    MethodError,
    RateLimited,
    Request,
    SetError,
    TransportError,
    check_set_response,
)
from ..jmap.push import PushListener
from ..jmap.types import (
    CAP_CONTACTS,
    CAP_CORE,
    CAP_MASKED_EMAIL,
    EMAIL_BODY_PROPERTIES,
    EMAIL_LIST_PROPERTIES,
    KW_DRAFT,
    KW_SEEN,
    MAX_BODY_VALUE_BYTES,
    ROLE_ARCHIVE,
    ROLE_DRAFTS,
    ROLE_INBOX,
    ROLE_JUNK,
    ROLE_SENT,
    ROLE_TRASH,
)
from .actions import EmailAction, RestoreAction, UndoRecord
from .db import Database

log = logging.getLogger(__name__)

PRIO_ACTION = 0
PRIO_LOAD = 1
PRIO_SYNC = 2
PRIO_BACKGROUND = 3
PRIO_BACKFILL = 4   # after everything the user is waiting for

BACKFILL_BATCH = 500
SEED_SENT_LIMIT = 2000   # newest sent messages whose recipients count as "written to"

SORT_NEWEST = [{"property": "receivedAt", "isAscending": False}]

SORT_OPTIONS: dict[str, list[dict]] = {
    "newest": SORT_NEWEST,
    "oldest": [{"property": "receivedAt", "isAscending": True}],
    "sender": [{"property": "from", "isAscending": True}, {"property": "receivedAt", "isAscending": False}],
    "subject": [{"property": "subject", "isAscending": True}, {"property": "receivedAt", "isAscending": False}],
    "size": [{"property": "size", "isAscending": False}],
}


def build_sort(key: str = "newest", flagged_first: bool = False, unread_first: bool = False) -> list[dict]:
    """JMAP sort comparators for the list; keyword comparators go first (RFC 8621 §4.4.2)."""
    sort: list[dict] = []
    if flagged_first:
        sort.append({"property": "someInThreadHaveKeyword", "keyword": "$flagged", "isAscending": False})
    if unread_first:
        sort.append({"property": "allInThreadHaveKeyword", "keyword": "$seen", "isAscending": True})
    return sort + SORT_OPTIONS.get(key, SORT_NEWEST)


def parse_sort(sort: list[dict] | None) -> tuple[str, bool, bool]:
    """Inverse of build_sort: (key, flagged_first, unread_first) from JMAP comparators
    (also understands the per-mailbox `sort` Fastmail stores on Mailbox objects)."""
    key, flagged, unread = "newest", False, False
    for comp in sort or []:
        prop = comp.get("property")
        if prop in ("someInThreadHaveKeyword", "hasKeyword") and comp.get("keyword") == "$flagged":
            flagged = True
        elif prop in ("allInThreadHaveKeyword", "someInThreadHaveKeyword") and comp.get("keyword") == "$seen":
            unread = True
        elif prop == "receivedAt" or prop == "sentAt":
            key = "oldest" if comp.get("isAscending") else "newest"
            break
        elif prop == "from":
            key = "sender"
            break
        elif prop == "subject":
            key = "subject"
            break
        elif prop == "size":
            key = "size"
            break
    return key, flagged, unread


def query_key(spec: dict) -> str:
    # a cache key, not a security hash
    return hashlib.sha1(json.dumps(spec, sort_keys=True).encode(), usedforsecurity=False).hexdigest()[:16]


def mailbox_query_spec(mailbox_id: str, sort: list[dict] | None = None, unread_only: bool = False) -> dict:
    filt: dict = {"inMailbox": mailbox_id}
    if unread_only:
        filt["notKeyword"] = KW_SEEN
    return {"filter": filt, "sort": sort or SORT_NEWEST, "collapseThreads": True}


# Search box grammar.  A token is a bare word, a "quoted phrase", or operator:value where the value may be quoted.
_TOKEN_RE = re.compile(r'(?:[^\s"]|"[^"]*"?)+')
_RELATIVE_RE = re.compile(r"^(\d+)\s*([hdwmy])$")
_DATE_RE = re.compile(r"^(\d{4})(?:[-/](\d{1,2})(?:[-/](\d{1,2}))?)?$")
_UNIT_DAYS = {"h": 1 / 24, "d": 1, "w": 7, "m": 30, "y": 365}

# Names `in:` accepts for the system mailboxes, whatever they are called in the account.
MAILBOX_ALIASES = {
    "inbox": ROLE_INBOX, "archive": ROLE_ARCHIVE, "drafts": ROLE_DRAFTS, "draft": ROLE_DRAFTS, "sent": ROLE_SENT,
    "spam": ROLE_JUNK, "junk": ROLE_JUNK, "trash": ROLE_TRASH, "bin": ROLE_TRASH, "deleted": ROLE_TRASH,
}
_EVERYWHERE = ("anywhere", "all", "everywhere")


def search_tokens(text: str) -> list[tuple[str, str]]:
    """Split a search string into (operator, value) pairs; a bare word or phrase has an empty operator.

    Double quotes group words: `subject:"team meeting"`, `"exact phrase"`; a quote left open runs to the end.
    """
    out: list[tuple[str, str]] = []
    for raw in _TOKEN_RE.findall(text):
        if raw.startswith('"'):
            phrase = raw.strip('"').strip()
            if phrase:
                out.append(("", phrase))
            continue
        key, sep, value = raw.partition(":")
        value = value.strip('"').strip()
        if sep and key and value:
            out.append((key.lower(), value))
        elif raw.strip('"').strip():
            out.append(("", raw.strip('"').strip()))
    return out


def _mailbox_key(name: str) -> str:
    return re.sub(r"[\s_\-]+", " ", name.strip().lower())


def resolve_mailbox(name: str, mailboxes: list[dict]) -> str | None:
    """The id of the mailbox a `label:`/`in:` value names.

    A role alias (inbox, spam, trash…), a full path (`work/projects`) or a name (`projects`); case, hyphens and
    underscores don't matter, so `label:to-do` finds "To Do".  A name shared by several mailboxes picks the
    shallowest.
    """
    parts = [_mailbox_key(p) for p in name.split("/") if _mailbox_key(p)]
    if not parts:
        return None
    role = MAILBOX_ALIASES.get(parts[0]) if len(parts) == 1 else None
    if role:
        for m in mailboxes:
            if m.get("role") == role:
                return m["id"]
    by_id = {m["id"]: m for m in mailboxes}
    want = "/".join(parts)
    best: tuple[int, str] | None = None
    for m in mailboxes:
        path: list[str] = []
        cur: dict | None = m
        while cur is not None and cur["id"] not in path:
            path.append(cur["id"])
            cur = by_id.get(cur.get("parentId") or "")
        names = "/".join(_mailbox_key(by_id[i].get("name") or "") for i in reversed(path))
        if (names == want or names.endswith("/" + want)) and (best is None or len(path) < best[0]):
            best = (len(path), m["id"])
    return best[1] if best else None


def search_date(value: str, now: datetime | None = None) -> str | None:
    """A `before:`/`after:` value as a JMAP UTC date: `2026-09-04`, `2026-09`, `2026`, or `7d`/`2w`/`3m`/`1y` ago."""
    m = _RELATIVE_RE.match(value.lower())
    if m:
        now = now or datetime.now(UTC)
        return (now - timedelta(days=int(m[1]) * _UNIT_DAYS[m[2]])).strftime("%Y-%m-%dT%H:%M:%SZ")
    m = _DATE_RE.match(value)
    if m:
        try:
            return datetime(int(m[1]), int(m[2] or 1), int(m[3] or 1)).strftime("%Y-%m-%dT00:00:00Z")
        except ValueError:
            return None
    if "T" in value and len(value) >= 16:
        return value if value.endswith("Z") else value + "Z"
    return None


def search_mailboxes(text: str, mailboxes: list[dict]) -> tuple[list[str], list[str], bool]:
    """What the `label:`/`in:` operators in a query name: (resolved ids, names that match nothing, in:anywhere)."""
    ids: list[str] = []
    unknown: list[str] = []
    everywhere = False
    for key, value in search_tokens(text):
        if key not in ("label", "in"):
            continue
        if value.lower() in _EVERYWHERE:
            everywhere = True
        elif mid := resolve_mailbox(value, mailboxes):
            ids.append(mid)
        else:
            unknown.append(value)
    return ids, unknown, everywhere


def search_query_spec(text: str, mailbox_id: str | None, trash_junk: list[str],
                      sort: list[dict] | None = None, unread_only: bool = False,
                      mailboxes: list[dict] | None = None, now: datetime | None = None) -> dict:
    """Turn a search box string into a JMAP filter.

    Operators: from:/to:/cc:/subject: (quoted values allowed), is:unread|read|flagged|starred|unflagged,
    has:attachment, before:/after: (a date, or `7d` ago), older_than:/newer_than: (same values),
    label:/in: (a mailbox name or path resolved against `mailboxes`).  Bare words search everything, a
    "quoted phrase" must appear as written; a date that does not parse is searched as text.

    Naming a mailbox replaces the folder scope `mailbox_id`, and naming Trash or Spam (or in:anywhere) lifts
    the exclusion an all-mail search applies.  A name that matches no mailbox is left out here; the window
    checks `search_mailboxes` first and shows "no such label" instead of running the query.
    """
    conditions: list[dict] = []
    words: list[str] = []
    ids, _unknown, everywhere = search_mailboxes(text, mailboxes or [])
    everywhere = everywhere or any(i in trash_junk for i in ids)
    if ids or everywhere:
        mailbox_id = None
    for key, value in search_tokens(text):
        if not key:
            if " " in value:
                conditions.append({"text": value})
            else:
                words.append(value)
        elif key in ("from", "to", "cc", "subject"):
            conditions.append({key: value})
        elif key == "is" and value in ("unread", "read", "flagged", "starred", "unflagged"):
            if value == "unread":
                conditions.append({"notKeyword": KW_SEEN})
            elif value == "read":
                conditions.append({"hasKeyword": KW_SEEN})
            elif value in ("flagged", "starred"):
                conditions.append({"hasKeyword": "$flagged"})
            else:
                conditions.append({"notKeyword": "$flagged"})
        elif key == "has" and value in ("attachment", "attachments"):
            conditions.append({"hasAttachment": True})
        elif key in ("before", "after", "older_than", "newer_than", "older", "newer") and (when := search_date(value, now)):
            conditions.append({"before" if key.startswith(("before", "older")) else "after": when})
        elif key in ("label", "in"):
            if (mid := resolve_mailbox(value, mailboxes or [])) and {"inMailbox": mid} not in conditions:
                conditions.append({"inMailbox": mid})
        else:
            words.append(f"{key}:{value}")
    if words:
        conditions.append({"text": " ".join(words)})
    if unread_only:
        conditions.append({"notKeyword": KW_SEEN})
    if mailbox_id:
        conditions.append({"inMailbox": mailbox_id})
    elif trash_junk and not everywhere:
        conditions.append({"inMailboxOtherThan": trash_junk})
    if not conditions:
        filt: dict = {}
    elif len(conditions) == 1:
        filt = conditions[0]
    else:
        filt = {"operator": "AND", "conditions": conditions}
    return {"filter": filt, "sort": sort or SORT_NEWEST, "collapseThreads": True}


@dataclass(order=True, slots=True)
class _Job:
    """Queue entry ordered by priority, then arrival; the callable itself never compares."""

    prio: int
    seq: int
    fn: Callable[[], None] = field(compare=False)
    name: str = field(compare=False)


class SyncEngine(GObject.Object):
    __gsignals__: ClassVar[dict] = {
        "mailboxes-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "emails-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "emails-destroyed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "query-updated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "query-failed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "body-ready": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "body-failed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "identities-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "masked-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "sync-status": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "push-status": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "new-mail": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "action-failed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "auth-failed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "cache-reset": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "rules-applied": (GObject.SignalFlags.RUN_FIRST, None, (object,)),   # {rule id: hits} (#22)
        "contacts-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, client: JMAPClient, db: Database, config: Config):
        super().__init__()
        self.client = client
        self.db = db
        self.config = config
        self._queue: queue.PriorityQueue[_Job] = queue.PriorityQueue()
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, name="sync-worker", daemon=True)
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="blob")
        self._push: PushListener | None = None
        self._sync_queued = False
        self._sync_lock = threading.Lock()
        self._last_sync = 0.0
        self._active_queries: dict[str, int] = {}  # key -> loaded count wanted
        self.roles: dict[str, str] = {}
        self.push_connected = False
        self.online = True
        self.page_size = int(config.get("thread_page_size", 50))
        self._load_roles()

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        self._worker.start()
        self.enqueue(PRIO_SYNC, self._job_bootstrap, "bootstrap")
        if self.client.session and self.client.session.event_source_url:
            self._push = PushListener(self.client, self._on_push_change, self._on_push_status)
            self._push.start()

    def stop(self) -> None:
        self._stop.set()
        if self._push:
            self._push.stop()
        self.enqueue(PRIO_ACTION, lambda: None, "wake")
        self._pool.shutdown(wait=False, cancel_futures=True)

    def enqueue(self, prio: int, fn: Callable[[], None], name: str = "") -> None:
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        self._queue.put(_Job(prio, seq, fn, name or getattr(fn, "__name__", "job")))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=15)
            except queue.Empty:
                self._maybe_periodic_sync()
                continue
            if self._stop.is_set():
                return
            try:
                job.fn()
            except AuthError:
                log.error("authentication failed during %s", job.name)
                self._emit("auth-failed")
            except RateLimited as e:
                log.warning("rate limited during %s; sleeping %.0fs", job.name, e.retry_after)
                self._emit("sync-status", "error", f"Rate limited by server, retrying in {e.retry_after:.0f}s")
                self._stop.wait(min(e.retry_after, 120))
                self.enqueue(job.prio, job.fn, job.name)
            except TransportError as e:
                log.warning("network error during %s: %s", job.name, e)
                self._set_online(False, str(e))
            except Exception as e:
                log.exception("job %s failed", job.name)
                self._emit("sync-status", "error", f"{job.name}: {e}")
            finally:
                self._queue.task_done()

    def _maybe_periodic_sync(self) -> None:
        interval = int(self.config.get("poll_interval_seconds", 300))
        if not self.push_connected:
            interval = min(interval, 60)
        if not self.online:
            interval = min(interval, 30)
        if time.monotonic() - self._last_sync >= interval:
            self.sync_now()

    def _emit(self, signal: str, *args: Any) -> None:
        GLib.idle_add(self._emit_main, signal, args)

    def _emit_main(self, signal: str, args: tuple) -> bool:
        self.emit(signal, *args)
        return False

    def _set_online(self, online: bool, message: str = "") -> None:
        if online != self.online:
            self.online = online
            self._emit("sync-status", "idle" if online else "offline", message)

    def _callback(self, cb: Callable | None, *args: Any) -> None:
        if cb is not None:
            GLib.idle_add(lambda: (cb(*args), False)[1])

    # ------------------------------------------------------------------ push

    def _on_push_change(self, changed: dict) -> None:
        session = self.client.session
        acc = changed.get(session.account_id if session else "", {}) or next(iter(changed.values()), {})
        if not isinstance(acc, dict):
            return
        stale = False
        for kind in ("Email", "Mailbox", "Thread"):
            new_state = acc.get(kind)
            if new_state and new_state != self.db.get_state(kind):
                stale = True
        if stale:
            self.sync_now()

    def _on_push_status(self, connected: bool) -> None:
        self.push_connected = connected
        self._emit("push-status", connected)

    # ------------------------------------------------------------- bootstrap

    def _load_roles(self) -> None:
        self.roles = {m["role"]: m["id"] for m in self.db.get_mailboxes() if m.get("role")}

    def trash_junk_ids(self) -> list[str]:
        return [self.roles[r] for r in (ROLE_TRASH, ROLE_JUNK) if r in self.roles]

    def _job_bootstrap(self) -> None:
        self._emit("sync-status", "syncing", "Connecting")
        session = self.client.session or self.client.fetch_session()
        self.db.set_session(session.raw)
        if self.db.get_state("Email") is None:
            self._full_sync()
        else:
            self._incremental_sync()
        self._set_online(True)
        self._emit("sync-status", "idle", "")
        self.enqueue(PRIO_BACKFILL, self._job_seed_correspondents, "seed-correspondents")
        self.enqueue(PRIO_BACKFILL, self._job_backfill_headers, "backfill-headers")
        self.enqueue(PRIO_BACKFILL, self._job_reclassify_after_upgrade, "reclassify")
        self.enqueue(PRIO_BACKFILL, self._job_retrain_bayes, "retrain")

    def _job_reclassify_after_upgrade(self) -> None:
        """A cache classified by older rules is run through the current ones once."""
        if self.db.get_meta("rules_version") == RULES_VERSION:
            return
        ids = self.db.reclassify()
        self.db.set_meta("rules_version", RULES_VERSION)
        if ids:
            log.info("reclassified %d cached messages with rules %s", len(ids), RULES_VERSION)
            self._emit("emails-changed", ids)

    def retrain_bayes(self) -> None:
        """After a correction (#23): rebuild the learned model and let it look at the
        messages the rules were unsure about."""
        self.enqueue(PRIO_BACKGROUND, self._job_retrain_bayes, "retrain")

    def _job_retrain_bayes(self) -> None:
        corrections = self.db.corrections_count()
        if corrections == 0 and (self.db.get_meta("bayes_corrections") or "0") == "0":
            return   # nothing to learn from yet
        size, n = self.db.retrain_bayes()
        log.info("learned model: %d documents, %d corrections, %s", size, n, "ready" if self.db.bayes_ready else "silent")
        if self.db.bayes_ready:
            ids = self.db.unsure_ids()
            if ids:
                before = self.db.get_categories(ids)
                self.db.reclassify(ids)
                changed = [i for i, cat in self.db.get_categories(ids).items() if before.get(i) != cat]
                if changed:
                    self._emit("emails-changed", changed)

    def _job_seed_correspondents(self) -> None:
        """Once per cache: who the user has written to, from the Sent folder (#18).
        Only recipients are fetched, the messages themselves are not cached."""
        sent = self.roles.get(ROLE_SENT)
        if not sent or self.db.get_meta("correspondents_seeded"):
            return
        acc = self.client.session.account_id
        req = Request()
        c_q = req.add("Email/query", {"accountId": acc, "filter": {"inMailbox": sent}, "collapseThreads": False,
                                      "sort": SORT_NEWEST, "limit": SEED_SENT_LIMIT})
        c_get = req.add("Email/get", {"accountId": acc, "properties": ["id", "to", "cc", "bcc", "receivedAt"],
                                      "#ids": Request.ref(c_q, "Email/query", "/ids")})
        resp = self.client.send(req)
        changed = self.db.record_correspondents(resp.get(c_get)["list"])
        self.db.set_meta("correspondents_seeded", "1")
        if changed:
            self._emit("emails-changed", changed)

    def _job_backfill_headers(self) -> None:
        """Messages cached before the categoriser's headers were list properties (#18):
        fetch the headers a batch at a time, newest first, and classify them again."""
        ids = self.db.emails_missing_headers(BACKFILL_BATCH)
        if not ids:
            return
        acc = self.client.session.account_id
        got = self.client.call("Email/get", {"accountId": acc, "ids": ids, "properties": ["id", *CLASSIFY_HEADERS]})
        self.db.merge_headers(ids, got["list"])
        self._emit("emails-changed", ids)
        if len(ids) == BACKFILL_BATCH:
            self.enqueue(PRIO_BACKFILL, self._job_backfill_headers, "backfill-headers")

    def _full_sync(self) -> None:
        session = self.client.session
        req = Request()
        c_mb = req.add("Mailbox/get", {"accountId": session.account_id, "ids": None})
        c_id = req.add("Identity/get", {"accountId": session.submission_account_id, "ids": None})
        c_em = req.add("Email/get", {"accountId": session.account_id, "ids": []})
        c_th = req.add("Thread/get", {"accountId": session.account_id, "ids": []})
        c_me = None
        if session.has_masked_email:
            c_me = req.add("MaskedEmail/get", {"accountId": session.masked_account_id, "ids": None})
        resp = self.client.send(req)
        mb = resp.get(c_mb)
        self.db.upsert_mailboxes(mb["list"])
        known = {m["id"] for m in mb["list"]}
        self.db.delete_mailboxes([m["id"] for m in self.db.get_mailboxes() if m["id"] not in known])
        self.db.set_state("Mailbox", mb["state"])
        self._load_roles()
        try:
            ident = resp.get(c_id)
            self.db.set_identities(ident["list"])
        except MethodError as e:
            log.warning("Identity/get failed: %s", e)
        self.db.set_state("Email", resp.get(c_em)["state"])
        self.db.set_state("Thread", resp.get(c_th)["state"])
        if c_me:
            try:
                self.db.set_masked_emails(resp.get(c_me)["list"])
            except MethodError as e:
                log.warning("MaskedEmail/get failed: %s", e)
        self._last_sync = time.monotonic()
        self._emit("mailboxes-changed")
        self._emit("identities-changed")
        self._emit("masked-changed")
        self._sync_contacts(full=True)

    # -------------------------------------------------------------- contacts

    def _sync_contacts(self, full: bool = False) -> None:
        """The address book (RFC 9610 ContactCard, #4): everything once, then changes.
        Silently nothing when the token lacks the contacts scope."""
        session = self.client.session
        if not session or not session.has_contacts:
            return
        acc = session.contacts_account_id
        using = [CAP_CORE, CAP_CONTACTS]
        since = None if full else self.db.get_state("Contact")
        if since:
            req = Request()
            c_ch = req.add("ContactCard/changes", {"accountId": acc, "sinceState": since, "maxChanges": 500})
            c_new = req.add("ContactCard/get", {"accountId": acc, "#ids": Request.ref(c_ch, "ContactCard/changes", "/created")})
            c_upd = req.add("ContactCard/get", {"accountId": acc, "#ids": Request.ref(c_ch, "ContactCard/changes", "/updated")})
            try:
                resp = self.client.send(req, using)
                ch = resp.get(c_ch)
                cards = resp.get(c_new)["list"] + resp.get(c_upd)["list"]
                if cards:
                    self.db.upsert_contacts(cards)
                if ch.get("destroyed"):
                    self.db.delete_contacts(ch["destroyed"])
                self.db.set_state("Contact", ch["newState"])
                if cards or ch.get("destroyed"):
                    self._emit("contacts-changed")
                return
            except MethodError as e:
                if e.type != "cannotCalculateChanges":
                    log.warning("ContactCard/changes failed: %s", e)
                    return
        try:
            res = self.client.call("ContactCard/get", {"accountId": acc, "ids": None}, using)
        except MethodError as e:
            log.warning("ContactCard/get failed: %s", e)
            return
        self.db.set_contacts(res["list"])
        self.db.set_state("Contact", res["state"])
        self._emit("contacts-changed")

    def reset_cache(self) -> None:
        self.db.clear_all()
        self.db.drop_queries()
        self._emit("cache-reset")
        self._full_sync()

    # ----------------------------------------------------------- incremental

    def sync_now(self) -> None:
        with self._sync_lock:
            if self._sync_queued:
                return
            self._sync_queued = True
        self.enqueue(PRIO_SYNC, self._job_incremental, "sync")

    def _job_incremental(self) -> None:
        with self._sync_lock:
            self._sync_queued = False
        self._emit("sync-status", "syncing", "Syncing")
        try:
            self._incremental_sync()
            self._set_online(True)
        finally:
            self._emit("sync-status", "idle", "")

    def _incremental_sync(self) -> None:
        session = self.client.session
        if self.db.get_state("Email") is None:
            self._full_sync()
            return
        acc = session.account_id
        # --- mailboxes
        req = Request()
        c_ch = req.add("Mailbox/changes", {"accountId": acc, "sinceState": self.db.get_state("Mailbox")})
        c_get = req.add("Mailbox/get", {"accountId": acc,
                                        "#ids": Request.ref(c_ch, "Mailbox/changes", "/created")})
        c_upd = req.add("Mailbox/get", {"accountId": acc,
                                        "#ids": Request.ref(c_ch, "Mailbox/changes", "/updated")})
        resp = self.client.send(req)
        mailboxes_changed = False
        try:
            ch = resp.get(c_ch)
            created = resp.get(c_get)["list"]
            updated = resp.get(c_upd)["list"]
            if created or updated:
                self.db.upsert_mailboxes(created + updated)
                mailboxes_changed = True
            if ch.get("destroyed"):
                self.db.delete_mailboxes(ch["destroyed"])
                mailboxes_changed = True
            self.db.set_state("Mailbox", ch["newState"])
        except MethodError as e:
            if e.type == "cannotCalculateChanges":
                mb = self.client.call("Mailbox/get", {"accountId": acc, "ids": None})
                self.db.upsert_mailboxes(mb["list"])
                known = {m["id"] for m in mb["list"]}
                self.db.delete_mailboxes([m["id"] for m in self.db.get_mailboxes() if m["id"] not in known])
                self.db.set_state("Mailbox", mb["state"])
                mailboxes_changed = True
            else:
                raise
        if mailboxes_changed:
            self._load_roles()
            self._emit("mailboxes-changed")
        # --- emails
        changed_ids: list[str] = []
        destroyed_ids: list[str] = []
        new_mail: list[dict] = []
        inbox = self.roles.get(ROLE_INBOX)
        while True:
            req = Request()
            c_ch = req.add("Email/changes", {"accountId": acc, "sinceState": self.db.get_state("Email"),
                                             "maxChanges": 500})
            c_new = req.add("Email/get", {"accountId": acc, "properties": EMAIL_LIST_PROPERTIES,
                                          "#ids": Request.ref(c_ch, "Email/changes", "/created")})
            c_upd = req.add("Email/get", {"accountId": acc, "properties": EMAIL_LIST_PROPERTIES,
                                          "#ids": Request.ref(c_ch, "Email/changes", "/updated")})
            resp = self.client.send(req)
            try:
                ch = resp.get(c_ch)
            except MethodError as e:
                if e.type == "cannotCalculateChanges":
                    log.warning("Email/changes cannot calculate; resetting cache")
                    self.reset_cache()
                    return
                raise
            created = resp.get(c_new)["list"]
            updated = resp.get(c_upd)["list"]
            if created:
                screened = self._screen(created)       # first-time senders, decided before the batch is cached
                self.db.upsert_emails(created)
                changed_ids += [e["id"] for e in created]
                if screened:
                    self.db.screener_set(screened, "pending")
                created = self._apply_rules(created)   # a rule may archive or read a message before it is announced
                for e in created:
                    if (inbox and (e.get("mailboxIds") or {}).get(inbox) and not (e.get("keywords") or {}).get(KW_SEEN)
                            and client_rules.sender_of(e) not in screened):
                        new_mail.append(e)
            if updated:
                # only refresh emails we already know; others are irrelevant until queried
                known = self.db.get_emails([e["id"] for e in updated])
                keep = [e for e in updated if e["id"] in known]
                if keep:
                    self.db.upsert_emails(keep)
                    changed_ids += [e["id"] for e in keep]
            if ch.get("destroyed"):
                self.db.delete_emails(ch["destroyed"])
                destroyed_ids += ch["destroyed"]
            self.db.set_state("Email", ch["newState"])
            if not ch.get("hasMoreChanges"):
                break
        # --- threads
        req = Request()
        c_ch = req.add("Thread/changes", {"accountId": acc, "sinceState": self.db.get_state("Thread"),
                                          "maxChanges": 500})
        c_get = req.add("Thread/get", {"accountId": acc, "#ids": Request.ref(c_ch, "Thread/changes", "/updated")})
        c_new = req.add("Thread/get", {"accountId": acc, "#ids": Request.ref(c_ch, "Thread/changes", "/created")})
        resp = self.client.send(req)
        try:
            ch = resp.get(c_ch)
            threads = resp.get(c_get)["list"] + resp.get(c_new)["list"]
            if threads:
                self.db.upsert_threads(threads)
                # fetch list props for thread members we do not have yet
                missing = self.db.missing_email_ids([i for t in threads for i in t.get("emailIds", [])])
                if missing:
                    got = self.client.call("Email/get", {"accountId": acc, "ids": missing[:500],
                                                         "properties": EMAIL_LIST_PROPERTIES})
                    self.db.upsert_emails(got["list"])
                    changed_ids += [e["id"] for e in got["list"]]
            if ch.get("destroyed"):
                self.db.delete_threads(ch["destroyed"])
            self.db.set_state("Thread", ch["newState"])
        except MethodError as e:
            if e.type != "cannotCalculateChanges":
                raise
            self.db.set_state("Thread", self.client.call("Thread/get", {"accountId": acc, "ids": []})["state"])
        # --- contacts
        self._sync_contacts()
        # --- keep visible queries current
        for key in list(self._active_queries):
            q = self.db.get_query(key)
            if q:
                self._refresh_query(q)
        self._last_sync = time.monotonic()
        if changed_ids:
            self._emit("emails-changed", sorted(set(changed_ids)))
        if destroyed_ids:
            self._emit("emails-destroyed", destroyed_ids)
        if new_mail:
            self._emit("new-mail", new_mail)

    # -------------------------------------------------------------- screener

    def _screen(self, created: list[dict]) -> set[str]:
        """With the screener on (#24): the senders of this batch's Inbox mail that the
        cache has never seen, as sender, recipient or correspondent."""
        inbox = self.roles.get(ROLE_INBOX)
        if not inbox or not self.config.get("screener", False):
            return set()
        out: set[str] = set()
        batch = {e["id"] for e in created}
        for e in created:
            if not (e.get("mailboxIds") or {}).get(inbox):
                continue
            addr = client_rules.sender_of(e)
            if addr and addr not in out and not self.db.knows_sender(addr, batch):
                out.add(addr)
        return out

    # ----------------------------------------------------------------- rules

    def _apply_rules(self, created: list[dict]) -> list[dict]:
        """Run the client-side rules (#22) over mail that just arrived; returns the
        messages as they are after the rules, so notifications see the outcome."""
        rules = client_rules.load_rules(self.config)
        if not rules:
            return created
        categories = self.db.get_categories([e["id"] for e in created])
        actions, hits = client_rules.plan(rules, created, categories, self.roles, self.roles.get(ROLE_INBOX))
        if not actions:
            return created
        for act in actions:
            self._job_perform(act, None)
        self._emit("rules-applied", hits)
        after = self.db.get_emails([e["id"] for e in created])
        return [after.get(e["id"], e) for e in created]

    def act_on_sender(self, address: str, action: EmailAction | Callable[[list[str]], EmailAction],
                      on_done: Callable[[UndoRecord | None], None] | None = None,
                      include_trash: bool = False) -> None:
        """Apply an action to every message from `address` the server has, cached or
        not (a cleanup-dialog bulk action, #21).  `action` is an EmailAction whose
        email_ids are ignored, or a function from the ids to one."""
        def job() -> None:
            acc = self.client.session.account_id
            filt: dict[str, Any] = {"from": address}
            others = self.trash_junk_ids() if not include_trash else [i for i in self.trash_junk_ids()
                                                                       if i != self.roles.get(ROLE_TRASH)]
            if others:
                filt = {"operator": "AND", "conditions": [filt, {"inMailboxOtherThan": others}]}
            res = self.client.call("Email/query", {"accountId": acc, "filter": filt, "limit": 5000,
                                                   "collapseThreads": False})
            ids = res.get("ids") or []
            missing = self.db.missing_email_ids(ids)
            if missing:
                got = self.client.call("Email/get", {"accountId": acc, "ids": missing, "properties": EMAIL_LIST_PROPERTIES})
                self.db.upsert_emails(got["list"])
            # the server's from: filter is a substring match on name and address; keep exact senders
            emails = self.db.get_emails(ids)
            want = address.strip().lower()
            ids = [i for i in ids if i in emails and client_rules.sender_of(emails[i]) == want]
            if not ids:
                self._callback(on_done, None)
                return
            act = action(ids) if callable(action) else EmailAction(
                ids, action.description, dict(action.keyword_changes), set(action.mailbox_add),
                set(action.mailbox_remove), action.mailbox_replace, action.destroy, action.undoable)
            self._job_perform(act, on_done)

        self.enqueue(PRIO_ACTION, job, "sender-action")

    # --------------------------------------------------------------- queries

    def load_query(self, spec: dict, want: int | None = None) -> str:
        """Ensure the first page of a query is loaded; returns the query key."""
        key = query_key(spec)
        want = want or self.page_size
        self._active_queries[key] = want
        self.enqueue(PRIO_LOAD, lambda: self._job_load_query(spec, key, 0, want), "query")
        return key

    def load_more(self, key: str) -> None:
        q = self.db.get_query(key)
        if not q or q["complete"]:
            return
        position = len(q["ids"])
        self._active_queries[key] = position + self.page_size
        self.enqueue(PRIO_LOAD, lambda: self._job_load_query(q["spec"], key, position, self.page_size), "query-more")

    def release_query(self, key: str) -> None:
        self._active_queries.pop(key, None)

    def _job_load_query(self, spec: dict, key: str, position: int, limit: int) -> None:
        try:
            self._fetch_query_page(spec, key, position, limit)
        except MethodError as e:
            self._emit("query-failed", key, e.description or e.type)
            return
        self._emit("query-updated", key)

    def _fetch_query_page(self, spec: dict, key: str, position: int, limit: int) -> None:
        session = self.client.session
        acc = session.account_id
        req = Request()
        c_q = req.add("Email/query", {
            "accountId": acc, "filter": spec["filter"], "sort": spec["sort"],
            "collapseThreads": spec.get("collapseThreads", True), "position": position, "limit": limit,
            "calculateTotal": True,
        })
        c_e = req.add("Email/get", {"accountId": acc, "properties": ["threadId"],
                                    "#ids": Request.ref(c_q, "Email/query", "/ids")})
        c_t = req.add("Thread/get", {"accountId": acc,
                                     "#ids": Request.ref(c_e, "Email/get", "/list/*/threadId")})
        c_all = req.add("Email/get", {"accountId": acc, "properties": EMAIL_LIST_PROPERTIES,
                                      "#ids": Request.ref(c_t, "Thread/get", "/list/*/emailIds")})
        resp = self.client.send(req)
        qres = resp.get(c_q)
        threads = resp.get(c_t)["list"]
        emails = resp.get(c_all)["list"]
        self.db.upsert_threads(threads)
        self.db.upsert_emails(emails)
        ids = qres["ids"]
        existing = self.db.get_query(key)
        if position == 0 or not existing:
            all_ids = list(ids)
        else:
            seen = set(existing["ids"])
            all_ids = existing["ids"] + [i for i in ids if i not in seen]
        total = qres.get("total")
        complete = len(ids) < limit or (total is not None and len(all_ids) >= total)
        self.db.set_query(key, spec, all_ids, total, qres.get("queryState"),
                          bool(qres.get("canCalculateChanges")), complete)

    def _refresh_query(self, q: dict) -> None:
        """Bring a cached query up to date after a sync (queryChanges when possible)."""
        session = self.client.session
        acc = session.account_id
        spec = q["spec"]
        key = q["key"]
        if q["can_calculate_changes"] and q["query_state"] and q["ids"]:
            req = Request()
            c_qc = req.add("Email/queryChanges", {
                "accountId": acc, "filter": spec["filter"], "sort": spec["sort"],
                "collapseThreads": spec.get("collapseThreads", True), "sinceQueryState": q["query_state"],
                "maxChanges": 500, "upToId": q["ids"][-1] if not q["complete"] else None,
                "calculateTotal": True,
            })
            c_e = req.add("Email/get", {"accountId": acc, "properties": ["threadId"],
                                        "#ids": Request.ref(c_qc, "Email/queryChanges", "/added/*/id")})
            c_t = req.add("Thread/get", {"accountId": acc,
                                         "#ids": Request.ref(c_e, "Email/get", "/list/*/threadId")})
            c_all = req.add("Email/get", {"accountId": acc, "properties": EMAIL_LIST_PROPERTIES,
                                          "#ids": Request.ref(c_t, "Thread/get", "/list/*/emailIds")})
            try:
                resp = self.client.send(req)
                qc = resp.get(c_qc)
                self.db.upsert_threads(resp.get(c_t)["list"])
                self.db.upsert_emails(resp.get(c_all)["list"])
                ids = [i for i in q["ids"] if i not in set(qc.get("removed") or [])]
                for item in sorted(qc.get("added") or [], key=lambda a: a["index"]):
                    idx = item["index"]
                    if idx <= len(ids):
                        ids.insert(idx, item["id"])
                    elif q["complete"]:
                        ids.append(item["id"])
                total = qc.get("total", q["total"])
                complete = q["complete"] or (total is not None and len(ids) >= total)
                self.db.set_query(key, spec, ids, total, qc["newQueryState"], True, complete)
                self._emit("query-updated", key)
                return
            except MethodError as e:
                if e.type not in ("cannotCalculateChanges", "tooManyChanges"):
                    raise
        want = max(self._active_queries.get(key, self.page_size), len(q["ids"]))
        self._fetch_query_page(spec, key, 0, min(want, 250))
        self._emit("query-updated", key)

    # ---------------------------------------------------------------- bodies

    def fetch_body(self, email_id: str, force: bool = False) -> None:
        if not force and self.db.get_email_body(email_id):
            self._emit("body-ready", email_id)
            return
        self.enqueue(PRIO_LOAD, lambda: self._job_fetch_body(email_id), "body")

    _body_properties: ClassVar[list[str]] = list(EMAIL_BODY_PROPERTIES)

    def _job_fetch_body(self, email_id: str) -> None:
        acc = self.client.session.account_id
        res = None
        for attempt in range(2):
            try:
                res = self.client.call("Email/get", {
                    "accountId": acc, "ids": [email_id], "properties": self._body_properties,
                    "fetchHTMLBodyValues": True, "fetchTextBodyValues": True,
                    "maxBodyValueBytes": MAX_BODY_VALUE_BYTES,
                })
                break
            except MethodError as e:
                # Servers differ in which header:… forms they accept; drop the ones they reject and retry.
                bad = [a for a in (e.extra.get("arguments") or []) if a.startswith("properties[")]
                if e.type == "invalidArguments" and attempt == 0:
                    rejected = {a.split(":", 1)[1].rstrip("]") for a in bad if ":" in a}
                    keep = [p for p in self._body_properties
                            if p not in rejected and (not p.startswith("header:") or not bad or p not in rejected)]
                    if not bad:
                        keep = [p for p in self._body_properties if not p.startswith("header:")]
                    if keep != self._body_properties:
                        log.warning("server rejected Email/get properties %s; retrying without", bad or "header:*")
                        SyncEngine._body_properties = keep
                        continue
                self._emit("body-failed", email_id, e.description or e.type)
                return
        if not res["list"]:
            self._emit("body-failed", email_id, "Message no longer exists")
            return
        self.db.set_email_body(res["list"][0])
        self._emit("body-ready", email_id)

    # ----------------------------------------------------------------- blobs

    def blob_path(self, blob_id: str, name: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_. ()" else "_" for c in (name or "attachment"))[:120]
        d = attachments_dir() / blob_id
        return d / (safe or "attachment")

    def fetch_blob(self, blob_id: str, name: str, content_type: str | None,
                   on_done: Callable[[Path], None], on_error: Callable[[str], None] | None = None) -> None:
        path = self.blob_path(blob_id, name)
        if path.exists():
            self._callback(on_done, path)
            return

        def work() -> None:
            try:
                data = self.client.download(blob_id, name, content_type)
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(path.suffix + ".part")
                tmp.write_bytes(data)
                tmp.replace(path)
            except JMAPError as e:
                log.warning("download %s failed: %s", blob_id, e)
                self._callback(on_error, str(e))
                return
            self._callback(on_done, path)

        self._pool.submit(work)

    def fetch_email_headers(self, email_id: str, properties: list[str], on_done: Callable[[dict], None],
                            on_error: Callable[[str], None] | None = None) -> None:
        """Fetch a few header properties for one message (for bodies cached by older versions)."""
        def job() -> None:
            acc = self.client.session.account_id
            try:
                res = self.client.call("Email/get", {"accountId": acc, "ids": [email_id], "properties": properties})
            except JMAPError as e:
                self._callback(on_error, str(e))
                return
            items = res.get("list") or []
            if not items:
                self._callback(on_error, "message not found")
                return
            headers = {k: v for k, v in items[0].items() if k.startswith("header:")}
            self.db.merge_body_headers(email_id, headers)
            self._callback(on_done, headers)

        self.enqueue(PRIO_ACTION, job, "headers")

    def unsubscribe_one_click(self, url: str, on_done: Callable[[], None],
                              on_error: Callable[[str], None] | None = None) -> None:
        """RFC 8058 POST to a List-Unsubscribe URL, off the UI thread."""
        from ..unsubscribe import UnsubscribeError, one_click_request

        def work() -> None:
            try:
                one_click_request(url)
            except UnsubscribeError as e:
                log.warning("one-click unsubscribe %s failed: %s", url, e)
                self._callback(on_error, str(e))
                return
            self._callback(on_done)

        self._pool.submit(work)

    def upload(self, data: bytes, content_type: str, on_done: Callable[[dict], None],
               on_error: Callable[[str], None] | None = None) -> None:
        def work() -> None:
            try:
                res = self.client.upload(data, content_type)
            except JMAPError as e:
                self._callback(on_error, str(e))
                return
            self._callback(on_done, res)

        self._pool.submit(work)

    # --------------------------------------------------------------- actions

    def perform(self, action: EmailAction | RestoreAction,
                on_done: Callable[[UndoRecord | None], None] | None = None) -> None:
        self.enqueue(PRIO_ACTION, lambda: self._job_perform(action, on_done), "action")

    def _job_perform(self, action: EmailAction | RestoreAction, on_done) -> None:
        acc = self.client.session.account_id
        ids = list(action.email_ids)
        emails = self.db.get_emails(ids)
        originals: dict[str, tuple[dict, dict]] = {}
        patches: dict[str, dict] = {}
        deltas: dict[str, tuple[int, int]] = {}
        fallback = self.roles.get(ROLE_ARCHIVE) or self.roles.get(ROLE_INBOX)

        trash = self.roles.get(ROLE_TRASH)
        if isinstance(action, EmailAction) and (action.destroy or (trash and action.mailbox_replace == {trash})):
            self.db.record_deletions([emails[eid] for eid in ids if eid in emails
                                      and not (trash and (emails[eid].get("mailboxIds") or {}).get(trash))])
        if isinstance(action, EmailAction) and action.destroy:
            for eid in ids:
                e = emails.get(eid)
                if e:
                    self._count_delta(deltas, e, {}, remove_only=True)
            self.db.delete_emails(ids)
            self.db.adjust_mailbox_counts(deltas)
            self._emit("emails-destroyed", ids)
            self._emit("mailboxes-changed")
            try:
                res = self.client.call("Email/set", {"accountId": acc, "destroy": ids})
                check_set_response(res, "destroyed")
            except (MethodError, SetError) as e:
                self._emit("action-failed", f"Could not delete: {e.description or e}")
                self.sync_now()
            self._callback(on_done, None)
            return

        for eid in ids:
            e = emails.get(eid)
            if not e:
                continue
            old_kw = dict(e.get("keywords") or {})
            old_mb = dict(e.get("mailboxIds") or {})
            if isinstance(action, RestoreAction):
                new_kw, new_mb = action.record.originals.get(eid, (old_kw, old_mb))
            else:
                new_kw, new_mb = action.apply_to(e, fallback)
            if new_kw == old_kw and new_mb == old_mb:
                continue
            originals[eid] = (old_kw, old_mb)
            patch: dict[str, Any] = {}
            for kw in set(old_kw) | set(new_kw):
                if bool(old_kw.get(kw)) != bool(new_kw.get(kw)):
                    patch[f"keywords/{kw}"] = True if new_kw.get(kw) else None
            if set(old_mb) != set(new_mb):
                if len(new_mb) == 1 and len(old_mb) != 1:
                    patch["mailboxIds"] = dict(new_mb)
                else:
                    for mb in set(old_mb) | set(new_mb):
                        if bool(old_mb.get(mb)) != bool(new_mb.get(mb)):
                            patch[f"mailboxIds/{mb}"] = True if new_mb.get(mb) else None
            patches[eid] = patch
            self._count_delta(deltas, e, {**e, "keywords": new_kw, "mailboxIds": new_mb})
            self.db.patch_email(eid, keywords=new_kw, mailbox_ids=new_mb)
        if not patches:
            self._callback(on_done, None)
            return
        self.db.adjust_mailbox_counts(deltas)
        self._emit("emails-changed", list(patches))
        self._emit("mailboxes-changed")
        try:
            res = self.client.call("Email/set", {"accountId": acc, "update": patches})
        except MethodError as e:
            self._revert(originals)
            self._emit("action-failed", f"{action.description} failed: {e.description or e.type}")
            self._callback(on_done, None)
            return
        failed = res.get("notUpdated") or {}
        if failed:
            self._revert({k: v for k, v in originals.items() if k in failed})
            first = next(iter(failed.values()))
            self._emit("action-failed", f"{action.description} failed for {len(failed)} message(s): "
                                        f"{first.get('description') or first.get('type')}")
        if res.get("newState"):
            # Our own change bumped the state; the next sync will notice it anyway.
            pass
        record = None
        if isinstance(action, EmailAction) and action.undoable and originals:
            record = UndoRecord(action.description, {k: v for k, v in originals.items() if k not in failed})
        self._callback(on_done, record)
        self.sync_now()

    def _revert(self, originals: dict[str, tuple[dict, dict]]) -> None:
        deltas: dict[str, tuple[int, int]] = {}
        for eid, (kw, mb) in originals.items():
            e = self.db.get_email(eid)
            if e:
                self._count_delta(deltas, e, {**e, "keywords": kw, "mailboxIds": mb})
            self.db.patch_email(eid, keywords=kw, mailbox_ids=mb)
        self.db.adjust_mailbox_counts(deltas)
        self._emit("emails-changed", list(originals))
        self._emit("mailboxes-changed")

    @staticmethod
    def _count_delta(deltas: dict[str, tuple[int, int]], old: dict, new: dict, remove_only: bool = False) -> None:
        old_unread = 0 if (old.get("keywords") or {}).get(KW_SEEN) else 1
        new_unread = 0 if (new.get("keywords") or {}).get(KW_SEEN) else 1
        old_mb = {m for m, on in (old.get("mailboxIds") or {}).items() if on}
        new_mb = set() if remove_only else {m for m, on in (new.get("mailboxIds") or {}).items() if on}
        for m in old_mb - new_mb:
            t, u = deltas.get(m, (0, 0))
            deltas[m] = (t - 1, u - old_unread)
        for m in new_mb - old_mb:
            t, u = deltas.get(m, (0, 0))
            deltas[m] = (t + 1, u + new_unread)
        for m in old_mb & new_mb:
            if old_unread != new_unread:
                t, u = deltas.get(m, (0, 0))
                deltas[m] = (t, u + (new_unread - old_unread))

    # ------------------------------------------------------------ mailboxes

    def mailbox_set(self, create: dict | None = None, update: dict | None = None, destroy: list[str] | None = None,
                    on_done: Callable[[dict], None] | None = None,
                    on_error: Callable[[str], None] | None = None) -> None:
        def job() -> None:
            acc = self.client.session.account_id
            args: dict[str, Any] = {"accountId": acc}
            if create:
                args["create"] = create
            if update:
                args["update"] = update
            if destroy:
                args["destroy"] = destroy
                args["onDestroyRemoveEmails"] = False
            try:
                res = self.client.call("Mailbox/set", args)
                for kind in ("created", "updated", "destroyed"):
                    check_set_response(res, kind)
            except (MethodError, SetError) as e:
                self._callback(on_error, e.description or getattr(e, "type", str(e)))
                return
            self._incremental_sync()
            self._callback(on_done, res)

        self.enqueue(PRIO_ACTION, job, "mailbox-set")

    def mark_mailbox_read(self, mailbox_id: str) -> None:
        def job() -> None:
            acc = self.client.session.account_id
            res = self.client.call("Email/query", {"accountId": acc, "limit": 5000,
                                                   "filter": {"inMailbox": mailbox_id, "notKeyword": KW_SEEN}})
            ids = res.get("ids") or []
            if not ids:
                return
            from .actions import mark_read

            # Emails not in cache: fetch list props first so the local patch works.
            missing = self.db.missing_email_ids(ids)
            if missing:
                got = self.client.call("Email/get", {"accountId": acc, "ids": missing, "properties": EMAIL_LIST_PROPERTIES})
                self.db.upsert_emails(got["list"])
            self._job_perform(mark_read(ids, True), None)

        self.enqueue(PRIO_ACTION, job, "mark-mailbox-read")

    def empty_mailbox(self, mailbox_id: str, on_done: Callable[[], None] | None = None) -> None:
        def job() -> None:
            acc = self.client.session.account_id
            res = self.client.call("Email/query", {"accountId": acc, "limit": 5000, "filter": {"inMailbox": mailbox_id}})
            ids = res.get("ids") or []
            if ids:
                from .actions import destroy

                self._job_perform(destroy(ids), None)
            self._callback(on_done)

        self.enqueue(PRIO_ACTION, job, "empty-mailbox")

    # ------------------------------------------------------- drafts / sending

    def _draft_object(self, draft: dict) -> dict:
        obj = dict(draft)
        obj["mailboxIds"] = {self.roles[ROLE_DRAFTS]: True}
        obj["keywords"] = {KW_DRAFT: True, KW_SEEN: True}
        return obj

    def save_draft(self, draft: dict, replace_id: str | None,
                   on_done: Callable[[str], None], on_error: Callable[[str], None] | None = None) -> None:
        def job() -> None:
            acc = self.client.session.account_id
            args: dict[str, Any] = {"accountId": acc, "create": {"draft": self._draft_object(draft)}}
            if replace_id:
                args["destroy"] = [replace_id]
            try:
                res = self.client.call("Email/set", args)
                check_set_response(res, "created")
            except (MethodError, SetError) as e:
                self._callback(on_error, e.description or getattr(e, "type", str(e)))
                return
            new_id = res["created"]["draft"]["id"]
            if replace_id:
                self.db.delete_emails([replace_id])
                self._emit("emails-destroyed", [replace_id])
            self._incremental_sync()
            self._callback(on_done, new_id)

        self.enqueue(PRIO_ACTION, job, "save-draft")

    def send_email(self, draft: dict, identity_id: str, replace_id: str | None,
                   on_done: Callable[[str], None], on_error: Callable[[str], None] | None = None,
                   in_reply_to_id: str | None = None, forwarded_id: str | None = None) -> None:
        def job() -> None:
            session = self.client.session
            acc = session.account_id
            drafts = self.roles.get(ROLE_DRAFTS)
            sent = self.roles.get(ROLE_SENT)
            req = Request()
            c_set = req.add("Email/set", {"accountId": acc, "create": {"draft": self._draft_object(draft)}})
            on_success: dict[str, Any] = {f"keywords/{KW_DRAFT}": None}
            if drafts:
                on_success[f"mailboxIds/{drafts}"] = None
            if sent:
                on_success[f"mailboxIds/{sent}"] = True
            c_sub = req.add("EmailSubmission/set", {
                "accountId": session.submission_account_id,
                "create": {"sub": {"identityId": identity_id, "emailId": "#draft"}},
                "onSuccessUpdateEmail": {"#sub": on_success},
            })
            c_del = None
            if replace_id:
                c_del = req.add("Email/set", {"accountId": acc, "destroy": [replace_id]})
            try:
                resp = self.client.send(req)
                check_set_response(resp.get(c_set), "created")
                check_set_response(resp.get(c_sub), "created")
            except (MethodError, SetError) as e:
                self._callback(on_error, e.description or getattr(e, "type", str(e)))
                return
            new_id = resp.get(c_set)["created"]["draft"]["id"]
            if c_del:
                self.db.delete_emails([replace_id])
                self._emit("emails-destroyed", [replace_id])
            from .actions import EmailAction

            if in_reply_to_id:
                self._job_perform(EmailAction([in_reply_to_id], "Answered", keyword_changes={"$answered": True},
                                              undoable=False), None)
            if forwarded_id:
                self._job_perform(EmailAction([forwarded_id], "Forwarded", keyword_changes={"$forwarded": True},
                                              undoable=False), None)
            self._incremental_sync()
            self._callback(on_done, new_id)

        self.enqueue(PRIO_ACTION, job, "send")

    # ------------------------------------------------------------ identities

    def refresh_identities(self) -> None:
        def job() -> None:
            res = self.client.call("Identity/get", {"accountId": self.client.session.submission_account_id, "ids": None})
            self.db.set_identities(res["list"])
            self._emit("identities-changed")

        self.enqueue(PRIO_LOAD, job, "identities")

    def identity_update(self, identity_id: str, patch: dict, on_done: Callable[[], None] | None = None,
                        on_error: Callable[[str], None] | None = None) -> None:
        def job() -> None:
            try:
                res = self.client.call("Identity/set", {"accountId": self.client.session.submission_account_id,
                                                        "update": {identity_id: patch}})
                check_set_response(res, "updated")
            except (MethodError, SetError) as e:
                self._callback(on_error, e.description or getattr(e, "type", str(e)))
                return
            res = self.client.call("Identity/get", {"accountId": self.client.session.submission_account_id, "ids": None})
            self.db.set_identities(res["list"])
            self._emit("identities-changed")
            self._callback(on_done)

        self.enqueue(PRIO_ACTION, job, "identity-update")

    # ---------------------------------------------------------- masked email

    def refresh_masked(self) -> None:
        session = self.client.session
        if not session.has_masked_email:
            return

        def job() -> None:
            res = self.client.call("MaskedEmail/get", {"accountId": session.masked_account_id, "ids": None},
                                   using=["urn:ietf:params:jmap:core", CAP_MASKED_EMAIL])
            self.db.set_masked_emails(res["list"])
            self._emit("masked-changed")

        self.enqueue(PRIO_LOAD, job, "masked")

    def masked_set(self, create: dict | None = None, update: dict | None = None, destroy: list[str] | None = None,
                   on_done: Callable[[dict], None] | None = None,
                   on_error: Callable[[str], None] | None = None) -> None:
        session = self.client.session

        def job() -> None:
            args: dict[str, Any] = {"accountId": session.masked_account_id}
            if create:
                args["create"] = {"new": create}
            if update:
                args["update"] = update
            if destroy:
                args["destroy"] = destroy
            try:
                res = self.client.call("MaskedEmail/set", args, using=["urn:ietf:params:jmap:core", CAP_MASKED_EMAIL])
                check_set_response(res, "created")
                check_set_response(res, "updated")
                check_set_response(res, "destroyed")
            except (MethodError, SetError) as e:
                self._callback(on_error, e.description or getattr(e, "type", str(e)))
                return
            got = self.client.call("MaskedEmail/get", {"accountId": session.masked_account_id, "ids": None},
                                   using=["urn:ietf:params:jmap:core", CAP_MASKED_EMAIL])
            self.db.set_masked_emails(got["list"])
            self._emit("masked-changed")
            created = (res.get("created") or {}).get("new") or {}
            self._callback(on_done, created)

        self.enqueue(PRIO_ACTION, job, "masked-set")

    # ----------------------------------------------------------- generic run

    def run(self, fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
            on_error: Callable[[str], None] | None = None, prio: int = PRIO_LOAD) -> None:
        def job() -> None:
            try:
                result = fn()
            except JMAPError as e:
                self._callback(on_error, str(e))
                return
            self._callback(on_done, result)

        self.enqueue(prio, job, getattr(fn, "__name__", "run"))
