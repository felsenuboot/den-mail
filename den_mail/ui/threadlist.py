"""The conversation list for one mailbox or search."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from ..avatars import sender_key
from ..models.thread import ThreadListModel, ThreadObject
from .sidebar import DRAG_PREFIX
from .widgets import avatar, chip


class ThreadRow(Gtk.Box):
    def __init__(self, avatars):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.avatars = avatars
        self.avatar_key: str | None = None
        self.add_css_class("thread-row")
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self.set_margin_start(4)
        self.set_margin_end(4)
        self.avatar = avatar("?", 36)
        self.avatar.set_valign(Gtk.Align.START)
        self.append(self.avatar)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        self.append(col)

        line1 = Gtk.Box(spacing=6)
        self.dot = Gtk.Image(icon_name="media-record-symbolic", pixel_size=8)
        self.dot.add_css_class("unread-dot")
        self.dot.set_valign(Gtk.Align.CENTER)
        line1.append(self.dot)
        self.participants = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)
        self.participants.add_css_class("participants")
        line1.append(self.participants)
        self.count = Gtk.Label()
        self.count.add_css_class("count-chip")
        line1.append(self.count)
        self.date = Gtk.Label(xalign=1)
        self.date.add_css_class("caption")
        self.date.add_css_class("dim-label")
        line1.append(self.date)
        col.append(line1)

        line2 = Gtk.Box(spacing=6)
        self.subject = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)
        self.subject.add_css_class("subject")
        line2.append(self.subject)
        self.attachment = Gtk.Image(icon_name="fm-attachment-symbolic", pixel_size=14)
        self.attachment.add_css_class("dim-label")
        line2.append(self.attachment)
        self.flag = Gtk.Image(icon_name="fm-star-symbolic", pixel_size=14)
        self.flag.add_css_class("flag-icon")
        line2.append(self.flag)
        col.append(line2)

        line3 = Gtk.Box(spacing=6)
        self.preview = Gtk.Label(xalign=0, ellipsize=3, hexpand=True, single_line_mode=True)
        self.preview.add_css_class("preview")
        self.preview.add_css_class("dim-label")
        line3.append(self.preview)
        self.labels = Gtk.Box(spacing=4, halign=Gtk.Align.END)
        line3.append(self.labels)
        col.append(line3)
        self._labels_text = None

        self.obj: ThreadObject | None = None
        self._handlers: list[int] = []

    def bind(self, obj: ThreadObject) -> None:
        self.obj = obj
        self._sync()
        for prop in ("participants", "subject", "preview", "date-text", "count", "unread", "flagged",
                     "has-attachment", "labels-text", "is-draft"):
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
    """Section header for a group of threads from one sender."""

    def __init__(self, avatars):
        super().__init__(spacing=10)
        self.avatars = avatars
        self.avatar_key: str | None = None
        self.email: str | None = None
        self.add_css_class("sender-header")
        self.avatar = avatar("?", 24)
        self.append(self.avatar)
        self.name = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)
        self.name.add_css_class("heading")
        self.append(self.name)
        self.address = Gtk.Label(xalign=0, ellipsize=3)
        self.address.add_css_class("dim-label")
        self.address.add_css_class("caption")
        self.append(self.address)
        self.count = Gtk.Label()
        self.count.add_css_class("count-chip")
        self.append(self.count)

    def bind(self, obj: ThreadObject, n: int) -> None:
        self.email = obj.sender_email or None
        self.avatar_key = sender_key(self.email)
        self.avatar.set_text(obj.sender_name or "?")
        self.name.set_label(obj.sender_name)
        self.address.set_label(self.email or "")
        self.address.set_visible(bool(self.email) and self.email.lower() != obj.sender_name.lower())
        self.count.set_label(str(n))
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
        self.sort_button = Gtk.MenuButton(icon_name="fm-sort-symbolic", tooltip_text="Sort",
                                          menu_model=self._build_sort_menu())
        header.pack_end(self.sort_button)
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (F5)")
        self.refresh_button.connect("clicked", lambda *_: on_refresh())
        header.pack_end(self.refresh_button)
        self.spinner = Adw.Spinner()
        self.spinner.set_visible(False)
        header.pack_end(self.spinner)
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
        self._header_factory = Gtk.SignalListItemFactory()
        self._header_factory.connect("setup", self._setup_header)
        self._header_factory.connect("bind", self._bind_header)
        self._header_factory.connect("unbind", self._unbind_header)
        self.set_grouped(model.grouped)
        self.listview.add_css_class("thread-list")
        self.listview.add_css_class("navigation-sidebar")
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
        self.set_child(view)
        self._want_top = False
        model.connect("items-changed", self._on_items_changed)
        model.connect("notify::loading", lambda *_: self._update_empty())

    # ------------------------------------------------------------ grouping

    def set_grouped(self, grouped: bool) -> None:
        """Show a header per sender (the model must be sorted by sender)."""
        self.model.set_grouped(grouped)
        self.listview.set_header_factory(self._header_factory if grouped else None)

    def _setup_header(self, _f, header: Gtk.ListHeader) -> None:
        header.set_child(SenderHeader(self.avatars))

    def _bind_header(self, _f, header: Gtk.ListHeader) -> None:
        widget = header.get_child()
        obj = header.get_item()
        if obj is not None:
            widget.bind(obj, header.get_end() - header.get_start())
        self._headers.add(widget)

    def _unbind_header(self, _f, header: Gtk.ListHeader) -> None:
        self._headers.discard(header.get_child())

    # ------------------------------------------------------------ sort

    on_sort_changed: Callable[[str, bool, bool, bool], None] = lambda self, key, flagged, unread, group: None

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
        self._group_action = Gio.SimpleAction.new_stateful("group-by-sender", None, GLib.Variant("b", False))
        self._group_action.connect("change-state", self._on_sort_state)
        group.add_action(self._group_action)
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
        section.append("Group by sender", "sort.group-by-sender")
        menu.append_section(None, section)
        return menu

    def set_sort(self, key: str, flagged_first: bool, unread_first: bool, group: bool | None = None) -> None:
        self._setting_sort = True
        try:
            if group is not None:
                self._group_action.set_state(GLib.Variant("b", group))
            grouped = self._group_action.get_state().get_boolean()
            # grouping needs the sender sort, so the other choices are locked while it is on
            self._sort_action.set_state(GLib.Variant("s", "sender" if grouped else key))
            self._flagged_action.set_state(GLib.Variant("b", False if grouped else flagged_first))
            self._unread_action.set_state(GLib.Variant("b", False if grouped else unread_first))
            for a in (self._sort_action, self._flagged_action, self._unread_action):
                a.set_enabled(not grouped)
        finally:
            self._setting_sort = False

    def _on_sort_state(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        if getattr(self, "_setting_sort", False):
            return
        self.on_sort_changed(self._sort_action.get_state().get_string(),
                             self._flagged_action.get_state().get_boolean(),
                             self._unread_action.get_state().get_boolean(),
                             self._group_action.get_state().get_boolean())

    # ------------------------------------------------------------ rows

    def _on_avatar_ready(self, _service, key: str) -> None:
        for w in (*self._rows, *self._headers):
            if w.avatar_key == key:
                w.refresh_avatar()

    def _setup_row(self, _f, list_item: Gtk.ListItem) -> None:
        row = ThreadRow(self.avatars)
        list_item.set_child(row)
        drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        drag.connect("prepare", self._on_drag_prepare, list_item)
        drag.connect("drag-begin", self._on_drag_begin, list_item)
        row.add_controller(drag)

    def _bind_row(self, _f, list_item: Gtk.ListItem) -> None:
        row = list_item.get_child()
        row.bind(list_item.get_item())
        self._rows.add(row)

    def _unbind_row(self, _f, list_item: Gtk.ListItem) -> None:
        row = list_item.get_child()
        row.unbind()
        self._rows.discard(row)

    def _on_drag_prepare(self, _source, _x, _y, list_item: Gtk.ListItem):
        obj: ThreadObject = list_item.get_item()
        selected = self.selected_threads()
        if obj not in selected:
            selected = [obj]
        ids = [eid for t in selected for eid in t.email_ids]
        return Gdk.ContentProvider.new_for_value(DRAG_PREFIX + ",".join(ids))

    def _on_drag_begin(self, source, _drag, list_item: Gtk.ListItem) -> None:
        paintable = Gtk.WidgetPaintable(widget=list_item.get_child())
        source.set_icon(paintable, 0, 0)

    # ------------------------------------------------------- selection

    def selected_threads(self) -> list[ThreadObject]:
        out = []
        bitset = self.selection.get_selection()
        ok, it, value = Gtk.BitsetIter.init_first(bitset)
        while ok:
            item = self.model.get_item(value)
            if item is not None:
                out.append(item)
            ok, value = it.next()
        return out

    def _on_selection_changed(self, *_) -> None:
        self.on_selection(self.selected_threads())

    def _on_activate(self, _view, position: int) -> None:
        item = self.model.get_item(position)
        if item is not None:
            self.on_activate(item)

    def _on_right_click(self, gesture, _n, x, y) -> None:
        widget = self.listview.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None and not isinstance(widget, ThreadRow):
            widget = widget.get_parent()
        if widget is None or widget.obj is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        obj = widget.obj
        if obj not in self.selected_threads():
            self.select_thread(obj.thread_id)
        self.on_context_menu(obj, int(x), int(y))

    def popup_menu(self, menu: Gio.MenuModel, x: int, y: int) -> None:
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self.listview)
        popover.set_has_arrow(False)
        popover.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = x, y, 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    def select_thread(self, thread_id: str) -> None:
        idx = self.model.index_of(thread_id)
        if idx >= 0:
            self.selection.select_item(idx, True)
            self.listview.scroll_to(idx, Gtk.ListScrollFlags.NONE, None)

    def select_position(self, position: int) -> None:
        if 0 <= position < self.model.get_n_items():
            self.selection.select_item(position, True)
            self.listview.scroll_to(position, Gtk.ListScrollFlags.FOCUS, None)

    def select_all(self) -> None:
        self.selection.select_all()

    def selected_position(self) -> int:
        bitset = self.selection.get_selection()
        return bitset.get_minimum() if not bitset.is_empty() else -1

    # ------------------------------------------------------------ misc

    def set_title(self, title: str, subtitle: str = "") -> None:  # type: ignore[override]
        self.title_widget.set_title(title)
        self.title_widget.set_subtitle(subtitle)
        Adw.NavigationPage.set_title(self, title)

    def set_syncing(self, syncing: bool) -> None:
        self.spinner.set_visible(syncing)
        self.refresh_button.set_visible(not syncing)

    def _update_empty(self) -> None:
        if self.model.get_n_items() > 0:
            self.stack.set_visible_child_name("list")
        elif self.model.loading:
            self.stack.set_visible_child_name("loading")
        else:
            self.stack.set_visible_child_name("empty")
        self._loading_more = False

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

    def _on_items_changed(self, model, position: int, removed: int, added: int) -> None:
        self._update_empty()
        if self._want_top and added and model.get_n_items() == added:
            self._want_top = False
            GLib.idle_add(lambda: self.scrolled.get_vadjustment().set_value(0) or False)

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
    def search_active(self) -> bool:
        """True while the list shows a search or the whole account instead of a mailbox."""
        return bool(self.search_entry.get_text().strip()) or (
            self.search_bar.get_search_mode() and self.scope.get_selected() == 1)
