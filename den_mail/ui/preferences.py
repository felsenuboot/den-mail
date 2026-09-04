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
                 rules_count: int = 0, contact_count: int = 0,
                 on_lock_changed: Callable[[], None] | None = None):
        """`on_open(action)` activates a window action such as "cleanup" or "rules"."""
        super().__init__(title="Preferences")
        self.config = config
        self.add(self._general_page(config, on_manage_identities))
        self.add(self._inbox_page(config, on_sidebar_views, on_screener, on_open, rules_count))
        self.add(self._account_page(config, session, on_sign_out, on_clear_cache, contact_count, on_lock_changed))

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

    # ------------------------------------------------------------- Privacy

    def _privacy_group(self, config, on_lock_changed) -> Adw.PreferencesGroup:
        from .. import lock

        changed = on_lock_changed or (lambda: None)
        polkit = lock.policy_installed()
        group = Adw.PreferencesGroup(
            title="Lock",
            description=("Hides the mail behind a lock page; unlocking asks the system's own authentication prompt."
                         if polkit else
                         "Hides the mail behind a lock page; unlocking asks for a passphrase set here. The system's "
                         "own prompt needs the polkit policy file installed (see the README). A privacy screen, "
                         "not a security boundary: the cache and the token are not encrypted."))
        enable = Adw.SwitchRow(title="Lock screen", subtitle="Adds Lock to the main menu and Ctrl+Shift+L",
                               active=config.get("lock_enabled", False))

        def on_enable(row, _p):
            if row.get_active() and not polkit and not config.get("lock_passphrase"):
                # Nothing could ask for anything yet: set a passphrase or PIN first, or the switch stays off (#65).
                self._set_passphrase(config, self._passphrase_row, then=lambda ok: row.set_active(ok))
                return
            config.set("lock_enabled", row.get_active())
            changed()

        enable.connect("notify::active", on_enable)
        group.add(enable)
        idle = Adw.ComboRow(title="Lock when idle", model=Gtk.StringList.new(
            ["Never", "After 1 minute", "After 5 minutes", "After 15 minutes", "After 30 minutes", "After an hour"]))
        current = int(config.get("lock_idle_minutes", 0))
        idle.set_selected(lock.IDLE_CHOICES.index(current) if current in lock.IDLE_CHOICES else 0)
        idle.connect("notify::selected", lambda r, _p: (config.set("lock_idle_minutes", lock.IDLE_CHOICES[r.get_selected()]), changed()))
        group.add(idle)
        group.add(_switch(config, "lock_with_session", True, "Lock with the session",
                          "When the desktop locks or the screensaver starts"))
        self._passphrase_row = None
        if not polkit:
            kind = Adw.ComboRow(title="Unlock with", model=Gtk.StringList.new(["A passphrase", "A PIN"]))
            kind.set_selected(1 if config.get("lock_kind") == "pin" else 0)
            kind.connect("notify::selected", lambda r, _p: config.set("lock_kind", "pin" if r.get_selected() else "passphrase"))
            group.add(kind)
            has = bool(config.get("lock_passphrase"))
            row = Adw.ActionRow(title="Passphrase or PIN", subtitle="Set" if has else "None yet: needed before the lock can be enabled",
                                activatable=True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda *_: self._set_passphrase(config, row))
            group.add(row)
            self._passphrase_row = row
        return group

    def _set_passphrase(self, config, row: Adw.ActionRow | None, then=None) -> None:
        from .. import lock

        pin = config.get("lock_kind") == "pin"
        what = "PIN" if pin else "passphrase"
        first = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text=what.capitalize())
        second = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="Again", activates_default=True)
        if pin:
            for e in (first, second):
                e.set_input_purpose(Gtk.InputPurpose.PIN)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(first)
        box.append(second)
        dlg = Adw.AlertDialog(heading=f"Den Mail {what}", body="Asked for when unlocking. Leave both empty to remove it.")
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Set")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("ok")
        dlg.set_close_response("cancel")

        def on_response(_d, response):
            if response != "ok":
                if then is not None:
                    then(bool(config.get("lock_passphrase")))
                return
            if first.get_text() != second.get_text() or (pin and not first.get_text().isdigit() and first.get_text()):
                self._set_passphrase(config, row, then)
                return
            config.set("lock_passphrase", lock.hash_passphrase(first.get_text()) if first.get_text() else "")
            if row is not None:
                row.set_subtitle("Set" if first.get_text() else "None yet: needed before the lock can be enabled")
            if then is not None:
                then(bool(first.get_text()))

        dlg.connect("response", on_response)
        dlg.present(self)
        first.grab_focus()

    # ------------------------------------------------------------- Account

    def _account_page(self, config, session, on_sign_out, on_clear_cache, contact_count: int = 0,
                      on_lock_changed=None) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Account", icon_name="avatar-default-symbolic", name="account")
        page.add(self._privacy_group(config, on_lock_changed))

        sync = Adw.PreferencesGroup(title="Sync and notifications")
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
            contacts = getattr(session, "has_contacts", False)
            account.add(Adw.ActionRow(
                title="Address book",
                subtitle=(f"{contact_count} contact{'s' if contact_count != 1 else ''} from Fastmail Contacts, for "
                          "completion and photos" if contacts else
                          "The token has no Contacts scope; completion uses the addresses in cached mail")))
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
