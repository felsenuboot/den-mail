"""Preferences dialog: four pages, General, Inbox, Assistant and Account."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk

from .. import version_string
from .a11y import watch as _a11y_watch

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


_PLAIN_CHIPS: Gtk.CssProvider | None = None


def apply_chip_colours(config) -> None:
    """Coloured chips for labels and categories, or plain ones: a provider added on top of
    the stylesheet overrides every colour rule with the same specificity (#105)."""
    from gi.repository import Gdk

    global _PLAIN_CHIPS
    display = Gdk.Display.get_default()
    if display is None:
        return
    if _PLAIN_CHIPS is None:
        chips = [f".chip.label-color-{n}" for n in range(12)] + [
            f".chip.category-{c}" for c in ("transactions", "security", "updates", "newsletters", "lists", "promotions")]
        images = [f"image.label-color-{n}" for n in range(12)]
        _PLAIN_CHIPS = Gtk.CssProvider()
        _PLAIN_CHIPS.load_from_string(
            ", ".join(chips) + " { background: alpha(currentColor, 0.12); color: inherit; }\n"
            + ", ".join(images) + " { color: inherit; }\n")
    if config.get("chip_colours", True):
        Gtk.StyleContext.remove_provider_for_display(display, _PLAIN_CHIPS)
    else:
        Gtk.StyleContext.add_provider_for_display(display, _PLAIN_CHIPS, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


def _switch(config, key: str, default: bool, title: str, subtitle: str = "",
            after: Callable[[bool], None] | None = None) -> Adw.SwitchRow:
    row = Adw.SwitchRow(title=title, subtitle=subtitle, active=config.get(key, default))

    def on_toggle(r, _p):
        config.set(key, r.get_active())
        if after is not None:
            after(r.get_active())

    row.connect("notify::active", on_toggle)
    return row


def advanced_row(*rows: Gtk.Widget) -> Adw.ExpanderRow:
    """The rows a page needs rarely, folded behind one "Advanced" row (#107)."""
    exp = Adw.ExpanderRow(title="Advanced", subtitle="Rarely needed")
    for r in rows:
        exp.add_row(r)
    return exp


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
                 on_lock_changed: Callable[[], None] | None = None,
                 assistant=None):
        """`on_open(action)` activates a window action such as "cleanup" or "rules";
        `assistant` is the window's `den_mail.llm.Assistant` (None: no Assistant page)."""
        super().__init__(title="Preferences", search_enabled=True)   # find a setting by name (#107)
        self.config = config
        self.add(self._general_page(config, on_manage_identities))
        self.add(self._inbox_page(config, on_sidebar_views, on_screener, on_open, rules_count))
        if assistant is not None:
            from .assistant import assistant_page
            self.add(assistant_page(self, config, assistant))
        self.add(self._account_page(config, session, on_sign_out, on_clear_cache, contact_count, on_lock_changed))
        _a11y_watch(self)   # icon-only buttons get their tooltip as accessible name (#123)

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
        appearance.add(_switch(config, "chip_colours", True, "Coloured labels and categories",
                               "Each label and category chip in its own colour", lambda _on: apply_chip_colours(config)))
        from ..avatars import SOURCES, logo_source

        logos = Adw.ComboRow(title="Sender logos", model=Gtk.StringList.new([
            "From each sender's site", "Through DuckDuckGo's icon service", "BIMI only, no web contact", "Off"]))
        logos.set_selected(SOURCES.index(logo_source(config)))
        subtitles = {
            "direct": "The domain's BIMI record, its favicon, or an icon its home page links to; each domain is contacted once",
            "proxy": "Favicons from icons.duckduckgo.com: sender sites see nothing, DuckDuckGo sees the domains",
            "bimi": "Only the BIMI logo from DNS; most senders have none, so most rows show initials",
            "off": "Initials only",
        }

        def on_logos(row, _p):
            source = SOURCES[row.get_selected()]
            config.set("avatar_source", source)
            config.set("sender_avatars", source != "off")
            row.set_subtitle(subtitles[source])

        logos.set_subtitle(subtitles[logo_source(config)])
        logos.connect("notify::selected", on_logos)
        appearance.add(logos)
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
            description="Works from the mail the app has listed; nothing leaves your computer.")
        cleanup.add(_link("Clean up…", "Senders ranked by how pointless their mail looks; archive, delete, "
                          "mark read or unsubscribe from many at once", opener("cleanup")))
        cleanup.add(_link("Rules…", (f"{rules_count} rule{'s' if rules_count != 1 else ''}: " if rules_count
                                     else "None yet: ") + "label, archive, read or delete mail from a sender, domain, "
                          "list or category as it arrives; Den Mail's own, apart from Fastmail's",
                          opener("rules")))
        cleanup.add(_link("Newsletters…", "Every sender with an unsubscribe header, and a way to leave them all",
                          opener("newsletters")))
        page.add(cleanup)


        screener = Adw.PreferencesGroup(
            title="First-time senders",
            description="Mail from senders you have never seen waits in a Screener view until you decide.")
        screener.add(_switch(config, "screener", False, "Screen first-time senders", "", on_screener))
        page.add(screener)
        sidebar = Adw.PreferencesGroup(title="Sorting")
        sidebar.add(_switch(config, "sidebar_views", True, "Views",
                            "Newsletters, Transactions, Security, Updates, Never read and Big attachments, "
                            "listed from the local cache", on_sidebar_views))
        sidebar.add(_switch(config, "label_suggestions", True, "Suggest labels",
                            "A chip offers a label learned from the mail you have labelled"))
        page.add(sidebar)
        return page

    # ------------------------------------------------------------- Privacy

    def _privacy_group(self, config, on_lock_changed) -> Adw.PreferencesGroup:
        from .. import lock

        changed = on_lock_changed or (lambda: None)
        polkit = lock.policy_installed()
        keyring = lock.keyring_available()
        group = Adw.PreferencesGroup(title="Lock", description="A privacy screen for the mail; nothing is encrypted by it.")
        enable = Adw.SwitchRow(title="Lock screen", subtitle="Lock in the main menu, Ctrl+Shift+L",
                               active=config.get("lock_enabled", False))
        methods = ([lock.METHOD_SYSTEM] if polkit else []) + [lock.METHOD_PASSPHRASE, lock.METHOD_PIN] \
            + ([lock.METHOD_KEYRING] if keyring else [])
        explain = {
            lock.METHOD_SYSTEM: "The system's own authentication prompt",
            lock.METHOD_PASSPHRASE: "A passphrase set below",
            lock.METHOD_PIN: "A PIN set below",
            lock.METHOD_KEYRING: "The keyring daemon's prompt, for a Den Mail keyring of its own",
        }
        self._passphrase_row = None
        self._keyring_row = None

        def on_enable(row, _p):
            m = lock.method(config)
            if row.get_active() and not lock.method_ready(config, m):
                # Nothing could ask for anything yet: the secret first, or the keyring (#65, #66).
                if m == lock.METHOD_KEYRING:
                    self._create_keyring(lambda ok: row.set_active(ok))
                else:
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

        kind = Adw.ComboRow(title="Unlock with", model=Gtk.StringList.new([lock.METHOD_TITLES[m] for m in methods]))
        current_method = lock.method(config)
        kind.set_selected(methods.index(current_method) if current_method in methods else 0)
        group.add(kind)
        row = Adw.ActionRow(activatable=True)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda *_: self._set_passphrase(config, row))
        group.add(row)
        self._passphrase_row = row
        keyring_row = Adw.ActionRow(title="Den Mail keyring")
        group.add(keyring_row)
        self._keyring_row = keyring_row

        def refresh() -> None:
            m = lock.method(config)
            kind.set_subtitle(explain[m])
            pin = m == lock.METHOD_PIN
            row.set_title("PIN" if pin else "Passphrase")
            row.set_subtitle("Set" if config.get("lock_passphrase") else "Not set yet")
            row.set_visible(m in (lock.METHOD_PASSPHRASE, lock.METHOD_PIN))
            keyring_row.set_visible(m == lock.METHOD_KEYRING)
            keyring_row.set_subtitle("Exists" if keyring and lock.keyring_exists() else "Not created yet")

        self._refresh_lock_rows = refresh

        def select(m: str) -> None:
            """Set the combo without running on_kind."""
            kind.handler_block(handler)
            kind.set_selected(methods.index(m))
            kind.handler_unblock(handler)

        def on_kind(r, _p):
            before = lock.method(config)
            m = methods[r.get_selected()]
            if m == before:
                return
            if m == lock.METHOD_KEYRING and not lock.keyring_exists():
                # The daemon asks for the new keyring's password now; cancelled or failed, the
                # choice goes back to what it was, so the lock never points at nothing (#95).
                def created(ok: bool) -> None:
                    if ok:
                        config.set("lock_method", m)
                    else:
                        select(before)
                    refresh()

                self._create_keyring(created)
                return
            config.set("lock_method", m)
            config.set("lock_kind", "pin" if m == lock.METHOD_PIN else "passphrase")
            if m in (lock.METHOD_PASSPHRASE, lock.METHOD_PIN) and before in (lock.METHOD_PASSPHRASE, lock.METHOD_PIN):
                config.set("lock_passphrase", "")   # a passphrase is not a PIN and the other way round
            refresh()
            if config.get("lock_enabled") and not lock.method_ready(config, m):
                enable.set_active(False)   # off until this method can ask something (#65)

        handler = kind.connect("notify::selected", on_kind)
        refresh()
        return group

    def _create_keyring(self, then) -> None:
        """Create the Den Mail collection on a thread (the daemon prompts); `then(ok)` on the main loop."""
        import threading

        from .. import lock

        def run() -> None:
            try:
                lock.keyring_create()
                ok, message = True, "Den Mail keyring created"
            except Exception as e:  # noqa: BLE001 - GLib.Error from the daemon (cancelled too), or no daemon
                ok, message = False, f"No keyring created: {getattr(e, 'message', e)}"
            GLib.idle_add(done, ok, message)

        def done(ok: bool, message: str) -> bool:
            self.add_toast(Adw.Toast(title=message))
            then(ok)
            return False

        threading.Thread(target=run, name="keyring-create", daemon=True).start()

    def _set_passphrase(self, config, row: Adw.ActionRow | None, then=None) -> None:
        from .. import lock
        from .widgets import secret_entry

        pin = lock.method(config) == lock.METHOD_PIN
        what = "PIN" if pin else "passphrase"
        first = secret_entry(pin, what.capitalize())
        second = secret_entry(pin, "Again", activates_default=True)
        problem = Gtk.Label(xalign=0, wrap=True, visible=False, css_classes=["error"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(first)
        box.append(second)
        box.append(problem)
        dlg = Adw.AlertDialog(heading=f"Set a {what}",
                              body=f"Asked for when unlocking. {'Digits only. ' if pin else ''}Leave both empty to remove it.")
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
            text = first.get_text()
            if text != second.get_text():
                problem.set_label("The two entries differ")
            elif pin and text and not text.isdigit():
                problem.set_label("A PIN is digits only")
            else:
                config.set("lock_passphrase", lock.hash_passphrase(text) if text else "")
                if row is not None:
                    row.set_subtitle("Set" if text else "Not set yet")
                if then is not None:
                    then(bool(text))
                return
            problem.set_visible(True)
            second.set_text("")
            second.grab_focus()
            dlg.present(self)   # keep asking in the same dialog rather than closing it

        dlg.connect("response", on_response)
        dlg.present(self)
        first.grab_focus()

    # ------------------------------------------------------------- Account

    def _account_page(self, config, session, on_sign_out, on_clear_cache, contact_count: int = 0,
                      on_lock_changed=None) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Account", icon_name="avatar-default-symbolic", name="account")
        # In the order they are needed (#107): the account, sync, the lock, then the rare rows.
        account = Adw.PreferencesGroup(title="Account")
        if session:
            account.add(Adw.ActionRow(title="Signed in as", subtitle=session.username))
            contacts = getattr(session, "has_contacts", False)
            account.add(Adw.ActionRow(
                title="Address book",
                subtitle=(f"{contact_count} contact{'s' if contact_count != 1 else ''} from Fastmail Contacts, for "
                          "completion and photos" if contacts else
                          "The token has no Contacts scope; completion uses the addresses in cached mail")))
        account.add(Adw.ActionRow(title="Version", subtitle=version_string()))   # with the commit from a checkout (#112)
        clear = Adw.ButtonRow(title="Clear local cache and resync")
        clear.connect("activated", lambda *_: on_clear_cache())
        account.add(clear)
        signout = Adw.ButtonRow(title="Sign out")
        signout.add_css_class("destructive-action")
        signout.connect("activated", lambda *_: on_sign_out())
        account.add(signout)
        page.add(account)

        sync = Adw.PreferencesGroup(title="Sync and notifications")
        sync.add(_switch(config, "notify_new_mail", True, "Notify about new mail"))
        sync.add(_switch(config, "run_in_background", False, "Keep running when the window is closed",
                         "Syncing and notifications go on; Quit (Ctrl+Q) ends it"))
        page.add(sync)

        page.add(self._privacy_group(config, on_lock_changed))

        advanced = Adw.PreferencesGroup(title="Advanced", description="Rarely needed.")
        poll = Adw.SpinRow.new_with_range(30, 3600, 30)
        poll.set_title("Fallback poll interval (seconds)")
        poll.set_subtitle("Used when the push connection is unavailable")
        poll.set_value(config.get("poll_interval_seconds", 300))
        poll.connect("notify::value", lambda r, _p: config.set("poll_interval_seconds", int(r.get_value())))
        advanced.add(poll)
        if session:
            caps = Adw.ExpanderRow(title="Server capabilities", subtitle="What this token's session advertises")
            for uri in sorted(session.capabilities):
                caps.add_row(Adw.ActionRow(title=uri))
            acc_caps = session.accounts.get(session.account_id, {}).get("accountCapabilities", {})
            for uri in sorted(acc_caps):
                if uri not in session.capabilities:
                    caps.add_row(Adw.ActionRow(title=uri, subtitle="account capability"))
            advanced.add(caps)
        page.add(advanced)
        return page
