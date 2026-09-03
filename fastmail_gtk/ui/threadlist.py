"""The conversation list for one mailbox or search."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from ..models.thread import ThreadListModel, ThreadObject
from .sidebar import DRAG_PREFIX
from .widgets import avatar


class ThreadRow(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
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
        self.labels = Gtk.Label(xalign=1, ellipsize=3)
        self.labels.add_css_class("label-chips")
        line3.append(self.labels)
        col.append(line3)

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
        self.participants.set_label(("Draft: " if o.is_draft else "") + o.participants)
        self.subject.set_label(o.subject)
        self.preview.set_label(o.preview or " ")
        self.date.set_label(o.date_text)
        self.count.set_label(str(o.count))
        self.count.set_visible(o.count > 1)
        self.attachment.set_visible(o.has_attachment)
        self.flag.set_visible(o.flagged)
        self.dot.set_opacity(1.0 if o.unread else 0.0)
        self.labels.set_label(o.labels_text)
        self.labels.set_visible(bool(o.labels_text))
        for w in (self.participants, self.subject):
            if o.unread:
                w.add_css_class("unread")
            else:
                w.remove_css_class("unread")


class ThreadList(Adw.NavigationPage):
    def __init__(self, model: ThreadListModel,
                 on_selection: Callable[[list[ThreadObject]], None],
                 on_activate: Callable[[ThreadObject], None],
                 on_search: Callable[[str, str], None],
                 on_load_more: Callable[[], None],
                 on_refresh: Callable[[], None]):
        super().__init__(title="Inbox", tag="threads")
        self.model = model
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
        self.listview.add_css_class("thread-list")
        self.listview.add_css_class("navigation-sidebar")
        self.listview.connect("activate", self._on_activate)
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
        model.connect("items-changed", lambda *_: self._update_empty())
        model.connect("notify::loading", lambda *_: self._update_empty())

    # ------------------------------------------------------------ rows

    def _setup_row(self, _f, list_item: Gtk.ListItem) -> None:
        row = ThreadRow()
        list_item.set_child(row)
        drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        drag.connect("prepare", self._on_drag_prepare, list_item)
        drag.connect("drag-begin", self._on_drag_begin, list_item)
        row.add_controller(drag)

    def _bind_row(self, _f, list_item: Gtk.ListItem) -> None:
        list_item.get_child().bind(list_item.get_item())

    def _unbind_row(self, _f, list_item: Gtk.ListItem) -> None:
        list_item.get_child().unbind()

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
        if not self.search_bar.get_search_mode() and self.search_entry.get_text():
            self.search_entry.set_text("")
            self._fire_search()

    def focus_search(self) -> None:
        self.search_bar.set_search_mode(True)
        self.search_entry.grab_focus()

    @property
    def search_active(self) -> bool:
        return bool(self.search_entry.get_text().strip())
