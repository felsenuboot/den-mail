"""Small shared widgets and helpers."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gdk, GLib, Gtk


def human_size(n: int | None) -> str:
    n = int(n or 0)
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".0 ", " ")
        n /= 1000
    return f"{n:.1f} GB"


def open_uri(uri: str, parent: Gtk.Window | None = None) -> None:
    Gtk.UriLauncher(uri=uri).launch(parent, None, lambda launcher, res: _finish_launch(launcher, res))


def _finish_launch(launcher, res) -> None:
    try:
        launcher.launch_finish(res)
    except GLib.Error:
        pass


def copy_text(widget: Gtk.Widget, text: str) -> None:
    widget.get_clipboard().set(text)


def toast(widget: Gtk.Widget, text: str, timeout: int = 3) -> None:
    """Find the nearest ToastOverlay above `widget` and show a toast."""
    w = widget
    while w is not None and not isinstance(w, Adw.ToastOverlay):
        w = w.get_parent()
    if w is None:
        root = widget.get_root()
        overlay = getattr(root, "toast_overlay", None)
        if overlay is None:
            return
        w = overlay
    w.add_toast(Adw.Toast(title=text, timeout=timeout))


def avatar(name: str, size: int = 32) -> Adw.Avatar:
    return Adw.Avatar(text=name or "?", show_initials=True, size=size)


def chip(text: str, css: str = "chip") -> Gtk.Label:
    lbl = Gtk.Label(label=text, ellipsize=3, max_width_chars=18)
    lbl.add_css_class(css)
    return lbl


def confirm(parent: Gtk.Widget, heading: str, body: str, action: str, destructive: bool,
            on_confirm: Callable[[], None]) -> None:
    dlg = Adw.AlertDialog(heading=heading, body=body)
    dlg.add_response("cancel", "Cancel")
    dlg.add_response("ok", action)
    if destructive:
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
    dlg.set_default_response("cancel")
    dlg.set_close_response("cancel")

    def on_response(_d, response):
        if response == "ok":
            on_confirm()

    dlg.connect("response", on_response)
    dlg.present(parent)


def text_prompt(parent: Gtk.Widget, heading: str, body: str, initial: str, action: str,
                on_done: Callable[[str], None]) -> None:
    dlg = Adw.AlertDialog(heading=heading, body=body)
    entry = Gtk.Entry(text=initial, activates_default=True)
    entry.select_region(0, -1)
    dlg.set_extra_child(entry)
    dlg.add_response("cancel", "Cancel")
    dlg.add_response("ok", action)
    dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("ok")
    dlg.set_close_response("cancel")

    def on_response(_d, response):
        if response == "ok" and entry.get_text().strip():
            on_done(entry.get_text().strip())

    dlg.connect("response", on_response)
    dlg.present(parent)
    entry.grab_focus()


class AddressEntry(Gtk.Box):
    """A recipient entry with completion from previously seen addresses."""

    def __init__(self, search: Callable[[str], list[dict]], placeholder: str = ""):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.search = search
        self.entry = Gtk.Entry(hexpand=True, placeholder_text=placeholder)
        self.entry.add_css_class("flat")
        self.append(self.entry)
        self.popover = Gtk.Popover(has_arrow=False, autohide=False, position=Gtk.PositionType.BOTTOM)
        self.popover.set_parent(self.entry)
        self.popover.add_css_class("menu")
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.connect("row-activated", self._on_pick)
        self.popover.set_child(self.listbox)
        self.entry.connect("changed", self._on_changed)
        self.entry.connect("notify::has-focus", lambda *_: self._maybe_hide())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.entry.add_controller(keys)
        self._debounce = 0

    # public
    def get_text(self) -> str:
        return self.entry.get_text()

    def set_text(self, text: str) -> None:
        self.entry.set_text(text)

    def grab_focus(self) -> bool:  # type: ignore[override]
        return self.entry.grab_focus()

    # completion
    def _current_token(self) -> str:
        text = self.entry.get_text()
        return text.rsplit(",", 1)[-1].strip()

    def _on_changed(self, _entry) -> None:
        if self._debounce:
            GLib.source_remove(self._debounce)
        self._debounce = GLib.timeout_add(120, self._update)

    def _update(self) -> bool:
        self._debounce = 0
        token = self._current_token()
        while child := self.listbox.get_first_child():
            self.listbox.remove(child)
        if len(token) < 2:
            self.popover.popdown()
            return False
        matches = self.search(token)
        if not matches:
            self.popover.popdown()
            return False
        for m in matches:
            row = Gtk.ListBoxRow()
            label = f"{m['name']} <{m['email']}>" if m.get("name") else m["email"]
            lbl = Gtk.Label(label=label, xalign=0, ellipsize=3)
            lbl.set_margin_top(4)
            lbl.set_margin_bottom(4)
            lbl.set_margin_start(8)
            lbl.set_margin_end(8)
            row.set_child(lbl)
            row.value = label
            self.listbox.append(row)
        self.popover.set_size_request(self.entry.get_width(), -1)
        self.popover.popup()
        return False

    def _on_pick(self, _lb, row) -> None:
        text = self.entry.get_text()
        head = text.rsplit(",", 1)[0] + ", " if "," in text else ""
        self.entry.set_text(f"{head}{row.value}, ")
        self.entry.set_position(-1)
        self.popover.popdown()

    def _on_key(self, _ctrl, keyval, _code, _state) -> bool:
        if not self.popover.get_visible():
            return False
        if keyval == Gdk.KEY_Escape:
            self.popover.popdown()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            row = self.listbox.get_selected_row()
            idx = row.get_index() if row else -1
            idx = idx + 1 if keyval == Gdk.KEY_Down else max(0, idx - 1)
            target = self.listbox.get_row_at_index(idx)
            if target:
                self.listbox.select_row(target)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Tab):
            row = self.listbox.get_selected_row() or self.listbox.get_row_at_index(0)
            if row:
                self._on_pick(self.listbox, row)
                return True
        return False

    def _maybe_hide(self) -> None:
        if not self.entry.has_focus():
            GLib.timeout_add(150, lambda: (self.popover.popdown(), False)[1])

    def do_unroot(self):  # ensure the popover is unparented with us
        self.popover.unparent()
        Gtk.Box.do_unroot(self)
