"""Compose window: new message, reply, reply-all, forward, or continue a draft."""

from __future__ import annotations

import mimetypes
from collections.abc import Callable

from gi.repository import Adw, Gio, GLib, Gtk

from ..html.compose import (
    forward_body,
    forward_subject,
    format_address_list,
    parse_address_list,
    reply_body,
    reply_subject,
    text_to_html,
)
from ..html.compose import email_body_text
from ..models.identity import IdentityObject
from .widgets import AddressEntry, confirm, human_size, toast

AUTOSAVE_SECONDS = 30


class ComposeWindow(Adw.Window):
    def __init__(self, parent: Gtk.Window, engine, db, identities: list[dict], mode: str = "new",
                 source: dict | None = None, mailto: dict | None = None,
                 on_closed: Callable[["ComposeWindow"], None] | None = None,
                 preferred_identity_id: str | None = None, default_identity_email: str | None = None):
        super().__init__(transient_for=None, default_width=760, default_height=640, title="New Message")
        self.parent_window = parent
        self.preferred_identity_id = preferred_identity_id
        self.engine = engine
        self.db = db
        primary = (default_identity_email or "").lower()
        self.identities = sorted(
            [IdentityObject(i) for i in identities],
            key=lambda i: (i.email.lower() != primary, i.is_wildcard, i.email.lower()),
        ) or [IdentityObject({"id": "", "email": ""})]
        self.mode = mode
        self.source = source
        self.on_closed = on_closed
        self.draft_id: str | None = source["id"] if mode == "draft" and source else None
        self.attachments: list[dict] = []  # {blobId, type, name, size, widget}
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
        self.send_button.connect("clicked", lambda *_: self.send())
        header.pack_end(self.send_button)
        attach = Gtk.Button(icon_name="fm-attachment-symbolic", tooltip_text="Attach files")
        attach.connect("clicked", lambda *_: self.pick_attachments())
        header.pack_end(attach)
        menu = Gio.Menu()
        menu.append("Save draft", "compose.save")
        menu.append("Discard", "compose.discard")
        header.pack_end(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu))
        view.add_top_bar(header)

        grid = Gtk.Grid(row_spacing=2, column_spacing=8)
        grid.set_margin_start(12)
        grid.set_margin_end(12)
        grid.set_margin_top(6)
        row = 0

        # From
        grid.attach(self._field_label("From"), 0, row, 1, 1)
        from_box = Gtk.Box(spacing=6, hexpand=True)
        self.identity_list = Gtk.StringList.new([i.display for i in self.identities])
        self.from_dropdown = Gtk.DropDown(model=self.identity_list, hexpand=True)
        self.from_dropdown.add_css_class("flat")
        self.from_dropdown.connect("notify::selected", lambda *_: self._on_identity_changed())
        from_box.append(self.from_dropdown)
        self.wildcard_entry = Gtk.Entry(placeholder_text="local-part", width_chars=16)
        self.wildcard_entry.set_visible(False)
        self.wildcard_entry.connect("changed", self._mark_dirty)
        from_box.append(self.wildcard_entry)
        self.wildcard_domain = Gtk.Label()
        self.wildcard_domain.set_visible(False)
        from_box.append(self.wildcard_domain)
        cc_toggle = Gtk.Button(label="Cc")
        cc_toggle.add_css_class("flat")
        cc_toggle.connect("clicked", lambda *_: self._show_row(self.cc_row, self.cc))
        bcc_toggle = Gtk.Button(label="Bcc")
        bcc_toggle.add_css_class("flat")
        bcc_toggle.connect("clicked", lambda *_: self._show_row(self.bcc_row, self.bcc))
        from_box.append(cc_toggle)
        from_box.append(bcc_toggle)
        grid.attach(from_box, 1, row, 1, 1)
        row += 1

        search = lambda prefix: db.search_addresses(prefix)  # noqa: E731
        grid.attach(self._field_label("To"), 0, row, 1, 1)
        self.to = AddressEntry(search, "Recipients")
        self.to.entry.connect("changed", self._mark_dirty)
        grid.attach(self.to, 1, row, 1, 1)
        row += 1
        self.cc_row = self._field_label("Cc")
        self.cc = AddressEntry(search, "")
        self.cc.entry.connect("changed", self._mark_dirty)
        grid.attach(self.cc_row, 0, row, 1, 1)
        grid.attach(self.cc, 1, row, 1, 1)
        row += 1
        self.bcc_row = self._field_label("Bcc")
        self.bcc = AddressEntry(search, "")
        self.bcc.entry.connect("changed", self._mark_dirty)
        grid.attach(self.bcc_row, 0, row, 1, 1)
        grid.attach(self.bcc, 1, row, 1, 1)
        row += 1
        for w in (self.cc_row, self.cc, self.bcc_row, self.bcc):
            w.set_visible(False)
        grid.attach(self._field_label("Subject"), 0, row, 1, 1)
        self.subject = Gtk.Entry(hexpand=True, placeholder_text="Subject")
        self.subject.add_css_class("flat")
        self.subject.connect("changed", self._mark_dirty)
        self.subject.connect("changed", lambda e: self.title_widget.set_title(e.get_text() or "New Message"))
        grid.attach(self.subject, 1, row, 1, 1)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(grid)
        content.append(Gtk.Separator())
        self.textview = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, vexpand=True, top_margin=10, bottom_margin=20,
                                     left_margin=14, right_margin=14)
        self.textview.add_css_class("compose-body")
        self.buffer = self.textview.get_buffer()
        self.buffer.connect("changed", self._mark_dirty)
        scrolled = Gtk.ScrolledWindow(child=self.textview, vexpand=True)
        content.append(scrolled)
        self.attach_box = Adw.WrapBox(child_spacing=6, line_spacing=6)
        self.attach_box.set_margin_start(12)
        self.attach_box.set_margin_end(12)
        self.attach_box.set_margin_top(6)
        self.attach_box.set_margin_bottom(8)
        self.attach_box.set_visible(False)
        content.append(self.attach_box)
        view.set_content(content)
        self.toast_overlay = Adw.ToastOverlay(child=view)
        self.set_content(self.toast_overlay)

        self._install_actions()
        self.connect("close-request", self._on_close_request)
        self._prefill(mailto)
        self._on_identity_changed()
        self.dirty = False

    # --------------------------------------------------------------- setup

    @staticmethod
    def _focus_later(widget: Gtk.Widget) -> None:
        # grab_focus() returns True, which GLib would read as "call me again forever".
        GLib.idle_add(lambda: (widget.grab_focus(), False)[1])

    @staticmethod
    def _field_label(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.add_css_class("compose-field-label")
        return lbl

    def _show_row(self, label: Gtk.Widget, entry: AddressEntry) -> None:
        label.set_visible(True)
        entry.set_visible(True)
        entry.grab_focus()

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
        ctrl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                           Gtk.CallbackAction.new(lambda *_: (self.close(), True)[1])))
        self.add_controller(ctrl)

    def _identity(self) -> IdentityObject:
        return self.identities[self.from_dropdown.get_selected()]

    def _on_identity_changed(self) -> None:
        ident = self._identity()
        self.wildcard_entry.set_visible(ident.is_wildcard)
        self.wildcard_domain.set_visible(ident.is_wildcard)
        self.wildcard_domain.set_label("@" + ident.domain)
        self._mark_dirty()

    def _select_identity_for(self, addresses: list[str]) -> None:
        for addr in addresses:
            for i, ident in enumerate(self.identities):
                if ident.matches(addr):
                    self.from_dropdown.set_selected(i)
                    if ident.is_wildcard:
                        self.wildcard_entry.set_text(addr.split("@", 1)[0])
                    return

    def _own_addresses(self) -> set[str]:
        return {i.email.lower() for i in self.identities if not i.is_wildcard}

    def _prefill(self, mailto: dict | None) -> None:
        src = self.source
        signature = self.identities[0].text_signature
        if self.mode in ("reply", "reply-all") and src:
            candidates = [src.get("header:Delivered-To:asText") or ""] + [
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
                    self._show_row(self.cc_row, self.cc)
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
                self._show_row(self.cc_row, self.cc)
                self.cc.set_text(format_address_list(src.get("cc")))
            if src.get("bcc"):
                self._show_row(self.bcc_row, self.bcc)
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
                for i, ident in enumerate(self.identities):
                    if ident.id == self.preferred_identity_id:
                        self.from_dropdown.set_selected(i)
                        signature = ident.text_signature
                        break
            if mailto:
                self.to.set_text(mailto.get("to", ""))
                if mailto.get("cc"):
                    self._show_row(self.cc_row, self.cc)
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
            data = open(path, "rb").read()
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
            toast(self, f"Upload of {name} failed: {msg}")
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
        remove = Gtk.Button(icon_name="window-close-symbolic")
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
            local = self.wildcard_entry.get_text().strip() or "mail"
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

    def send(self) -> None:
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
            confirm(self, "Send without a subject?", "The message has no subject.", "Send", False, self._do_send)
            return
        self._do_send()

    def _do_send(self) -> None:
        self._sending = True
        self.send_button.set_sensitive(False)
        email = self.build_email()
        ident = self._identity()
        reply_id = self.source["id"] if self.mode in ("reply", "reply-all") and self.source else None
        fwd_id = self.source["id"] if self.mode == "forward" and self.source else None

        def done(_new_id: str) -> None:
            self.dirty = False
            toast(self.parent_window, "Message sent")
            self.destroy()

        def failed(message: str) -> None:
            self._sending = False
            self.send_button.set_sensitive(True)
            dlg = Adw.AlertDialog(heading="Could not send", body=message)
            dlg.add_response("ok", "OK")
            dlg.present(self)

        self.engine.send_email(email, ident.id, self.draft_id, done, failed, in_reply_to_id=reply_id,
                               forwarded_id=fwd_id)

    def save_draft(self, close: bool = False, quiet: bool = False) -> None:
        email = self.build_email()

        def done(new_id: str) -> None:
            self.draft_id = new_id
            self.dirty = False
            if not quiet:
                toast(self.parent_window if close else self, "Draft saved")
            if close:
                self.destroy()

        def failed(message: str) -> None:
            toast(self, f"Draft not saved: {message}")

        self.engine.save_draft(email, self.draft_id, done, failed)

    def discard(self) -> None:
        def do_discard() -> None:
            self.dirty = False
            if self.draft_id:
                from ..store.actions import destroy

                self.engine.perform(destroy([self.draft_id]))
            self.destroy()

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
                self.destroy()

        dlg.connect("response", on_response)
        dlg.present(self)
        return True

    def destroy(self) -> None:  # type: ignore[override]
        if self._autosave:
            GLib.source_remove(self._autosave)
            self._autosave = 0
        if self.on_closed:
            self.on_closed(self)
        Adw.Window.destroy(self)
