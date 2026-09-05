"""The reading pane while several conversations are selected (#151).

Instead of a count alone: the selected conversations as a stack (the first
dozen, then how many more), and, when they all come from one sender whose mail
carries List-Unsubscribe, an Unsubscribe button that works like the one in a
message header.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

from ..models.thread import ThreadObject
from ..newsletters import HEADER_POST, HEADER_UNSUBSCRIBE, cached_plan
from ..unsubscribe import UnsubscribePlan, parse_list_unsubscribe
from .a11y import watch as _a11y_watch

SHOWN = 12   # rows before "and N more"

OnUnsubscribe = Callable[[dict, UnsubscribePlan, Gtk.Button, str], None]


class SelectionPage(Gtk.Box):
    def __init__(self, db, engine, config, on_unsubscribe: OnUnsubscribe):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.START)
        self.db = db
        self.engine = engine
        self.config = config
        self.on_unsubscribe = on_unsubscribe
        for side in ("start", "end", "top", "bottom"):
            getattr(self, f"set_margin_{side}")(16 if side in ("start", "end") else 24)
        self.title = Gtk.Label(xalign=0, wrap=True)
        self.title.add_css_class("title-2")
        self.append(self.title)
        self.hint = Gtk.Label(xalign=0, wrap=True)
        self.hint.add_css_class("dim-label")
        self.append(self.hint)
        self.unsubscribe = Gtk.Button(halign=Gtk.Align.START, visible=False)
        self.unsubscribe.connect("clicked", self._on_unsubscribe)
        self.append(self.unsubscribe)
        self.rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.rows.add_css_class("boxed-list")
        self.append(self.rows)
        self._threads: list[ThreadObject] = []
        self._email: dict | None = None
        self._plan: UnsubscribePlan | None = None
        self._sender = ""
        self._generation = 0
        _a11y_watch(self)

    def show(self, threads: list[ThreadObject]) -> None:
        self._threads = list(threads)
        n = len(threads)
        senders = {(t.sender_email or "").lower() for t in threads} - {""}
        self._sender = next(iter(senders)) if len(senders) == 1 else ""
        who = ""
        if self._sender:
            who = next((t.sender_name for t in threads if t.sender_name), "") or threads[0].participants or self._sender
        self.title.set_label(f"{n} conversations from {who}" if who else f"{n} conversations selected")
        self.hint.set_label("Archive, delete, flag or label them with the buttons in the header bar. "
                            "Ctrl-click or Shift-click adds to the selection; the Select button shows checkboxes.")
        while child := self.rows.get_first_child():
            self.rows.remove(child)
        for t in threads[:SHOWN]:
            self.rows.append(self._row(t))
        if n > SHOWN:
            more = Adw.ActionRow(title=f"and {n - SHOWN} more", activatable=False)
            more.add_css_class("dim-label")
            self.rows.append(more)
        self._find_plan()

    @staticmethod
    def _row(t: ThreadObject) -> Adw.ActionRow:
        row = Adw.ActionRow(title=t.subject or "(no subject)", activatable=False, use_markup=False)
        who = t.sender_name or t.participants
        row.set_subtitle(f"{who} · {t.count} messages" if t.count > 1 else who)
        when = Gtk.Label(label=t.date_text, valign=Gtk.Align.CENTER)
        when.add_css_class("dim-label")
        when.add_css_class("caption")
        row.add_suffix(when)
        if t.unread:
            row.add_css_class("unread")
        return row

    # ------------------------------------------------------------ unsubscribe

    def _find_plan(self) -> None:
        """Offer Unsubscribe for a single-sender selection: from a cached body first, else after
        one request for the newest conversation's headers."""
        self._generation += 1
        generation = self._generation
        self._email, self._plan = None, None
        self.unsubscribe.set_visible(False)
        if not self._sender:
            return
        found = cached_plan(self.db, [t.email_id for t in self._threads])
        if found is not None:
            self._offer(*found)
            return
        first = self._threads[0].email_id

        def got(headers: dict) -> None:
            if generation != self._generation:
                return   # the selection moved on
            plan = parse_list_unsubscribe(headers.get(HEADER_UNSUBSCRIBE), headers.get(HEADER_POST))
            if plan is not None:
                email = dict(self.db.get_email(first) or {"id": first})
                email.update(headers)
                self._offer(email, plan)

        self.engine.fetch_email_headers(first, [HEADER_UNSUBSCRIBE, HEADER_POST], got, lambda message: None)

    def _offer(self, email: dict, plan: UnsubscribePlan) -> None:
        self._email, self._plan = email, plan
        self.refresh()

    def refresh(self) -> None:
        """Relabel the button after an unsubscribe went through (here or in a message card)."""
        plan = self._plan
        self.unsubscribe.set_visible(plan is not None)
        if plan is None:
            return
        when = self.config.unsubscribed().get(self._sender)
        self.unsubscribe.set_label("Unsubscribed" if when else "Unsubscribe")
        if when:
            self.unsubscribe.add_css_class("dim-label")
        else:
            self.unsubscribe.remove_css_class("dim-label")
        how = {"one-click": f"Send an unsubscribe request to {plan.target}",
               "browser": f"Open the unsubscribe page at {plan.target}",
               "mailto": f"Send an unsubscribe message to {plan.target}"}[plan.kind]
        self.unsubscribe.set_tooltip_text((f"Unsubscribed on {when}. " if when else "") + how)

    def _on_unsubscribe(self, _button) -> None:
        if self._email is not None and self._plan is not None:
            self.on_unsubscribe(self._email, self._plan, self.unsubscribe, self._sender)
