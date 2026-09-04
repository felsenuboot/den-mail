"""Masked Email manager (Fastmail extension)."""

from __future__ import annotations

from gi.repository import Adw, Gio, GLib, Gtk

from ..models.thread import format_date
from .a11y import watch as _a11y_watch
from .widgets import confirm, copy_text, open_uri, toast

STATE_LABEL = {"enabled": "Active", "disabled": "Blocked (to Trash)", "deleted": "Deleted (bounces)",
               "pending": "Pending"}


class MaskedEmailDialog(Adw.Dialog):
    def __init__(self, engine, db):
        super().__init__(title="Masked Email", content_width=620, content_height=680)
        self.engine = engine
        self.db = db
        self.show_deleted = False
        self._signal = engine.connect("masked-changed", lambda *_: self.reload())
        self.connect("closed", lambda *_: engine.disconnect(self._signal))

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        new = Gtk.Button()
        new.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="New"))
        new.add_css_class("suggested-action")
        new.connect("clicked", lambda *_: self.create())
        header.pack_start(new)
        self.deleted_toggle = Gtk.ToggleButton(icon_name="user-trash-symbolic", tooltip_text="Show deleted addresses")
        self.deleted_toggle.connect("toggled", self._on_toggle_deleted)
        header.pack_end(self.deleted_toggle)
        view.add_top_bar(header)
        self.search = Gtk.SearchEntry(placeholder_text="Search addresses, sites, descriptions")
        self.search.set_margin_start(12)
        self.search.set_margin_end(12)
        self.search.set_margin_bottom(6)
        self.search.connect("search-changed", lambda *_: self.listbox.invalidate_filter())
        view.add_top_bar(self.search)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_filter_func(self._filter)
        self.listbox.set_margin_start(12)
        self.listbox.set_margin_end(12)
        self.listbox.set_margin_bottom(12)
        self.empty = Adw.StatusPage(icon_name="fm-important-symbolic", title="No masked addresses",
                                    description="Create one to hide your real address from a website.")
        self.empty.add_css_class("compact")
        self.stack = Gtk.Stack()
        self.stack.add_named(Gtk.ScrolledWindow(child=self.listbox, hscrollbar_policy=Gtk.PolicyType.NEVER), "list")
        self.stack.add_named(self.empty, "empty")
        view.set_content(self.stack)
        self.toast_overlay = Adw.ToastOverlay(child=view)
        self.set_child(self.toast_overlay)
        _a11y_watch(self)   # icon-only buttons get their tooltip as accessible name (#123)
        self.reload()
        engine.refresh_masked()

    def _on_toggle_deleted(self, button: Gtk.ToggleButton) -> None:
        self.show_deleted = button.get_active()
        self.reload()

    def _filter(self, row: Gtk.ListBoxRow) -> bool:
        text = self.search.get_text().strip().lower()
        if not text:
            return True
        m = row.item
        hay = " ".join([m.get("email") or "", m.get("description") or "", m.get("forDomain") or ""]).lower()
        return text in hay

    def reload(self) -> None:
        while child := self.listbox.get_first_child():
            self.listbox.remove(child)
        items = [m for m in self.db.get_masked_emails() if self.show_deleted or m.get("state") != "deleted"]
        for m in items:
            self.listbox.append(self._row(m))
        self.stack.set_visible_child_name("list" if items else "empty")

    def _row(self, m: dict) -> Adw.ActionRow:
        """A boxed-list row: the address, what it is for, and at most two controls
        (the on/off switch and a menu). Activating the row copies the address."""
        state = m.get("state") or "enabled"
        site = (m.get("forDomain") or "").removeprefix("https://").removeprefix("http://").rstrip("/")
        subtitle_parts = [p for p in (m.get("description"), site) if p]
        if m.get("lastMessageAt"):
            subtitle_parts.append(f"last mail {format_date(m['lastMessageAt'])}")
        if state not in ("enabled", "disabled"):
            subtitle_parts.append(STATE_LABEL.get(state, state))
        row = Adw.ActionRow(title=GLib.markup_escape_text(m.get("email") or ""),
                            subtitle=GLib.markup_escape_text(" · ".join(subtitle_parts)), activatable=True,
                            tooltip_text="Copy the address")
        row.item = m
        row.connect("activated", lambda *_: (copy_text(self, m["email"]), toast(self, "Address copied")))
        icon = Gtk.Image(icon_name={"enabled": "object-select-symbolic", "disabled": "fm-blocked-symbolic",
                                    "deleted": "user-trash-symbolic"}.get(state, "fm-pending-symbolic"),
                         tooltip_text=STATE_LABEL.get(state, state))
        icon.add_css_class(f"state-{state}")
        row.add_prefix(icon)
        if state in ("enabled", "disabled"):
            switch = Gtk.Switch(active=state == "enabled", valign=Gtk.Align.CENTER,
                                tooltip_text="Active: mail to this address reaches your inbox")
            switch.connect("state-set", lambda _s, on: self._set_state(m, "enabled" if on else "disabled"))
            row.add_suffix(switch)
        menu = Gio.Menu()
        menu.append("Copy address", f"masked.copy::{m['id']}")
        menu.append("Edit…", f"masked.edit::{m['id']}")
        if m.get("forDomain", "").startswith("http"):
            menu.append("Open website", f"masked.open::{m['id']}")
        if state == "deleted":
            menu.append("Restore", f"masked.restore::{m['id']}")
        else:
            menu.append("Delete", f"masked.delete::{m['id']}")
        if not m.get("lastMessageAt"):
            menu.append("Delete permanently", f"masked.destroy::{m['id']}")
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, valign=Gtk.Align.CENTER, tooltip_text="More")
        more.add_css_class("flat")
        row.add_suffix(more)
        self._ensure_actions()
        return row

    def _ensure_actions(self) -> None:
        if getattr(self, "_actions", None):
            return
        group = Gio.SimpleActionGroup()
        for name, fn in (("copy", lambda m: (copy_text(self, m.get("email") or ""), toast(self, "Address copied"))),
                         ("edit", self.edit), ("open", self._open_site),
                         ("restore", lambda m: self._set_state(m, "enabled")),
                         ("delete", lambda m: self._set_state(m, "deleted")), ("destroy", self._destroy)):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            a.connect("activate", lambda _a, p, fn=fn: fn(self._by_id(p.get_string())))
            group.add_action(a)
        self.insert_action_group("masked", group)
        self._actions = group

    def _by_id(self, mid: str) -> dict:
        return next((m for m in self.db.get_masked_emails() if m["id"] == mid), {"id": mid})

    def _open_site(self, m: dict) -> None:
        open_uri(m.get("forDomain") or "", self.get_root())

    def _set_state(self, m: dict, state: str) -> bool:
        self.engine.masked_set(update={m["id"]: {"state": state}},
                               on_error=lambda msg: toast(self, f"Change failed: {msg}"))
        return False

    def _destroy(self, m: dict) -> None:
        confirm(self, "Delete permanently?", f"{m.get('email')} will be removed for good.", "Delete", True,
                lambda: self.engine.masked_set(destroy=[m["id"]],
                                               on_error=lambda msg: toast(self, f"Delete failed: {msg}")))

    # ------------------------------------------------------- create / edit

    def create(self) -> None:
        dlg = Adw.AlertDialog(heading="New masked address",
                              body="Fastmail generates a random address that forwards to your inbox.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        group = Adw.PreferencesGroup()
        desc = Adw.EntryRow(title="Description")
        domain = Adw.EntryRow(title="Website (https://example.com)")
        prefix = Adw.EntryRow(title="Prefix (optional, a-z 0-9 _)")
        for r in (desc, domain, prefix):
            group.add(r)
        box.append(group)
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("create", "Create")
        dlg.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("create")

        def on_response(_d, response):
            if response != "create":
                return
            obj = {"state": "enabled", "description": desc.get_text().strip(),
                   "forDomain": domain.get_text().strip()}
            if prefix.get_text().strip():
                obj["emailPrefix"] = prefix.get_text().strip().lower()
            self.engine.masked_set(create=obj, on_done=self._created,
                                   on_error=lambda msg: toast(self, f"Could not create: {msg}"))

        dlg.connect("response", on_response)
        dlg.present(self)

    def _created(self, created: dict) -> None:
        email = created.get("email") or "(unknown)"
        copy_text(self, email)
        dlg = Adw.AlertDialog(heading="Masked address created", body=f"{email}\n\nCopied to the clipboard.")
        dlg.add_response("ok", "Done")
        dlg.present(self)

    def edit(self, m: dict) -> None:
        dlg = Adw.AlertDialog(heading=m.get("email") or "Edit")
        group = Adw.PreferencesGroup()
        desc = Adw.EntryRow(title="Description", text=m.get("description") or "")
        domain = Adw.EntryRow(title="Website", text=m.get("forDomain") or "")
        group.add(desc)
        group.add(domain)
        dlg.set_extra_child(group)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("save", "Save")
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("save")

        def on_response(_d, response):
            if response == "save":
                self.engine.masked_set(update={m["id"]: {"description": desc.get_text().strip(),
                                                         "forDomain": domain.get_text().strip()}},
                                       on_error=lambda msg: toast(self, f"Save failed: {msg}"))

        dlg.connect("response", on_response)
        dlg.present(self)
