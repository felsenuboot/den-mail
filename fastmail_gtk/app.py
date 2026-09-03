from __future__ import annotations

import logging
import os
import sys
from urllib.parse import parse_qs, unquote, urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, VERSION  # noqa: E402
from .config import Config  # noqa: E402
from .ui.window import MainWindow  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

ACCELS = {
    "win.compose": ["c", "<Control>n"],
    "win.reply": ["r"],
    "win.reply-all": ["a"],
    "win.forward": ["f"],
    "win.archive": ["e"],
    "win.trash": ["numbersign", "Delete"],
    "win.junk": ["exclam"],
    "win.flag": ["s"],
    "win.mark-unread": ["<Shift>u"],
    "win.mark-read": ["<Shift>i"],
    "win.labels": ["l"],
    "win.move": ["v"],
    "win.search": ["slash", "<Control>f"],
    "win.refresh": ["F5", "<Control>r"],
    "win.select-all": ["<Control>a"],
    "win.next": ["j"],
    "win.previous": ["k"],
    "win.open": ["Return", "o"],
    "win.back": ["Escape"],
    "win.preferences": ["<Control>comma"],
    "win.shortcuts": ["<Control>question"],
    "app.quit": ["<Control>q"],
}


class FastmailApp(Adw.Application):
    def __init__(self) -> None:
        flags = Gio.ApplicationFlags.HANDLES_OPEN
        if os.environ.get("FASTMAIL_GTK_SESSION_URL") or os.environ.get("FASTMAIL_GTK_AUTOPILOT"):
            flags |= Gio.ApplicationFlags.NON_UNIQUE  # test instances must not join a running app
        super().__init__(application_id=APP_ID, flags=flags)
        GLib.set_application_name(APP_NAME)
        self.config = Config()
        self.window: MainWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(HERE, "style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(os.path.join(HERE, "icons"))
        from .ui.preferences import apply_color_scheme

        apply_color_scheme(self.config)
        for name, cb in (("about", self._about), ("quit", lambda *_: self.quit()), ("activate", lambda *_: self.activate())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)
        for action, accels in ACCELS.items():
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
        Adw.Application.do_shutdown(self)

    def _about(self, *_) -> None:
        dlg = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=VERSION,
            developer_name="felsenuboot",
            license_type=Gtk.License.MIT_X11,
            website="https://github.com/felsenuboot/fastmail-gtk",
            issue_url="https://github.com/felsenuboot/fastmail-gtk/issues",
            comments="A Fastmail client built on JMAP, GTK4 and libadwaita.",
        )
        dlg.present(self.window)


def main_entry() -> int:
    logging.basicConfig(level=logging.DEBUG if os.environ.get("FASTMAIL_GTK_DEBUG") else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = FastmailApp()
    return app.run(sys.argv)
