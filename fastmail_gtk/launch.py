"""Open links and files, optionally steering where the target window ends up.

Hyprland puts *new* windows on the focused workspace but does not switch to a
browser that merely gained a tab on another workspace. Two opt-in modes
(Preferences → Reading → "Open links and attachments") address that:

  * "new-window": start the default browser with its new-window flag, so the page
    opens next to the mail client;
  * "focus": launch as usual, then ask Hyprland (``hyprctl``) to focus the target
    app's most recently used window, switching workspaces if necessary.

"default" leaves everything to the browser (a tab in the existing window).
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from gi.repository import Gdk, Gio, GLib, Gtk

log = logging.getLogger(__name__)

OPEN_MODES = ["default", "new-window", "focus"]

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


def mode() -> str:
    value = _config.get("open_mode", "default") if _config is not None else "default"
    return value if value in OPEN_MODES else "default"


def hyprland_available() -> bool:
    return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")) and shutil.which("hyprctl") is not None


# ------------------------------------------------------------------ pure helpers (tested)


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


def window_classes(app_id: str | None, wm_class: str | None, executable: str | None) -> list[str]:
    """Candidate Wayland app-ids for the windows of an application."""
    cands: list[str] = []
    if app_id:
        cands.append(app_id[:-8] if app_id.endswith(".desktop") else app_id)
    if wm_class:
        cands.append(wm_class)
    exe = Path(executable).name if executable else ""
    if exe:
        cands.append(exe)
        if exe.endswith("-bin"):
            cands.append(exe[:-4])
    seen: list[str] = []
    for c in cands:
        c = c.lower()
        if c and c not in seen:
            seen.append(c)
    return seen


def pick_window(clients: list[dict], classes: list[str]) -> str | None:
    """Address of the most recently focused Hyprland client whose class matches."""
    wanted = {c.lower() for c in classes}
    matches = [c for c in clients
               if c.get("mapped", True) and not c.get("hidden", False)
               and (str(c.get("class") or "").lower() in wanted or str(c.get("initialClass") or "").lower() in wanted)]
    if not matches:
        return None
    best = min(matches, key=lambda c: c.get("focusHistoryID", 1 << 30))
    return best.get("address")


# ------------------------------------------------------------------ Hyprland


def _hyprctl(*args: str) -> str:
    return subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=5, check=False).stdout


def _focus_window(address: str) -> None:
    # Hyprland >= 0.56 takes Lua for `dispatch`; older releases take the classic "focuswindow" form.
    out = _hyprctl("dispatch", f'hl.dsp.focus({{ window = "address:{address}" }})')
    if not out.strip().startswith("ok"):
        _hyprctl("dispatch", "focuswindow", f"address:{address}")


def focus_app_later(classes: list[str], delays: tuple[float, ...] = (0.4, 0.8, 1.2, 1.6)) -> None:
    """After a launch, focus the app's window once it exists (switching workspaces)."""
    if not classes or not hyprland_available():
        return

    def run() -> None:
        for delay in delays:
            time.sleep(delay)
            try:
                clients = json.loads(_hyprctl("clients", "-j") or "[]")
                address = pick_window(clients, classes)
                if address:
                    _focus_window(address)
                    return
            except (OSError, ValueError, subprocess.SubprocessError) as e:
                log.debug("hyprctl failed: %s", e)
                return
        log.debug("no window found for %s", classes)

    threading.Thread(target=run, name="focus-window", daemon=True).start()


def _classes_for(app: Gio.AppInfo | None) -> list[str]:
    if app is None:
        return []
    wm_class = app.get_startup_wm_class() if isinstance(app, Gio.DesktopAppInfo) else None
    return window_classes(app.get_id(), wm_class, app.get_executable())


def _spawn(argv: list[str], parent: Gtk.Widget | None) -> bool:
    try:
        display = parent.get_display() if parent is not None else Gdk.Display.get_default()
        info = Gio.AppInfo.create_from_commandline(" ".join(shlex.quote(a) for a in argv), None,
                                                   Gio.AppInfoCreateFlags.NONE)
        return info.launch([], display.get_app_launch_context() if display else None)
    except GLib.Error as e:
        log.warning("could not run %s: %s", argv[0], e.message)
        return False


# ------------------------------------------------------------------ public API


def open_uri(uri: str, parent: Gtk.Window | None = None, on_error: Callable[[str], None] | None = None) -> None:
    current = mode()
    scheme = urlparse(uri).scheme.lower()
    app = Gio.AppInfo.get_default_for_uri_scheme(scheme) if scheme in ("http", "https") else None
    if current == "new-window" and app is not None:
        argv = new_window_argv(app.get_commandline() or "", uri)
        if argv and _spawn(argv, parent):
            return

    def finish(launcher, res) -> None:
        try:
            launcher.launch_finish(res)
        except GLib.Error as e:
            log.warning("could not open %s: %s", uri, e.message)
            if on_error:
                on_error(e.message)
            return
        if current == "focus":
            focus_app_later(_classes_for(app))

    Gtk.UriLauncher(uri=uri).launch(parent, None, finish)


def open_file(path: Path, content_type: str | None, parent: Gtk.Window | None,
              on_done: Callable[[str | None], None]) -> None:
    """Open a downloaded attachment; `on_done` gets an error message or None."""
    current = mode()
    ctype = content_type or Gio.content_type_guess(str(path), None)[0]
    app = Gio.AppInfo.get_default_for_type(ctype, False) if ctype else None
    if current == "new-window" and app is not None:
        argv = new_window_argv(app.get_commandline() or "", path.as_uri())
        if argv and _spawn(argv, parent):
            on_done(None)
            return

    def finish(launcher, res) -> None:
        try:
            launcher.launch_finish(res)
        except GLib.Error as e:
            on_done(e.message)
            return
        if current == "focus":
            focus_app_later(_classes_for(app))
        on_done(None)

    Gtk.FileLauncher(file=Gio.File.new_for_path(str(path))).launch(parent, None, finish)
