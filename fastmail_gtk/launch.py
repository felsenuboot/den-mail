"""Open links and downloaded attachments.

Everything goes through the desktop's default handlers (portal / GIO). The one
option is "new browser window": instead of letting the browser add a tab to
whichever window it likes, start it with its new-window switch so the page
appears next to the mail client. That is plain browser command-line usage and
works on any desktop.

Whether a browser that merely gained a tab is allowed to take focus is the
compositor's decision (xdg-activation): the launch passes an activation token
along, and GNOME/KDE honour it by default; Hyprland needs
``misc.focus_on_activate = true``.
"""

from __future__ import annotations

import logging
import shlex
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from gi.repository import Gdk, Gio, GLib, Gtk

log = logging.getLogger(__name__)

# Executable name or desktop-entry id -> arguments that force a new window.
NEW_WINDOW_ARGS: dict[str, list[str]] = {}
for _name in (
    # Firefox family
    "firefox", "firefox-bin", "firefox-esr", "firefox-developer-edition", "firefox-nightly", "org.mozilla.firefox",
    "zen", "zen-bin", "zen-browser", "app.zen_browser.zen", "librewolf", "io.gitlab.librewolf-community",
    "floorp", "one.ablaze.floorp", "waterfox", "net.waterfox.waterfox", "mullvad-browser", "torbrowser",
    "torbrowser-launcher", "org.torproject.torbrowser-launcher",
    # Chromium family
    "chromium", "chromium-browser", "org.chromium.Chromium", "ungoogled-chromium", "chrome", "google-chrome",
    "google-chrome-stable", "google-chrome-beta", "google-chrome-unstable", "com.google.Chrome", "brave",
    "brave-browser", "brave-bin", "com.brave.Browser", "vivaldi", "vivaldi-stable", "com.vivaldi.Vivaldi",
    "microsoft-edge", "microsoft-edge-stable", "com.microsoft.Edge", "opera", "thorium", "thorium-browser",
    # others with a documented new-window switch
    "epiphany", "org.gnome.Epiphany", "falkon", "org.kde.falkon",
):
    NEW_WINDOW_ARGS[_name] = ["--new-window"]
NEW_WINDOW_ARGS["qutebrowser"] = ["--target", "window"]
NEW_WINDOW_ARGS["org.qutebrowser.qutebrowser"] = ["--target", "window"]

TARGET_CODES = {"%u", "%U", "%f", "%F"}

_config = None


def configure(config) -> None:
    global _config
    _config = config


def wants_new_window() -> bool:
    return bool(_config.get("open_links_new_window", False)) if _config is not None else False


def new_window_argv(commandline: str, target: str) -> list[str] | None:
    """Turn a desktop-entry Exec line into argv opening `target` in a new window.

    Returns None when the application is not a browser we know a new-window
    switch for. Wrappers such as ``env`` or ``flatpak run … org.mozilla.firefox``
    are handled by looking at every token, not just the first.
    """
    try:
        tokens = shlex.split(commandline)
    except ValueError:
        return None
    flags = None
    for tok in tokens:
        if tok.startswith("%"):
            continue
        flags = NEW_WINDOW_ARGS.get(Path(tok).name)
        if flags:
            break
    if not flags:
        return None
    argv: list[str] = []
    placed = False
    for tok in tokens:
        if tok in TARGET_CODES:
            if not placed:
                # flatpak's file forwarding treats everything between "@@u" and "@@" as a URI, so the
                # switch goes in front of that span
                at = len(argv) - 1 if argv and argv[-1] in ("@@u", "@@") else len(argv)
                argv[at:at] = flags
                argv.append(target)
                placed = True
            continue
        if len(tok) == 2 and tok.startswith("%") and tok != "%%":
            continue  # %i, %c, %k and deprecated codes
        argv.append(tok.replace("%%", "%"))
    if not placed:
        argv.extend(flags)
        argv.append(target)
    return argv


def _spawn(argv: list[str], parent: Gtk.Widget | None) -> bool:
    try:
        display = parent.get_display() if parent is not None else Gdk.Display.get_default()
        info = Gio.AppInfo.create_from_commandline(" ".join(shlex.quote(a) for a in argv), None,
                                                   Gio.AppInfoCreateFlags.NONE)
        return info.launch([], display.get_app_launch_context() if display else None)
    except GLib.Error as e:
        log.warning("could not run %s: %s", argv[0], e.message)
        return False


def _new_window_launch(app: Gio.AppInfo | None, target: str, parent: Gtk.Widget | None) -> bool:
    if not wants_new_window() or app is None:
        return False
    argv = new_window_argv(app.get_commandline() or "", target)
    return bool(argv) and _spawn(argv, parent)


def open_uri(uri: str, parent: Gtk.Window | None = None, on_error: Callable[[str], None] | None = None) -> None:
    scheme = urlparse(uri).scheme.lower()
    app = Gio.AppInfo.get_default_for_uri_scheme(scheme) if scheme in ("http", "https") else None
    if _new_window_launch(app, uri, parent):
        return

    def finish(launcher, res) -> None:
        try:
            launcher.launch_finish(res)
        except GLib.Error as e:
            log.warning("could not open %s: %s", uri, e.message)
            if on_error:
                on_error(e.message)

    Gtk.UriLauncher(uri=uri).launch(parent, None, finish)


def open_file(path: Path, content_type: str | None, parent: Gtk.Window | None,
              on_done: Callable[[str | None], None]) -> None:
    """Open a downloaded attachment; `on_done` gets an error message or None."""
    ctype = content_type or Gio.content_type_guess(str(path), None)[0]
    app = Gio.AppInfo.get_default_for_type(ctype, False) if ctype else None
    if _new_window_launch(app, path.as_uri(), parent):
        on_done(None)
        return

    def finish(launcher, res) -> None:
        try:
            launcher.launch_finish(res)
        except GLib.Error as e:
            on_done(e.message)
            return
        on_done(None)

    Gtk.FileLauncher(file=Gio.File.new_for_path(str(path))).launch(parent, None, finish)
