"""Identities & aliases: every address you can send from, with editable name/signature."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from .a11y import watch as _a11y_watch
from .widgets import open_uri, toast

ALIASES_URL = "https://app.fastmail.com/settings/addresses"


class IdentitiesDialog(Adw.Dialog):
    def __init__(self, engine, db, config=None):
        super().__init__(title="Identities & Aliases", content_width=620, content_height=680)
        self.engine = engine
        self.db = db
        self.config = config
        self._signal = engine.connect("identities-changed", lambda *_: self.reload())
        self.connect("closed", lambda *_: engine.disconnect(self._signal))

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        refresh.connect("clicked", lambda *_: engine.refresh_identities())
        header.pack_end(refresh)
        view.add_top_bar(header)
        self.search = Gtk.SearchEntry(placeholder_text="Filter by name or address")
        self.search.set_margin_start(12)
        self.search.set_margin_end(12)
        self.search.set_margin_bottom(6)
        self.search.connect("search-changed", lambda *_: self._filter())
        view.add_top_bar(self.search)
        self.page = Adw.PreferencesPage()
        view.set_content(self.page)
        self.toast_overlay = Adw.ToastOverlay(child=view)
        self.set_child(self.toast_overlay)
        _a11y_watch(self)   # icon-only buttons get their tooltip as accessible name (#123)
        self.groups: list[Adw.PreferencesGroup] = []
        self.rows: list[tuple[Adw.ExpanderRow, str]] = []
        self.reload()
        self.connect("map", lambda *_: self.search.grab_focus())

    def _filter(self) -> None:
        text = self.search.get_text().strip().lower()
        shown = 0
        for row, hay in self.rows:
            visible = not text or text in hay
            row.set_visible(visible)
            shown += visible
        if self.groups:
            total = len(self.rows)
            self.groups[0].set_title("Send-as addresses" if not text else f"Send-as addresses ({shown} of {total})")

    def reload(self) -> None:
        for g in self.groups:
            self.page.remove(g)
        self.groups.clear()
        favs = len(self.config.favorite_identities()) if self.config else 0
        group = Adw.PreferencesGroup(title="Send-as addresses",
                                     description="Aliases and identities configured in your Fastmail account. "
                                                 "Wildcard entries (*@domain) let you send from any address at "
                                                 "that domain. Star the ones you use: the compose window then "
                                                 f"lists only those ({favs} starred).")
        identities = sorted(self.db.get_identities(),
                            key=lambda i: ((i.get("email") or "").startswith("*@"), (i.get("email") or "").lower()))
        self.rows = []
        for ident in identities:
            row = self._row(ident)
            group.add(row)
            self.rows.append((row, f"{ident.get('name') or ''} {ident.get('email') or ''}".lower()))
        self.page.add(group)
        self.groups.append(group)
        self._filter()

        info = Adw.PreferencesGroup(title="Creating aliases")
        row = Adw.ActionRow(title="Manage aliases in Fastmail settings",
                            subtitle="Fastmail's public JMAP API has no method for creating regular aliases yet; "
                                     "use Masked Email for on-the-fly addresses.")
        button = Gtk.Button(label="Open settings", valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda *_: open_uri(ALIASES_URL, self.get_root()))
        row.add_suffix(button)
        info.add(row)
        self.page.add(info)
        self.groups.append(info)

    def _row(self, ident: dict) -> Adw.ExpanderRow:
        email = ident.get("email") or ""
        row = Adw.ExpanderRow(title=GLib.markup_escape_text(ident.get("name") or email),
                              subtitle=GLib.markup_escape_text(email))
        if email.startswith("*@"):
            tag = Gtk.Label(label="wildcard", valign=Gtk.Align.CENTER)  # centred, or it fills the row's height
            tag.add_css_class("chip")
            row.add_suffix(tag)
        if self.config is not None:
            fav = ident["id"] in self.config.favorite_identities()
            star = Gtk.ToggleButton(active=fav, valign=Gtk.Align.CENTER,
                                    icon_name="fm-star-symbolic" if fav else "fm-star-outline-symbolic",
                                    tooltip_text="Show in the compose window")
            star.add_css_class("flat")

            def on_star(button, ident_id=ident["id"]):
                on = button.get_active()
                button.set_icon_name("fm-star-symbolic" if on else "fm-star-outline-symbolic")
                self.config.set_favorite_identity(ident_id, on)

            star.connect("toggled", on_star)
            row.add_suffix(star)
        name = Adw.EntryRow(title="Sender name", text=ident.get("name") or "")
        reply_to = Adw.EntryRow(title="Reply-To (optional)",
                                text=", ".join(a.get("email", "") for a in ident.get("replyTo") or []))
        bcc = Adw.EntryRow(title="Always Bcc (optional)",
                           text=", ".join(a.get("email", "") for a in ident.get("bcc") or []))
        sig_row = Adw.ActionRow(title="Text signature")
        sig = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6, bottom_margin=6, left_margin=6,
                           right_margin=6, accepts_tab=False)
        sig.get_buffer().set_text(ident.get("textSignature") or "")
        sig.set_size_request(-1, 80)
        sig_frame = Gtk.Frame(child=sig, margin_top=4, margin_bottom=8)
        sig_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sig_box.append(sig_row)
        sig_box.append(sig_frame)
        save = Gtk.Button(label="Save", halign=Gtk.Align.END, margin_bottom=8, margin_end=8)
        save.add_css_class("suggested-action")

        def on_save(*_):
            buf = sig.get_buffer()
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            patch = {
                "name": name.get_text().strip(),
                "textSignature": text,
                "replyTo": [{"name": None, "email": a.strip()} for a in reply_to.get_text().split(",") if a.strip()] or None,
                "bcc": [{"name": None, "email": a.strip()} for a in bcc.get_text().split(",") if a.strip()] or None,
            }
            self.engine.identity_update(ident["id"], patch, on_done=lambda: toast(self, "Identity saved"),
                                        on_error=lambda m: toast(self, f"Could not save: {m}"))

        save.connect("clicked", on_save)
        for w in (name, reply_to, bcc):
            row.add_row(w)
        row.add_row(sig_box)
        row.add_row(save)
        return row
