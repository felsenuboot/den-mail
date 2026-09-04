"""Thread list model: a Gio.ListModel over cached thread summaries."""

from __future__ import annotations

from datetime import UTC, datetime

from gi.repository import Gio, GObject

from ..avatars import registrable_domain
from ..store.db import Database, ThreadSummary

GROUP_MODES = ("off", "sender", "domain")


def domain_group_key(addr: dict | None) -> str:
    """Grouping key by organisation: the registrable domain of the address
    (lippu.vr.fi and tili.vr.fi both become vr.fi); falls back to the sender key."""
    email = (addr or {}).get("email") or ""
    if "@" not in email:
        return sender_group_key(addr)
    return registrable_domain(email.rsplit("@", 1)[1].strip().lower().strip("."))


def sender_group_key(addr: dict | None) -> str:
    """Grouping key for a From address: the display name, else the address,
    lowercased -- the same value the JMAP "from" sort comparator orders by,
    so groups are contiguous in a sender-sorted query."""
    if not addr:
        return ""
    return (addr.get("name") or addr.get("email") or "").strip().lower()


def format_date(iso: str) -> str:
    """Short, list-friendly date: time today, weekday this week, else date."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return iso
    now = datetime.now(UTC).astimezone()
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    if (now - dt).days < 7 and dt.year == now.year:
        return dt.strftime("%a")
    if dt.year == now.year:
        return dt.strftime("%-d %b")
    return dt.strftime("%d/%m/%y")


def format_date_long(iso: str) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return iso
    return dt.strftime("%a, %-d %b %Y, %H:%M")


class ThreadObject(GObject.Object):
    __gtype_name__ = "FmThreadObject"

    thread_id = GObject.Property(type=str, default="")
    email_id = GObject.Property(type=str, default="")
    subject = GObject.Property(type=str, default="")
    participants = GObject.Property(type=str, default="")
    preview = GObject.Property(type=str, default="")
    date_text = GObject.Property(type=str, default="")
    received_at = GObject.Property(type=str, default="")
    count = GObject.Property(type=int, default=1)
    unread = GObject.Property(type=bool, default=False)
    flagged = GObject.Property(type=bool, default=False)
    has_attachment = GObject.Property(type=bool, default=False)
    is_draft = GObject.Property(type=bool, default=False)
    labels_text = GObject.Property(type=str, default="")
    sender_name = GObject.Property(type=str, default="")
    sender_email = GObject.Property(type=str, default="")
    category = GObject.Property(type=str, default="primary")

    def __init__(self, summary: ThreadSummary):
        super().__init__()
        self.summary = summary
        self.thread_id = summary.thread_id
        self.labels: list[tuple[str, int]] = []  # (name, palette index)
        self.update(summary, [])

    def update(self, summary: ThreadSummary, labels: list[tuple[str, int]]) -> None:
        self.summary = summary
        self.labels = labels
        labels_text = "\x1f".join(f"{n}:{c}" for n, c in labels)
        first = summary.from_addresses[0] if summary.from_addresses else {}
        self.sender_key = sender_group_key(first)
        self.domain_key = domain_group_key(first)
        for prop, value in (
            ("sender_name", first.get("name") or first.get("email") or "(unknown sender)"),
            ("sender_email", first.get("email") or ""),
            ("email_id", summary.email_id),
            ("subject", summary.subject or "(no subject)"),
            ("participants", ", ".join(summary.participants) or "(unknown sender)"),
            ("preview", " ".join(summary.preview.split())),
            ("date_text", format_date(summary.received_at)),
            ("received_at", summary.received_at),
            ("count", summary.count),
            ("unread", summary.unread),
            ("flagged", summary.flagged),
            ("has_attachment", summary.has_attachment),
            ("is_draft", summary.is_draft),
            ("labels_text", labels_text),
            ("category", summary.category),
        ):
            if self.get_property(prop) != value:
                self.set_property(prop, value)

    @property
    def email_ids(self) -> list[str]:
        return list(self.summary.email_ids)


class SenderGroup(GObject.Object):
    """Header row for the threads of one sender when the list is grouped."""

    __gtype_name__ = "FmSenderGroup"

    name = GObject.Property(type=str, default="")
    email = GObject.Property(type=str, default="")     # address of the first thread (for the logo)
    detail = GObject.Property(type=str, default="")    # secondary text: address, or domain and sender count
    count = GObject.Property(type=int, default=0)
    unread = GObject.Property(type=int, default=0)
    collapsed = GObject.Property(type=bool, default=False)

    def __init__(self, key: str):
        super().__init__()
        self.key = key
        self.threads: list[ThreadObject] = []

    def update(self, threads: list[ThreadObject], collapsed: bool, mode: str = "sender") -> None:
        self.threads = threads
        first = threads[0]
        if mode == "domain":
            names: dict[str, int] = {}
            for t in threads:
                names[t.sender_name] = names.get(t.sender_name, 0) + 1
            name = max(names, key=lambda n: (names[n], -list(names).index(n)))
            senders = len({t.sender_email.lower() for t in threads if t.sender_email})
            detail = self.key + (f" · {senders} senders" if senders > 1 else "")
        else:
            name, detail = first.sender_name, first.sender_email
        for prop, value in (("name", name), ("email", first.sender_email), ("detail", detail),
                            ("count", len(threads)), ("unread", sum(1 for t in threads if t.unread)),
                            ("collapsed", collapsed)):
            if self.get_property(prop) != value:
                self.set_property(prop, value)


class ThreadListModel(GObject.Object, Gio.ListModel):
    """Ordered list for one query; ThreadObjects keep identity by thread id.

    `all_threads` holds every thread of the query in order and `threads` the
    ones that pass the category filter (#18).  `items` is what the list view
    shows: those threads, or -- with `grouped` set -- one SenderGroup row per
    sender followed by that sender's threads.  Groups keep the order of the
    active sort (the group of a sender sits where its first thread was), and
    the threads of collapsed groups are left out."""

    __gtype_name__ = "FmThreadListModel"

    loading = GObject.Property(type=bool, default=False)
    complete = GObject.Property(type=bool, default=True)
    total = GObject.Property(type=int, default=0)

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.all_threads: list[ThreadObject] = []
        self.threads: list[ThreadObject] = []
        self.items: list[GObject.Object] = []
        self.category_filter: str | None = None
        self.by_thread: dict[str, ThreadObject] = {}
        self.groups: dict[str, SenderGroup] = {}
        self.collapsed: set[str] = set()
        self.group_mode = "off"
        self.mailbox_id: str | None = None
        self.trash_junk: set[str] = set()
        self.label_namer = lambda mailbox_ids: []

    # Gio.ListModel
    def do_get_item_type(self):
        return GObject.Object.__gtype__

    def do_get_n_items(self):
        return len(self.items)

    def do_get_item(self, position):
        return self.items[position] if position < len(self.items) else None

    # ------------------------------------------------------ grouping

    @property
    def grouped(self) -> bool:
        return self.group_mode != "off"

    def set_grouped(self, mode) -> None:
        """mode: "off", "sender" (by display name) or "domain" (by organisation)."""
        if mode is True:
            mode = "sender"
        elif mode is False or mode is None:
            mode = "off"
        if mode not in GROUP_MODES:
            mode = "off"
        if mode != self.group_mode:
            self.group_mode = mode
            self._rebuild_visible()

    def key_of(self, thread: ThreadObject) -> str:
        return thread.domain_key if self.group_mode == "domain" else thread.sender_key

    # ------------------------------------------------------ category filter

    def set_category_filter(self, category: str | None) -> None:
        """Show only the threads whose latest message is in this category; None for all."""
        category = category or None
        if category != self.category_filter:
            self.category_filter = category
            self._apply_filter()

    def _passes(self, thread: ThreadObject) -> bool:
        return self.category_filter is None or thread.category == self.category_filter

    def _apply_filter(self) -> None:
        self.threads = [t for t in self.all_threads if self._passes(t)]
        self._rebuild_visible()

    @property
    def hidden_by_filter(self) -> int:
        return len(self.all_threads) - len(self.threads)

    def toggle_collapsed(self, key: str) -> None:
        self.collapsed.symmetric_difference_update({key})
        self._rebuild_visible()

    def set_all_collapsed(self, collapsed: bool) -> None:
        self.collapsed = {g.key for g in self.groups.values()} if collapsed else set()
        self._rebuild_visible()

    def group_of(self, thread: ThreadObject) -> SenderGroup | None:
        return self.groups.get(self.key_of(thread)) if self.grouped else None

    def reveal(self, thread_id: str) -> None:
        """Expand the group hiding this thread, if any."""
        obj = self.by_thread.get(thread_id)
        if obj is not None and self.key_of(obj) in self.collapsed:
            self.toggle_collapsed(self.key_of(obj))

    def _rebuild_visible(self) -> None:
        if not self.grouped:
            self._replace(list(self.threads))
            return
        runs: dict[str, list[ThreadObject]] = {}
        for t in self.threads:
            runs.setdefault(self.key_of(t), []).append(t)
        visible: list[GObject.Object] = []
        groups: dict[str, SenderGroup] = {}
        for key, run in runs.items():
            group = self.groups.get(key) or SenderGroup(key)
            group.update(run, key in self.collapsed, self.group_mode)
            groups[key] = group
            visible.append(group)
            if not group.collapsed:
                visible.extend(run)
        self.groups = groups
        self._replace(visible)

    # ----------------------------------------------------------------

    def _summary(self, thread_id: str) -> ThreadSummary | None:
        return self.db.thread_summary(thread_id, self.mailbox_id, self.trash_junk)

    def set_email_ids(self, email_ids: list[str], total: int | None, complete: bool) -> None:
        """Rebuild from the representative email ids returned by Email/query."""
        emails = self.db.get_emails(email_ids)
        thread_ids: list[str] = []
        seen: set[str] = set()
        for eid in email_ids:
            e = emails.get(eid)
            tid = e["threadId"] if e else None
            if tid and tid not in seen:
                seen.add(tid)
                thread_ids.append(tid)
        threads: list[ThreadObject] = []
        for tid in thread_ids:
            summary = self._summary(tid)
            if summary is None:
                continue
            obj = self.by_thread.get(tid)
            labels = self.label_namer(summary.mailbox_ids)
            if obj is None:
                obj = ThreadObject(summary)
            obj.update(summary, labels)
            threads.append(obj)
        self.all_threads = threads
        self.by_thread = {o.thread_id: o for o in threads}
        self._apply_filter()
        self.total = total if total is not None else len(threads)
        self.complete = complete

    def _replace(self, new_items: list) -> None:
        old = self.items
        # common prefix / suffix to keep the change minimal
        prefix = 0
        while prefix < len(old) and prefix < len(new_items) and old[prefix] is new_items[prefix]:
            prefix += 1
        suffix = 0
        while (suffix < len(old) - prefix and suffix < len(new_items) - prefix
               and old[-1 - suffix] is new_items[-1 - suffix]):
            suffix += 1
        removed = len(old) - prefix - suffix
        added = len(new_items) - prefix - suffix
        self.items = new_items
        if removed or added:
            self.items_changed(prefix, removed, added)

    def refresh_threads(self, email_ids: list[str]) -> None:
        """Re-read summaries for threads containing any of these emails."""
        thread_ids = set()
        for eid in email_ids:
            tid = self.db.thread_of_email(eid)
            if tid and tid in self.by_thread:
                thread_ids.add(tid)
        for tid in thread_ids:
            obj = self.by_thread[tid]
            summary = self._summary(tid)
            if summary is not None:
                obj.update(summary, self.label_namer(summary.mailbox_ids))
        if thread_ids and self.category_filter and [t for t in self.all_threads if self._passes(t)] != self.threads:
            self._apply_filter()  # a reclassified message moved in or out of the filter
        elif thread_ids and self.grouped:
            for group in self.groups.values():
                group.update(group.threads, group.collapsed, self.group_mode)

    def remove_threads(self, thread_ids: set[str]) -> None:
        self.all_threads = [o for o in self.all_threads if o.thread_id not in thread_ids]
        self.by_thread = {o.thread_id: o for o in self.all_threads}
        self._apply_filter()

    def index_of(self, thread_id: str) -> int:
        for i, o in enumerate(self.items):
            if isinstance(o, ThreadObject) and o.thread_id == thread_id:
                return i
        return -1

    def clear(self) -> None:
        self.all_threads = []
        self.threads = []
        self.by_thread = {}
        self.groups = {}
        self._replace([])
        self.total = 0
        self.complete = True
