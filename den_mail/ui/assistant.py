"""The Assistant page of Preferences (#69): which language model, where, with what key."""

from __future__ import annotations

import threading

from gi.repository import Adw, GLib, Gtk

from .. import llm
from ..llm.http import host_of
from .preferences import advanced_row

LIMIT_RANGE = (1, 5000, 10)


def assistant_page(dialog: Adw.PreferencesDialog, config, assistant: llm.Assistant) -> Adw.PreferencesPage:
    page = Adw.PreferencesPage(title="Assistant", icon_name="fm-assistant-symbolic", name="assistant")
    keys = list(llm.PROVIDERS)

    group = Adw.PreferencesGroup(
        title="Language model",
        description="Summaries and other features ask a language model, only when you use them.")
    page.add(group)

    enable = Adw.SwitchRow(title="Use an assistant", active=config.get("assistant_enabled", False))
    group.add(enable)

    provider = Adw.ComboRow(title="Provider", model=Gtk.StringList.new([llm.PROVIDERS[k].title for k in keys]))
    current = str(config.get("assistant_provider") or llm.DEFAULT_PROVIDER)
    provider.set_selected(keys.index(current) if current in keys else 0)
    group.add(provider)

    url = Adw.EntryRow(title="Server URL", text=config.get("assistant_url") or "", show_apply_button=True)
    group.add(url)
    model = Adw.EntryRow(title="Model", text=config.get("assistant_model") or "", show_apply_button=True)
    group.add(model)
    key = Adw.PasswordEntryRow(title="API key", show_apply_button=True)
    group.add(key)

    limit = Adw.SpinRow.new_with_range(*LIMIT_RANGE)
    limit.set_title("Requests per day")
    limit.set_subtitle("All features together; the status bar counts them")
    limit.set_value(assistant.limit)
    group.add(advanced_row(limit))

    where = Adw.ActionRow(title="Where the mail text goes")
    where_icon = Gtk.Image()
    where.add_prefix(where_icon)
    group.add(where)

    connection = Adw.ActionRow(title="Connection", subtitle="Not tested yet")
    test = Gtk.Button(label="Test", valign=Gtk.Align.CENTER)
    connection.add_suffix(test)
    group.add(connection)

    today = Adw.PreferencesGroup(title="Today")
    usage = Adw.ActionRow(title="Requests", subtitle=assistant.describe())
    today.add(usage)
    page.add(today)

    # -- keep the rows honest about the chosen provider

    def spec() -> llm.Spec:
        return llm.PROVIDERS[keys[provider.get_selected()]]

    def refresh() -> None:
        s, u, _m = llm.settings(config)
        url.set_tooltip_text(f"Blank: {s.default_url}")
        model.set_tooltip_text(f"Blank: {s.default_model}")
        key.set_visible(s.needs_key)
        stored = bool(llm.load_key(s.key)) if s.needs_key else False
        key.set_title("API key (stored; type to replace)" if stored else "API key (kept in the keyring)")
        if llm.is_local(u):
            where.set_subtitle(f"Stays on this machine: {u} is local")
            where_icon.set_from_icon_name("fm-shield-symbolic")
        else:
            where.set_subtitle(f"Leaves this machine for {host_of(u)}")
            where_icon.set_from_icon_name("dialog-warning-symbolic")
        usage.set_subtitle(assistant.describe())
        assistant.reset()

    def on_provider(_r, _p):
        config.set("assistant_provider", spec().key)
        refresh()

    enable.connect("notify::active", lambda r, _p: (config.set("assistant_enabled", r.get_active()), refresh()))
    provider.connect("notify::selected", on_provider)
    url.connect("apply", lambda r: (config.set("assistant_url", r.get_text().strip()), refresh()))
    model.connect("apply", lambda r: (config.set("assistant_model", r.get_text().strip()), refresh()))
    limit.connect("notify::value", lambda r, _p: (config.set("assistant_daily_limit", int(r.get_value())), refresh()))

    def on_key(row):
        text = row.get_text().strip()
        if text:
            ok = llm.store_key(spec().key, text)
            dialog.add_toast(Adw.Toast(title="Key stored in the keyring" if ok else "The keyring refused the key"))
        else:
            llm.clear_key(spec().key)
            dialog.add_toast(Adw.Toast(title="Key removed from the keyring"))
        row.set_text("")
        refresh()

    key.connect("apply", on_key)

    # -- the Test button: reach the server on a thread, report on the row

    def on_test(_b):
        test.set_sensitive(False)
        connection.set_subtitle("Testing…")
        typed = key.get_text().strip() or None

        def run():
            try:
                verdict = llm.build(config, key=typed).check()
                ok = True
            except llm.LLMError as e:
                verdict, ok = str(e), False
            GLib.idle_add(done, verdict, ok)

        def done(verdict: str, ok: bool) -> bool:
            connection.set_subtitle(verdict)
            connection.remove_css_class("error")
            if not ok:
                connection.add_css_class("error")
            test.set_sensitive(True)
            return False

        threading.Thread(target=run, name="assistant-test", daemon=True).start()

    test.connect("clicked", on_test)
    refresh()
    return page
