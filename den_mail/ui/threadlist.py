"""The conversation list for one mailbox or search."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk

from ..avatars import sender_key
from ..classify.rules import CATEGORIES, CATEGORY_NAMES, PRIMARY
from ..models.thread import SenderGroup, ThreadListModel, ThreadObject
from .sidebar import DRAG_PREFIX
from .widgets import avatar, chip


class ThreadRow(Gtk.Box):
    def __init__(self, avatars):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.avatars = avatars
        self.avatar_key: str | None = None
        self.list_item: Gtk.ListItem | None = None
        self.add_css_class("thread-row")
        self.check = Gtk.CheckButton(visible=False, valign=Gtk.Align.CENTER, can_focus=False)
        self.check.add_css_class("selection-check")
        self.append(self.check)
        # The unread dot has a column of its own, so the text lines share one left edge.
        self.dot = Gtk.Image(icon_name="media-record-symbolic", pixel_size=8, valign=Gtk.Align.START,
                             margin_top=6)
        self.dot.add_css_class("unread-dot")
        self.append(self.dot)
        self.avatar = avatar("?", 36)
        self.avatar.set_valign(Gtk.Align.START)
        self.append(self.avatar)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        self.append(col)

        line1 = Gtk.Box(spacing=6)
        self.participants = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)
        self.participants.add_css_class("participants")
        line1.append(self.participants)
        self.count = Gtk.Label(valign=Gtk.Align.CENTER)
        self.count.add_css_class("count-chip")
        line1.append(self.count)
        self.date = Gtk.Label(xalign=1)
        self.date.add_css_class("caption")
        self.date.add_css_class("dim-label")
        line1.append(self.date)
        col.append(line1)

        line2 = Gtk.Box(spacing=6)
        # in a sender group line 1 is dropped, so the count and date move here
        self.subject = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)
        self.subject.add_css_class("subject")
        line2.append(self.subject)
        self.attachment = Gtk.Image(icon_name="fm-attachment-symbolic", pixel_size=14)
        self.attachment.add_css_class("dim-label")
        line2.append(self.attachment)
        self.flag = Gtk.Image(icon_name="fm-star-symbolic", pixel_size=14)
        self.flag.add_css_class("flag-icon")
        line2.append(self.flag)
        self.count2 = Gtk.Label(visible=False, valign=Gtk.Align.CENTER)
        self.count2.add_css_class("count-chip")
        line2.append(self.count2)
        self.date2 = Gtk.Label(xalign=1, visible=False)
        self.date2.add_css_class("caption")
        self.date2.add_css_class("dim-label")
        line2.append(self.date2)
        col.append(line2)
        self.line1 = line1
        self.compact = False

        line3 = Gtk.Box(spacing=6)
        self.preview = Gtk.Label(xalign=0, ellipsize=3, hexpand=True, single_line_mode=True)
        self.preview.add_css_class("preview")
        self.preview.add_css_class("dim-label")
        line3.append(self.preview)
        # The category chip (#18) sits before the labels; Primary shows none.
        self.category = chip("", "chip")
        self.category.add_css_class("chip-category")
        self.category.set_visible(False)
        line3.append(self.category)
        self.labels = Gtk.Box(spacing=4, halign=Gtk.Align.END)
        line3.append(self.labels)
        col.append(line3)
        self._labels_text = None
        self._category = None

        self.obj: ThreadObject | None = None
        self._handlers: list[int] = []

    def set_last_in_card(self, last: bool) -> None:
        """The bottom row of a sender card gets the rounded corners."""
        if last:
            self.add_css_class("card-last")
        else:
            self.remove_css_class("card-last")

    def set_compact(self, compact: bool) -> None:
        """Inside a sender group the sender is already in the header: drop the
        avatar and sender line, indent, keep only what is specific to the thread."""
        if compact == self.compact:
            return
        self.compact = compact
        self.avatar.set_visible(not compact)
        for w in (self.count2, self.date2):
            w.set_visible(compact)
        if compact:
            self.add_css_class("in-group")
        else:
            self.remove_css_class("in-group")
        self._sync()

    def bind(self, obj: ThreadObject) -> None:
        self.obj = obj
        self._sync()
        for prop in ("participants", "subject", "preview", "date-text", "count", "unread", "flagged",
                     "has-attachment", "labels-text", "is-draft", "category"):
            self._handlers.append(obj.connect(f"notify::{prop}", lambda *_: self._sync()))

    def unbind(self) -> None:
        if self.obj is not None:
            for hid in self._handlers:
                self.obj.disconnect(hid)
        self._handlers.clear()
        self.obj = None

    def _sync(self) -> None:
        o = self.obj
        if o is None:
            return
        first = o.summary.from_addresses[0] if o.summary.from_addresses else None
        self.avatar.set_text((first.get("name") or first.get("email") or "?") if first else "?")
        self.avatar_key = sender_key(first.get("email")) if first else None
        self.refresh_avatar(first.get("email") if first else None)
        self.participants.set_label(("Draft: " if o.is_draft else "") + o.participants)
        self.subject.set_label(o.subject)
        self.preview.set_label(o.preview or " ")
        self.date.set_label(o.date_text)
        self.count.set_label(str(o.count))
        self.count.set_visible(o.count > 1)
        self.attachment.set_visible(o.has_attachment)
        self.flag.set_visible(o.flagged)
        self.dot.set_opacity(1.0 if o.unread else 0.0)
        if self.compact:
            self.date2.set_label(o.date_text)
            self.count2.set_label(str(o.count))
            self.count2.set_visible(o.count > 1)
            # the sender line only stays when the thread has other participants
            self.line1.set_visible(o.is_draft or o.participants != o.sender_name)
            self.date.set_visible(False)
            self.count.set_visible(False)
        else:
            self.line1.set_visible(True)
            self.date.set_visible(True)
        if o.category != self._category:
            if self._category:
                self.category.remove_css_class(f"category-{self._category}")
            self._category = o.category
            self.category.set_label(CATEGORY_NAMES.get(o.category, o.category))
            self.category.add_css_class(f"category-{o.category}")
            self.category.set_visible(o.category != PRIMARY)
        if o.labels_text != self._labels_text:
            self._labels_text = o.labels_text
            while child := self.labels.get_first_child():
                self.labels.remove(child)
            for name, color in o.labels:
                c = chip(name, "chip" if color >= 0 else "chip-neutral")
                if color >= 0:
                    c.add_css_class(f"label-color-{color}")
                self.labels.append(c)
            self.labels.set_visible(bool(o.labels))
        for w in (self.participants, self.subject):
            if o.unread:
                w.add_css_class("unread")
            else:
                w.remove_css_class("unread")

    def refresh_avatar(self, email: str | None = None) -> None:
        if email is None and self.obj is not None and self.obj.summary.from_addresses:
            email = self.obj.summary.from_addresses[0].get("email")
        texture = self.avatars.get(email) if (self.avatars and email) else None
        self.avatar.set_custom_image(texture)


class SenderHeader(Gtk.Box):
    """Row for a SenderGroup: expander, logo, name, address and conversation count.
    Clicking the row selects the whole group; the arrow folds it."""

    def __init__(self, avatars, on_toggle: Callable[[SenderGroup], None]):
        super().__init__(spacing=8)
        self.avatars = avatars
        self.on_toggle = on_toggle
        self.group: SenderGroup | None = None
        self.avatar_key: str | None = None
        self.email: str | None = None
        self.list_item: Gtk.ListItem | None = None
        self._handlers: list[int] = []
        self.add_css_class("sender-header")
        self.check = Gtk.CheckButton(visible=False, valign=Gtk.Align.CENTER, can_focus=False)
        self.check.add_css_class("selection-check")
        self.append(self.check)
        self.avatar = avatar("?", 32)
        self.append(self.avatar)
        self.name = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)
        self.name.add_css_class("heading")
        self.append(self.name)
        self.address = Gtk.Label(xalign=1, ellipsize=3)
        self.address.add_css_class("dim-label")
        self.address.add_css_class("caption")
        self.append(self.address)
        # Centred, or the chip would stretch to the header's height and lose its shape.
        self.count = Gtk.Label(valign=Gtk.Align.CENTER)
        self.count.add_css_class("count-chip")
        self.append(self.count)
        # The fold arrow sits at the trailing edge, like the arrow of an expander row.
        self.expander = Gtk.Button(icon_name="pan-down-symbolic", has_frame=False, tooltip_text="Fold this sender")
        self.expander.add_css_class("expander")
        self.expander.add_css_class("flat")
        self.expander.set_valign(Gtk.Align.CENTER)
        self.expander.connect("clicked", lambda *_: self.group is not None and self.on_toggle(self.group))
        self.append(self.expander)

    def bind(self, group: SenderGroup) -> None:
        self.unbind()
        self.group = group
        for prop in ("name", "email", "detail", "count", "unread", "collapsed"):
            self._handlers.append(group.connect(f"notify::{prop}", lambda *_: self._sync()))
        self._sync()

    def unbind(self) -> None:
        if self.group is not None:
            for hid in self._handlers:
                self.group.disconnect(hid)
        self._handlers = []
        self.group = None

    def _sync(self) -> None:
        g = self.group
        if g is None:
            return
        self.email = g.email or None
        self.avatar_key = sender_key(self.email)
        self.avatar.set_text(g.name or "?")
        self.name.set_label(g.name)
        self.address.set_label(g.detail or "")
        self.address.set_visible(bool(g.detail) and g.detail.lower() != g.name.lower())
        self.count.set_label(f"{g.unread} / {g.count}" if g.unread else str(g.count))
        for w in (self.name, self.count):  # an unread group's chip is the sidebar's blue badge
            if g.unread:
                w.add_css_class("unread")
            else:
                w.remove_css_class("unread")
        self.expander.set_icon_name("pan-end-symbolic" if g.collapsed else "pan-down-symbolic")
        self.expander.set_tooltip_text("Unfold this sender" if g.collapsed else "Fold this sender")
        if g.collapsed:  # an open group's chevron takes the accent colour, like an expander row's
            self.expander.remove_css_class("expanded")
        else:
            self.expander.add_css_class("expanded")
        # A folded group is a card of its own; an open one is the top of its card.
        if g.collapsed:
            self.add_css_class("card-only")
        else:
            self.remove_css_class("card-only")
        self.refresh_avatar()

    def refresh_avatar(self) -> None:
        texture = self.avatars.get(self.email) if (self.avatars and self.email) else None
        self.avatar.set_custom_image(texture)


class ThreadList(Adw.NavigationPage):
    def __init__(self, model: ThreadListModel,
                 on_selection: Callable[[list[ThreadObject]], None],
                 on_activate: Callable[[ThreadObject], None],
                 on_search: Callable[[str, str], None],
                 on_load_more: Callable[[], None],
                 on_refresh: Callable[[], None],
                 avatars=None):
        super().__init__(title="Inbox", tag="threads")
        self.model = model
        self.avatars = avatars
        self._rows: set[ThreadRow] = set()
        self._headers: set[SenderHeader] = set()
        if avatars is not None:
            avatars.connect("avatar-ready", self._on_avatar_ready)
            # dark logos get a light plate only on the dark theme
            Adw.StyleManager.get_default().connect(
                "notify::dark", lambda *_: [w.refresh_avatar() for w in (*self._rows, *self._headers)])
        self.on_selection = on_selection
        self.on_activate = on_activate
        self.on_search = on_search
        self.on_load_more = on_load_more
        self._search_timer = 0
        self._loading_more = False

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title="Inbox", subtitle="")
        header.set_title_widget(self.title_widget)
        self.search_button = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Search (Ctrl+F)")
        header.pack_end(self.search_button)
        # Unread filter (#34): the window re-runs the mailbox or search query with it.
        self.on_unread_filter: Callable[[bool], None] = lambda on: None
        self.unread_button = Gtk.ToggleButton(icon_name="fm-mail-unread-symbolic", tooltip_text="Only unread")
        self.unread_button.connect("toggled", lambda b: self.on_unread_filter(b.get_active()))
        header.pack_end(self.unread_button)
        # Category filter (#18): local to the loaded list; the list keeps loading pages
        # until enough conversations of that category are on screen.
        self.on_category_filter: Callable[[str | None], None] = lambda category: None
        self.category_button = Gtk.MenuButton(icon_name="fm-filter-symbolic", tooltip_text="Filter by category",
                                              menu_model=self._build_category_menu())
        header.pack_end(self.category_button)
        self.select_button = Gtk.ToggleButton(icon_name="fm-select-symbolic", tooltip_text="Select conversations")
        self.select_button.connect("toggled", lambda b: self.set_selection_mode(b.get_active()))
        header.pack_start(self.select_button)
        self.fold_button = Gtk.Button(icon_name="fm-fold-symbolic", tooltip_text="Fold all groups", visible=False)
        self.fold_button.connect("clicked", lambda *_: self.fold_all(not self._all_folded()))
        header.pack_start(self.fold_button)
        self.sort_button = Gtk.MenuButton(icon_name="fm-sort-symbolic", tooltip_text="Sort",
                                          menu_model=self._build_sort_menu())
        header.pack_end(self.sort_button)
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (F5)")
        self.refresh_button.connect("clicked", lambda *_: on_refresh())
        header.pack_end(self.refresh_button)
        # While syncing the spinner takes the icon's place inside the button, so the
        # header keeps its width and a cramped title does not re-flow.
        self.spinner = Adw.Spinner(width_request=16, height_request=16, valign=Gtk.Align.CENTER)
        view.add_top_bar(header)

        self.search_bar = Gtk.SearchBar(search_mode_enabled=False, show_close_button=True)
        search_box = Gtk.Box(spacing=6)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search  (from: to: subject: is:unread has:attachment)",
                                            hexpand=True)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("stop-search", lambda *_: self.search_bar.set_search_mode(False))
        search_box.append(self.search_entry)
        self.scope = Gtk.DropDown.new_from_strings(["This mailbox", "All mail"])
        self.scope.connect("notify::selected", lambda *_: self._on_search_changed(self.search_entry))
        search_box.append(self.scope)
        self.search_bar.set_child(search_box)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.bind_property("search-mode-enabled", self.search_button, "active",
                                      GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        self.search_bar.connect("notify::search-mode-enabled", self._on_search_mode)
        view.add_top_bar(self.search_bar)

        self.selection = Gtk.MultiSelection(model=model)
        self.selection.connect("selection-changed", self._on_selection_changed)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        factory.connect("unbind", self._unbind_row)
        self.listview = Gtk.ListView(model=self.selection, factory=factory)
        self._selected_groups: set[SenderGroup] = set()
        self._syncing_selection = False
        self.listview.add_css_class("thread-list")
        self.listview.add_css_class("separators")
        self.listview.connect("activate", self._on_activate)
        right = Gtk.GestureClick(button=3)
        right.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        right.connect("pressed", self._on_right_click)
        self.listview.add_controller(right)
        press = Gtk.GestureLongPress()
        press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        press.connect("pressed", lambda g, x, y: self._on_right_click(g, 1, x, y))
        self.listview.add_controller(press)
        self.on_context_menu: Callable[[ThreadObject, int, int], None] = lambda t, x, y: None
        self.scrolled = Gtk.ScrolledWindow(child=self.listview, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scrolled.get_vadjustment().connect("value-changed", self._on_scroll)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self.scrolled, "list")
        self.empty = Adw.StatusPage(icon_name="fm-mail-read-symbolic", title="No conversations",
                                    description="Nothing here yet.")
        self.empty.add_css_class("compact")
        self.stack.add_named(self.empty, "empty")
        self.loading = Adw.StatusPage(title="Loading…")
        self.loading.set_child(Adw.Spinner())
        self.stack.add_named(self.loading, "loading")
        view.set_content(self.stack)
        self.action_bar = self._build_action_bar()
        view.add_bottom_bar(self.action_bar)
        self.selection_mode = False
        click = Gtk.GestureClick(button=1)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_click_in_selection_mode)
        self.listview.add_controller(click)
        self.set_child(view)
        self._want_top = False
        self._sync_fold_button()
        model.connect("items-changed", self._on_items_changed)
        model.connect("notify::loading", lambda *_: self._update_empty())

    # ------------------------------------------------------ selection mode

    def _build_action_bar(self) -> Gtk.ActionBar:
        bar = Gtk.ActionBar(revealed=False)
        bar.add_css_class("selection-bar")
        # Kept narrow on purpose: a hidden action bar still reserves its width, and the
        # list must be able to shrink to the split view's 300sp minimum (#27).
        self.selection_count = Gtk.Label(label="0 selected", ellipsize=3)
        self.selection_count.add_css_class("dim-label")
        bar.pack_start(self.selection_count)
        all_btn = Gtk.Button(label="All", action_name="win.select-all", tooltip_text="Select all (Ctrl+A)")
        bar.pack_start(all_btn)
        more = Gio.Menu()
        more.append("Mark as read", "win.mark-read")
        more.append("Mark as unread", "win.mark-unread")
        more.append("Flag", "win.flag")
        more.append("Mark as spam", "win.junk")
        bar.pack_end(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=more, tooltip_text="More"))
        for icon, action, tip in (("fm-archive-symbolic", "win.archive", "Archive (e)"),
                                  ("user-trash-symbolic", "win.trash", "Delete (#)"),
                                  ("fm-tag-symbolic", "win.labels", "Labels (l)"),
                                  ("folder-symbolic", "win.move", "Move to (v)")):
            bar.pack_end(Gtk.Button(icon_name=icon, action_name=action, tooltip_text=tip))
        return bar

    def set_selection_mode(self, on: bool) -> None:
        """Checkboxes on every row, plain clicks toggle, bulk actions in a bottom bar."""
        if on == self.selection_mode:
            return
        self.selection_mode = on
        self.select_button.set_active(on)
        self.action_bar.set_revealed(on)
        for w in (*self._rows, *self._headers):
            w.check.set_visible(on)
        self._sync_checks()

    def _sync_checks(self) -> None:
        n = len(self.selected_threads())
        self.selection_count.set_label(f"{n} selected")
        if not self.selection_mode:
            return
        sel = self.selection
        for w in self._rows:
            if w.list_item is not None:
                w.check.set_active(sel.is_selected(w.list_item.get_position()))
        for w in self._headers:
            if w.group is not None and w.group.threads:
                selected = self.selected_threads()
                w.check.set_active(all(t in selected for t in w.group.threads))

    def _toggle_position(self, position: int) -> None:
        item = self.model.get_item(position)
        if item is None:
            return
        if isinstance(item, SenderGroup):
            selected = self.selected_threads()
            if all(t in selected for t in item.threads):
                self._syncing_selection = True
                try:
                    self.selection.unselect_item(position)
                    if not item.collapsed:
                        self.selection.unselect_range(position + 1, len(item.threads))
                finally:
                    self._syncing_selection = False
                self._selected_groups.discard(item)
                self._on_selection_changed()
            else:
                self.selection.select_item(position, False)
        elif self.selection.is_selected(position):
            self.selection.unselect_item(position)
        else:
            self.selection.select_item(position, False)

    def _on_click_in_selection_mode(self, gesture, _n, x, y) -> None:
        if not self.selection_mode:
            return
        widget = self.listview.pick(x, y, Gtk.PickFlags.DEFAULT)
        w = widget
        while w is not None and not isinstance(w, (ThreadRow, SenderHeader)):
            if isinstance(w, Gtk.Button):   # expander and checkbox keep their own behaviour
                return
            w = w.get_parent()
        if w is None or w.list_item is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._toggle_position(w.list_item.get_position())

    # ------------------------------------------------------------ grouping

    def set_grouped(self, mode) -> None:
        """Show a row per sender ("sender") or organisation ("domain") above its
        threads, in the order of the active sort; "off" for a flat list."""
        self.model.set_grouped(mode)
        for row in self._rows:
            row.set_compact(self.model.grouped)
        self._sync_fold_button()

    def _toggle_group(self, group: SenderGroup) -> None:
        self.model.toggle_collapsed(group.key)
        self._sync_fold_button()

    def _all_folded(self) -> bool:
        groups = self.model.groups.values()
        return bool(groups) and all(g.collapsed for g in groups)

    def fold_all(self, collapsed: bool) -> None:
        self.model.set_all_collapsed(collapsed)
        self._sync_fold_button()
        self.scroll_to_top()

    def _sync_fold_button(self) -> None:
        groups = list(self.model.groups.values()) if self.model.grouped else []
        self.fold_button.set_visible(bool(groups))
        folded = self._all_folded()
        self.fold_button.set_icon_name("fm-unfold-symbolic" if folded else "fm-fold-symbolic")
        self.fold_button.set_tooltip_text("Unfold all groups" if folded else "Fold all groups")
        # menu entries only when they would change something
        self._fold_actions[True].set_enabled(any(not g.collapsed for g in groups))
        self._fold_actions[False].set_enabled(any(g.collapsed for g in groups))

    # ------------------------------------------------------ category filter

    def _build_category_menu(self) -> Gio.Menu:
        group = Gio.SimpleActionGroup()
        self._category_action = Gio.SimpleAction.new_stateful("filter", GLib.VariantType.new("s"),
                                                               GLib.Variant("s", ""))
        self._category_action.connect("change-state", self._on_category_state)
        group.add_action(self._category_action)
        self.insert_action_group("category", group)
        menu = Gio.Menu()
        item = Gio.MenuItem.new("All categories", None)
        item.set_action_and_target_value("category.filter", GLib.Variant("s", ""))
        menu.append_item(item)
        section = Gio.Menu()
        for category in CATEGORIES:
            item = Gio.MenuItem.new(CATEGORY_NAMES[category], None)
            item.set_action_and_target_value("category.filter", GLib.Variant("s", category))
            section.append_item(item)
        menu.append_section(None, section)
        return menu

    @property
    def category_filter(self) -> str | None:
        return self._category_action.get_state().get_string() or None

    def set_category_filter(self, category: str | None) -> None:
        """Reflect a filter chosen elsewhere (start-up) without firing the callback."""
        self._setting_category = True
        try:
            self._category_action.set_state(GLib.Variant("s", category or ""))
        finally:
            self._setting_category = False
        self._sync_category_button()

    def _on_category_state(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        self._sync_category_button()
        if getattr(self, "_setting_category", False):
            return
        self.on_category_filter(value.get_string() or None)

    def _sync_category_button(self) -> None:
        category = self.category_filter
        if category:
            self.category_button.add_css_class("filter-active")
            self.category_button.set_tooltip_text(f"Showing {CATEGORY_NAMES.get(category, category)} only")
        else:
            self.category_button.remove_css_class("filter-active")
            self.category_button.set_tooltip_text("Filter by category")

    def _fill_filtered_list(self) -> None:
        """A category filter hides most of a page; keep fetching until the list has
        enough rows to scroll, or the query is exhausted."""
        if (self.model.category_filter and not self.model.complete and not self.model.loading
                and not self._loading_more and self.model.get_n_items() < 30):
            self._loading_more = True
            self.on_load_more()

    # ------------------------------------------------------------ sort

    on_sort_changed: Callable[[str, bool, bool, str], None] = lambda self, key, flagged, unread, group: None

    def _build_sort_menu(self) -> Gio.Menu:
        group = Gio.SimpleActionGroup()
        self._sort_action = Gio.SimpleAction.new_stateful("by", GLib.VariantType.new("s"), GLib.Variant("s", "newest"))
        self._sort_action.connect("change-state", self._on_sort_state)
        group.add_action(self._sort_action)
        self._flagged_action = Gio.SimpleAction.new_stateful("flagged-first", None, GLib.Variant("b", False))
        self._flagged_action.connect("change-state", self._on_sort_state)
        group.add_action(self._flagged_action)
        self._unread_action = Gio.SimpleAction.new_stateful("unread-first", None, GLib.Variant("b", False))
        self._unread_action.connect("change-state", self._on_sort_state)
        group.add_action(self._unread_action)
        self._group_action = Gio.SimpleAction.new_stateful("group", GLib.VariantType.new("s"), GLib.Variant("s", "off"))
        self._group_action.connect("change-state", self._on_sort_state)
        group.add_action(self._group_action)
        self._fold_actions = {}
        for name, collapsed in (("fold-all", True), ("unfold-all", False)):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_, c=collapsed: self.fold_all(c))
            a.set_enabled(False)
            group.add_action(a)
            self._fold_actions[collapsed] = a
        self.insert_action_group("sort", group)
        menu = Gio.Menu()
        section = Gio.Menu()
        for label, key in (("Newest first", "newest"), ("Oldest first", "oldest"), ("By sender", "sender"),
                           ("By subject", "subject"), ("By size", "size")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("sort.by", GLib.Variant("s", key))
            section.append_item(item)
        menu.append_section("Sort", section)
        section = Gio.Menu()
        section.append("Flagged on top", "sort.flagged-first")
        section.append("Unread on top", "sort.unread-first")
        menu.append_section(None, section)
        section = Gio.Menu()
        for label, mode in (("Don't group", "off"), ("By sender", "sender"), ("By organisation", "domain")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("sort.group", GLib.Variant("s", mode))
            section.append_item(item)
        menu.append_section("Group", section)
        section = Gio.Menu()
        section.append("Fold all", "sort.fold-all")
        section.append("Unfold all", "sort.unfold-all")
        menu.append_section(None, section)
        return menu

    def set_sort(self, key: str, flagged_first: bool, unread_first: bool, group: str | None = None) -> None:
        self._setting_sort = True
        try:
            if group is not None:
                self._group_action.set_state(GLib.Variant("s", group))
            self._sort_action.set_state(GLib.Variant("s", key))
            self._flagged_action.set_state(GLib.Variant("b", flagged_first))
            self._unread_action.set_state(GLib.Variant("b", unread_first))
        finally:
            self._setting_sort = False

    def _on_sort_state(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        if getattr(self, "_setting_sort", False):
            return
        self.on_sort_changed(self._sort_action.get_state().get_string(),
                             self._flagged_action.get_state().get_boolean(),
                             self._unread_action.get_state().get_boolean(),
                             self._group_action.get_state().get_string())

    # ------------------------------------------------------------ rows

    def _on_avatar_ready(self, _service, key: str) -> None:
        for w in (*self._rows, *self._headers):
            if w.avatar_key == key:
                w.refresh_avatar()

    def _setup_row(self, _f, list_item: Gtk.ListItem) -> None:
        # One widget per list item that can show either a thread or a sender group.
        stack = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        row, header = ThreadRow(self.avatars), SenderHeader(self.avatars, self._toggle_group)
        stack.add_named(row, "thread")
        stack.add_named(header, "group")
        for w in (row, header):
            w.list_item = list_item
            w.check.connect("toggled", self._on_check_toggled, w)
        list_item.set_child(stack)
        drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        drag.connect("prepare", self._on_drag_prepare, list_item)
        drag.connect("drag-begin", self._on_drag_begin, list_item)
        stack.add_controller(drag)

    def _bind_row(self, _f, list_item: Gtk.ListItem) -> None:
        stack = list_item.get_child()
        item = list_item.get_item()
        if isinstance(item, SenderGroup):
            header = stack.get_child_by_name("group")
            header.bind(item)
            stack.set_visible_child(header)
            self._headers.add(header)
            w = header
        else:
            row = stack.get_child_by_name("thread")
            row.set_compact(self.model.grouped)
            row.bind(item)
            self._sync_card_end(row, list_item.get_position())
            stack.set_visible_child(row)
            self._rows.add(row)
            w = row
        w.check.set_visible(self.selection_mode)
        if self.selection_mode:
            self._syncing_checks = True
            try:
                pos = list_item.get_position()
                w.check.set_active(self.selection.is_selected(pos) if isinstance(item, ThreadObject)
                                   else all(t in self.selected_threads() for t in item.threads))
            finally:
                self._syncing_checks = False

    def _unbind_row(self, _f, list_item: Gtk.ListItem) -> None:
        stack = list_item.get_child()
        row, header = stack.get_child_by_name("thread"), stack.get_child_by_name("group")
        row.unbind()
        header.unbind()
        self._rows.discard(row)
        self._headers.discard(header)

    def _on_check_toggled(self, check: Gtk.CheckButton, w) -> None:
        if getattr(self, "_syncing_checks", False) or w.list_item is None:
            return
        pos = w.list_item.get_position()
        if check.get_active() != self.selection.is_selected(pos) or isinstance(w, SenderHeader):
            self._toggle_position(pos)

    def _on_drag_prepare(self, _source, _x, _y, list_item: Gtk.ListItem):
        obj = list_item.get_item()
        selected = self.selected_threads()
        if isinstance(obj, SenderGroup):
            if not any(t in selected for t in obj.threads):
                selected = list(obj.threads)
        elif obj not in selected:
            selected = [obj]
        ids = [eid for t in selected for eid in t.email_ids]
        return Gdk.ContentProvider.new_for_value(DRAG_PREFIX + ",".join(ids))

    def _on_drag_begin(self, source, _drag, list_item: Gtk.ListItem) -> None:
        paintable = Gtk.WidgetPaintable(widget=list_item.get_child())
        source.set_icon(paintable, 0, 0)

    # ------------------------------------------------------- selection

    def _selected_items(self) -> list:
        out = []
        bitset = self.selection.get_selection()
        ok, it, value = Gtk.BitsetIter.init_first(bitset)
        while ok:
            item = self.model.get_item(value)
            if item is not None:
                out.append(item)
            ok, value = it.next()
        return out

    def selected_threads(self) -> list[ThreadObject]:
        """Selected threads in list order; a selected group stands for all of
        its threads (its visible rows are selected too, hidden ones implied)."""
        out: list[ThreadObject] = []
        seen: set[str] = set()
        for item in self._selected_items():
            threads = item.threads if isinstance(item, SenderGroup) else [item]
            for t in threads:
                if t.thread_id not in seen:
                    seen.add(t.thread_id)
                    out.append(t)
        return out

    def _on_selection_changed(self, *_) -> None:
        if self._syncing_selection:
            return
        groups = {i for i in self._selected_items() if isinstance(i, SenderGroup)}
        newly = groups - self._selected_groups
        self._selected_groups = groups
        if newly:
            # a freshly selected sender row takes its visible threads along
            self._syncing_selection = True
            try:
                for group in newly:
                    if not group.collapsed:
                        pos = self.model.items.index(group)
                        self.selection.select_range(pos + 1, len(group.threads), False)
            finally:
                self._syncing_selection = False
        self._syncing_checks = True
        try:
            self._sync_checks()
        finally:
            self._syncing_checks = False
        self.on_selection(self.selected_threads())

    def _on_activate(self, _view, position: int) -> None:
        item = self.model.get_item(position)
        if isinstance(item, SenderGroup):
            self._toggle_group(item)
        elif item is not None:
            self.on_activate(item)

    def _on_right_click(self, gesture, _n, x, y) -> None:
        widget = self.listview.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None and not isinstance(widget, (ThreadRow, SenderHeader)):
            widget = widget.get_parent()
        if widget is None:
            return
        if isinstance(widget, SenderHeader):
            group = widget.group
            if group is None or not group.threads:
                return
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            if not all(t in self.selected_threads() for t in group.threads):
                self.selection.select_item(self.model.items.index(group), True)
            self.on_context_menu(group.threads[0], int(x), int(y))
            return
        if widget.obj is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        obj = widget.obj
        if obj not in self.selected_threads():
            self.select_thread(obj.thread_id)
        self.on_context_menu(obj, int(x), int(y))

    def popup_menu(self, menu: Gio.MenuModel, x: int, y: int) -> None:
        """x, y in list view coordinates.  The popover is parented to the page,
        not the (taller than visible) list view, so it can use the pane's full
        height instead of scrolling inside a clipped area."""
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self)
        popover.set_has_arrow(False)
        # to the right of the pointer: GTK then slides a tall menu up to fit
        # instead of shrinking it into a scrolled box
        popover.set_position(Gtk.PositionType.RIGHT)
        popover.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
        ok, point = self.listview.compute_point(self, Graphene.Point().init(x, y))
        rect = Gdk.Rectangle()
        rect.x, rect.y = (int(point.x), int(point.y)) if ok else (x, y)
        rect.width = rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()

    def select_thread(self, thread_id: str) -> None:
        self.model.reveal(thread_id)
        idx = self.model.index_of(thread_id)
        if idx >= 0:
            self.selection.select_item(idx, True)
            self.listview.scroll_to(idx, Gtk.ListScrollFlags.NONE, None)

    def select_position(self, position: int, step: int = 0) -> None:
        """Select the row at position; with a step, skip sender rows in that direction
        (keyboard navigation moves between conversations, not groups)."""
        n = self.model.get_n_items()
        if not 0 <= position < n:
            return
        if step and isinstance(self.model.get_item(position), SenderGroup):
            probe = position
            while 0 <= probe < n and isinstance(self.model.get_item(probe), SenderGroup):
                probe += step
            if not 0 <= probe < n:   # ran off the end: look the other way
                probe = position
                while 0 <= probe < n and isinstance(self.model.get_item(probe), SenderGroup):
                    probe -= step
            if not 0 <= probe < n:
                return
            position = probe
        self.selection.select_item(position, True)
        self.listview.scroll_to(position, Gtk.ListScrollFlags.FOCUS, None)

    def select_all(self) -> None:
        self.selection.select_all()

    def selected_position(self) -> int:
        bitset = self.selection.get_selection()
        return bitset.get_minimum() if not bitset.is_empty() else -1

    # ------------------------------------------------------------ misc

    def set_scope_label(self, label: str) -> None:
        """What the narrow search scope is called: "This mailbox", or "This view" (#19)."""
        model = self.scope.get_model()
        if model.get_string(0) != label:
            model.splice(0, 1, [label])

    def set_title(self, title: str, subtitle: str = "") -> None:  # type: ignore[override]
        self.title_widget.set_title(title)
        self.title_widget.set_subtitle(subtitle)
        Adw.NavigationPage.set_title(self, title)

    def set_syncing(self, syncing: bool) -> None:
        if syncing:
            self.refresh_button.set_child(self.spinner)
            self.refresh_button.add_css_class("image-button")  # set_child drops it; keeps the padding
        else:
            self.refresh_button.set_icon_name("view-refresh-symbolic")

    def _update_empty(self) -> None:
        self._loading_more = False
        self._fill_filtered_list()
        if self.model.get_n_items() > 0:
            self.stack.set_visible_child_name("list")
        elif self.model.loading or self._loading_more:
            self.stack.set_visible_child_name("loading")
        else:
            self.stack.set_visible_child_name("empty")

    def set_empty_text(self, title: str, description: str) -> None:
        self.empty.set_title(title)
        self.empty.set_description(description)

    def _on_scroll(self, adj: Gtk.Adjustment) -> None:
        if self.model.complete or self.model.loading or self._loading_more:
            return
        if adj.get_upper() - (adj.get_value() + adj.get_page_size()) < 800:
            self._loading_more = True
            self.on_load_more()

    def scroll_to_top(self) -> None:
        self.scrolled.get_vadjustment().set_value(0)
        # The list is usually empty at this point; when the rows land, GTK keeps
        # its anchor row at the top edge, which hides that row's section header.
        self._want_top = True

    def _sync_card_end(self, row: ThreadRow, position: int) -> None:
        nxt = self.model.get_item(position + 1)
        row.set_last_in_card(self.model.grouped and (nxt is None or isinstance(nxt, SenderGroup)))

    def _sync_list_style(self) -> None:
        """Grouped: one boxed-list card per sender. Flat: a content list with separators."""
        grouped = self.model.grouped
        for css, on in (("grouped", grouped), ("separators", not grouped)):
            if on:
                self.listview.add_css_class(css)
            else:
                self.listview.remove_css_class(css)
        for row in self._rows:  # rows GTK did not rebind may have become the end of a card
            if row.list_item is not None:
                self._sync_card_end(row, row.list_item.get_position())

    def _on_items_changed(self, model, position: int, removed: int, added: int) -> None:
        self._update_empty()
        self._sync_fold_button()
        self._sync_list_style()
        adj = self.scrolled.get_vadjustment()
        if self._want_top and added and model.get_n_items() == added:
            self._want_top = False
            GLib.idle_add(lambda: adj.set_value(0) or False)
        elif added and position == 0 and adj.get_value() < 1:
            # GTK keeps the row under the top edge in place, so a message arriving above
            # it would land out of sight; a list that sits at the top stays at the top.
            GLib.idle_add(lambda: adj.set_value(0) or False)

    # ---------------------------------------------------------- search

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        if self._search_timer:
            GLib.source_remove(self._search_timer)
        self._search_timer = GLib.timeout_add(350, self._fire_search)

    def _fire_search(self) -> bool:
        self._search_timer = 0
        scope = "all" if self.scope.get_selected() == 1 else "mailbox"
        self.on_search(self.search_entry.get_text().strip(), scope)
        return False

    def _on_search_mode(self, *_) -> None:
        if not self.search_bar.get_search_mode() and (self.search_entry.get_text() or self.scope.get_selected() == 1):
            self.search_entry.set_text("")
            self.scope.set_selected(0)   # back to the mailbox (also re-fires the query)
            if not self.search_entry.get_text():
                self._fire_search()

    def focus_search(self) -> None:
        self.search_bar.set_search_mode(True)
        self.search_entry.grab_focus()

    @property
    def unread_only(self) -> bool:
        return self.unread_button.get_active()

    @property
    def search_active(self) -> bool:
        """True while the list shows a search or the whole account instead of a mailbox."""
        return bool(self.search_entry.get_text().strip()) or (
            self.search_bar.get_search_mode() and self.scope.get_selected() == 1)
