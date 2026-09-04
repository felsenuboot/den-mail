"""Compose window: new message, reply, reply-all, forward, or continue a draft."""

from __future__ import annotations

import mimetypes
from collections.abc import Callable

from gi.repository import Adw, Gio, GLib, Gtk

from ..html.compose import (
    email_body_text,
    format_address_list,
    forward_body,
    forward_subject,
    parse_address_list,
    reply_body,
    reply_subject,
    text_to_html,
)
from ..jmap.types import delivered_to
from ..models.identity import IdentityObject
from .a11y import watch as _a11y_watch
from .widgets import AddressCompletion, confirm, human_size, toast

AUTOSAVE_SECONDS = 30
SHOW_ALL = "Show all identities…"


def _find_descendant(widget: Gtk.Widget, cls: type) -> Gtk.Widget | None:
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, cls):
            return child
        found = _find_descendant(child, cls)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


class ComposeWindow(Adw.Window):
    def __init__(self, parent: Gtk.Window, engine, db, identities: list[dict], mode: str = "new",
                 source: dict | None = None, mailto: dict | None = None,
                 on_closed: Callable[[ComposeWindow], None] | None = None,
                 preferred_identity_id: str | None = None, default_identity_email: str | None = None,
                 config=None):
        super().__init__(application=parent.get_application(), default_width=820, default_height=680,
                         title="New Message")
        self.parent_window = parent
        if hasattr(parent, "track_activity"):
            parent.track_activity(self)   # typing here keeps the idle lock away (#55)
        self.preferred_identity_id = preferred_identity_id
        self.engine = engine
        self.db = db
        self.config = config
        primary = (default_identity_email or "").lower()
        # Favourites (plus the primary address) keep the From list short; "Show all…" expands it, with
        # the favourites still on top so they stay easy to find.
        favs = set(config.favorite_identities()) if config else set()
        self.all_identities = sorted(
            [IdentityObject(i) for i in identities],
            key=lambda i: (i.email.lower() != primary, i.id not in favs, i.is_wildcard, i.email.lower()),
        ) or [IdentityObject({"id": "", "email": ""})]
        self.favorite_displays = {i.display for i in self.all_identities if i.id in favs}
        shortlist = [i for i in self.all_identities if i.id in favs or i.email.lower() == primary]
        self.identities = shortlist if favs and shortlist else list(self.all_identities)
        self.showing_all = len(self.identities) == len(self.all_identities)
        self._current_identity: IdentityObject | None = None  # last real (non-sentinel) From choice
        self.mode = mode
        self.source = source
        self.on_closed = on_closed
        self.draft_id: str | None = source["id"] if mode == "draft" and source else None
        self.attachments: list[dict] = []  # {blobId, type, name, size}
        self.dirty = False
        self._sending = False
        self._autosave = GLib.timeout_add_seconds(AUTOSAVE_SECONDS, self._autosave_tick)

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title="New Message")
        header.set_title_widget(self.title_widget)
        self.send_button = Gtk.Button()
        self.send_button.set_child(Adw.ButtonContent(icon_name="fm-send-symbolic", label="Send"))
        self.send_button.add_css_class("suggested-action")
        self.send_button.set_tooltip_text("Send (Ctrl+Return)")
        self.send_button.connect("clicked", lambda *_: self.send())
        header.pack_end(self.send_button)
        # Send later (#6): the presets, and a picker for any other time.
        self.later_button = Gtk.MenuButton(icon_name="fm-scheduled-symbolic", tooltip_text="Send later",
                                           menu_model=self._build_later_menu())
        header.pack_end(self.later_button)
        attach = Gtk.Button(icon_name="fm-attachment-symbolic", tooltip_text="Attach files")
        attach.connect("clicked", lambda *_: self.pick_attachments())
        header.pack_end(attach)
        menu = Gio.Menu()
        menu.append("Save draft", "compose.save")
        menu.append("Discard", "compose.discard")
        header.pack_end(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, tooltip_text="More"))
        view.add_top_bar(header)

        # --- header fields as Adwaita rows
        fields = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        fields.add_css_class("boxed-list")
        fields.set_margin_start(12)
        fields.set_margin_end(12)
        fields.set_margin_top(6)
        fields.set_margin_bottom(6)

        self.identity_list = Gtk.StringList.new(self._identity_strings())
        # use-subtitle shows the selected identity under "From" (instead of an ellipsised label on the
        # right); use-markup must be off because addresses contain "<…>".
        self.from_row = Adw.ComboRow(title="From", model=self.identity_list, use_subtitle=True, use_markup=False,
                                     enable_search=len(self.identities) > 6,
                                     expression=Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        self.from_row.set_list_factory(self._identity_factory())
        self.from_row.connect("notify::selected", lambda *_: self._on_identity_changed())
        fields.append(self.from_row)
        self.wildcard_row = Adw.EntryRow(title="Address at this domain (local part)")
        self.wildcard_row.set_visible(False)
        self.wildcard_row.connect("changed", self._mark_dirty)
        fields.append(self.wildcard_row)

        search = db.search_addresses
        self.to = Adw.EntryRow(title="To")
        self.to.connect("changed", self._mark_dirty)
        self._to_completion = AddressCompletion(self.to, search)
        cc_toggle = Gtk.Button(label="Cc", valign=Gtk.Align.CENTER)
        cc_toggle.add_css_class("flat")
        cc_toggle.connect("clicked", lambda *_: self._show_row(self.cc))
        bcc_toggle = Gtk.Button(label="Bcc", valign=Gtk.Align.CENTER)
        bcc_toggle.add_css_class("flat")
        bcc_toggle.connect("clicked", lambda *_: self._show_row(self.bcc))
        self.to.add_suffix(cc_toggle)
        self.to.add_suffix(bcc_toggle)
        fields.append(self.to)
        self.cc = Adw.EntryRow(title="Cc")
        self.cc.connect("changed", self._mark_dirty)
        self._cc_completion = AddressCompletion(self.cc, search)
        self.cc.set_visible(False)
        fields.append(self.cc)
        self.bcc = Adw.EntryRow(title="Bcc")
        self.bcc.connect("changed", self._mark_dirty)
        self._bcc_completion = AddressCompletion(self.bcc, search)
        self.bcc.set_visible(False)
        fields.append(self.bcc)
        self.subject = Adw.EntryRow(title="Subject")
        self.subject.connect("changed", self._mark_dirty)
        self.subject.connect("changed", lambda e: self.title_widget.set_title(e.get_text() or "New Message"))
        fields.append(self.subject)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(fields)
        self.textview = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, vexpand=True, top_margin=12,
                                     bottom_margin=24, left_margin=18, right_margin=18)
        self.textview.add_css_class("compose-body")
        self.buffer = self.textview.get_buffer()
        self.buffer.connect("changed", self._mark_dirty)
        scrolled = Gtk.ScrolledWindow(child=self.textview, vexpand=True)
        scrolled.add_css_class("compose-scroller")
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add_css_class("card")
        frame.add_css_class("compose-card")
        frame.set_margin_start(12)
        frame.set_margin_end(12)
        frame.set_margin_bottom(12)
        frame.set_vexpand(True)
        frame.append(scrolled)
        self.attach_box = Adw.WrapBox(child_spacing=6, line_spacing=6)
        self.attach_box.set_margin_start(12)
        self.attach_box.set_margin_end(12)
        self.attach_box.set_margin_top(8)
        self.attach_box.set_margin_bottom(10)
        self.attach_box.set_visible(False)
        frame.append(self.attach_box)
        content.append(frame)
        view.set_content(content)
        self.toast_overlay = Adw.ToastOverlay(child=view)
        self.set_content(self.toast_overlay)
        _a11y_watch(self)   # icon-only buttons get their tooltip as accessible name (#123)

        self._install_actions()
        self.connect("close-request", self._on_close_request)
        self._draft_handler = self.engine.connect("draft-created", self._on_draft_created) if self.engine else 0
        # Suggestion navigation is handled at the window level, in the capture phase, so no
        # widget between the window and the entry can swallow Up/Down/Return first.
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_window_key)
        self.add_controller(keys)
        self._prefill(mailto)
        self._on_identity_changed()
        self.dirty = False

    def _on_window_key(self, _ctrl, keyval, _code, _state) -> bool:
        for completion in (self._to_completion, self._cc_completion, self._bcc_completion):
            if completion.visible:
                return completion.handle_key(keyval)
        return False

    # --------------------------------------------------------------- setup

    def _identity_factory(self) -> Gtk.SignalListItemFactory:
        """Two-line rows (name / address) for the From popup, never ellipsised."""
        factory = Gtk.SignalListItemFactory()

        def setup(_f, item):
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
            box.name_label = Gtk.Label(xalign=0)
            box.email_label = Gtk.Label(xalign=0)
            box.email_label.add_css_class("dim-label")
            box.email_label.add_css_class("caption")
            text.append(box.name_label)
            text.append(box.email_label)
            box.star = Gtk.Image(icon_name="fm-star-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Favourite")
            box.star.add_css_class("accent")
            box.append(text)
            box.append(box.star)
            item.set_child(box)

        def bind(_f, item):
            display = item.get_item().get_string()
            name, _, email = display.rpartition(" <")
            box = item.get_child()
            box.name_label.set_label(name or display)
            box.email_label.set_label(email.rstrip(">") if name else "")
            box.email_label.set_visible(bool(name))
            box.star.set_visible(display in self.favorite_displays)

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        return factory

    @staticmethod
    def _focus_later(widget: Gtk.Widget) -> None:
        # grab_focus() returns True, which GLib would read as "call me again forever".
        GLib.idle_add(lambda: (widget.grab_focus(), False)[1])

    def _show_row(self, row: Adw.EntryRow) -> None:
        row.set_visible(True)
        row.grab_focus()

    def _install_actions(self) -> None:
        group = Gio.SimpleActionGroup()
        for name, cb in (("save", lambda: self.save_draft(close=False)), ("discard", self.discard),
                         ("send", self.send)):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_a, cb=cb: cb())
            group.add_action(a)
        self.insert_action_group("compose", group)
        ctrl = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
        ctrl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Control>Return"),
                                           Gtk.NamedAction.new("compose.send")))
        ctrl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Control>s"),
                                           Gtk.NamedAction.new("compose.save")))
        ctrl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Control>w"),
                                           Gtk.CallbackAction.new(lambda *_: (self.close(), True)[1])))
        self.add_controller(ctrl)
        # Ctrl+Q would otherwise reach the application's quit accelerator and skip the draft prompt
        first = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        first.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Control>q"),
                                            Gtk.CallbackAction.new(lambda *_: (self.close(), True)[1])))
        self.add_controller(first)

    def _identity_strings(self) -> list[str]:
        strings = [i.display for i in self.identities]
        if not self.showing_all:
            strings.append(SHOW_ALL)
        return strings

    def _rebuild_identity_list(self, select: IdentityObject | None) -> None:
        self._rebuilding = True
        try:
            self.identity_list.splice(0, self.identity_list.get_n_items(), self._identity_strings())
            self.from_row.set_enable_search(len(self.identities) > 6)
            if select is not None and select in self.identities:
                self.from_row.set_selected(self.identities.index(select))
                self._current_identity = select
                self._sync_wildcard_row(select)
        finally:
            self._rebuilding = False

    def _show_all_identities(self) -> None:
        current = self._current_identity or self.identities[0]  # the row itself points at the sentinel now
        self.identities = list(self.all_identities)
        self.showing_all = True
        self._rebuild_identity_list(current)

    def _reopen_from_list(self) -> bool:
        """Picking "Show all identities…" closed the row's list; open it again on the full list."""
        popover = _find_descendant(self.from_row, Gtk.Popover)
        if popover is not None:
            popover.popup()
        return False

    def _ensure_identity_visible(self, ident: IdentityObject) -> None:
        if ident not in self.identities:
            self.identities = [i for i in self.all_identities if i in self.identities or i is ident]
            self._rebuild_identity_list(ident)

    def _identity(self) -> IdentityObject:
        idx = self.from_row.get_selected()
        if idx >= len(self.identities):  # the "Show all…" sentinel or an invalid position
            return self._current_identity or self.identities[0]
        return self.identities[idx]

    def _sync_wildcard_row(self, ident: IdentityObject) -> None:
        self.wildcard_row.set_visible(ident.is_wildcard)
        self.wildcard_row.set_title(f"Address at @{ident.domain} (local part)" if ident.is_wildcard else "")

    def _on_identity_changed(self) -> None:
        if getattr(self, "_rebuilding", False):
            return
        if not self.showing_all and self.from_row.get_selected() == len(self.identities):
            self._show_all_identities()
            GLib.idle_add(self._reopen_from_list)
            return
        ident = self._identity()
        self._current_identity = ident
        self._sync_wildcard_row(ident)
        self._mark_dirty()

    def _select_identity_for(self, addresses: list[str]) -> None:
        for addr in addresses:
            # exact identities before wildcards, whatever the (favourites-first) display order
            for ident in sorted(self.all_identities, key=lambda i: i.is_wildcard):
                if ident.matches(addr):
                    self._ensure_identity_visible(ident)
                    self.from_row.set_selected(self.identities.index(ident))
                    if ident.is_wildcard:
                        self.wildcard_row.set_text(addr.split("@", 1)[0])
                    return

    def _own_addresses(self) -> set[str]:
        return {i.email.lower() for i in self.all_identities if not i.is_wildcard}

    def _prefill(self, mailto: dict | None) -> None:
        src = self.source
        signature = self.identities[0].text_signature
        if self.mode in ("reply", "reply-all") and src:
            candidates = [delivered_to(src) or ""] + [
                a.get("email", "") for a in (src.get("to") or []) + (src.get("cc") or [])]
            self._select_identity_for([c for c in candidates if c])
            signature = self._identity().text_signature
            reply_to = src.get("replyTo") or src.get("from") or []
            own = self._own_addresses()
            to = [a for a in reply_to if a.get("email", "").lower() not in own] or reply_to
            self.to.set_text(format_address_list(to))
            if self.mode == "reply-all":
                seen = {a.get("email", "").lower() for a in to}
                extra = [a for a in (src.get("to") or []) if a.get("email", "").lower() not in own | seen]
                if extra:
                    self.to.set_text(format_address_list(to + extra))
                    seen |= {a.get("email", "").lower() for a in extra}
                cc = [a for a in (src.get("cc") or []) if a.get("email", "").lower() not in own | seen]
                if cc:
                    self.cc.set_visible(True)
                    self.cc.set_text(format_address_list(cc))
            self.subject.set_text(reply_subject(src.get("subject")))
            self.buffer.set_text(reply_body(src, signature))
            self.buffer.place_cursor(self.buffer.get_start_iter())
            self.title_widget.set_title(self.subject.get_text())
            self._focus_later(self.textview)
        elif self.mode == "forward" and src:
            self.subject.set_text(forward_subject(src.get("subject")))
            self.buffer.set_text(forward_body(src, signature))
            self.buffer.place_cursor(self.buffer.get_start_iter())
            for a in src.get("attachments") or []:
                if a.get("disposition") == "attachment" or not a.get("cid"):
                    self._add_attachment_chip({"blobId": a["blobId"], "type": a.get("type"), "name": a.get("name"),
                                               "size": a.get("size")})
            self.title_widget.set_title(self.subject.get_text())
            self._focus_later(self.to)
        elif self.mode == "draft" and src:
            self._select_identity_for([a.get("email", "") for a in src.get("from") or []])
            self.to.set_text(format_address_list(src.get("to")))
            if src.get("cc"):
                self.cc.set_visible(True)
                self.cc.set_text(format_address_list(src.get("cc")))
            if src.get("bcc"):
                self.bcc.set_visible(True)
                self.bcc.set_text(format_address_list(src.get("bcc")))
            self.subject.set_text(src.get("subject") or "")
            self.buffer.set_text(email_body_text(src))
            for a in src.get("attachments") or []:
                self._add_attachment_chip({"blobId": a["blobId"], "type": a.get("type"), "name": a.get("name"),
                                           "size": a.get("size")})
            self.title_widget.set_title(self.subject.get_text() or "Draft")
            self._focus_later(self.textview)
        else:
            if self.preferred_identity_id:
                for ident in self.all_identities:
                    if ident.id == self.preferred_identity_id:
                        self._ensure_identity_visible(ident)
                        self.from_row.set_selected(self.identities.index(ident))
                        signature = ident.text_signature
                        break
            if mailto:
                self.to.set_text(mailto.get("to", ""))
                if mailto.get("cc"):
                    self.cc.set_visible(True)
                    self.cc.set_text(mailto["cc"])
                self.subject.set_text(mailto.get("subject", ""))
                body = mailto.get("body", "")
            else:
                body = ""
            if signature:
                body = f"{body}\n\n-- \n{signature}" if body else f"\n\n-- \n{signature}"
            self.buffer.set_text(body)
            self.buffer.place_cursor(self.buffer.get_start_iter())
            self._focus_later(self.to)

    # ------------------------------------------------------------- state

    def _mark_dirty(self, *_) -> None:
        self.dirty = True

    def _has_content(self) -> bool:
        start, end = self.buffer.get_bounds()
        return bool(self.to.get_text().strip() or self.subject.get_text().strip()
                    or self.buffer.get_text(start, end, False).strip() or self.attachments)

    def _autosave_tick(self) -> bool:
        if self.dirty and self._has_content() and not self._sending:
            self.save_draft(close=False, quiet=True)
        return True

    # -------------------------------------------------------- attachments

    def pick_attachments(self) -> None:
        dialog = Gtk.FileDialog()

        def on_done(dlg, res):
            try:
                files = dlg.open_multiple_finish(res)
            except GLib.Error:
                return
            for i in range(files.get_n_items()):
                self._upload_file(files.get_item(i))

        dialog.open_multiple(self, None, on_done)

    def _upload_file(self, gfile: Gio.File) -> None:
        path = gfile.get_path()
        name = gfile.get_basename()
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            toast(self, f"Cannot read {name}: {e}")
            return
        if len(data) > self.engine.client.session.max_upload_size():
            toast(self, f"{name} is larger than the server allows")
            return
        chip = self._add_attachment_chip({"blobId": None, "type": ctype, "name": name, "size": len(data)})
        chip.spinner.set_visible(True)

        def done(res: dict) -> None:
            chip.att["blobId"] = res["blobId"]
            chip.spinner.set_visible(False)
            self._mark_dirty()

        def failed(msg: str) -> None:
            toast(self, f"Could not upload {name}: {msg}")
            self._remove_attachment(chip)

        self.engine.upload(data, ctype, done, failed)

    def _add_attachment_chip(self, att: dict) -> Gtk.Box:
        box = Gtk.Box(spacing=4)
        box.add_css_class("attachment-chip")
        box.att = att
        box.append(Gtk.Image(icon_name="fm-attachment-symbolic"))
        box.append(Gtk.Label(label=att.get("name") or "attachment", ellipsize=3, max_width_chars=30))
        size = Gtk.Label(label=human_size(att.get("size")))
        size.add_css_class("dim-label")
        size.add_css_class("caption")
        box.append(size)
        box.spinner = Adw.Spinner()
        box.spinner.set_visible(False)
        box.append(box.spinner)
        remove = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Remove attachment")
        remove.add_css_class("flat")
        remove.add_css_class("circular")
        remove.connect("clicked", lambda *_: self._remove_attachment(box))
        box.append(remove)
        self.attach_box.append(box)
        self.attach_box.set_visible(True)
        self.attachments.append(att)
        self._mark_dirty()
        return box

    def _remove_attachment(self, chip: Gtk.Box) -> None:
        self.attach_box.remove(chip)
        if chip.att in self.attachments:
            self.attachments.remove(chip.att)
        self.attach_box.set_visible(bool(self.attachments))
        self._mark_dirty()

    # ------------------------------------------------------------ build

    def _from_address(self) -> dict:
        ident = self._identity()
        if ident.is_wildcard:
            local = self.wildcard_row.get_text().strip() or "mail"
            return {"name": ident.name or None, "email": f"{local}@{ident.domain}"}
        return {"name": ident.name or None, "email": ident.email}

    def build_email(self) -> dict:
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, False)
        ident = self._identity()
        email: dict = {
            "from": [self._from_address()],
            "to": parse_address_list(self.to.get_text()),
            "subject": self.subject.get_text(),
            "textBody": [{"partId": "t", "type": "text/plain"}],
            "htmlBody": [{"partId": "h", "type": "text/html"}],
            "bodyValues": {"t": {"value": text}, "h": {"value": text_to_html(text)}},
        }
        cc = parse_address_list(self.cc.get_text())
        bcc = parse_address_list(self.bcc.get_text())
        if ident.data.get("bcc"):
            bcc += ident.data["bcc"]
        if cc:
            email["cc"] = cc
        if bcc:
            email["bcc"] = bcc
        if ident.data.get("replyTo"):
            email["replyTo"] = ident.data["replyTo"]
        if self.mode in ("reply", "reply-all") and self.source:
            mid = self.source.get("messageId") or []
            refs = (self.source.get("references") or []) + mid
            if mid:
                email["inReplyTo"] = mid
            if refs:
                email["references"] = refs[-20:]
        atts = [{"blobId": a["blobId"], "type": a.get("type") or "application/octet-stream",
                 "name": a.get("name"), "disposition": "attachment"} for a in self.attachments if a.get("blobId")]
        if atts:
            email["attachments"] = atts
        return email

    # ----------------------------------------------------------- actions

    def _build_later_menu(self) -> Gio.Menu:
        from .. import schedule

        group = Gio.SimpleActionGroup()
        later = Gio.SimpleAction.new("send-later", GLib.VariantType.new("s"))
        later.connect("activate", lambda _a, p: self.send(send_at=p.get_string()))
        group.add_action(later)
        pick = Gio.SimpleAction.new("pick-time", None)
        pick.connect("activate", lambda *_: self._pick_time())
        group.add_action(pick)
        self.insert_action_group("later", group)
        menu = Gio.Menu()
        section = Gio.Menu()
        for label, when in schedule.presets():
            item = Gio.MenuItem.new(f"{label} ({when.strftime('%a %H:%M')})", None)
            item.set_action_and_target_value("later.send-later", GLib.Variant("s", schedule.to_utc(when)))
            section.append_item(item)
        menu.append_section("Send later", section)
        section = Gio.Menu()
        section.append("Pick a time…", "later.pick-time")
        menu.append_section(None, section)
        return menu

    def _pick_time(self) -> None:
        from datetime import datetime

        from .. import schedule

        calendar = Gtk.Calendar()
        hour = Gtk.SpinButton.new_with_range(0, 23, 1)
        minute = Gtk.SpinButton.new_with_range(0, 59, 5)
        now = datetime.now().astimezone()
        hour.set_value(min(23, now.hour + 1))
        minute.set_value(0)
        row = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        row.append(Gtk.Label(label="at"))
        row.append(hour)
        row.append(Gtk.Label(label=":"))
        row.append(minute)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(calendar)
        box.append(row)
        dlg = Adw.AlertDialog(heading="Send later", body="The message waits in Scheduled until then; it can be cancelled there.")
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Schedule")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("ok")
        dlg.set_close_response("cancel")

        def on_response(_d, response):
            if response != "ok":
                return
            date = calendar.get_date()
            when = datetime(date.get_year(), date.get_month(), date.get_day_of_month(),
                            int(hour.get_value()), int(minute.get_value())).astimezone()
            if when <= datetime.now().astimezone():
                toast(self, "That time has passed")
                return
            self.send(send_at=schedule.to_utc(when))

        dlg.connect("response", on_response)
        dlg.present(self)

    def send(self, send_at: str | None = None) -> None:
        if self._sending:
            return
        email = self.build_email()
        if not (email["to"] or email.get("cc") or email.get("bcc")):
            toast(self, "Add at least one recipient")
            self.to.grab_focus()
            return
        if any(a.get("blobId") is None for a in self.attachments):
            toast(self, "Attachments are still uploading")
            return
        if not email["subject"].strip():
            confirm(self, "Send without a subject?", "The message has no subject.", "Send", False,
                    lambda: self._do_send(send_at))
            return
        self._do_send(send_at)

    def _do_send(self, send_at: str | None = None) -> None:
        from .. import schedule

        self._sending = True
        self.send_button.set_sensitive(False)
        self.later_button.set_sensitive(False)
        email = self.build_email()
        ident = self._identity()
        reply_id = self.source["id"] if self.mode in ("reply", "reply-all") and self.source else None
        fwd_id = self.source["id"] if self.mode == "forward" and self.source else None

        def done(new_id: str) -> None:
            self.dirty = False
            if not new_id:   # queued while offline (#8)
                toast(self.parent_window, "Offline: the message goes out when the connection is back", 6)
            else:
                toast(self.parent_window, f"Scheduled for {schedule.describe(send_at)}" if send_at else "Message sent")
            self.destroy()

        def failed(message: str) -> None:
            self._sending = False
            self.send_button.set_sensitive(True)
            self.later_button.set_sensitive(True)
            dlg = Adw.AlertDialog(heading="Could not send", body=message)
            dlg.add_response("ok", "OK")
            dlg.present(self)

        seconds = int(self.config.get("undo_send_seconds", 10)) if self.config else 0
        schedule_send = getattr(self.parent_window, "schedule_send", None)
        if send_at or not self.engine.online:
            # A scheduled message needs no undo countdown (it can be cancelled until it goes); offline,
            # the draft could not be parked on the server anyway, so the message is queued as it is.
            self.engine.send_email(email, ident.id, self.draft_id, done, failed, in_reply_to_id=reply_id,
                                   forwarded_id=fwd_id, send_at=send_at)
            return
        if seconds > 0 and schedule_send is not None:
            # Undo send (#7): park the message as a draft and let the main window count down.
            def saved(draft_id: str) -> None:
                self.dirty = False
                schedule_send(email, ident.id, draft_id, reply_id, fwd_id, seconds)
                self.destroy()

            self.engine.save_draft(email, self.draft_id, saved, failed)
            return
        self.engine.send_email(email, ident.id, self.draft_id, done, failed, in_reply_to_id=reply_id,
                               forwarded_id=fwd_id)

    def save_draft(self, close: bool = False, quiet: bool = False) -> None:
        email = self.build_email()

        def done(new_id: str) -> None:
            self.draft_id = new_id
            self.dirty = False
            if not quiet:
                toast(self.parent_window if close else self,
                      "Offline: the draft is kept here and saved with the next sync" if self.engine.is_local(new_id)
                      else "Draft saved")
            if close:
                self.destroy()

        def failed(message: str) -> None:
            toast(self, f"Could not save the draft: {message}")

        self.engine.save_draft(email, self.draft_id, done, failed)

    def _on_draft_created(self, _engine, local_id: str, new_id: str) -> None:
        """A draft saved offline reached the server (#61): later saves replace the server's copy."""
        if self.draft_id == local_id:
            self.draft_id = new_id

    def discard(self) -> None:
        def do_discard() -> None:
            self.dirty = False
            if self.draft_id:
                self.engine.discard_draft(self.draft_id)
            self._destroy_after_dialog()

        if self._has_content():
            confirm(self, "Discard this message?", "Unsaved changes will be lost.", "Discard", True, do_discard)
        else:
            do_discard()

    def _on_close_request(self, *_) -> bool:
        if self._sending or not self.dirty or not self._has_content():
            return False
        dlg = Adw.AlertDialog(heading="Save draft?", body="The message has unsaved changes.")
        dlg.add_response("discard", "Discard")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("save", "Save draft")
        dlg.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("save")
        dlg.set_close_response("cancel")

        def on_response(_d, response):
            if response == "save":
                self.save_draft(close=True)
            elif response == "discard":
                self.dirty = False
                self._destroy_after_dialog()

        dlg.connect("response", on_response)
        dlg.present(self)
        return True

    def _destroy_after_dialog(self) -> None:
        """Destroy the window once the dialog that asked has closed.

        Destroying it from inside the dialog's response handler aborted the app on
        GTK 4.22 (AT-SPI updated the dialog's toplevel after the window was gone).
        """
        GLib.idle_add(lambda: (self.destroy(), False)[1])

    def destroy(self) -> None:  # type: ignore[override]
        if self._autosave:
            GLib.source_remove(self._autosave)
            self._autosave = 0
        if self._draft_handler:
            self.engine.disconnect(self._draft_handler)
            self._draft_handler = 0
        if self.on_closed:
            self.on_closed(self)
        Adw.Window.destroy(self)
