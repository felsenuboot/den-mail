"""Conversation (thread) view: one expandable card per message."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk

from .. import launch
from ..classify.rules import CATEGORY_NAMES
from ..html.body import assemble_body
from ..jmap.types import KW_DRAFT, KW_SEEN, address_display, address_full, delivered_to
from ..models.thread import ThreadObject, format_date_long
from ..summaries import Summariser
from ..unsubscribe import identity_for, parse_list_unsubscribe
from .a11y import watch as _a11y_watch
from .message_body import MessageBody, warm_up_renderer
from .widgets import avatar, confirm, copy_text, human_size, open_uri, toast

COLUMN_WIDTH = 980  # the message column's maximum width


class StartClamp(Gtk.Widget):
    """One child, at most `maximum` wide, at the start of the widget rather than centred."""

    def __init__(self, child: Gtk.Widget, maximum: int):
        super().__init__()
        self.maximum = maximum
        self._child = child
        child.set_parent(self)

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            minimum, natural, _mb, _nb = self._child.measure(orientation, -1)
            return minimum, max(minimum, min(natural, self.maximum)), -1, -1
        width = min(for_size, self.maximum) if for_size > 0 else for_size
        minimum, natural, _mb, _nb = self._child.measure(orientation, width)
        return minimum, natural, -1, -1

    def do_size_allocate(self, width, height, baseline):
        column = min(width, self.maximum)
        transform = None
        if self.get_direction() == Gtk.TextDirection.RTL and column < width:
            transform = Gsk.Transform().translate(Graphene.Point().init(width - column, 0))
        self._child.allocate(column, height, baseline, transform)

    def do_dispose(self):
        if self._child is not None:
            self._child.unparent()
            self._child = None
        Gtk.Widget.do_dispose(self)


class AttachmentChip(Gtk.Button):
    """One attachment: click opens it, right-click offers Open / Save as / Show in folder."""

    def __init__(self, att: dict, on_open: Callable[[dict, AttachmentChip], None],
                 on_save: Callable[[dict], None], on_folder: Callable[[dict, AttachmentChip], None]):
        super().__init__()
        self.att = att
        self.add_css_class("attachment-chip")
        self.add_css_class("flat")
        box = Gtk.Box(spacing=6)
        icon = "image-x-generic-symbolic" if (att.get("type") or "").startswith("image/") else \
            "fm-attachment-symbolic"
        self.icon = Gtk.Image(icon_name=icon)
        box.append(self.icon)
        self.spinner = Adw.Spinner()
        self.spinner.set_visible(False)
        box.append(self.spinner)
        name = Gtk.Label(label=att.get("name") or "attachment", ellipsize=3, max_width_chars=28)
        box.append(name)
        size = Gtk.Label(label=human_size(att.get("size")))
        size.add_css_class("dim-label")
        size.add_css_class("caption")
        box.append(size)
        self.set_child(box)
        app = default_app_name(att)
        self.set_tooltip_text(f"Open with {app}" if app else "No application registered for this file type")
        self.connect("clicked", lambda *_: on_open(att, self))
        menu = Gio.Menu()
        menu.append(f"Open with {app}" if app else "Open", "att.open")
        menu.append("Save as…", "att.save")
        menu.append("Show in folder", "att.folder")
        self.popover = Gtk.PopoverMenu.new_from_model(menu)
        self.popover.set_parent(self)
        self.popover.set_has_arrow(False)
        group = Gio.SimpleActionGroup()
        for name_, fn in (("open", lambda: on_open(att, self)), ("save", lambda: on_save(att)),
                          ("folder", lambda: on_folder(att, self))):
            a = Gio.SimpleAction.new(name_, None)
            a.connect("activate", lambda *_a, fn=fn: fn())
            group.add_action(a)
        self.insert_action_group("att", group)
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self._on_right_click)
        self.add_controller(click)
        self.connect("unrealize", lambda *_: self.popover.unparent())

    def _on_right_click(self, _g, _n, x, y) -> None:
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self.popover.set_pointing_to(rect)
        self.popover.popup()

    def set_busy(self, busy: bool) -> None:
        self.spinner.set_visible(busy)
        self.icon.set_visible(not busy)
        self.set_sensitive(not busy)


def default_app_name(att: dict) -> str | None:
    ctype = att.get("type") or Gio.content_type_guess(att.get("name") or "", None)[0]
    try:
        info = Gio.AppInfo.get_default_for_type(ctype or "application/octet-stream", False)
    except Exception:  # noqa: BLE001
        info = None
    if info is None and att.get("name"):
        guessed = Gio.content_type_guess(att["name"], None)[0]
        info = Gio.AppInfo.get_default_for_type(guessed, False) if guessed else None
    return info.get_display_name() if info else None


class MessageCard(Gtk.Box):
    def __init__(self, view: ConversationView, email: dict, expanded: bool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        _a11y_watch(self)   # (#123)
        self.view = view
        self.email = email
        self.email_id = email["id"]
        self.expanded = expanded
        self.body_loaded = False
        self.has_html = False
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
        self.sender_email = sender.get("email")
        self.avatar_key = view.avatars.key_for(self.sender_email) if view.avatars else None
        self.refresh_avatar()
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
        self.date = Gtk.Label(label=format_date_long(email.get("receivedAt") or ""), xalign=1, ellipsize=3)
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
        self.unsubscribe_plan = None
        self.unsubscribe_btn = Gtk.Button(label="Unsubscribe", valign=Gtk.Align.CENTER)
        self.unsubscribe_btn.add_css_class("flat")
        self.unsubscribe_btn.set_visible(False)
        self.unsubscribe_btn.connect("clicked", lambda *_: self.view.unsubscribe(self.email_id))
        actions.append(self.unsubscribe_btn)
        self.light_toggle = Gtk.ToggleButton(icon_name="fm-light-mode-symbolic",
                                             tooltip_text="Show this message with its original light colours")
        self.light_toggle.add_css_class("flat")
        self.light_toggle.set_visible(False)
        self.light_toggle.connect("toggled", lambda b: self._set_original_colours(b.get_active()))
        actions.append(self.light_toggle)
        self.wide_only: list[Gtk.Widget] = [self.from_email]  # hidden when the pane is narrow
        for icon, tip, mode in (("fm-reply-symbolic", "Reply", "reply"),
                                ("fm-reply-all-symbolic", "Reply all", "reply-all"),
                                ("fm-forward-symbolic", "Forward", "forward")):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, m=mode: self.view.on_compose(m, self.email_id))
            actions.append(b)
            if mode != "reply":
                self.wide_only.append(b)
        menu = Gio.Menu()
        menu.append("Reply all", f"conv.reply-all::{self.email_id}")
        menu.append("Forward", f"conv.forward::{self.email_id}")
        menu.append("Mark as unread", f"conv.unread::{self.email_id}")
        menu.append("Show details", f"conv.details::{self.email_id}")
        menu.append("Toggle dark adaptation", f"conv.colours::{self.email_id}")
        trust = Gio.Menu()
        trust.append("Always load remote content from this sender", f"conv.trust::{self.email_id}")
        trust.append("Stop trusting this sender", f"conv.untrust::{self.email_id}")
        menu.append_section(None, trust)
        danger = Gio.Menu()
        danger.append("Delete this message", f"conv.trash::{self.email_id}")
        menu.append_section(None, danger)
        self.force_original_colours = False
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, tooltip_text="More actions for this message")
        more.add_css_class("flat")
        actions.append(more)
        header.append(actions)
        self.header = header
        self.append(header)
        self.set_compact(view.compact)
        click = Gtk.GestureClick(button=1)
        click.connect("released", self._on_header_click)
        header.add_controller(click)

        # --- body area (attachments first, like a real attachment strip)
        self.body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.attachments = Adw.WrapBox(child_spacing=6, line_spacing=6)
        self.attachments_expander = Gtk.Expander(label="Attachments")
        self.attachments_expander.add_css_class("attachments-expander")
        self.attachments_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.attachments_holder.add_css_class("attachments-row")
        self.attachments_holder.set_visible(False)
        self.body_box.append(self.attachments_holder)
        # The schema.org summary (#20): "Parcel shipped · DHL · 0034…" with a copy button.
        self.structured = Gtk.Box(spacing=6, visible=False)
        self.structured.add_css_class("structured-row")
        self.structured_label = Gtk.Label(xalign=0, ellipsize=3, hexpand=True, selectable=True)
        self.structured.append(self.structured_label)
        self.structured_copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy the number", visible=False)
        self.structured_copy.add_css_class("flat")
        self.structured.append(self.structured_copy)
        self.body_box.append(self.structured)
        self.banner = RemoteContentBar(self._on_allow_remote, self._on_trust_sender)
        self.body_box.append(self.banner)
        self.truncated = Gtk.Label(label="Message was too large; showing the beginning only.", xalign=0)
        self.truncated.add_css_class("dim-label")
        self.truncated.set_visible(False)
        self.body_box.append(self.truncated)
        self.body = MessageBody()
        self.body_box.append(self.body)
        self.loading = Adw.Spinner()
        self.loading.set_margin_top(12)
        self.loading.set_margin_bottom(12)
        self.body_box.append(self.loading)
        self.append(self.body_box)
        self._fill_summary()
        self._fill_details()
        self.set_expanded(expanded, initial=True)

    def set_compact(self, compact: bool) -> None:
        """Narrow pane: hide the sender address and the per-message Reply all / Forward buttons
        (both stay reachable from the message menu)."""
        for w in self.wide_only:
            w.set_visible(not compact)

    def refresh_avatar(self) -> None:
        avatars = getattr(self.view, "avatars", None)
        self.avatar.set_custom_image(avatars.get(self.sender_email) if avatars else None)

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

    def _fill_structured(self) -> None:
        info = self.view.db.get_structured(self.email_id) if self.view.db else None
        self.structured.set_visible(info is not None)
        if info is None:
            return
        self.structured_label.set_label(info["text"])
        self.structured_copy.set_visible(bool(info.get("copy")))
        if info.get("copy"):
            if getattr(self, "_copy_handler", None):
                self.structured_copy.disconnect(self._copy_handler)
            self._copy_handler = self.structured_copy.connect(
                "clicked", lambda *_: (copy_text(self, info["copy"]), toast(self, f"Copied {info['copy']}")))

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
        delivered = delivered_to(e)
        if delivered:
            row = Gtk.Label(label=f"Delivered to: {delivered}", xalign=0, wrap=True, selectable=True)
            row.add_css_class("caption")
            self.details.append(row)
        mid = (e.get("messageId") or [""])[0]
        if mid:
            row = Gtk.Label(label=f"Message-ID: {mid}", xalign=0, wrap=True, selectable=True)
            row.add_css_class("caption")
            self.details.append(row)
        why = self.view.db.get_classification(self.email_id) if self.view.db else None
        if why:
            source = {"user": "your choice", "bayes": why.get("reason") or "learned from your corrections"}.get(
                why["source"], f"rule: {why.get('reason') or 'no signals'}")
            row = Gtk.Label(label=f"Category: {CATEGORY_NAMES.get(why['category'], why['category'])} · {source}",
                            xalign=0, wrap=True, selectable=True)
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
        self.email = {**self.email, **{k: v for k, v in full.items() if k != "bodyValues"}}
        self._fill_details()
        self._fill_structured()
        self.loading.set_visible(False)
        content = assemble_body(full)
        html, text, truncated = content.html, content.text, content.truncated
        policy = self.view.config.get("load_remote_images", "ask")
        trusted = self.view.config.is_trusted(self.sender_email)
        self.has_html = content.html is not None   # the sun toggle applies to any HTML view, pictures included
        self.light_toggle.set_visible(self.has_html and self.view.style_manager.get_dark())
        self.unsubscribe_plan = parse_list_unsubscribe(full.get("header:List-Unsubscribe:asRaw"),
                                                       full.get("header:List-Unsubscribe-Post:asRaw"))
        self.refresh_unsubscribe()
        if html:
            allow = self.remote_allowed or policy == "always" or (trusted and policy != "never")
            self.body.show_html(html, self.email_id, allow_remote=allow, dark=self.wants_dark(), text=text)
            self.banner.set_sender(address_display((self.email.get("from") or [{}])[0]) or self.sender_email or "")
            self._remote_asks = not allow and policy == "ask"
            self.body.on_quotes_toggled = self._update_banner  # remote images may live only in the quote
            self._update_banner()
        elif text is not None:
            self.body.show_text(text)
            self.banner.set_revealed(False)
        else:
            self.body.show_text(full.get("preview") or "(empty message)")
        self.truncated.set_visible(truncated)
        atts = [a for a in full.get("attachments") or []
                if a.get("disposition") == "attachment" or not a.get("cid")]
        self._fill_attachments(atts)

    def _update_banner(self) -> None:
        self.banner.set_revealed(bool(self.body.has_remote) and getattr(self, "_remote_asks", False))

    def _fill_attachments(self, atts: list[dict]) -> None:
        while child := self.attachments.get_first_child():
            self.attachments.remove(child)
        holder = self.attachments_holder
        while child := holder.get_first_child():
            holder.remove(child)
        if self.attachments.get_parent() is not None:
            self.attachments.get_parent().remove(self.attachments) if isinstance(self.attachments.get_parent(), Gtk.Box) \
                else self.attachments_expander.set_child(None)
        for a in atts:
            self.attachments.append(AttachmentChip(a, self.view.open_attachment, self.view.save_attachment,
                                                   self.view.show_attachment_folder))
        if len(atts) > 4:
            total = sum(int(a.get("size") or 0) for a in atts)
            self.attachments_expander.set_label(f"{len(atts)} attachments · {human_size(total)}")
            self.attachments_expander.set_child(self.attachments)
            self.attachments_expander.set_expanded(False)
            holder.append(self.attachments_expander)
        elif atts:
            holder.append(self.attachments)
        holder.set_visible(bool(atts))

    def wants_dark(self) -> bool:
        return self.view.is_dark() and not self.force_original_colours

    def set_dark(self, _dark: bool | None = None) -> None:
        self.light_toggle.set_visible(self.has_html and self.view.style_manager.get_dark())
        if self.body_loaded:
            self.body.set_dark(self.wants_dark())

    def _set_original_colours(self, on: bool) -> None:
        if self.force_original_colours == on:
            return
        self.force_original_colours = on
        if self.light_toggle.get_active() != on:
            self.light_toggle.set_active(on)
        self.set_dark()

    def toggle_colours(self) -> None:
        self._set_original_colours(not self.force_original_colours)

    def refresh_unsubscribe(self) -> None:
        plan = self.unsubscribe_plan
        self.unsubscribe_btn.set_visible(plan is not None)
        if plan is None:
            return
        when = self.view.config.unsubscribed().get((self.sender_email or "").lower())
        self.unsubscribe_btn.set_label("Unsubscribed" if when else "Unsubscribe")
        if when:
            self.unsubscribe_btn.add_css_class("dim-label")
        else:
            self.unsubscribe_btn.remove_css_class("dim-label")
        how = {"one-click": f"Send an unsubscribe request to {plan.target}",
               "browser": f"Open the unsubscribe page at {plan.target}",
               "mailto": f"Send an unsubscribe message to {plan.target}"}[plan.kind]
        self.unsubscribe_btn.set_tooltip_text((f"Unsubscribed on {when}. " if when else "") + how)

    def show_body_error(self, message: str) -> None:
        self.loading.set_visible(False)
        self.body.show_text(f"Could not load message: {message}")

    def _on_allow_remote(self, *_) -> None:
        self.remote_allowed = True
        self.banner.set_revealed(False)
        self.body.allow_remote()

    def _on_trust_sender(self, *_) -> None:
        if self.sender_email:
            self.view.config.trust_sender(self.sender_email)
            toast(self, f"Remote content from {self.sender_email} will load automatically", 4)
        self._on_allow_remote()

    def _on_untrust_sender(self) -> None:
        if self.sender_email:
            self.view.config.untrust_sender(self.sender_email)
            toast(self, f"{self.sender_email} is no longer trusted", 4)


JISHO_URL = "https://jisho.org/search/%E4%BC%9D"  # both readings, as jisho.org lists them
CALLIGRAPHY = Path(__file__).resolve().parent.parent / "den-calligraphy.png"

# (reading, romaji, senses, note)
ENTRIES = (
    ("でん", "den", ("legend; tradition", "biography; life", "method; way",
                    "horseback transportation and communication relay system used in ancient Japan"), None),
    ("つて", "tsute", ("means of making contact; intermediary; go-between",
                      "connections; influence; pull; good offices"),
     "usually written using kana alone; also 伝手, ツテ"),
)


def _calligraphy(height: int) -> Gtk.Widget:
    """The 伝 calligraphy scaled to `height`, or the character in a large font without the file."""
    try:
        texture = Gdk.Texture.new_from_filename(str(CALLIGRAPHY))
    except GLib.Error:
        label = Gtk.Label(label="伝")
        label.add_css_class("kanji-fallback")
        return label
    width = round(height * texture.get_width() / texture.get_height())
    snapshot = Gtk.Snapshot()
    snapshot.append_texture(texture, Graphene.Rect().init(0, 0, width, height))
    return Gtk.Picture(paintable=snapshot.to_paintable(Graphene.Size().init(width, height)), can_shrink=False)


def _open_link(label: Gtk.Label, uri: str) -> bool:
    open_uri(uri, label.get_root())
    return True  # handled: GTK must not launch it a second time


class TipCard(Gtk.Box):
    """A tip under the placeholder: one per start, the next one on request (#tips)."""

    def __init__(self, config):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)
        from .. import tips

        self.tips = tips
        self.config = config
        self.index = int(config.get("tip_index", 0))
        self.add_css_class("card")       # the HIG's card: background, border, rounded corners (#106)
        self.add_css_class("tip-card")
        self.set_size_request(420, -1)
        head = Gtk.Label(label="Tip", xalign=0)
        head.add_css_class("caption")
        head.add_css_class("dim-label")
        self.append(head)
        self.title = Gtk.Label(xalign=0, wrap=True, max_width_chars=48)
        self.title.add_css_class("heading")
        self.append(self.title)
        self.text = Gtk.Label(xalign=0, wrap=True, max_width_chars=48)
        self.append(self.text)
        row = Gtk.Box(spacing=6, margin_top=6)
        self.button = Gtk.Button()        # the tip's own action: a real button, not a flat link
        row.append(self.button)
        spacer = Gtk.Box(hexpand=True)
        row.append(spacer)
        nxt = Gtk.Button(label="Next tip", tooltip_text="Another one; a new tip comes with every start")
        nxt.add_css_class("flat")
        nxt.connect("clicked", lambda *_: self.show(self.index + 1))
        row.append(nxt)
        self.append(row)
        self.show(self.index)

    def show(self, index: int) -> None:
        self.index = index % len(self.tips.TIPS)
        tip = self.tips.tip_for(self.index)
        self.title.set_label(tip.title)
        self.text.set_label(tip.text)
        self.button.set_visible(tip.action is not None)
        if tip.action:
            self.button.set_label(tip.button)
            self.button.set_action_name(tip.action)


