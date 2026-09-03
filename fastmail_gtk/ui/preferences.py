"""Preferences dialog."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

REMOTE_OPTIONS = ["ask", "always", "never"]
SCHEME_OPTIONS = ["system", "light", "dark"]


def apply_color_scheme(config) -> None:
    scheme = config.get("color_scheme", "system")
    Adw.StyleManager.get_default().set_color_scheme({
        "light": Adw.ColorScheme.FORCE_LIGHT,
        "dark": Adw.ColorScheme.FORCE_DARK,
    }.get(scheme, Adw.ColorScheme.DEFAULT))


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, config, session, on_sign_out: Callable[[], None], on_clear_cache: Callable[[], None]):
        super().__init__(title="Preferences")
        self.config = config
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        self.add(page)

        appearance = Adw.PreferencesGroup(title="Appearance")
        scheme = Adw.ComboRow(title="Theme", model=Gtk.StringList.new(["Follow system", "Light", "Dark"]))
        scheme.set_selected(SCHEME_OPTIONS.index(config.get("color_scheme", "system")))

        def on_scheme(row, _p):
            config.set("color_scheme", SCHEME_OPTIONS[row.get_selected()])
            apply_color_scheme(config)

        scheme.connect("notify::selected", on_scheme)
        appearance.add(scheme)
        avatars = Adw.SwitchRow(title="Show sender logos",
                                subtitle="Looks up the sender domain's BIMI logo or favicon (contacts that domain once)",
                                active=config.get("sender_avatars", True))
        avatars.connect("notify::active", lambda r, _p: config.set("sender_avatars", r.get_active()))
        appearance.add(avatars)
        page.add(appearance)

        reading = Adw.PreferencesGroup(title="Reading")
        remote = Adw.ComboRow(title="Remote images in HTML mail",
                              subtitle="Loading them reveals that you opened the message",
                              model=Gtk.StringList.new(["Ask each time", "Always load", "Never load"]))
        remote.set_selected(REMOTE_OPTIONS.index(config.get("load_remote_images", "ask")))
        remote.connect("notify::selected", lambda r, _p: config.set("load_remote_images", REMOTE_OPTIONS[r.get_selected()]))
        reading.add(remote)
        mark = Adw.SwitchRow(title="Mark conversations as read when opened", active=config.get("mark_read_on_open", True))
        mark.connect("notify::active", lambda r, _p: config.set("mark_read_on_open", r.get_active()))
        reading.add(mark)
        page.add(reading)

        sync = Adw.PreferencesGroup(title="Sync & notifications")
        notify = Adw.SwitchRow(title="Notify about new mail", active=config.get("notify_new_mail", True))
        notify.connect("notify::active", lambda r, _p: config.set("notify_new_mail", r.get_active()))
        sync.add(notify)
        poll = Adw.SpinRow.new_with_range(30, 3600, 30)
        poll.set_title("Fallback poll interval (seconds)")
        poll.set_subtitle("Used when the push connection is unavailable")
        poll.set_value(config.get("poll_interval_seconds", 300))
        poll.connect("notify::value", lambda r, _p: config.set("poll_interval_seconds", int(r.get_value())))
        sync.add(poll)
        page.add(sync)

        account = Adw.PreferencesGroup(title="Account")
        if session:
            account.add(Adw.ActionRow(title="Signed in as", subtitle=session.username))
            caps = Adw.ExpanderRow(title="Server capabilities", subtitle="What this token's session advertises")
            for uri in sorted(session.capabilities):
                caps.add_row(Adw.ActionRow(title=uri))
            acc_caps = session.accounts.get(session.account_id, {}).get("accountCapabilities", {})
            for uri in sorted(acc_caps):
                if uri not in session.capabilities:
                    caps.add_row(Adw.ActionRow(title=uri, subtitle="account capability"))
            account.add(caps)
        clear = Adw.ButtonRow(title="Clear local cache and resync")
        clear.connect("activated", lambda *_: on_clear_cache())
        account.add(clear)
        signout = Adw.ButtonRow(title="Sign out")
        signout.add_css_class("destructive-action")
        signout.connect("activated", lambda *_: on_sign_out())
        account.add(signout)
        page.add(account)
