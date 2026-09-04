"""Preferences dialog: three pages, General, Inbox and Account."""

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


def _switch(config, key: str, default: bool, title: str, subtitle: str = "",
            after: Callable[[bool], None] | None = None) -> Adw.SwitchRow:
    row = Adw.SwitchRow(title=title, subtitle=subtitle, active=config.get(key, default))

    def on_toggle(r, _p):
        config.set(key, r.get_active())
        if after is not None:
            after(r.get_active())

    row.connect("notify::active", on_toggle)
    return row


def _link(title: str, subtitle: str, on_activate: Callable[[], None] | None) -> Adw.ActionRow:
    """A row that opens something else (a dialog, a page)."""
    row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=on_activate is not None)
    row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
    if on_activate is not None:
        row.connect("activated", lambda *_: on_activate())
    return row


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
                 on_screener: Callable[[bool], None] | None = None,
                 on_open: Callable[[str], None] | None = None,
                 rules_count: int = 0):
        """`on_open(action)` activates a window action such as "cleanup" or "rules"."""
        super().__init__(title="Preferences")
        self.config = config
        self.add(self._general_page(config, on_manage_identities))
        self.add(self._inbox_page(config, on_sidebar_views, on_screener, on_open, rules_count))
        self.add(self._account_page(config, session, on_sign_out, on_clear_cache))

    # ------------------------------------------------------------- General

    def _general_page(self, config, on_manage_identities) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic", name="general")

        appearance = Adw.PreferencesGroup(title="Appearance")
        scheme = Adw.ComboRow(title="Theme", model=Gtk.StringList.new(["Follow system", "Light", "Dark"]))
        scheme.set_selected(SCHEME_OPTIONS.index(config.get("color_scheme", "system")))

        def on_scheme(row, _p):
            config.set("color_scheme", SCHEME_OPTIONS[row.get_selected()])
            apply_color_scheme(config)

        scheme.connect("notify::selected", on_scheme)
        appearance.add(scheme)
        appearance.add(_switch(config, "sender_avatars", True, "Show sender logos",
                               "Looks up the sender domain's BIMI logo or favicon (contacts that domain once)"))
        page.add(appearance)

        reading = Adw.PreferencesGroup(title="Reading")
        remote = Adw.ComboRow(title="Remote images in HTML mail",
                              subtitle="Loading them reveals that you opened the message",
                              model=Gtk.StringList.new(["Ask each time", "Always load", "Never load"]))
        remote.set_selected(REMOTE_OPTIONS.index(config.get("load_remote_images", "ask")))
        remote.connect("notify::selected", lambda r, _p: config.set("load_remote_images", REMOTE_OPTIONS[r.get_selected()]))
        reading.add(remote)
        trusted = Adw.ExpanderRow(title="Trusted senders",
                                  subtitle="Remote content loads automatically from these addresses")
        self._fill_trusted(trusted, config)
        reading.add(trusted)
        reading.add(_switch(config, "mark_read_on_open", True, "Mark conversations as read when opened"))
        reading.add(_switch(config, "dark_html", True, "Adapt HTML mail to the dark theme",
                            "Flips the message's own colours; images stay as they are"))
        reading.add(_switch(config, "open_links_new_window", False, "Open links in a new browser window",
                            "Instead of a tab in whichever window the browser picks; "
                            "the new window appears on the current workspace"))
        page.add(reading)

        composing = Adw.PreferencesGroup(title="Composing")
        favs = len(config.favorite_identities())
        identities = _link("Favourite identities",
                           f"{favs} starred: the From list shows only these" if favs
                           else "None starred: the From list shows every identity", on_manage_identities)
        composing.add(identities)
        undo = Adw.ComboRow(title="Undo send", subtitle="How long a message waits before it really goes out",
                            model=Gtk.StringList.new(["Off", "5 seconds", "10 seconds", "20 seconds", "30 seconds"]))
        current = int(config.get("undo_send_seconds", 10))
        undo.set_selected(UNDO_SEND_OPTIONS.index(current) if current in UNDO_SEND_OPTIONS else 2)
        undo.connect("notify::selected", lambda r, _p: config.set("undo_send_seconds", UNDO_SEND_OPTIONS[r.get_selected()]))
        composing.add(undo)
        page.add(composing)
        return page

    # --------------------------------------------------------------- Inbox

    def _inbox_page(self, config, on_sidebar_views, on_screener, on_open, rules_count: int) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Inbox", icon_name="fm-inbox-symbolic", name="inbox")
        opener = (lambda name: (lambda: on_open(name))) if on_open is not None else (lambda name: None)

        cleanup = Adw.PreferencesGroup(
            title="Cleaning up",
            description="Everything here works from the mail the app has listed so far; nothing leaves your computer.")
        cleanup.add(_link("Clean up…", "Senders ranked by how pointless their mail looks; archive, delete, "
                          "mark read or unsubscribe from many at once", opener("cleanup")))
        cleanup.add(_link("Rules…", (f"{rules_count} rule{'s' if rules_count != 1 else ''}: " if rules_count
                                     else "None yet: ") + "what happens to mail from a sender, domain, list or "
                          "category as it arrives. Right-click a conversation for “Always for this sender…”",
                          opener("rules")))
        cleanup.add(_link("Newsletters…", "Every sender with an unsubscribe header, and a way to leave them all",
                          opener("newsletters")))
        page.add(cleanup)

        sidebar = Adw.PreferencesGroup(title="Sidebar")
        sidebar.add(_switch(config, "sidebar_views", True, "Views",
                            "Newsletters, Transactions, Security, Updates, Never read and Big attachments, "
                            "listed from the local cache", on_sidebar_views))
        page.add(sidebar)

        screener = Adw.PreferencesGroup(
            title="First-time senders",
            description="Mail from a sender you have never seen waits in a Screener view, out of the Inbox and "
                        "without a notification, until you let them through or screen them out. Only mail that "
                        "arrives while Den Mail is open is screened.")
        screener.add(_switch(config, "screener", False, "Screen first-time senders", "", on_screener))
        page.add(screener)
        return page

    # ------------------------------------------------------------- Account

    def _account_page(self, config, session, on_sign_out, on_clear_cache) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Account", icon_name="avatar-default-symbolic", name="account")

        sync = Adw.PreferencesGroup(title="Sync & notifications")
        sync.add(_switch(config, "notify_new_mail", True, "Notify about new mail"))
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
        return page