QUICK_LINKS = (
    ("Clean up", "fm-broom-symbolic", "win.cleanup", "Senders ranked by how pointless their mail looks"),
    ("Newsletters", "fm-newsletter-symbolic", "win.newsletters", "Unsubscribe from many senders at once"),
    ("Rules", "fm-filter-symbolic", "win.rules", "What happens to mail from a sender, domain, list or category"),
    ("Search", "system-search-symbolic", "win.search",
     "Search the list or all mail; operators such as from:, is:unread, has:attachment and older_than:7d narrow it"),
    ("Shortcuts", "input-keyboard-symbolic", "win.shortcuts", "Every key the app answers to"),
)


def quick_links() -> Gtk.Widget:
    """One labelled button per tool worth knowing about, whatever tip is showing (#43); they
    wrap when the pane is narrow (#106)."""
    row = Adw.WrapBox(child_spacing=6, line_spacing=6, halign=Gtk.Align.CENTER, justify=Adw.JustifyMode.NONE)
    row.add_css_class("quick-links")
    for label, icon, action, tip in QUICK_LINKS:
        b = Gtk.Button(child=Adw.ButtonContent(icon_name=icon, label=label), tooltip_text=tip)
        b.set_action_name(action)
        row.append(b)
    return row


def kanji_placeholder(config=None) -> Gtk.Widget:
    """What the conversation pane shows while nothing is selected: the name of the app as a
    dictionary entry, the way the tour introduces it, and a tip below."""
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=36, halign=Gtk.Align.CENTER,
                    valign=Gtk.Align.CENTER)
    box = Gtk.Box(spacing=28, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    box.add_css_class("kanji-placeholder")
    box.append(_calligraphy(150))
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER)
    for n, (kana, romaji, senses, note) in enumerate(ENTRIES):
        head = Gtk.Label(xalign=0, use_markup=True,
                         label=f"<span size='x-large' weight='bold'>伝</span>  〔{kana} · <i>{romaji}</i>〕  "  # noqa: RUF001 - dictionary brackets
                               "<span size='small' alpha='60%'>noun</span>")
        if n:
            head.set_margin_top(14)
        column.append(head)
        for i, sense in enumerate(senses, 1):
            column.append(Gtk.Label(label=f"{i}. {sense}", xalign=0, wrap=True, max_width_chars=36))
        if note:
            note_label = Gtk.Label(label=note, xalign=0, wrap=True, max_width_chars=36)
            note_label.add_css_class("caption")
            note_label.add_css_class("dim-label")
            column.append(note_label)
    link = Gtk.Label(xalign=0, use_markup=True, label=f"<a href='{JISHO_URL}'>jisho.org</a>")
    link.add_css_class("caption")
    link.connect("activate-link", _open_link)
    link.set_margin_top(8)
    column.append(link)
    box.append(column)
    outer.append(box)
    if config is not None:
        below = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        below.append(TipCard(config))
        below.append(quick_links())
        outer.append(below)
    return outer


