from __future__ import annotations

import logging
import os
import sys
import threading
from urllib.parse import parse_qs, unquote, urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import APP_ID, APP_NAME, VERSION
from .config import Config
from .shortcuts import application_accels
from .ui.window import MainWindow

HERE = os.path.dirname(os.path.abspath(__file__))



class FastmailApp(Adw.Application):
    def __init__(self) -> None:
        flags = Gio.ApplicationFlags.HANDLES_OPEN
        if os.environ.get("DEN_MAIL_SESSION_URL") or os.environ.get("DEN_MAIL_AUTOPILOT"):
            flags |= Gio.ApplicationFlags.NON_UNIQUE  # test instances must not join a running app
        super().__init__(application_id=APP_ID, flags=flags)
        GLib.set_application_name(APP_NAME)
        self.config = Config()
        self.window: MainWindow | None = None
        self._held = False   # the hold that keeps the process alive while the window is hidden (#2)

    def hide_to_background(self) -> None:
        """The window closed with "keep running" on: hide it, keep syncing and notifying."""
        if not self._held:
            self.hold()
            self._held = True
        if self.window is not None:
            self.window.set_visible(False)

    def _release_background(self) -> None:
        if self._held:
            self.release()
            self._held = False

    def _quit(self, *_) -> None:
        win = getattr(self, "window", None)
        if win is not None:
            win.quitting = True   # the close that follows must not hide to the background (#2)
            for w in list(win.compose_windows):
                w.close()  # may show the "Save draft?" dialog and stay open
            if win.compose_windows or win.flush_sends(self.quit):
                return
        self._release_background()
        self.quit()

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(HERE, "style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(os.path.join(HERE, "icons"))
        from . import launch
        from .ui.preferences import apply_color_scheme

        launch.configure(self.config)
        apply_color_scheme(self.config)
        self._log_theme_state("at startup")
        GLib.timeout_add_seconds(5, lambda: (self._log_theme_state("5s later"), False)[1])
        for name, cb in (("about", self._about), ("quit", self._quit), ("activate", lambda *_: self.activate())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)
        # Remote-controllable (gapplication action <id> set-theme "'light'") for debugging theme switches.
        theme = Gio.SimpleAction.new("set-theme", GLib.VariantType.new("s"))
        theme.connect("activate", self._set_theme)
        self.add_action(theme)

    def _set_theme(self, _action, param) -> None:
        from .ui.preferences import apply_color_scheme

        self.config.set("color_scheme", param.get_string())
        apply_color_scheme(self.config)
        self._log_theme_state("after set-theme")

    def _log_theme_state(self, when: str) -> None:
        sm = Adw.StyleManager.get_default()
        gs = Gtk.Settings.get_default()
        logging.getLogger(__name__).info(
            "theme %s: scheme=%s dark=%s system-supports=%s gtk-theme=%s prefer-dark=%s",
            when, sm.get_color_scheme().value_nick, sm.get_dark(), sm.get_system_supports_color_schemes(),
            gs.get_property("gtk-theme-name"), gs.get_property("gtk-application-prefer-dark-theme"))
        # Only app.* actions are application accelerators; those run before the
        # focused widget sees the key, so the window shortcuts live in a
        # bubble-phase controller instead (shortcuts.py, #33).
        for action, accels in application_accels().items():
            self.set_accels_for_action(action, accels)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MainWindow(self, self.config)
            self.window.start()
            from . import autopilot

            autopilot.install(self)
        self.window.present()

    def do_open(self, files, n_files, hint) -> None:
        self.do_activate()
        for f in files:
            uri = f.get_uri()
            if uri.startswith("mailto:"):
                self._open_mailto(uri)

    def _open_mailto(self, uri: str) -> None:
        parsed = urlparse(uri)
        params = {k: unquote(v[0]) for k, v in parse_qs(parsed.query).items()}
        mailto = {"to": unquote(parsed.path), "cc": params.get("cc", ""), "subject": params.get("subject", ""),
                  "body": params.get("body", "")}

        def when_ready() -> bool:
            if self.window and self.window.engine:
                self.window.compose("new", mailto=mailto)
                return False
            return True

        GLib.timeout_add(300, when_ready)

    def do_shutdown(self) -> None:
        if self.window:
            self.window.shutdown()
            # Take the window down and flush the request now: the main loop is gone, and the
            # interpreter still waits for pool threads that are mid-request (a logo or a
            # body being fetched during a first sync), for up to their timeouts. Meanwhile
            # the compositor would see a window that no longer answers and report it as
            # not responding. The timer bounds that wait as well.
            self.window.destroy()
            self.window = None
            display = Gdk.Display.get_default()
            if display is not None:
                display.flush()
        Adw.Application.do_shutdown(self)
        leave = threading.Timer(2.0, os._exit, [0])
        leave.daemon = True
        leave.start()

    def _about(self, *_) -> None:
        dlg = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=VERSION,
            developer_name="felsenuboot",
            license_type=Gtk.License.MIT_X11,
            website="https://github.com/felsenuboot/den-mail",
            issue_url="https://github.com/felsenuboot/den-mail/issues",
            comments="A Fastmail client built on JMAP, GTK4 and libadwaita.",
        )
        dlg.present(self.window)


def main_entry() -> int:
    try:
        import setproctitle
    except ImportError:  # optional: without it the process merely shows as python3
        pass
    else:
        setproctitle.setproctitle("den-mail")  # what top, btop and ps show (#30)
    logging.basicConfig(level=logging.DEBUG if os.environ.get("DEN_MAIL_DEBUG") else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from . import timing

    timing.install_watchdog()
    app = FastmailApp()
    return app.run(sys.argv)
