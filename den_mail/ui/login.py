"""First-run page: paste a Fastmail API token."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

from .widgets import open_uri

TOKEN_URL = "https://app.fastmail.com/settings/security/tokens"


class LoginPage(Adw.Bin):
    def __init__(self, on_submit: Callable[[str], None]):
        super().__init__()
        self.on_submit = on_submit
        page = Adw.StatusPage(
            icon_name="fm-mail-unread-symbolic",
            title="Sign in to Fastmail",
            description=(
                "Create an API token in Fastmail under Settings → Privacy &amp; Security → API tokens.\n"
                "Give it the Mail, Submission and Masked Email scopes (plus read-only if you prefer).\n"
                "The token is stored in your system keyring."
            ),
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        box.set_size_request(380, -1)
        self.entry = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="fmu1-…", activates_default=True)
        self.entry.connect("activate", lambda *_: self._submit())
        box.append(self.entry)
        self.error = Gtk.Label(wrap=True, xalign=0)
        self.error.add_css_class("error")
        self.error.set_visible(False)
        box.append(self.error)
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        link = Gtk.Button(label="Open token settings")
        link.connect("clicked", lambda *_: open_uri(TOKEN_URL, self.get_root()))
        buttons.append(link)
        self.button = Gtk.Button(label="Sign in")
        self.button.add_css_class("suggested-action")
        self.button.connect("clicked", lambda *_: self._submit())
        buttons.append(self.button)
        box.append(buttons)
        self.spinner = Adw.Spinner()
        self.spinner.set_visible(False)
        box.append(self.spinner)
        page.set_child(box)
        self.set_child(page)

    def _submit(self) -> None:
        token = self.entry.get_text().strip()
        if not token:
            self.show_error("Paste your API token first.")
            return
        self.set_busy(True)
        self.on_submit(token)

    def set_busy(self, busy: bool) -> None:
        self.button.set_sensitive(not busy)
        self.entry.set_sensitive(not busy)
        self.spinner.set_visible(busy)
        if busy:
            self.error.set_visible(False)

    def show_error(self, message: str) -> None:
        self.set_busy(False)
        self.error.set_label(message)
        self.error.set_visible(True)
