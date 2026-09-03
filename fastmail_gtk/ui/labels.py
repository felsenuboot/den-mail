"""Popovers for applying labels to, or moving, a set of conversations."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gtk

from ..models.mailbox import MailboxObject, MailboxTree


class MailboxPickerPopover(Gtk.Popover):
    """mode='labels': check rows toggle membership immediately.
    mode='move': activating a row moves the conversation there."""

    def __init__(self, tree: MailboxTree, mode: str,
                 on_toggle: Callable[[MailboxObject, bool], None] | None = None,
                 on_pick: Callable[[MailboxObject], None] | None = None,
                 on_create: Callable[[str], None] | None = None):
        super().__init__()
        self.tree = tree
        self.mode = mode
        self.on_toggle = on_toggle
        self.on_pick = on_pick
        self.on_create = on_create
        self._present: set[str] = set()
        self._partial: set[str] = set()
        self._rows: list[tuple[Gtk.ListBoxRow, MailboxObject, Gtk.CheckButton | None]] = []
        self._building = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_size_request(280, -1)
        self.entry = Gtk.SearchEntry(placeholder_text="Filter labels…" if mode == "labels" else "Move to…")
        self.entry.connect("search-changed", lambda *_: self._filter())
        self.entry.connect("activate", self._on_entry_activate)
        box.append(self.entry)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-activated", self._on_row_activated)
        self.listbox.set_filter_func(self._row_visible)
        scrolled = Gtk.ScrolledWindow(child=self.listbox, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      propagate_natural_height=True, max_content_height=360)
        box.append(scrolled)
        if mode == "labels" and on_create:
            self.create_button = Gtk.Button()
            self.create_button.add_css_class("flat")
            self.create_button.connect("clicked", lambda *_: self._create())
            self.create_button.set_visible(False)
            box.append(self.create_button)
        else:
            self.create_button = None
        self.set_child(box)
        self.connect("show", lambda *_: (self.entry.set_text(""), self.entry.grab_focus()))

    def present_for(self, present: set[str], partial: set[str] | None = None) -> None:
        self._present = set(present)
        self._partial = set(partial or ())
        self._rebuild()
        self.popup()

    def _rebuild(self) -> None:
        self._building = True
        while child := self.listbox.get_first_child():
            self.listbox.remove(child)
        self._rows.clear()
        items = self.tree.labels() if self.mode == "labels" else self.tree.all()
        for mb in items:
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(spacing=8)
            hbox.set_margin_start(8 + 14 * mb.depth)
            hbox.set_margin_end(8)
            hbox.set_margin_top(4)
            hbox.set_margin_bottom(4)
            check = None
            if self.mode == "labels":
                check = Gtk.CheckButton(active=mb.id in self._present, inconsistent=mb.id in self._partial)
                check.connect("toggled", self._on_check, mb)
                hbox.append(check)
            else:
                hbox.append(Gtk.Image(icon_name=mb.icon_name))
            hbox.append(Gtk.Label(label=mb.name, xalign=0, ellipsize=3, hexpand=True))
            row.set_child(hbox)
            row.mailbox = mb
            self.listbox.append(row)
            self._rows.append((row, mb, check))
        self._building = False
        self._filter()

    def _row_visible(self, row: Gtk.ListBoxRow) -> bool:
        text = self.entry.get_text().strip().lower()
        return not text or text in row.mailbox.name.lower()

    def _filter(self) -> None:
        self.listbox.invalidate_filter()
        text = self.entry.get_text().strip()
        if self.create_button is not None:
            exact = any(mb.name.lower() == text.lower() for _r, mb, _c in self._rows)
            self.create_button.set_visible(bool(text) and not exact)
            self.create_button.set_label(f"Create label “{text}”")

    def _on_check(self, check: Gtk.CheckButton, mb: MailboxObject) -> None:
        if self._building or self.on_toggle is None:
            return
        check.set_inconsistent(False)
        self.on_toggle(mb, check.get_active())

    def _on_row_activated(self, _lb, row: Gtk.ListBoxRow) -> None:
        if self.mode == "labels":
            check = next((c for r, _m, c in self._rows if r is row), None)
            if check is not None:
                check.set_active(not check.get_active())
        elif self.on_pick is not None:
            self.popdown()
            self.on_pick(row.mailbox)

    def _on_entry_activate(self, *_) -> None:
        for row, _mb, _c in self._rows:
            if self._row_visible(row):
                self._on_row_activated(self.listbox, row)
                return
        if self.create_button is not None and self.create_button.get_visible():
            self._create()

    def _create(self) -> None:
        name = self.entry.get_text().strip()
        if name and self.on_create:
            self.popdown()
            self.on_create(name)
