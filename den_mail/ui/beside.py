"""A second conversation beside the reading pane on a wide window (#35).

The same idea as the thread window, in place: its own ConversationView with a
"win" action group of its own, so Reply, Archive and the rest inside it act
on the pinned thread while the list's selection keeps driving the main pane.
"""

from __future__ import annotations

from gi.repository import Gio, Gtk

from ..models.thread import ThreadObject
from .a11y import watch as _a11y_watch
from .conversation import ConversationView
from .labels import MailboxPickerPopover

MIN_WINDOW_WIDTH = 2200   # sp; below this "Open beside" opens a thread window instead


class BesideColumn(Gtk.Box):
    """Hidden until a thread is pinned; `close()` empties and hides it."""

    def __init__(self, main) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, visible=False, hexpand=True)
        self.add_css_class("beside-column")
        self.main = main
        self.thread: ThreadObject | None = None
        self.mailbox_id: str | None = None
        self.conversation = ConversationView(main.db, main.engine, main.tree, main.config, main._compose_from,
                                             self._email_action, avatars=main.avatars, assistant=main.assistant)
        self.conversation.on_remove_label = lambda mid: self._with_thread(
            lambda t: main._label_toggle(main.tree.get(mid), False, threads=[t]))
        self.conversation.on_add_label = lambda mid: self._with_thread(
            lambda t: main._label_toggle(main.tree.get(mid), True, threads=[t]))
        self.labels_popover = MailboxPickerPopover(
            main.tree, "labels",
            on_toggle=lambda mb, on: self._with_thread(lambda t: main._label_toggle(mb, on, threads=[t])),
            on_create=lambda name: self._with_thread(lambda t: main._create_label_and_apply(name, threads=[t])))
        self.conversation.labels_button.set_popover(self.labels_popover)
        self.conversation.labels_button.connect(
            "notify::active", lambda b, _p: self._with_thread(lambda t: main._present_labels(self.labels_popover, [t]))
            if b.get_active() else None)
        self.move_popover = MailboxPickerPopover(main.tree, "move",
                                                 on_pick=lambda mb: self._with_thread(lambda t: main._move_to(mb, threads=[t])))
        self.conversation.move_button.set_popover(self.move_popover)
        self.conversation.move_button.connect(
            "notify::active", lambda b, _p: self.move_popover._rebuild() if b.get_active() else None)
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close this column")
        close.connect("clicked", lambda *_: self.close())
        self.conversation.header.pack_end(close)
        self.append(self.conversation)
        self._install_actions()
        _a11y_watch(self)   # icon-only buttons get their tooltip as accessible name (#123)
        self._handlers = [main.engine.connect("emails-changed", self._on_emails_changed),
                          main.engine.connect("emails-destroyed", self._on_emails_changed)]

    # ------------------------------------------------------------ actions

    def _install_actions(self) -> None:
        group = Gio.SimpleActionGroup()
        specs = {
            "reply": lambda: self.main._compose_from("reply", self.conversation.latest_email_id()),
            "reply-all": lambda: self.main._compose_from("reply-all", self.conversation.latest_email_id()),
            "forward": lambda: self.main._compose_from("forward", self.conversation.latest_email_id()),
            "labels": lambda: self.conversation.labels_button.popup(),
            "move": lambda: self.conversation.move_button.popup(),
            "summarise": lambda: self.conversation.summarise(),
        }
        for kind in ("archive", "trash", "junk", "not-junk", "flag", "mark-read", "mark-unread", "delete-permanently"):
            specs[kind] = lambda kind=kind: self._with_thread(
                lambda t: self._email_action(kind if kind != "delete-permanently" else "destroy", t.email_ids))
        for name, fn in specs.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=fn: fn())
            group.add_action(action)
        # The conversation's toolbar targets win.*; inside this column they mean the pinned thread.
        self.insert_action_group("win", group)

    def _with_thread(self, fn) -> None:
        if self.thread is not None:
            fn(self.thread)

    def _email_action(self, kind: str, ids: list[str]) -> None:
        if self.thread is None:
            return
        closes = kind in ("archive", "trash", "junk", "destroy") and self.mailbox_id is not None
        self.main._email_action(kind, ids, threads=[self.thread])
        if closes:
            self.close()

    # ------------------------------------------------------------- state

    def show(self, thread: ThreadObject, mailbox_id: str | None) -> None:
        self.thread = thread
        self.mailbox_id = mailbox_id
        self.conversation.show_thread(thread, mailbox_id)
        self.set_visible(True)

    def close(self) -> None:
        self.thread = None
        self.conversation.clear()
        self.set_visible(False)

    def _on_emails_changed(self, _engine, ids: list[str]) -> None:
        if self.thread is None:
            return
        main = self.main
        if not set(ids) & set(self.thread.email_ids) and main.db.thread_of_email(ids[0] if ids else "") != self.thread.thread_id:
            return
        summary = main.db.thread_summary(self.thread.thread_id, self.mailbox_id, set(main.engine.trash_junk_ids()))
        if summary is None:
            self.close()
            return
        self.thread.update(summary, main._label_names(summary.mailbox_ids))
        self.conversation.refresh_thread(self.thread)

    def detach(self) -> None:
        for hid in self._handlers:
            self.main.engine.disconnect(hid)
        self._handlers = []
        self.conversation.detach()
