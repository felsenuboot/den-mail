"""Mailbox sidebar: system folders, then the label tree. Supports drag-and-drop
of conversations onto mailboxes and a context menu for label management."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from ..jmap.types import ROLE_JUNK, ROLE_TRASH
from ..models.mailbox import MailboxObject, MailboxTree

DRAG_PREFIX = "fastmail-gtk-emails:"


class MailboxRow(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.icon = Gtk.Image()
        self.label = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)
        self.badge = Gtk.Label(valign=Gtk.Align.CENTER)
        self.badge.add_css_class("unread-badge")
        self.append(self.icon)
        self.append(self.label)
        self.append(self.badge)
        self._handlers: list[tuple[GObject.Object, int]] = []
        self.obj: MailboxObject | None = None

    def bind(self, obj: MailboxObject) -> None:
        self.obj = obj
        self._sync()
        for prop in ("name", "unread", "total", "icon-name", "color-index"):
            self._handlers.append((obj, obj.connect(f"notify::{prop}", lambda *_: self._sync())))

    def unbind(self) -> None:
        for obj, hid in self._handlers:
            obj.disconnect(hid)
        self._handlers.clear()
        self.obj = None

    def _sync(self) -> None:
        obj = self.obj
        if obj is None:
            return
        if obj.is_section:
            self.icon.set_visible(False)
            self.label.set_label(obj.name)
            self.label.add_css_class("heading")
            self.label.add_css_class("dim-label")
            self.badge.set_visible(False)
            return
        self.icon.set_visible(True)
        self.icon.set_from_icon_name(obj.icon_name)
        for cls in list(self.icon.get_css_classes()):
            if cls.startswith("label-color-"):
                self.icon.remove_css_class(cls)
        if not obj.is_system:
            self.icon.add_css_class(f"label-color-{obj.color_index}")
        self.label.set_label(obj.name)
        self.label.remove_css_class("heading")
        self.label.remove_css_class("dim-label")
        show_badge = obj.unread > 0 and obj.role not in (ROLE_TRASH, ROLE_JUNK, "sent", "drafts")
        if obj.role == "drafts" and obj.total > 0:
            self.badge.set_label(str(obj.total))
            self.badge.set_visible(True)
        else:
            self.badge.set_label(str(obj.unread))
            self.badge.set_visible(show_badge)
        if show_badge:
            self.label.add_css_class("unread")
        else:
            self.label.remove_css_class("unread")


class Sidebar(Adw.NavigationPage):
    def __init__(self, tree: MailboxTree, account_name: str,
                 on_select: Callable[[MailboxObject], None],
                 on_drop: Callable[[MailboxObject, list[str], bool], None],
                 primary_menu: Gio.MenuModel):
        super().__init__(title="Mailboxes", tag="sidebar")
        self.tree = tree
        self.on_select = on_select
        self.on_drop = on_drop
        self._context_mailbox: MailboxObject | None = None
        self._suppress_select = False

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        title = Adw.WindowTitle(title="Fastmail", subtitle=account_name)
        header.set_title_widget(title)
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=primary_menu, primary=True)
        header.pack_end(menu_button)
        view.add_top_bar(header)

        compose = Gtk.Button(halign=Gtk.Align.FILL)
        compose_content = Adw.ButtonContent(icon_name="fm-compose-symbolic", label="New Message")
        compose.set_child(compose_content)
        compose.add_css_class("suggested-action")
        compose.set_action_name("win.compose")
        compose.set_margin_start(12)
        compose.set_margin_end(12)
        compose.set_margin_top(6)
        compose.set_margin_bottom(6)
        view.add_top_bar(compose)

        # Expansion follows Fastmail's per-label isCollapsed until the user toggles a row.
        self.tree_model = Gtk.TreeListModel.new(tree.root, False, False, self._create_children)
        self._user_toggled: set[str] = set()
        self._applying_expansion = False
        # autoselect must be off before the model is set, or GTK silently selects row 0.
        self.selection = Gtk.SingleSelection(can_unselect=False, autoselect=False)
        self.selection.set_model(self.tree_model)
        self.selection.connect("selection-changed", self._on_selection_changed)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        factory.connect("unbind", self._unbind_row)
        self.listview = Gtk.ListView(model=self.selection, factory=factory, single_click_activate=False)
        self.listview.add_css_class("navigation-sidebar")
        scrolled = Gtk.ScrolledWindow(child=self.listview, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        view.set_content(scrolled)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bottom.set_margin_start(8)
        bottom.set_margin_end(8)
        bottom.set_margin_top(4)
        bottom.set_margin_bottom(4)
        self.status = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)
        self.status.add_css_class("dim-label")
        self.status.add_css_class("caption")
        bottom.append(self.status)
        new_label = Gtk.Button(icon_name="fm-tag-new-symbolic", tooltip_text="New label")
        new_label.add_css_class("flat")
        new_label.set_action_name("sidebar.new-label")
        bottom.append(new_label)
        view.add_bottom_bar(bottom)
        self.set_child(view)

        self._install_actions()
        self.menu = Gio.Menu()
        self.menu.append("Mark all as read", "sidebar.mark-read")
        section = Gio.Menu()
        section.append("New label", "sidebar.new-label")
        section.append("New sub-label", "sidebar.new-sublabel")
        section.append("Rename…", "sidebar.rename")
        section.append("Delete label", "sidebar.delete")
        self.menu.append_section(None, section)
        section2 = Gio.Menu()
        section2.append("Empty mailbox…", "sidebar.empty")
        self.menu.append_section(None, section2)
        self.popover = Gtk.PopoverMenu.new_from_model(self.menu)
        self.popover.set_parent(self.listview)
        self.popover.set_has_arrow(False)

    # ------------------------------------------------------------ actions

    def _install_actions(self) -> None:
        group = Gio.SimpleActionGroup()
        for name, cb in (
            ("new-label", self._act_new_label),
            ("new-sublabel", self._act_new_sublabel),
            ("rename", self._act_rename),
            ("delete", self._act_delete),
            ("mark-read", self._act_mark_read),
            ("empty", self._act_empty),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=cb: cb())
            group.add_action(action)
        self.actions = group
        self.insert_action_group("sidebar", group)

    def _set_action_enabled(self, name: str, enabled: bool) -> None:
        self.actions.lookup_action(name).set_enabled(enabled)

    # callbacks provided by the window
    on_new_label: Callable[[str | None], None] = lambda self, parent_id: None
    on_rename: Callable[[MailboxObject], None] = lambda self, mb: None
    on_delete: Callable[[MailboxObject], None] = lambda self, mb: None
    on_mark_read: Callable[[MailboxObject], None] = lambda self, mb: None
    on_empty: Callable[[MailboxObject], None] = lambda self, mb: None

    def _act_new_label(self) -> None:
        self.on_new_label(None)

    def _act_new_sublabel(self) -> None:
        if self._context_mailbox:
            self.on_new_label(self._context_mailbox.id)

    def _act_rename(self) -> None:
        if self._context_mailbox:
            self.on_rename(self._context_mailbox)

    def _act_delete(self) -> None:
        if self._context_mailbox:
            self.on_delete(self._context_mailbox)

    def _act_mark_read(self) -> None:
        if self._context_mailbox:
            self.on_mark_read(self._context_mailbox)

    def _act_empty(self) -> None:
        if self._context_mailbox:
            self.on_empty(self._context_mailbox)

    # ------------------------------------------------------------- rows

    @staticmethod
    def _create_children(item: MailboxObject):
        if item.is_section:
            return None
        return item.children

    def _setup_row(self, _factory, list_item: Gtk.ListItem) -> None:
        expander = Gtk.TreeExpander(indent_for_icon=False, indent_for_depth=True)
        row = MailboxRow()
        expander.set_child(row)
        list_item.set_child(expander)
        # right-click / long-press context menu
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self._on_right_click, list_item)
        expander.add_controller(click)
        press = Gtk.GestureLongPress()
        press.connect("pressed", lambda g, x, y: self._on_right_click(g, 1, x, y, list_item))
        expander.add_controller(press)
        # drop target for conversations
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        drop.connect("accept", lambda _t, d: True)
        drop.connect("enter", self._on_drag_enter, list_item)
        drop.connect("leave", self._on_drag_leave, list_item)
        drop.connect("motion", self._on_drag_motion, list_item)
        drop.connect("drop", self._on_drop, list_item)
        expander.add_controller(drop)

    def apply_expansion(self) -> None:
        """Expand/collapse rows per Fastmail's isCollapsed, skipping rows the user toggled."""
        self._applying_expansion = True
        try:
            i = 0
            while i < self.tree_model.get_n_items():
                tree_row = self.tree_model.get_item(i)
                obj = tree_row.get_item()
                if tree_row.is_expandable() and obj.id not in self._user_toggled:
                    want = not obj.starts_collapsed
                    if tree_row.get_expanded() != want:
                        tree_row.set_expanded(want)
                i += 1
        finally:
            self._applying_expansion = False

    def _on_row_expanded(self, tree_row: Gtk.TreeListRow, _p) -> None:
        if not self._applying_expansion:
            obj = tree_row.get_item()
            if obj is not None:
                self._user_toggled.add(obj.id)

    def _bind_row(self, _factory, list_item: Gtk.ListItem) -> None:
        tree_row: Gtk.TreeListRow = list_item.get_item()
        expander: Gtk.TreeExpander = list_item.get_child()
        expander.set_list_row(tree_row)
        if not getattr(tree_row, "_fm_hooked", False):
            tree_row.connect("notify::expanded", self._on_row_expanded)
            tree_row._fm_hooked = True
        obj: MailboxObject = tree_row.get_item()
        expander.get_child().bind(obj)
        list_item.set_selectable(not obj.is_section)
        list_item.set_activatable(not obj.is_section)
        expander.set_hide_expander(obj.is_section or obj.children.get_n_items() == 0)
        if obj.is_section:
            expander.add_css_class("section-row")
        else:
            expander.remove_css_class("section-row")

    def _unbind_row(self, _factory, list_item: Gtk.ListItem) -> None:
        expander: Gtk.TreeExpander = list_item.get_child()
        expander.get_child().unbind()
        expander.set_list_row(None)

    @staticmethod
    def _mailbox_of(list_item: Gtk.ListItem) -> MailboxObject | None:
        tree_row = list_item.get_item()
        return tree_row.get_item() if tree_row else None

    # --------------------------------------------------------- selection

    def _on_selection_changed(self, *_) -> None:
        if self._suppress_select:
            return
        tree_row = self.selection.get_selected_item()
        if tree_row is None:
            return
        obj = tree_row.get_item()
        if obj.is_section:
            return
        self.on_select(obj)

    def select_mailbox(self, mailbox_id: str, notify: bool = True) -> bool:
        for i in range(self.tree_model.get_n_items()):
            tree_row = self.tree_model.get_item(i)
            obj = tree_row.get_item()
            if obj.id == mailbox_id:
                self._suppress_select = True
                self.selection.set_selected(i)
                self._suppress_select = False
                if notify:
                    self.on_select(obj)
                return True
        return False

    @property
    def selected(self) -> MailboxObject | None:
        tree_row = self.selection.get_selected_item()
        return tree_row.get_item() if tree_row else None

    def set_status(self, text: str) -> None:
        self.status.set_label(text)

    # ------------------------------------------------------ context menu

    def _on_right_click(self, gesture, _n, x, y, list_item: Gtk.ListItem) -> None:
        obj = self._mailbox_of(list_item)
        if obj is None or obj.is_section:
            return
        self._context_mailbox = obj
        self._set_action_enabled("rename", not obj.is_system and obj.may("mayRename"))
        self._set_action_enabled("delete", not obj.is_system and obj.may("mayDelete"))
        self._set_action_enabled("new-sublabel", obj.may("mayCreateChild"))
        self._set_action_enabled("mark-read", obj.unread > 0)
        self._set_action_enabled("empty", obj.role in (ROLE_TRASH, ROLE_JUNK) and obj.total > 0)
        widget = gesture.get_widget()
        ok, px, py = widget.translate_coordinates(self.listview, x, y)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(px), int(py), 1, 1
        self.popover.set_pointing_to(rect)
        self.popover.popup()

    # ------------------------------------------------------------- DnD

    def _on_drag_enter(self, _target, _x, _y, list_item) -> Gdk.DragAction:
        obj = self._mailbox_of(list_item)
        if obj is None or obj.is_section or not obj.may("mayAddItems"):
            return 0
        list_item.get_child().add_css_class("drop-target")
        return Gdk.DragAction.MOVE

    def _on_drag_motion(self, _target, _x, _y, list_item) -> Gdk.DragAction:
        obj = self._mailbox_of(list_item)
        if obj is None or obj.is_section:
            return 0
        state = _target.get_current_drop().get_display().get_default_seat().get_keyboard().get_modifier_state()
        return Gdk.DragAction.COPY if state & Gdk.ModifierType.CONTROL_MASK else Gdk.DragAction.MOVE

    def _on_drag_leave(self, _target, list_item) -> None:
        list_item.get_child().remove_css_class("drop-target")

    def _on_drop(self, target, value, _x, _y, list_item) -> bool:
        list_item.get_child().remove_css_class("drop-target")
        obj = self._mailbox_of(list_item)
        if obj is None or obj.is_section or not isinstance(value, str) or not value.startswith(DRAG_PREFIX):
            return False
        ids = [i for i in value[len(DRAG_PREFIX):].split(",") if i]
        copy = bool(target.get_current_drop().get_actions() & Gdk.DragAction.COPY) and not (
            target.get_current_drop().get_actions() & Gdk.DragAction.MOVE)
        self.on_drop(obj, ids, copy)
        return True
