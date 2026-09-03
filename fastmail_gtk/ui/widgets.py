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
    Gtk.UriLauncher(uri=uri).launch(parent, None, lambda launcher, res: _finish_launch(launcher, res, parent, uri))


def _finish_launch(launcher, res, parent, uri: str) -> None:
    try:
        launcher.launch_finish(res)
    except GLib.Error as e:
        import logging

        logging.getLogger(__name__).warning("could not open %s: %s", uri, e.message)
        if parent is not None:
            toast(parent, f"Could not open link: {e.message}", 6)


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
    lbl = Gtk.Label(label=text, ellipsize=3, max_width_chars=30)
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


class AddressCompletion:
    """Attach recipient completion (from previously seen addresses) to an Adw.EntryRow.

    Suggestions show in a popover under the row; Up/Down/Tab/Return pick them,
    Escape closes. Keys are intercepted in the capture phase because the row's
    internal text widget otherwise consumes the arrow keys."""

    def __init__(self, row: Adw.EntryRow, search: Callable[[str], list[dict]]):
        self.row = row
        self.search = search
        self.popover = Gtk.Popover(has_arrow=False, autohide=False, position=Gtk.PositionType.BOTTOM)
        self.popover.set_parent(row)
        self.popover.add_css_class("completion-popover")
        self.popover.set_can_focus(False)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("completion-list")
        self.listbox.connect("row-activated", self._on_pick)
        scrolled = Gtk.ScrolledWindow(child=self.listbox, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      propagate_natural_height=True, max_content_height=280)
        self.popover.set_child(scrolled)
        row.connect("changed", self._on_changed)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", lambda _c, keyval, _code, _state: self.handle_key(keyval))
        row.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: GLib.timeout_add(150, self._hide_if_unfocused))
        row.add_controller(focus)
        row.connect("unrealize", lambda *_: self.popover.unparent())
        self._debounce = 0
        self._suppress = False
        self._rows: list[Gtk.ListBoxRow] = []
        self._active = -1

    @property
    def visible(self) -> bool:
        return self.popover.get_visible()

    def _set_active(self, index: int) -> None:
        if not self._rows:
            self._active = -1
            return
        index = max(0, min(index, len(self._rows) - 1))
        for i, r in enumerate(self._rows):
            if i == index:
                r.add_css_class("active")
            else:
                r.remove_css_class("active")
        self._active = index
        self._rows[index].grab_focus() if False else None  # focus must stay in the entry

    def _current_token(self) -> str:
        return self.row.get_text().rsplit(",", 1)[-1].strip()

    def _on_changed(self, _row) -> None:
        if self._suppress:
            return
        if self._debounce:
            GLib.source_remove(self._debounce)
        self._debounce = GLib.timeout_add(120, self._update)

    def _update(self) -> bool:
        self._debounce = 0
        token = self._current_token()
        while child := self.listbox.get_first_child():
            self.listbox.remove(child)
        self._rows = []
        matches = self.search(token) if len(token) >= 2 else []
        if not matches:
            self.popover.popdown()
            return False
        for m in matches:
            lbrow = Gtk.ListBoxRow(focusable=False)
            lbrow.add_css_class("completion-row")
            label = f"{m['name']} <{m['email']}>" if m.get("name") else m["email"]
            lbl = Gtk.Label(label=label, xalign=0, ellipsize=3)
            lbl.set_margin_top(6)
            lbl.set_margin_bottom(6)
            lbl.set_margin_start(10)
            lbl.set_margin_end(10)
            lbrow.set_child(lbl)
            lbrow.value = label
            self.listbox.append(lbrow)
            self._rows.append(lbrow)
        self._set_active(0)
        self.popover.set_size_request(max(320, self.row.get_width() - 24), -1)
        if not self.popover.get_visible():
            self.popover.popup()
        return False

    def _on_pick(self, _lb, lbrow) -> None:
        text = self.row.get_text()
        head = text.rsplit(",", 1)[0] + ", " if "," in text else ""
        self._suppress = True
        self.row.set_text(f"{head}{lbrow.value}, ")
        self._suppress = False
        self.row.set_position(-1)
        self.popover.popdown()

    def handle_key(self, keyval: int) -> bool:
        """Consume navigation keys while suggestions are showing. Returns True if handled."""
        if not self.popover.get_visible():
            return False
        if keyval == Gdk.KEY_Escape:
            self.popover.popdown()
            return True
        if keyval == Gdk.KEY_Down:
            self._set_active(self._active + 1)
            return True
        if keyval == Gdk.KEY_Up:
            self._set_active(self._active - 1)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            if 0 <= self._active < len(self._rows):
                self._on_pick(self.listbox, self._rows[self._active])
                return True
        return False

    def _hide_if_unfocused(self) -> bool:
        if not self.row.has_focus() and not self.row.get_focus_child():
            self.popover.popdown()
        return False
