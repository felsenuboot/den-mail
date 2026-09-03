"""Conversation (thread) view: one expandable card per message."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from ..jmap.types import KW_DRAFT, KW_SEEN, address_display, address_full
from ..models.thread import ThreadObject, format_date_long
from .message_body import MessageBody
from .widgets import avatar, chip, human_size, open_uri, toast


class AttachmentChip(Gtk.Button):
    def __init__(self, att: dict, on_open: Callable[[dict], None], on_save: Callable[[dict], None]):
        super().__init__()
        self.add_css_class("attachment-chip")
        self.add_css_class("flat")
        box = Gtk.Box(spacing=6)
        icon = "image-x-generic-symbolic" if (att.get("type") or "").startswith("image/") else \
            "fm-attachment-symbolic"
        box.append(Gtk.Image(icon_name=icon))
        name = Gtk.Label(label=att.get("name") or "attachment", ellipsize=3, max_width_chars=28)
        box.append(name)
        size = Gtk.Label(label=human_size(att.get("size")))
        size.add_css_class("dim-label")
        size.add_css_class("caption")
        box.append(size)
        self.set_child(box)
        self.set_tooltip_text(f"{att.get('name')} ({att.get('type')}) — click to open, right-click to save")
        self.connect("clicked", lambda *_: on_open(att))
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", lambda *_: on_save(att))
        self.add_controller(click)


class MessageCard(Gtk.Box):
    def __init__(self, view: "ConversationView", email: dict, expanded: bool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.view = view
        self.email = email
        self.email_id = email["id"]
        self.expanded = expanded
        self.body_loaded = False
        self.remote_allowed = False
        self.add_css_class("message-card")
        self.add_css_class("card")
        if not (email.get("keywords") or {}).get(KW_SEEN):
            self.add_css_class("unread")

        # --- header (always visible)
        header = Gtk.Box(spacing=10)
        header.add_css_class("message-header")
        sender = (email.get("from") or [{}])[0]
        self.avatar = avatar(address_display(sender) or "?", 36)
        self.avatar.set_valign(Gtk.Align.START)
        header.append(self.avatar)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        line1 = Gtk.Box(spacing=6)
        self.from_name = Gtk.Label(label=address_display(sender) or sender.get("email", ""), xalign=0, ellipsize=3)
        self.from_name.add_css_class("from-name")
        line1.append(self.from_name)
        self.from_email = Gtk.Label(label=sender.get("email", "") if sender.get("name") else "", xalign=0, ellipsize=3,
                                    hexpand=True)
        self.from_email.add_css_class("from-email")
        self.from_email.add_css_class("dim-label")
        line1.append(self.from_email)
        self.date = Gtk.Label(label=format_date_long(email.get("receivedAt") or ""), xalign=1)
        self.date.add_css_class("date")
        self.date.add_css_class("dim-label")
        line1.append(self.date)
        col.append(line1)
        self.summary = Gtk.Label(xalign=0, ellipsize=3)
        self.summary.add_css_class("dim-label")
        self.summary.add_css_class("caption")
        col.append(self.summary)
        self.details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.details.set_visible(False)
        col.append(self.details)
        header.append(col)
        # per-message actions
        actions = Gtk.Box(spacing=0, valign=Gtk.Align.START)
        for icon, tip, mode in (("fm-reply-symbolic", "Reply", "reply"),
                                ("fm-reply-all-symbolic", "Reply all", "reply-all"),
                                ("fm-forward-symbolic", "Forward", "forward")):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, m=mode: self.view.on_compose(m, self.email_id))
            actions.append(b)
        menu = Gio.Menu()
        menu.append("Mark as unread", f"conv.unread::{self.email_id}")
        menu.append("Show details", f"conv.details::{self.email_id}")
        menu.append("Delete this message", f"conv.trash::{self.email_id}")
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu)
        more.add_css_class("flat")
        actions.append(more)
        header.append(actions)
        self.header = header
        self.append(header)
        click = Gtk.GestureClick(button=1)
        click.connect("released", self._on_header_click)
        header.add_controller(click)

        # --- body area
        self.body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.banner = Adw.Banner(title="This message loads content from remote servers", button_label="Load")
        self.banner.connect("button-clicked", self._on_allow_remote)
        self.banner.set_revealed(False)
        self.body_box.append(self.banner)
        self.truncated = Gtk.Label(label="Message was too large; showing the beginning only.", xalign=0)
        self.truncated.add_css_class("dim-label")
        self.truncated.set_visible(False)
        self.body_box.append(self.truncated)
        self.body = MessageBody()
        self.body_box.append(self.body)
        self.attachments = Adw.WrapBox(child_spacing=6, line_spacing=6)
        self.attachments.set_margin_start(12)
        self.attachments.set_margin_end(12)
        self.attachments.set_margin_top(8)
        self.attachments.set_margin_bottom(10)
        self.attachments.set_visible(False)
        self.body_box.append(self.attachments)
        self.loading = Adw.Spinner()
        self.loading.set_margin_top(12)
        self.loading.set_margin_bottom(12)
        self.body_box.append(self.loading)
        self.append(self.body_box)
        self._fill_summary()
        self._fill_details()
        self.set_expanded(expanded, initial=True)

    def _fill_summary(self) -> None:
        e = self.email
        to = ", ".join(address_display(a) for a in (e.get("to") or [])[:3])
        extra = len(e.get("to") or []) - 3
        if extra > 0:
            to += f" +{extra}"
        if self.expanded:
            self.summary.set_label(f"to {to}" if to else "")
        else:
            self.summary.set_label(" ".join((e.get("preview") or "").split())[:140])

    def _fill_details(self) -> None:
        e = self.email
        while child := self.details.get_first_child():
            self.details.remove(child)
        for key, label in (("from", "From"), ("to", "To"), ("cc", "Cc"), ("bcc", "Bcc"), ("replyTo", "Reply-To")):
            vals = e.get(key)
            if vals:
                row = Gtk.Label(label=f"{label}: " + ", ".join(address_full(a) for a in vals), xalign=0, wrap=True,
                                selectable=True)
                row.add_css_class("caption")
                self.details.append(row)
        delivered = e.get("header:Delivered-To:asText")
        if delivered:
            row = Gtk.Label(label=f"Delivered-To: {delivered}", xalign=0, wrap=True, selectable=True)
            row.add_css_class("caption")
            self.details.append(row)
        mid = (e.get("messageId") or [""])[0]
        if mid:
            row = Gtk.Label(label=f"Message-ID: {mid}", xalign=0, wrap=True, selectable=True)
            row.add_css_class("caption")
            self.details.append(row)

    def _on_header_click(self, gesture, n_press, x, y) -> None:
        # ignore clicks on the action buttons
        target = gesture.get_widget().pick(x, y, Gtk.PickFlags.DEFAULT)
        w = target
        while w is not None and w is not self.header:
            if isinstance(w, (Gtk.Button, Gtk.MenuButton)):
                return
            w = w.get_parent()
        self.set_expanded(not self.expanded)

    def set_expanded(self, expanded: bool, initial: bool = False) -> None:
        self.expanded = expanded
        self.body_box.set_visible(expanded)
        if expanded:
            self.remove_css_class("collapsed")
        else:
            self.add_css_class("collapsed")
        self._fill_summary()
        if expanded and not self.body_loaded:
            self.view.request_body(self.email_id)

    def toggle_details(self) -> None:
        self.details.set_visible(not self.details.get_visible())

    def show_body(self, full: dict) -> None:
        self.body_loaded = True
        self.email = {**self.email, **{k: v for k, v in full.items() if k not in ("bodyValues",)}}
        self._fill_details()
        self.loading.set_visible(False)
        values = full.get("bodyValues") or {}
        html = None
        text = None
        truncated = False
        for part in full.get("htmlBody") or []:
            v = values.get(part.get("partId"))
            if v and v.get("value") is not None:
                html = v["value"]
                truncated = truncated or bool(v.get("isTruncated"))
                break
        for part in full.get("textBody") or []:
            v = values.get(part.get("partId"))
            if v and v.get("value") is not None:
                text = v["value"]
                truncated = truncated or bool(v.get("isTruncated"))
                break
        policy = self.view.config.get("load_remote_images", "ask")
        if html:
            self.body.show_html(html, self.email_id, allow_remote=self.remote_allowed or policy == "always")
            self.banner.set_revealed(bool(self.body.has_remote) and not self.remote_allowed and policy == "ask")
        elif text is not None:
            self.body.show_text(text)
            self.banner.set_revealed(False)
        else:
            self.body.show_text(full.get("preview") or "(empty message)")
        self.truncated.set_visible(truncated)
        atts = [a for a in full.get("attachments") or []
                if a.get("disposition") == "attachment" or not a.get("cid")]
        while child := self.attachments.get_first_child():
            self.attachments.remove(child)
        for a in atts:
            self.attachments.append(AttachmentChip(a, self.view.open_attachment, self.view.save_attachment))
        self.attachments.set_visible(bool(atts))

    def show_body_error(self, message: str) -> None:
        self.loading.set_visible(False)
        self.body.show_text(f"Could not load message: {message}")

    def _on_allow_remote(self, *_) -> None:
        self.remote_allowed = True
        self.banner.set_revealed(False)
        self.body.allow_remote()


class ConversationView(Adw.NavigationPage):
    def __init__(self, db, engine, tree, config, on_compose: Callable[[str, str], None],
                 on_email_action: Callable[[str, list[str]], None]):
        super().__init__(title="Conversation", tag="conversation")
        self.db = db
        self.engine = engine
        self.tree = tree
        self.config = config
        self.on_compose = on_compose
        self.on_email_action = on_email_action
        self.thread_id: str | None = None
        self.cards: dict[str, MessageCard] = {}
        self.current_mailbox_id: str | None = None
        self.on_remove_label: Callable[[str], None] = lambda mailbox_id: None

        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_title=False)
        for icon, tip, action in (("fm-reply-symbolic", "Reply (R)", "win.reply"),
                                  ("fm-reply-all-symbolic", "Reply all (A)", "win.reply-all"),
                                  ("fm-forward-symbolic", "Forward (F)", "win.forward")):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.set_action_name(action)
            header.pack_start(b)
        more_menu = Gio.Menu()
        more_menu.append("Mark as unread", "win.mark-unread")
        more_menu.append("Mark as read", "win.mark-read")
        more_menu.append("Mark as spam", "win.junk")
        more_menu.append("Not spam", "win.not-junk")
        more_menu.append("Delete permanently", "win.delete-permanently")
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=more_menu, tooltip_text="More")
        header.pack_end(more)
        self.labels_button = Gtk.MenuButton(icon_name="fm-tag-symbolic", tooltip_text="Labels (L)")
        header.pack_end(self.labels_button)
        self.move_button = Gtk.MenuButton(icon_name="folder-symbolic", tooltip_text="Move to… (V)")
        header.pack_end(self.move_button)
        self.flag_button = Gtk.Button(icon_name="fm-star-outline-symbolic", tooltip_text="Flag (S)")
        self.flag_button.set_action_name("win.flag")
        header.pack_end(self.flag_button)
        trash = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete (#)")
        trash.set_action_name("win.trash")
        header.pack_end(trash)
        archive = Gtk.Button(icon_name="fm-archive-symbolic", tooltip_text="Archive (E)")
        archive.set_action_name("win.archive")
        header.pack_end(archive)
        view.add_top_bar(header)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.placeholder = Adw.StatusPage(icon_name="fm-mail-read-symbolic", title="Select a conversation",
                                          description="Choose a conversation from the list to read it here.")
        self.stack.add_named(self.placeholder, "empty")
        self.multi = Adw.StatusPage(icon_name="edit-select-all-symbolic", title="Multiple conversations selected")
        self.stack.add_named(self.multi, "multi")
        self.scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.content.set_margin_start(16)
        self.content.set_margin_end(16)
        self.content.set_margin_top(12)
        self.content.set_margin_bottom(24)
        self.subject = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.subject.add_css_class("conversation-subject")
        self.content.append(self.subject)
        self.chips = Adw.WrapBox(child_spacing=6, line_spacing=6)
        self.content.append(self.chips)
        self.cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.cards_box.set_margin_top(8)
        self.content.append(self.cards_box)
        clamp = Adw.Clamp(maximum_size=980, tightening_threshold=800, child=self.content)
        self.scrolled.set_child(clamp)
        self.stack.add_named(self.scrolled, "thread")
        view.set_content(self.stack)
        self.set_child(view)
        self._install_actions()

    def _install_actions(self) -> None:
        group = Gio.SimpleActionGroup()
        for name, fn in (("unread", lambda eid: self.on_email_action("mark-unread", [eid])),
                         ("trash", lambda eid: self.on_email_action("trash", [eid])),
                         ("details", lambda eid: self.cards[eid].toggle_details() if eid in self.cards else None)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", lambda _a, param, fn=fn: fn(param.get_string()))
            group.add_action(action)
        self.insert_action_group("conv", group)

    # ------------------------------------------------------------ display

    def clear(self) -> None:
        self.thread_id = None
        self.cards = {}
        while child := self.cards_box.get_first_child():
            self.cards_box.remove(child)
        self.stack.set_visible_child_name("empty")

    def show_multi(self, count: int) -> None:
        self.thread_id = None
        self.multi.set_title(f"{count} conversations selected")
        self.multi.set_description("Archive, delete, flag or label them with the toolbar buttons.")
        self.stack.set_visible_child_name("multi")

    def show_thread(self, thread: ThreadObject, mailbox_id: str | None) -> None:
        emails = self.db.thread_emails(thread.thread_id)
        if not emails:
            self.clear()
            return
        self.current_mailbox_id = mailbox_id
        same_thread = thread.thread_id == self.thread_id
        self.thread_id = thread.thread_id
        self.subject.set_label(thread.subject)
        self._fill_chips(thread)
        if not same_thread:
            self.cards = {}
            while child := self.cards_box.get_first_child():
                self.cards_box.remove(child)
            trash_junk = self.engine.trash_junk_ids()
            visible = [e for e in emails if mailbox_id in trash_junk or not (set(e.get("mailboxIds") or {}) & set(trash_junk))]
            if not visible:
                visible = emails
            for i, e in enumerate(visible):
                unread = not (e.get("keywords") or {}).get(KW_SEEN)
                expanded = unread or i == len(visible) - 1 or len(visible) == 1
                card = MessageCard(self, e, expanded)
                self.cards[e["id"]] = card
                self.cards_box.append(card)
            self.scrolled.get_vadjustment().set_value(0)
        else:
            for e in emails:
                card = self.cards.get(e["id"])
                if card:
                    card.email = {**card.email, **e}
                    if (e.get("keywords") or {}).get(KW_SEEN):
                        card.remove_css_class("unread")
                    else:
                        card.add_css_class("unread")
        self.stack.set_visible_child_name("thread")
        self._update_flag_button(thread)

    def _fill_chips(self, thread: ThreadObject) -> None:
        while child := self.chips.get_first_child():
            self.chips.remove(child)
        shown = 0
        for mid in sorted(thread.summary.mailbox_ids, key=lambda m: self.tree.path_name(m)):
            mb = self.tree.get(mid)
            if mb is None or mb.is_system:
                continue
            box = Gtk.Box(spacing=2)
            box.add_css_class("chip")
            box.add_css_class("removable")
            box.append(Gtk.Label(label=self.tree.path_name(mid)))
            x = Gtk.Button(icon_name="window-close-symbolic")
            x.add_css_class("flat")
            x.add_css_class("circular")
            x.set_tooltip_text("Remove label")
            x.connect("clicked", lambda _b, m=mid: self.on_remove_label(m))
            box.append(x)
            self.chips.append(box)
            shown += 1
        self.chips.set_visible(shown > 0)

    def _update_flag_button(self, thread: ThreadObject) -> None:
        self.flag_button.set_icon_name("fm-star-symbolic" if thread.flagged else "fm-star-outline-symbolic")
        self.flag_button.set_tooltip_text("Unflag (S)" if thread.flagged else "Flag (S)")

    def refresh_thread(self, thread: ThreadObject) -> None:
        if thread.thread_id == self.thread_id:
            self.show_thread(thread, self.current_mailbox_id)

    # ------------------------------------------------------------- bodies

    def request_body(self, email_id: str) -> None:
        self.engine.fetch_body(email_id)

    def on_body_ready(self, email_id: str) -> None:
        card = self.cards.get(email_id)
        if card is None or card.body_loaded:
            return
        full = self.db.get_email_body(email_id)
        if full:
            card.show_body(full)

    def on_body_failed(self, email_id: str, message: str) -> None:
        card = self.cards.get(email_id)
        if card is not None:
            card.show_body_error(message)

    def unread_email_ids(self) -> list[str]:
        return [eid for eid, c in self.cards.items() if not (c.email.get("keywords") or {}).get(KW_SEEN)]

    def draft_email_ids(self) -> list[str]:
        return [eid for eid, c in self.cards.items() if (c.email.get("keywords") or {}).get(KW_DRAFT)]

    def latest_email_id(self) -> str | None:
        return next(reversed(self.cards), None) if self.cards else None

    # -------------------------------------------------------- attachments

    def open_attachment(self, att: dict) -> None:
        def opened(path: Path) -> None:
            Gtk.FileLauncher(file=Gio.File.new_for_path(str(path))).launch(self.get_root(), None, None)

        self.engine.fetch_blob(att["blobId"], att.get("name") or "attachment", att.get("type"), opened,
                               lambda m: toast(self, f"Download failed: {m}"))

    def save_attachment(self, att: dict) -> None:
        dialog = Gtk.FileDialog(initial_name=att.get("name") or "attachment")

        def on_chosen(dlg, res):
            try:
                target = dlg.save_finish(res)
            except GLib.Error:
                return

            def fetched(path: Path) -> None:
                Gio.File.new_for_path(str(path)).copy(target, Gio.FileCopyFlags.OVERWRITE, None, None, None)
                toast(self, f"Saved {target.get_basename()}")

            self.engine.fetch_blob(att["blobId"], att.get("name") or "attachment", att.get("type"), fetched,
                                   lambda m: toast(self, f"Download failed: {m}"))

        dialog.save(self.get_root(), None, on_chosen)
