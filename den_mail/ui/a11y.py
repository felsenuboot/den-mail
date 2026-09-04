"""Accessible names for icon-only buttons (#123).

A tooltip is not read by a screen reader as the control's name; GTK wants an
accessible label. Rather than remembering that at every `Gtk.Button(icon_name=…)`,
each window, dialog and card calls `watch()` once, and when it maps, every
button in it that shows an icon and no text gets its tooltip as its label.
"""

from __future__ import annotations

from gi.repository import Adw, Gtk

BUTTONS = (Gtk.Button, Gtk.MenuButton, Gtk.ToggleButton)


def _has_text(button: Gtk.Widget) -> bool:
    if isinstance(button, Gtk.Button | Gtk.ToggleButton) and button.get_label():
        return True
    child = button.get_child()
    return (isinstance(child, Adw.ButtonContent) and bool(child.get_label())) or isinstance(child, Gtk.Label)


def label_icon_buttons(root: Gtk.Widget) -> int:
    """Give every icon-only button under `root` its tooltip as the accessible name; the count."""
    named = 0
    stack = [root]
    while stack:
        w = stack.pop()
        if isinstance(w, BUTTONS) and not _has_text(w):
            tip = w.get_tooltip_text()
            if tip and not getattr(w, "_a11y_named", False):
                w.update_property([Gtk.AccessibleProperty.LABEL], [tip])
                w._a11y_named = True
                named += 1
        child = w.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return named


def watch(widget: Gtk.Widget) -> None:
    """Name the icon buttons under `widget` every time it maps (dialogs map once; a window
    that hides to the background maps again, which is harmless)."""
    widget.connect("map", lambda w: label_icon_buttons(w))
