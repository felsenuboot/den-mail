"""Colour-scheme handling that survives user GTK CSS.

Wallpaper theming tools (Matugen, pywal, ML4W) write `~/.config/gtk-4.0/colors.css`
redefining libadwaita's named colours to a fixed dark palette at user priority.
That is fine while the app is dark, but it also wins when the app is forced light,
leaving the sidebar and header bars dark. While the app is light we therefore
re-assert libadwaita's light palette from a provider one notch above user priority.
Accent colours are left to the user's file so the app still matches the desktop.
"""

from __future__ import annotations

from gi.repository import Adw, Gdk, Gtk

LIGHT_NAMED_COLOURS = """
@define-color window_bg_color #fafafb;
@define-color window_fg_color rgba(0, 0, 6, 0.8);
@define-color view_bg_color #ffffff;
@define-color view_fg_color rgba(0, 0, 6, 0.8);
@define-color headerbar_bg_color #ffffff;
@define-color headerbar_fg_color rgba(0, 0, 6, 0.8);
@define-color headerbar_border_color rgba(0, 0, 6, 0.8);
@define-color headerbar_backdrop_color #fafafb;
@define-color headerbar_shade_color rgba(0, 0, 6, 0.12);
@define-color headerbar_darker_shade_color rgba(0, 0, 6, 0.12);
@define-color sidebar_bg_color #ebebed;
@define-color sidebar_fg_color rgba(0, 0, 6, 0.8);
@define-color sidebar_backdrop_color #f2f2f4;
@define-color sidebar_border_color rgba(0, 0, 6, 0.07);
@define-color sidebar_shade_color rgba(0, 0, 6, 0.07);
@define-color secondary_sidebar_bg_color #f3f3f5;
@define-color secondary_sidebar_fg_color rgba(0, 0, 6, 0.8);
@define-color secondary_sidebar_backdrop_color #f6f6fa;
@define-color secondary_sidebar_border_color rgba(0, 0, 6, 0.07);
@define-color secondary_sidebar_shade_color rgba(0, 0, 6, 0.07);
@define-color card_bg_color #ffffff;
@define-color card_fg_color rgba(0, 0, 6, 0.8);
@define-color card_shade_color rgba(0, 0, 6, 0.07);
@define-color dialog_bg_color #fafafb;
@define-color dialog_fg_color rgba(0, 0, 6, 0.8);
@define-color popover_bg_color #ffffff;
@define-color popover_fg_color rgba(0, 0, 6, 0.8);
@define-color popover_shade_color rgba(0, 0, 6, 0.07);
@define-color thumbnail_bg_color #ffffff;
@define-color thumbnail_fg_color rgba(0, 0, 6, 0.8);
@define-color shade_color rgba(0, 0, 6, 0.07);
@define-color scrollbar_outline_color #ffffff;
"""

class _State:
    provider: Gtk.CssProvider | None = None
    installed = False   # provider currently added to the display
    hooked = False      # notify::dark handler connected


_state = _State()


def _sync(*_args) -> None:
    display = Gdk.Display.get_default()
    if display is None:
        return
    if _state.provider is None:
        _state.provider = Gtk.CssProvider()
        _state.provider.load_from_string(LIGHT_NAMED_COLOURS)
    want = not Adw.StyleManager.get_default().get_dark()
    if want and not _state.installed:
        Gtk.StyleContext.add_provider_for_display(display, _state.provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        _state.installed = True
    elif not want and _state.installed:
        Gtk.StyleContext.remove_provider_for_display(display, _state.provider)
        _state.installed = False
    for win in Gtk.Window.list_toplevels():
        win.queue_draw()


def install_palette_guard() -> None:
    """Call once after the display exists; keeps the light palette intact from then on."""
    if not _state.hooked:
        Adw.StyleManager.get_default().connect("notify::dark", _sync)
        _state.hooked = True
    _sync()
