"""A conversation opened in its own window (double-click / Enter in the list)."""

from __future__ import annotations

from gi.repository import Adw, Gio, Gtk

from .. import shortcuts
from ..models.thread import ThreadObject
from ..store import actions
from .conversation import ConversationView
from .labels import MailboxPickerPopover


class ThreadWindow(Adw.Window):
    def __init__(self, main, thread: ThreadObject, mailbox_id: str | None):
        super().__init__(application=main.get_application(), default_width=960, default_height=760,
                         title=thread.subject or "Conversation")
        self.main = main
        self.thread = thread
        self.mailbox_id = mailbox_id
        self.conversation = ConversationView(main.db, main.engine, main.tree, main.config, main._compose_from,
                                             self._email_action, avatars=main.avatars)
        self.conversation.on_remove_label = lambda mid: main._label_toggle(main.tree.get(mid), False,
                                                                           threads=[self.thread])
        self.labels_popover = MailboxPickerPopover(
            main.tree, "labels",
            on_toggle=lambda mb, on: main._label_toggle(mb, on, threads=[self.thread]),
            on_create=lambda name: main._create_label_and_apply(name, threads=[self.thread]))
        self.conversation.labels_button.set_popover(self.labels_popover)
        self.conversation.labels_button.connect(
            "notify::active", lambda b, _p: main._present_labels(self.labels_popover, [self.thread]) if b.get_active() else None)
        self.move_popover = MailboxPickerPopover(main.tree, "move",
                                                 on_pick=lambda mb: main._move_to(mb, threads=[self.thread]))
        self.conversation.move_button.set_popover(self.move_popover)
        self.conversation.move_button.connect(
            "notify::active", lambda b, _p: self.move_popover._rebuild() if b.get_active() else None)
        self.toast_overlay = Adw.ToastOverlay(child=self.conversation)
        self.set_content(self.toast_overlay)
        self._install_actions()
        self._handlers = [
            main.engine.connect("emails-changed", self._on_emails_changed),
            main.engine.connect("emails-destroyed", self._on_emails_changed),
        ]
        self.connect("close-request", self._on_close)
        ctrl = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
        ctrl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                           Gtk.CallbackAction.new(lambda *_: (self.close(), True)[1])))
        self.add_controller(ctrl)
        self.conversation.show_thread(thread, mailbox_id)
        if main.config.get("mark_read_on_open", True):
            unread = self.conversation.unread_email_ids()
            if unread:
                main.engine.perform(actions.mark_read(unread, True))

    def _install_actions(self) -> None:
        group = Gio.SimpleActionGroup()
        specs = {
            "reply": lambda: self.main._compose_from("reply", self.conversation.latest_email_id()),
            "reply-all": lambda: self.main._compose_from("reply-all", self.conversation.latest_email_id()),
            "forward": lambda: self.main._compose_from("forward", self.conversation.latest_email_id()),
            "labels": lambda: self.conversation.labels_button.popup(),
            "move": lambda: self.conversation.move_button.popup(),
        }
        for kind in ("archive", "trash", "junk", "not-junk", "flag", "mark-read", "mark-unread", "delete-permanently"):
            specs[kind] = lambda kind=kind: self._email_action(kind if kind != "delete-permanently" else "destroy",
                                                              self.thread.email_ids)
        for name, fn in specs.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=fn: fn())
            group.add_action(action)
        # The conversation toolbar targets win.* — provide them for this window too.
        # The shortcuts that name an action this window lacks simply do nothing.
        self.insert_action_group("win", group)
        shortcuts.install(self)

    def _email_action(self, kind: str, ids: list[str]) -> None:
        closes = kind in ("archive", "trash", "junk", "destroy") and self.mailbox_id is not None
        self.main._email_action(kind, ids, threads=[self.thread])
        if closes:
            self.close()

    def _on_emails_changed(self, _engine, ids: list[str]) -> None:
        if not set(ids) & set(self.thread.email_ids) and self.main.db.thread_of_email(ids[0] if ids else "") != self.thread.thread_id:
            return
        summary = self.main.db.thread_summary(self.thread.thread_id, self.mailbox_id, set(self.main.engine.trash_junk_ids()))
        if summary is None:
            self.close()
            return
        self.thread.update(summary, self.main._label_names(summary.mailbox_ids))
        self.conversation.refresh_thread(self.thread)

    def _on_close(self, *_) -> bool:
        for hid in self._handlers:
            self.main.engine.disconnect(hid)
        self._handlers = []
        self.conversation.detach()
        if self in self.main.thread_windows:
            self.main.thread_windows.remove(self)
        return False
