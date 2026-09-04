"""Preferences dialog."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

REMOTE_OPTIONS = ["ask", "always", "never"]
UNDO_SEND_OPTIONS = [0, 5, 10, 20, 30]
SCHEME_OPTIONS = ["system", "light", "dark"]


def apply_color_scheme(config) -> None:
    from .theming import install_palette_guard

    scheme = config.get("color_scheme", "system")
    Adw.StyleManager.get_default().set_color_scheme({
        "light": Adw.ColorScheme.FORCE_LIGHT,
        "dark": Adw.ColorScheme.FORCE_DARK,
    }.get(scheme, Adw.ColorScheme.DEFAULT))
    install_palette_guard()


class PreferencesDialog(Adw.PreferencesDialog):
    def _fill_trusted(self, expander: Adw.ExpanderRow, config) -> None:
        senders = config.trusted_senders()
        expander.set_subtitle(f"{len(senders)} address{'es' if len(senders) != 1 else ''} load remote content automatically")
        for rows in list(getattr(expander, "_rows", [])):
            expander.remove(rows)
        expander._rows = []
        for addr in senders:
            row = Adw.ActionRow(title=addr)
            remove = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Forget")
            remove.add_css_class("flat")
            remove.connect("clicked", lambda _b, a=addr: (config.untrust_sender(a), self._fill_trusted(expander, config)))
            row.add_suffix(remove)
            expander.add_row(row)
            expander._rows.append(row)
        expander.set_enable_expansion(bool(senders))

    def __init__(self, config, session, on_sign_out: Callable[[], None], on_clear_cache: Callable[[], None],
                 on_manage_identities: Callable[[], None] | None = None,
                 on_sidebar_views: Callable[[bool], None] | None = None,
                 on_screener: Callable[[bool], None] | None = None):
        super().__init__(title="Preferences")
        self.config = config
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        self.add(page)

        composing = Adw.PreferencesGroup(title="Composing")
        favs = len(config.favorite_identities())
        identities = Adw.ActionRow(
            title="Favourite identities",
            subtitle=(f"{favs} starred: the From list shows only these" if favs
                      else "None starred: the From list shows every identity"),
            activatable=on_manage_identities is not None)
        identities.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        if on_manage_identities is not None:
            identities.connect("activated", lambda *_: on_manage_identities())
        composing.add(identities)
        undo = Adw.ComboRow(title="Undo send", subtitle="How long a message waits before it really goes out",
                            model=Gtk.StringList.new(["Off", "5 seconds", "10 seconds", "20 seconds", "30 seconds"]))
        current = int(config.get("undo_send_seconds", 10))
        undo.set_selected(UNDO_SEND_OPTIONS.index(current) if current in UNDO_SEND_OPTIONS else 2)
        undo.connect("notify::selected", lambda r, _p: config.set("undo_send_seconds", UNDO_SEND_OPTIONS[r.get_selected()]))
        composing.add(undo)
        page.add(composing)

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
        views = Adw.SwitchRow(title="Views in the sidebar",
                              subtitle="Newsletters, Transactions, Security, Updates, Never read and "
                                       "Big attachments, listed from the local cache",
                              active=config.get("sidebar_views", True))

        def on_views(row, _p):
            config.set("sidebar_views", row.get_active())
            if on_sidebar_views is not None:
                on_sidebar_views(row.get_active())

        views.connect("notify::active", on_views)
        appearance.add(views)
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
        dark_html = Adw.SwitchRow(title="Adapt HTML mail to the dark theme",
                                  subtitle="Flips the message's own colours; images stay as they are",
                                  active=config.get("dark_html", True))
        dark_html.connect("notify::active", lambda r, _p: config.set("dark_html", r.get_active()))
        reading.add(dark_html)
        new_window = Adw.SwitchRow(title="Open links in a new browser window",
                                   subtitle="Instead of a tab in whichever window the browser picks; "
                                            "the new window appears on the current workspace",
                                   active=config.get("open_links_new_window", False))
        new_window.connect("notify::active", lambda r, _p: config.set("open_links_new_window", r.get_active()))
        reading.add(new_window)
        screener = Adw.SwitchRow(title="Screen first-time senders",
                                 subtitle="Mail from a sender you have never seen waits in the Screener view, out of the "
                                          "Inbox, until you let them through or screen them out",
                                 active=config.get("screener", False))

        def on_screen(row, _p):
            config.set("screener", row.get_active())
            if on_screener is not None:
                on_screener(row.get_active())

        screener.connect("notify::active", on_screen)
        reading.add(screener)
        trusted = Adw.ExpanderRow(title="Trusted senders",
                                  subtitle="Remote content loads automatically from these addresses")
        self._fill_trusted(trusted, config)
        reading.add(trusted)
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
