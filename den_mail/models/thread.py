"""Thread list model: a Gio.ListModel over cached thread summaries."""

from __future__ import annotations

from datetime import datetime, timezone

import bisect

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject, Gtk  # noqa: E402

from ..store.db import Database, ThreadSummary


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
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return iso
    now = datetime.now(timezone.utc).astimezone()
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
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
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
        ):
            if self.get_property(prop) != value:
                self.set_property(prop, value)

    @property
    def email_ids(self) -> list[str]:
        return list(self.summary.email_ids)


class ThreadListModel(GObject.Object, Gio.ListModel, Gtk.SectionModel):
    """Ordered list of ThreadObjects for one query; objects keep identity by thread id.

    With `grouped` set, consecutive threads from the same sender form a section
    (Gtk.SectionModel), which the list view renders with a header per sender."""

    __gtype_name__ = "FmThreadListModel"

    loading = GObject.Property(type=bool, default=False)
    complete = GObject.Property(type=bool, default=True)
    total = GObject.Property(type=int, default=0)

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.items: list[ThreadObject] = []
        self.by_thread: dict[str, ThreadObject] = {}
        self.mailbox_id: str | None = None
        self.trash_junk: set[str] = set()
        self.label_namer = lambda mailbox_ids: []
        self.grouped = False
        self._section_starts: list[int] = []   # start index of every section

    # Gtk.SectionModel
    def set_grouped(self, grouped: bool) -> None:
        if grouped == self.grouped:
            return
        self.grouped = grouped
        self._compute_sections()
        if self.items:
            self.sections_changed(0, len(self.items))

    def _compute_sections(self) -> None:
        starts: list[int] = []
        if self.grouped:
            last = object()
            for i, o in enumerate(self.items):
                if o.sender_key != last:
                    starts.append(i)
                    last = o.sender_key
        self._section_starts = starts

    def sections(self) -> list[tuple[int, int]]:
        """(start, end) of every section, for tests and headers."""
        n = len(self.items)
        if not self.grouped:
            return [(0, n)] if n else []
        starts = self._section_starts
        return [(s, starts[i + 1] if i + 1 < len(starts) else n) for i, s in enumerate(starts)]

    def do_get_section(self, position):
        n = len(self.items)
        if position >= n:
            return n, GLib.MAXUINT32
        if not self.grouped:
            return 0, n
        i = bisect.bisect_right(self._section_starts, position) - 1
        start = self._section_starts[i]
        end = self._section_starts[i + 1] if i + 1 < len(self._section_starts) else n
        return start, end

    # Gio.ListModel
    def do_get_item_type(self):
        return ThreadObject.__gtype__

    def do_get_n_items(self):
        return len(self.items)

    def do_get_item(self, position):
        return self.items[position] if position < len(self.items) else None

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
        new_items: list[ThreadObject] = []
        for tid in thread_ids:
            summary = self._summary(tid)
            if summary is None:
                continue
            obj = self.by_thread.get(tid)
            labels = self.label_namer(summary.mailbox_ids)
            if obj is None:
                obj = ThreadObject(summary)
                obj.update(summary, labels)
            else:
                obj.update(summary, labels)
            new_items.append(obj)
        self._replace(new_items)
        self.by_thread = {o.thread_id: o for o in self.items}
        self.total = total if total is not None else len(self.items)
        self.complete = complete

    def _replace(self, new_items: list[ThreadObject]) -> None:
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
        self._compute_sections()
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

    def remove_threads(self, thread_ids: set[str]) -> None:
        self._replace([o for o in self.items if o.thread_id not in thread_ids])
        self.by_thread = {o.thread_id: o for o in self.items}

    def index_of(self, thread_id: str) -> int:
        for i, o in enumerate(self.items):
            if o.thread_id == thread_id:
                return i
        return GLib.MAXUINT32 if False else -1

    def clear(self) -> None:
        self._replace([])
        self.by_thread = {}
        self.total = 0
        self.complete = True