class RemoteContentBar(Gtk.Revealer):
    """'This message loads remote content' with Load once / Always from sender."""

    def __init__(self, on_load, on_trust):
        super().__init__(reveal_child=False, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        box = Gtk.Box(spacing=8)
        box.add_css_class("remote-bar")
        self.label = Gtk.Label(label="This message loads content from remote servers", xalign=0, hexpand=True,
                               ellipsize=3)
        box.append(self.label)
        self.trust_label = Gtk.Label(label="Always from sender", ellipsize=3)
        self.trust_button = Gtk.Button(child=self.trust_label)
        self.trust_button.add_css_class("flat")
        self.trust_button.connect("clicked", on_trust)
        box.append(self.trust_button)
        load = Gtk.Button(label="Load")
        load.add_css_class("suggested-action")
        load.connect("clicked", on_load)
        box.append(load)
        self.set_child(box)

    def set_sender(self, name: str) -> None:
        self.trust_label.set_label(f"Always from {name}" if name else "Always from sender")
        self.trust_button.set_tooltip_text("Remember this sender and load remote content automatically")

    def set_revealed(self, revealed: bool) -> None:
        self.set_reveal_child(revealed)


class ConversationView(Adw.NavigationPage):
    # Set by the breakpoint bin around the content when the pane is narrow; message cards
    # then drop their secondary buttons so the pane can shrink to the header bar's width.
    compact = GObject.Property(type=bool, default=False)
    wide = GObject.Property(type=bool, default=False)

    def __init__(self, db, engine, tree, config, on_compose: Callable[[str, str], None],
                 on_email_action: Callable[[str, list[str]], None], avatars=None, assistant=None):
        super().__init__(title="Conversation", tag="conversation")
        self.db = db
        self.engine = engine
        self.tree = tree
        self.config = config
        self.avatars = avatars
        self.summariser = Summariser(db, engine, assistant) if assistant is not None and db is not None else None
        self.style_manager = Adw.StyleManager.get_default()
        self._handlers: list[tuple[object, int]] = []
        self._handlers.append((self.style_manager,
                               self.style_manager.connect("notify::dark", lambda *_: self._on_theme_changed())))
        if avatars is not None:
            self._handlers.append((avatars, avatars.connect("avatar-ready", self._on_avatar_ready)))
        self._handlers.append((engine, engine.connect("body-ready", lambda _e, eid: self.on_body_ready(eid))))
        self._handlers.append((engine, engine.connect("body-failed", lambda _e, eid, msg: self.on_body_failed(eid, msg))))
        self.on_compose = on_compose
        self.on_email_action = on_email_action
        self.thread_id: str | None = None
        self.cards: dict[str, MessageCard] = {}
        self.current_mailbox_id: str | None = None
        self.on_remove_label: Callable[[str], None] = lambda mailbox_id: None
        self.on_add_label: Callable[[str], None] = lambda mailbox_id: None   # a suggestion chip (#60)

        view = Adw.ToolbarView()
        self.header = Adw.HeaderBar(show_title=False)
        for icon, tip, action in (("fm-reply-symbolic", "Reply (R)", "win.reply"),
                                  ("fm-reply-all-symbolic", "Reply all (A)", "win.reply-all"),
                                  ("fm-forward-symbolic", "Forward (F)", "win.forward")):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.set_action_name(action)
            self.header.pack_start(b)
        # Summarise (#68): only while the assistant is on; the bar with the answer sits above the cards.
        self.summarise_button = Gtk.Button(icon_name="fm-assistant-symbolic", tooltip_text="Summarise this conversation",
                                           visible=False)
        self.summarise_button.set_action_name("win.summarise")
        self.header.pack_start(self.summarise_button)
        more_menu = Gio.Menu()
        more_menu.append("Mark as unread", "win.mark-unread")
        more_menu.append("Mark as read", "win.mark-read")
        more_menu.append("Mark as spam", "win.junk")
        more_menu.append("Not spam", "win.not-junk")
        more_menu.append("Delete permanently", "win.delete-permanently")
        # The thread actions travel: in the header bar normally, and next to the
        # message column when the pane is much wider than it (see `wide`).
        self.actions = Gtk.Box(spacing=6)
        archive = Gtk.Button(icon_name="fm-archive-symbolic", tooltip_text="Archive (E)")
        archive.set_action_name("win.archive")
        self.actions.append(archive)
        trash = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete (#)")
        trash.set_action_name("win.trash")
        self.actions.append(trash)
        self.flag_button = Gtk.Button(icon_name="fm-star-outline-symbolic", tooltip_text="Flag (S)")
        self.flag_button.set_action_name("win.flag")
        self.actions.append(self.flag_button)
        self.move_button = Gtk.MenuButton(icon_name="folder-symbolic", tooltip_text="Move to… (V)")
        self.actions.append(self.move_button)
        self.labels_button = Gtk.MenuButton(icon_name="fm-tag-symbolic", tooltip_text="Labels (L)")
        self.actions.append(self.labels_button)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=more_menu, tooltip_text="More")
        self.actions.append(more)
        self.header.pack_end(self.actions)
        view.add_top_bar(self.header)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.placeholder = kanji_placeholder(config)
        self.stack.add_named(self.placeholder, "empty")
        self.multi = Adw.StatusPage(icon_name="edit-select-all-symbolic", title="Multiple conversations selected")
        self.stack.add_named(self.multi, "multi")
        self.scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.content.set_margin_start(16)
        self.content.set_margin_end(16)
        self.content.set_margin_top(12)
        self.content.set_margin_bottom(24)
        self.subject = Gtk.Label(xalign=0, wrap=True, selectable=True, focusable=False, hexpand=True)
        self.subject.add_css_class("conversation-subject")
        self.subject_row = Gtk.Box(spacing=12)
        self.subject_row.append(self.subject)
        self.content.append(self.subject_row)
        self.chips = Adw.WrapBox(child_spacing=6, line_spacing=6)
        self.content.append(self.chips)
        # The screener's question (#24): shown while the sender waits for a decision.
        self.screener_check: Callable[[str], bool] = lambda email: False
        self.on_screener_decision: Callable[[str, bool], None] = lambda email, allow: None
        self.screener_bar = Gtk.Box(spacing=8, visible=False)
        self.screener_bar.add_css_class("screener-bar")
        self.screener_label = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        self.screener_bar.append(self.screener_label)
        self._screener_sender = ""
        allow = Gtk.Button(label="Let through", valign=Gtk.Align.CENTER, tooltip_text="Their mail goes to the Inbox, now and in future")
        allow.add_css_class("suggested-action")
        allow.connect("clicked", lambda *_: self.on_screener_decision(self._screener_sender, True))
        self.screener_bar.append(allow)
        block = Gtk.Button(label="Screen out", valign=Gtk.Align.CENTER, tooltip_text="Archive their mail, now and in future")
        block.connect("clicked", lambda *_: self.on_screener_decision(self._screener_sender, False))
        self.screener_bar.append(block)
        self.content.append(self.screener_bar)
        # A message waiting in Scheduled (#6): when it goes, and a way to take it back.
        self.on_cancel_scheduled: Callable[[str], None] = lambda email_id: None
        self.scheduled_bar = Gtk.Box(spacing=8, visible=False)
        self.scheduled_bar.add_css_class("screener-bar")
        self.scheduled_label = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        self.scheduled_bar.append(self.scheduled_label)
        self._scheduled_id = ""
        cancel = Gtk.Button(label="Cancel send", valign=Gtk.Align.CENTER, tooltip_text="Back to Drafts, not sent")
        cancel.connect("clicked", lambda *_: self.on_cancel_scheduled(self._scheduled_id))
        self.scheduled_bar.append(cancel)
        self.content.append(self.scheduled_bar)
        # The summary (#68) is a card like the tip: a caption, the text, the controls in the corner (#118).
        self.summary_bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, visible=False)
        self.summary_bar.add_css_class("card")
        self.summary_bar.add_css_class("summary-card")
        head = Gtk.Box(spacing=4)
        self.summary_caption = Gtk.Label(label="Summary", xalign=0, hexpand=True, valign=Gtk.Align.CENTER)
        self.summary_caption.add_css_class("caption")
        self.summary_caption.add_css_class("dim-label")
        head.append(self.summary_caption)
        again = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Summarise again")
        again.add_css_class("flat")
        again.add_css_class("circular")
        again.connect("clicked", lambda *_: self.summarise(force=True))
        head.append(again)
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Hide the summary")
        close.add_css_class("flat")
        close.add_css_class("circular")
        close.connect("clicked", lambda *_: self.summary_bar.set_visible(False))
        head.append(close)
        self.summary_bar.append(head)
        self.summary_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.summary_bar.append(self.summary_label)
        self.content.append(self.summary_bar)
        self.cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.cards_box.set_margin_top(8)
        self.content.append(self.cards_box)
        # The message column keeps a readable width and stays at the start of the pane,
        # next to the list, rather than drifting to the middle of an ultrawide screen.
        self.scrolled.set_child(StartClamp(self.content, COLUMN_WIDTH))
        self.stack.add_named(self.scrolled, "thread")
        # The bin decouples the pane's minimum width from the cards' natural layout: below
        # the breakpoint the cards switch to their compact form, which fits the bin's request.
        content_bin = Adw.BreakpointBin(width_request=360, height_request=240)
        content_bin.set_child(self.stack)
        compact_bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 580sp"))
        compact_bp.add_setter(self, "compact", True)
        content_bin.add_breakpoint(compact_bp)
        # Far wider than the column, the header bar's actions would sit a screen away from
        # the message; they move into the subject row then.
        wide_bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f"min-width: {COLUMN_WIDTH + 320}sp"))
        wide_bp.add_setter(self, "wide", True)
        content_bin.add_breakpoint(wide_bp)
        view.set_content(content_bin)
        self.connect("notify::compact", lambda *_: [c.set_compact(self.compact) for c in self.cards.values()])
        self.connect("notify::wide", lambda *_: self._place_actions())
        self.set_child(view)
        self._install_actions()
        GLib.idle_add(self._warm_up, priority=GLib.PRIORITY_LOW)  # after the first paint

    @staticmethod
    def _warm_up() -> bool:
        warm_up_renderer()
        return False

    def _place_actions(self) -> None:
        """Move the thread actions between the header bar and the subject row."""
        parent = self.actions.get_parent()
        if self.wide and parent is not self.subject_row:
            if parent is not None:
                self.header.remove(self.actions)
            self.actions.set_valign(Gtk.Align.START)
            self.subject_row.append(self.actions)
        elif not self.wide and parent is self.subject_row:
            self.subject_row.remove(self.actions)
            self.header.pack_end(self.actions)

    def _install_actions(self) -> None:
        group = Gio.SimpleActionGroup()
        for name, fn in (("unread", lambda eid: self.on_email_action("mark-unread", [eid])),
                         ("reply-all", lambda eid: self.on_compose("reply-all", eid)),
                         ("forward", lambda eid: self.on_compose("forward", eid)),
                         ("trash", lambda eid: self.on_email_action("trash", [eid])),
                         ("colours", lambda eid: self.cards[eid].toggle_colours() if eid in self.cards else None),
                         ("trust", lambda eid: self.cards[eid]._on_trust_sender() if eid in self.cards else None),
                         ("untrust", lambda eid: self.cards[eid]._on_untrust_sender() if eid in self.cards else None),
                         ("details", lambda eid: self.cards[eid].toggle_details() if eid in self.cards else None)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", lambda _a, param, fn=fn: fn(param.get_string()))
            group.add_action(action)
        self.insert_action_group("conv", group)

    # ----------------------------------------------------------- unsubscribe

    UNSUBSCRIBE_HEADERS: ClassVar[list[str]] = ["header:List-Unsubscribe:asRaw", "header:List-Unsubscribe-Post:asRaw"]

    def unsubscribe(self, email_id: str) -> None:
        card = self.cards.get(email_id)
        if card is None or card.unsubscribe_plan is None:
            return
        if any(h not in card.email for h in self.UNSUBSCRIBE_HEADERS):
            # body cached by an older version without the -Post header: check before picking a method
            card.unsubscribe_btn.set_sensitive(False)

            def got(headers: dict) -> None:
                card.unsubscribe_btn.set_sensitive(True)
                card.email.update(headers)
                card.unsubscribe_plan = parse_list_unsubscribe(headers.get(self.UNSUBSCRIBE_HEADERS[0]),
                                                               headers.get(self.UNSUBSCRIBE_HEADERS[1]))
                card.refresh_unsubscribe()
                if card.unsubscribe_plan is not None:
                    self._confirm_unsubscribe(card)

            def failed(message: str) -> None:
                card.unsubscribe_btn.set_sensitive(True)
                toast(self, f"Could not check the unsubscribe headers: {message}", 6)

            self.engine.fetch_email_headers(card.email_id, self.UNSUBSCRIBE_HEADERS, got, failed)
            return
        self._confirm_unsubscribe(card)

    def _confirm_unsubscribe(self, card: MessageCard) -> None:
        plan = card.unsubscribe_plan
        sender = address_display((card.email.get("from") or [{}])[0]) or card.sender_email or "this sender"
        body = {"one-click": f"Sends an unsubscribe request to {plan.target}.",
                "browser": f"Opens the sender's unsubscribe page at {plan.target} in your browser.",
                "mailto": f"Sends an unsubscribe message to {plan.target}."}[plan.kind]
        if plan.kind == "mailto":
            ident = self.identity_for(card.email)
            if ident:
                body += f" It goes out from {ident.get('email')}."
        confirm(self, f"Unsubscribe from {sender}?", body, "Unsubscribe", False,
                lambda: self._run_unsubscribe(card, plan))

    def _run_unsubscribe(self, card: MessageCard, plan) -> None:
        from .newsletters import done_text, run_unsubscribe
        sender_email = (card.sender_email or "").lower()
        button = card.unsubscribe_btn
        button.set_sensitive(False)

        def done(used) -> None:
            button.set_sensitive(True)
            self.config.mark_unsubscribed(sender_email)
            for c in self.cards.values():
                if (c.sender_email or "").lower() == sender_email:
                    c.refresh_unsubscribe()
            toast(self, done_text(used), 4)

        def failed(message: str) -> None:
            button.set_sensitive(True)
            toast(self, message, 6)

        run_unsubscribe(self.engine, self.db, card.email, plan, self.get_root(), done, failed,
                        lambda note: toast(self, note, 6))

    def identity_for(self, email: dict) -> dict | None:
        """The identity a message was delivered to, else the primary one."""
        session = getattr(getattr(self.engine, "client", None), "session", None)
        return identity_for(self.db.get_identities(), getattr(session, "username", "") or "", email)

    def _on_avatar_ready(self, _service, key: str) -> None:
        for card in self.cards.values():
            if card.avatar_key == key:
                card.refresh_avatar()

    def is_dark(self) -> bool:
        return bool(self.config.get("dark_html", True)) and self.style_manager.get_dark()

    def _on_theme_changed(self) -> None:
        for card in self.cards.values():
            card.set_dark()
            card.refresh_avatar()   # dark logos get a light plate only on the dark theme

    def detach(self) -> None:
        """Disconnect from engine/avatar signals (for views living in closable windows)."""
        for obj, hid in self._handlers:
            obj.disconnect(hid)
        self._handlers = []

    # ------------------------------------------------------------ display

    def clear(self) -> None:
        self.thread_id = None
        self.cards = {}
        while child := self.cards_box.get_first_child():
            self.cards_box.remove(child)
        self.screener_bar.set_visible(False)
        self.scheduled_bar.set_visible(False)
        self.summary_bar.set_visible(False)
        self.stack.set_visible_child_name("empty")

    def show_multi(self, count: int) -> None:
        self.thread_id = None
        self.multi.set_title(f"{count} conversations selected")
        self.multi.set_description("Archive, delete, flag or label them with the toolbar buttons.\nCtrl-click or Shift-click adds to the selection; the Select button shows checkboxes.")
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
        self._fill_screener(thread)
        self._fill_scheduled(emails)
        self._fill_summary(same_thread)
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

    def _fill_scheduled(self, emails: list[dict]) -> None:
        from .. import schedule

        self._scheduled_id = ""
        for e in reversed(emails):
            sub = self.db.get_submission(e["id"]) if self.db else None
            if sub:
                self._scheduled_id = e["id"]
                self.scheduled_label.set_label(f"Scheduled: goes out {schedule.describe(sub['send_at'] or '')}.")
                break
        self.scheduled_bar.set_visible(bool(self._scheduled_id))

    def _fill_summary(self, same_thread: bool) -> None:
        """The cached summary appears at once; a stale one (new replies) is hidden until asked again."""
        available = self.summariser is not None and self.summariser.available
        self.summarise_button.set_visible(available)
        hit = self.summariser.cached_thread(self.thread_id) if available and self.thread_id else None
        if hit is not None:
            self._show_summary(hit.text)
        elif not same_thread or not available:
            self.summary_bar.set_visible(False)

    def _show_summary(self, text: str, error: bool = False) -> None:
        self.summary_label.set_label(text)
        self.summary_caption.set_label("Summary failed" if error else "Summary")
        self.summary_bar.remove_css_class("error")
        if error:
            self.summary_bar.add_css_class("error")
        self.summary_bar.set_visible(True)

    def summarise(self, force: bool = False) -> None:
        """Ask the assistant for the thread's summary (#68); the answer lands above the cards."""
        if self.summariser is None or not self.thread_id:
            return
        if not self.summariser.available:
            self._show_summary("The assistant is off; turn it on in Preferences → Assistant.", error=True)
            return
        thread_id = self.thread_id
        self._show_summary("Summarising…")

        def done(summary) -> None:
            if self.thread_id == thread_id:
                self._show_summary(summary.text)

        def failed(message: str) -> None:
            if self.thread_id == thread_id:
                self._show_summary(message, error=True)

        self.summariser.thread(thread_id, done, failed, force=force)

    def _fill_screener(self, thread: ThreadObject) -> None:
        first = thread.summary.from_addresses[0] if thread.summary.from_addresses else {}
        sender = (first.get("email") or "").strip().lower()
        pending = bool(sender) and self.screener_check(sender)
        self._screener_sender = sender if pending else ""
        if pending:
            who = first.get("name") or sender
            self.screener_label.set_label(f"First-time sender: {who} ({sender}). Let their mail into the Inbox?"
                                          if first.get("name") else f"First-time sender: {sender}. Let their mail into the Inbox?")
        self.screener_bar.set_visible(pending)

    def _fill_chips(self, thread: ThreadObject) -> None:
        while child := self.chips.get_first_child():
            self.chips.remove(child)
        shown = 0
        # Inbox first, then labels by path; Archive is implied by the absence of Inbox and stays hidden.
        ids = sorted(thread.summary.mailbox_ids,
                     key=lambda m: (0 if (self.tree.get(m) and self.tree.get(m).role == "inbox") else 1,
                                    self.tree.path_name(m)))
        for mid in ids:
            mb = self.tree.get(mid)
            if mb is None or mb.role == "archive":
                continue
            box = Gtk.Box(spacing=2)
            box.add_css_class("chip")
            if mb.is_system:
                box.add_css_class("chip-system")
            else:
                box.add_css_class(f"label-color-{mb.color_index}")
            box.append(Gtk.Label(label=self.tree.path_name(mid)))
            if not mb.is_system or mb.role == "inbox":
                box.add_css_class("removable")
                x = Gtk.Button(icon_name="window-close-symbolic")
                x.add_css_class("flat")
                x.add_css_class("circular")
                x.set_tooltip_text("Archive" if mb.role == "inbox" else "Remove label")
                x.connect("clicked", lambda _b, m=mid: self.on_remove_label(m))
                box.append(x)
            self.chips.append(box)
            shown += 1
        shown += self._fill_suggestions(thread)
        self.chips.set_visible(shown > 0)

    def _fill_suggestions(self, thread: ThreadObject) -> int:
        """Labels the learned models are sure about (#60): a dashed chip with a plus that applies it."""
        if not self.db or not self.config.get("label_suggestions", True) or not self.db.labels_ready:
            return 0
        newest = self.db.get_email(thread.email_ids[-1]) if thread.email_ids else None
        present = set(thread.summary.mailbox_ids)
        shown = 0
        for s in self.db.label_suggestions(newest):
            mb = self.tree.get(s.label_id)
            if mb is None or s.label_id in present:
                continue
            box = Gtk.Box(spacing=2)
            box.add_css_class("chip")
            box.add_css_class("chip-suggestion")
            box.add_css_class(f"label-color-{mb.color_index}")
            words = ", ".join(t.split(":", 1)[-1] for t in s.evidence[:3])
            box.set_tooltip_text(f"The app thinks this belongs under {self.tree.path_name(s.label_id)} "
                                 f"({s.probability:.0%}, going by {words}). Click to add the label.")
            add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add this label")
            add.add_css_class("flat")
            add.add_css_class("circular")
            add.connect("clicked", lambda _b, m=s.label_id: self.on_add_label(m))
            box.append(add)
            box.append(Gtk.Label(label=f"{self.tree.path_name(s.label_id)}?"))
            self.chips.append(box)
            shown += 1
        return shown

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

    def open_attachment(self, att: dict, chip: AttachmentChip | None = None) -> None:
        name = att.get("name") or "attachment"
        app = default_app_name(att)
        if chip:
            chip.set_busy(True)

        def launched(error: str | None) -> None:
            if chip:
                chip.set_busy(False)
            if error:
                toast(self, f"Could not open {name}: {error}", 6)
                return
            toast(self, f"Opened {name} in {app}" if app else f"Opened {name}", 4)

        def opened(path: Path) -> None:
            launch.open_file(path, att.get("type"), self.get_root(), launched)

        def failed(message: str) -> None:
            if chip:
                chip.set_busy(False)
            toast(self, f"Download failed: {message}", 6)

        self.engine.fetch_blob(att["blobId"], name, att.get("type"), opened, failed)

    def show_attachment_folder(self, att: dict, chip: AttachmentChip | None = None) -> None:
        name = att.get("name") or "attachment"
        if chip:
            chip.set_busy(True)

        def done(launcher, res) -> None:
            if chip:
                chip.set_busy(False)
            try:
                launcher.open_containing_folder_finish(res)
            except GLib.Error as e:
                toast(self, f"Could not open folder: {e.message}", 6)

        def fetched(path: Path) -> None:
            Gtk.FileLauncher(file=Gio.File.new_for_path(str(path))).open_containing_folder(self.get_root(), None, done)

        self.engine.fetch_blob(att["blobId"], name, att.get("type"), fetched,
                               lambda m: (chip and chip.set_busy(False), toast(self, f"Download failed: {m}", 6)))

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
