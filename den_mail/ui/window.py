"""Main window: sidebar | conversation list | conversation, wired to the sync engine."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from gi.repository import Adw, Gio, GLib, Gtk

from .. import APP_NAME, lock, rules, secrets, shortcuts, timing, views
from ..avatars import AvatarService
from ..classify.rules import CATEGORY_NAMES
from ..config import Config, database_path
from ..html.body import find_inline_part
from ..jmap.client import AuthError, JMAPClient, JMAPError
from ..jmap.types import KW_FLAGGED, ROLE_ARCHIVE, ROLE_DRAFTS, ROLE_INBOX, ROLE_JUNK, ROLE_TRASH
from ..llm import Assistant
from ..models.mailbox import MailboxObject, MailboxTree
from ..models.thread import ThreadListModel, ThreadObject
from ..store import actions
from ..store.actions import UndoRecord
from ..store.db import Database
from ..store.sync import (
    SyncEngine,
    build_sort,
    mailbox_query_spec,
    parse_sort,
    search_mailboxes,
    search_query_spec,
)
from .beside import MIN_WINDOW_WIDTH, BesideColumn
from .cleanup import CleanupDialog
from .compose import ComposeWindow
from .conversation import ConversationView
from .identities import IdentitiesDialog
from .labels import MailboxPickerPopover
from .login import LoginPage
from .masked import MaskedEmailDialog
from .message_body import set_cid_resolver
from .newsletters import NewslettersDialog
from .outbox import PendingSend
from .preferences import PreferencesDialog
from .rules import RulesDialog, prompt_sender_rule
from .sidebar import Sidebar
from .thread_window import ThreadWindow
from .threadlist import ThreadList
from .widgets import confirm, secret_entry, text_prompt

log = logging.getLogger(__name__)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, config: Config):
        super().__init__(application=app, title=APP_NAME)
        self.config = config
        win = config.get("window", {})
        self.set_default_size(int(win.get("width", 1400)), int(win.get("height", 900)))
        if win.get("maximized"):
            self.maximize()
        self.client: JMAPClient | None = None
        self.quitting = False   # set by app.quit: closing then really closes (#2)
        self.db: Database | None = None
        self.engine: SyncEngine | None = None
        self.tree = MailboxTree()
        # The sidebar views (#19): local queries, listed between the folders and the labels.
        self.tree.set_views([MailboxObject.for_view(v) for v in views.sidebar_views(bool(config.get("screener")))])
        self.tree.show_views = bool(config.get("sidebar_views", True))
        self._view_ids: list[str] = []      # what the current view lists, in order
        self._view_shown = 0                # how many of them the list has been given
        self._view_search = ""              # a search typed while a view is shown
        self._view_timer = 0
        self.current_mailbox: MailboxObject | None = None
        self.query_key: str | None = None
        self.selected: list[ThreadObject] = []
        self.compose_windows: list[ComposeWindow] = []
        self.pending_sends: list[PendingSend] = []  # counting down behind an Undo toast (#7)
        self._sends_in_flight = 0
        self._after_sends: Callable[[], None] | None = None
        self.thread_windows: list[ThreadWindow] = []
        self.identities: list[dict] = []
        self._pending_select_position: int | None = None
        self.avatars = AvatarService(config)
        self.assistant = Assistant(config)   # the one provider handle every feature shares (#69)
        self.assistant.listeners.append(lambda _a: GLib.idle_add(self._update_status))
        self.tree.color_overrides = {k: int(v) for k, v in (config.get("label_colors", {}) or {}).items()}
        self.sort = dict(config.get("sort", {"key": "newest", "flagged_first": False, "unread_first": False}))
        # The tip under the placeholder moves on with every start.
        config.set("tip_index", int(config.get("tip_index", 0)) + 1)
        # The Inbox tip about Clean up shows for the first few starts, then leaves the user alone.
        self._tip_starts = int(config.get("cleanup_tip_starts", 0)) + 1
        if not config.get("cleanup_opened") and self._tip_starts <= 4:
            config.set("cleanup_tip_starts", self._tip_starts)

        self.toast_overlay = Adw.ToastOverlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toast_overlay.set_child(self.stack)
        self.set_content(self.toast_overlay)
        self.login = LoginPage(self._on_login_submit)
        self.stack.add_named(self.login, "login")
        self.loading = Adw.StatusPage(title="Connecting…")
        self.loading.set_child(Adw.Spinner())
        self.stack.add_named(self.loading, "loading")
        # The lock screen (#28): the mail is hidden until the user proves it is them.
        self.locked = False
        self._before_lock = "main"
        self.lock_page = Adw.StatusPage(icon_name="fm-lock-symbolic", title="Locked",
                                        description="Your mail is hidden until you unlock Den Mail.")
        unlock = Gtk.Button(label="Unlock", halign=Gtk.Align.CENTER)
        unlock.add_css_class("suggested-action")
        unlock.add_css_class("pill")
        unlock.connect("clicked", lambda *_: self.unlock())
        self.lock_page.set_child(unlock)
        self.stack.add_named(self.lock_page, "locked")
        self.idle = lock.IdleTimer(self.lock_idle)
        self.idle.set_minutes(int(config.get("lock_idle_minutes", 0)) if config.get("lock_enabled") else 0)
        self.track_activity(self)
        self._session_subs = lock.watch_session_lock(self.lock_from_session) if config.get("lock_enabled") else []
        self.main: Adw.NavigationSplitView | None = None
        self._install_actions()
        shortcuts.install(self)
        self.connect("close-request", self._on_close_request)

    # ------------------------------------------------------------ account

    def start(self) -> None:
        timing.mark("inbox-start")  # the first mailbox listing closes it (docs/BENCHMARK.md)
        self._timing_pending: str | None = "inbox"
        token = secrets.load_token()
        if not token:
            self.stack.set_visible_child_name("login")
            return
        self.stack.set_visible_child_name("loading")
        self._connect(token, first_login=False)

    def _on_login_submit(self, token: str) -> None:
        self._connect(token, first_login=True)

    def _connect(self, token: str, first_login: bool) -> None:
        client = JMAPClient(token, self.config.session_url)
        account_key = self.config.get("last_account")
        cached = None
        if account_key and not first_login:
            db = Database(database_path(account_key))
            cached = db.get_session()
            if cached:
                try:
                    client.load_session(cached)
                except ValueError:
                    cached = None
            if cached:
                self._start_engine(client, db, token, first_login=False)
                return
            db.close()

        def work() -> None:
            try:
                client.fetch_session()
            except AuthError:
                GLib.idle_add(self._login_failed, "That token was rejected. Check that it has the Mail scope.")
                return
            except JMAPError as e:
                GLib.idle_add(self._login_failed, f"Could not reach Fastmail: {e}")
                return
            GLib.idle_add(self._session_ready, client, token, first_login)

        threading.Thread(target=work, daemon=True).start()

    def _login_failed(self, message: str) -> bool:
        self.stack.set_visible_child_name("login")
        self.login.show_error(message)
        return False

    def _session_ready(self, client: JMAPClient, token: str, first_login: bool) -> bool:
        session = client.session
        if first_login:
            secrets.store_token(token)
        self.config.set("last_account", session.username)
        db = Database(database_path(session.username))
        db.set_session(session.raw)
        self._start_engine(client, db, token, first_login)
        return False

    def _start_engine(self, client: JMAPClient, db: Database, token: str, first_login: bool) -> None:
        self.client = client
        self.db = db
        self.engine = SyncEngine(client, db, self.config)
        self.tree.update(db.get_mailboxes())
        self.identities = db.get_identities()
        self._build_main()
        e = self.engine
        e.connect("mailboxes-changed", self._on_mailboxes_changed)
        e.connect("query-updated", self._on_query_updated)
        e.connect("query-failed", lambda _e, key, msg: self._toast(f"Could not load: {msg}"))
        e.connect("emails-changed", self._on_emails_changed)
        e.connect("emails-destroyed", self._on_emails_changed)
        e.connect("sync-status", self._on_sync_status)
        e.connect("push-status", lambda _e, on: self._update_status())
        e.connect("new-mail", self._on_new_mail)
        e.connect("action-failed", lambda _e, msg: self._toast(msg, 6))
        e.connect("auth-failed", lambda _e: self._auth_failed())
        e.connect("cache-reset", lambda _e: self._reload_current())
        e.connect("identities-changed", lambda _e: setattr(self, "identities", self.db.get_identities()))
        e.connect("rules-applied", lambda _e, hits: rules.bump_hits(self.config, hits))
        # Contact photos (#14) and completion (#4) come from the address book in the cache.
        self.avatars.contact_photo = self.db.contact_photo_for
        self.avatars.download_blob = e.fetch_blob
        e.connect("contacts-changed", lambda _e: self._on_contacts_changed())
        e.connect("outbox-changed", lambda _e: self._update_status())
        set_cid_resolver(self._resolve_cid)
        e.start()
        self.stack.set_visible_child_name("main")
        timing.mark("session-ready")
        self._on_mailboxes_changed(e)

    def _auth_failed(self) -> None:
        self._toast("Fastmail rejected the API token. Please sign in again.", 8)
        self.sign_out(clear=True)

    def sign_out(self, clear: bool = False) -> None:
        if self.engine:
            self.engine.stop()
        secrets.clear_token()
        self.config.set("last_account", None)
        self.engine = None
        self.client = None
        if self.db:
            if clear:
                self.db.clear_all()
            self.db.close()
            self.db = None
        if self.main:
            self.stack.remove(self.main)
            self.main = None
        self.stack.set_visible_child_name("login")

    # --------------------------------------------------------------- UI

    def _build_main(self) -> None:
        primary = Gio.Menu()
        section = Gio.Menu()
        section.append("Clean up…", "win.cleanup")
        section.append("Newsletters…", "win.newsletters")
        section.append("Rules…", "win.rules")
        primary.append_section("Inbox", section)
        section = Gio.Menu()
        section.append("Masked Email…", "win.masked")
        section.append("Identities & Aliases…", "win.identities")
        primary.append_section("Account", section)
        section = Gio.Menu()
        section.append("Lock", "win.lock")
        section.append("Preferences", "win.preferences")
        section.append("Keyboard Shortcuts", "win.shortcuts")
        section.append("About Den Mail", "app.about")
        section.append("Quit", "app.quit")
        primary.append_section(None, section)

        self.sidebar = Sidebar(self.tree, self.client.session.account_name if self.client and self.client.session else "",
                               self._on_select_mailbox, self._on_drop, primary)
        self.sidebar.on_new_label = self.new_label
        self.sidebar.on_rename = self.rename_label
        self.sidebar.on_delete = self.delete_label
        self.sidebar.on_mark_read = self.mark_all_read
        self.sidebar.on_empty = self.empty_mailbox
        self.sidebar.on_color = self.set_label_color
        self.sidebar.on_refresh = self.refresh_mailbox
        self.tree.color_overrides = {k: int(v) for k, v in (self.config.get("label_colors", {}) or {}).items()}

        self.model = ThreadListModel(self.db)
        self.model.label_namer = self._label_names
        self.threadlist = ThreadList(self.model, self._on_selection, self._on_activate_thread, self._on_search,
                                     self._on_load_more, lambda: self.engine.sync_now(), avatars=self.avatars)
        self.threadlist.set_sort(self.sort["key"], self.sort["flagged_first"], self.sort["unread_first"],
                                 self._group_mode())
        self.threadlist.unread_button.set_active(bool(self.config.get("unread_only", False)))
        self.threadlist.on_unread_filter = self._on_unread_filter
        category = self.config.get("category_filter") or None
        self.model.set_category_filter(category)
        self.threadlist.set_category_filter(category)
        self.threadlist.on_category_filter = self._on_category_filter
        self.threadlist.set_grouped(self._group_mode())
        self.threadlist.on_sort_changed = self._on_sort_changed
        self.threadlist.on_context_menu = self._on_thread_context_menu
        self.conversation = ConversationView(self.db, self.engine, self.tree, self.config, self._compose_from,
                                             self._email_action, avatars=self.avatars, assistant=self.assistant)
        self.conversation.on_remove_label = lambda mid: self._label_toggle(self.tree.get(mid), False)
        self.conversation.on_add_label = lambda mid: self._label_toggle(self.tree.get(mid), True)
        self.conversation.screener_check = self._screener_pending
        self.conversation.on_screener_decision = self.screener_decide
        self.conversation.on_cancel_scheduled = self.cancel_scheduled
        self.labels_popover = MailboxPickerPopover(self.tree, "labels",
                                                   on_toggle=self._label_toggle,
                                                   on_create=self._create_label_and_apply)
        self.conversation.labels_button.set_popover(self.labels_popover)
        self.conversation.labels_button.connect("notify::active", self._on_labels_button)
        self.move_popover = MailboxPickerPopover(self.tree, "move", on_pick=self._move_to)
        self.conversation.move_button.set_popover(self.move_popover)
        self.conversation.move_button.connect("notify::active", lambda b, _p: self.move_popover._rebuild() if b.get_active() else None)

        # The reading pane: the conversation the list drives, and beside it, on a wide
        # window, a second one pinned with "Open beside" (#35).
        self.beside = BesideColumn(self)
        self.reading = Gtk.Box(spacing=0)
        self.reading.append(self.conversation)
        self.reading.append(self.beside)
        self.conversation.set_hexpand(True)
        reading_page = Adw.NavigationPage(child=self.reading, title="Conversation", tag="reading")
        self.inner = Adw.NavigationSplitView(sidebar=self.threadlist, content=reading_page,
                                             min_sidebar_width=300, max_sidebar_width=520,
                                             sidebar_width_fraction=0.36)
        inner_page = Adw.NavigationPage(child=self.inner, title="Mail", tag="mail")
        self.main = Adw.NavigationSplitView(sidebar=self.sidebar, content=inner_page, min_sidebar_width=200,
                                            max_sidebar_width=300, sidebar_width_fraction=0.2)
        self.stack.add_named(self.main, "main")
        # The panes' minimum widths are sidebar 200, thread list 300 and conversation
        # ~412 (its header bar), measured with the autopilot "measure" command.  Three
        # panes therefore need 912sp, so the sidebar folds away below 920sp and a
        # half-screen tile on a 1920px display still shows all three; below 720sp the
        # thread list and the conversation share one pane too (#27).
        # When the sidebar folds away, show the mail, not the sidebar: a mailbox is
        # always selected and the back button leads to the sidebar.
        bp1 = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 920sp"))
        bp1.add_setter(self.main, "collapsed", True)
        bp1.add_setter(self.main, "show-content", True)
        bp2 = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        bp2.add_setter(self.main, "collapsed", True)
        bp2.add_setter(self.main, "show-content", True)
        bp2.add_setter(self.inner, "collapsed", True)
        self.add_breakpoint(bp1)
        self.add_breakpoint(bp2)
        # Too narrow for two conversations: the pinned one goes (a thread window is the fallback).
        bp3 = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f"max-width: {self._beside_min_width() - 1}sp"))
        bp3.connect("apply", lambda *_: self.beside.close())
        self.add_breakpoint(bp3)

    def _install_actions(self) -> None:
        specs = {
            "compose": lambda: self.compose("new"),
            "reply": lambda: self._compose_from("reply", self.conversation.latest_email_id()),
            "reply-all": lambda: self._compose_from("reply-all", self.conversation.latest_email_id()),
            "forward": lambda: self._compose_from("forward", self.conversation.latest_email_id()),
            "archive": lambda: self._selection_action("archive"),
            "trash": lambda: self._selection_action("trash"),
            "junk": lambda: self._selection_action("junk"),
            "not-junk": lambda: self._selection_action("not-junk"),
            "flag": lambda: self._selection_action("flag"),
            "mark-read": lambda: self._selection_action("mark-read"),
            "mark-unread": lambda: self._selection_action("mark-unread"),
            "delete-permanently": lambda: self._selection_action("destroy"),
            "labels": lambda: self.conversation.labels_button.popup(),
            "move": lambda: self.conversation.move_button.popup(),
            "search": lambda: self.threadlist.focus_search(),
            "refresh": lambda: self.engine and self.engine.sync_now(),
            "select-all": lambda: self.threadlist.select_all(),
            "next": lambda: self._move_selection(1),
            "previous": lambda: self._move_selection(-1),
            "open": lambda: self.selected and self._on_activate_thread(self.selected[-1]),
            "open-beside": self.open_beside,
            "back": self._go_back,
            "masked": lambda: MaskedEmailDialog(self.engine, self.db).present(self),
            "newsletters": lambda: NewslettersDialog(self.engine, self.db, self.config, self._after_action).present(self),
            "rules": lambda: RulesDialog(self.engine, self.db, self.config, self.tree).present(self),
            "cleanup": self.show_cleanup,
            "identities": lambda: IdentitiesDialog(self.engine, self.db, self.config).present(self),
            "preferences": self.show_preferences,
            "lock": self.lock,
            "preferences-inbox": lambda: self.show_preferences("inbox"),
            "preferences-assistant": lambda: self.show_preferences("assistant"),
            "summarise": lambda: self.conversation.summarise(),
            "shortcuts": self.show_shortcuts,
            "goto-inbox": lambda: self._goto_role(ROLE_INBOX),
            "goto-drafts": lambda: self._goto_role(ROLE_DRAFTS),
        }
        for name, fn in specs.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=fn: self.engine and fn())
            self.add_action(action)
        self.lookup_action("lock").set_enabled(bool(self.config.get("lock_enabled")))   # off until enabled (#65)
        # Parameterised actions used by the conversation context menu.
        for name, fn in (
            ("find-sender", self._find_sender),
            ("sender-rule", self.sender_rule),
            ("screen-allow", lambda addr: self.screener_decide(addr, True)),
            ("categorise", self.categorise),
            ("screen-block", lambda addr: self.screener_decide(addr, False)),
            ("toggle-label", lambda mid: self._label_toggle(self.tree.get(mid), not self._selection_has_label(mid))),
            ("move-to", lambda mid: self.tree.get(mid) and self._move_to(self.tree.get(mid))),
        ):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", lambda _a, p, fn=fn: self.engine and fn(p.get_string()))
            self.add_action(action)

    # ------------------------------------------------------ context menu

    def _selection_has_label(self, mailbox_id: str) -> bool:
        return all(mailbox_id in t.summary.mailbox_ids for t in self.selected) if self.selected else False

    def _find_sender(self, email: str) -> None:
        self.threadlist.scope.set_selected(1)
        self.threadlist.focus_search()
        self.threadlist.search_entry.set_text(f"from:{email}")

    # ------------------------------------------------------------ screener

    def _screener_pending(self, sender: str) -> bool:
        return bool(self.config.get("screener")) and self.db is not None and self.db.screener_decision(sender) == "pending"

    def _sync_screened(self) -> None:
        """The Inbox hides the threads of senders the screener holds (#24)."""
        mb = self.current_mailbox
        inbox = mb is not None and mb.role == ROLE_INBOX and not self.threadlist.search_active
        pending = self.db.screener_pending() if inbox and self.config.get("screener") else set()
        self.model.set_screened(pending)

    def screener_decide(self, sender: str, allow: bool) -> None:
        """"Let through" puts the sender's mail in the Inbox; "Screen out" archives it, now and
        by a rule from now on (#24, #22)."""
        sender = sender.strip().lower()
        if not sender:
            return
        self.db.screener_set([sender], "allow" if allow else "block")
        if allow:
            self._toast(f"{sender} reaches the Inbox from now on")
        else:
            rule = rules.add_rule(self.config, rules.Rule("sender", sender, "archive"))
            self.engine.act_on_sender(sender, lambda ids: rules.combine(ids, [rule], self.engine.roles),
                                      self._after_action)
            self._toast(f"Mail from {sender} is archived from now on (a rule; see Rules…)")
        self.conversation.screener_bar.set_visible(False)
        self._schedule_view_refresh()

    def set_screener(self, on: bool) -> None:
        """The Preferences switch: the Screener view comes and goes with it."""
        self.tree.set_views([MailboxObject.for_view(v) for v in views.sidebar_views(on)])
        self.tree.refresh()
        if not on and self.current_mailbox is not None and self.current_mailbox.id == views.SCREENER:
            self._goto_role(ROLE_INBOX)
        self._sync_screened()
        self._update_held()
        self._schedule_view_refresh()

    def cancel_scheduled(self, email_id: str) -> None:
        """Take a scheduled message back (#6): it returns to Drafts unsent."""
        if not email_id:
            return
        self.engine.cancel_scheduled(email_id, lambda: self._toast("Send cancelled; the message is back in Drafts"),
                                     lambda m: self._toast(f"Could not cancel: {m}", 6))
        self.conversation.scheduled_bar.set_visible(False)

    def sender_rule(self, sender: str) -> None:
        """"Always for this sender…" (#22): store a rule, optionally run it over their mail now."""
        def done(rule: rules.Rule, apply_now: bool) -> None:
            self._toast(f"Rule added: {rule.describe_match()} → {rule.describe_action()}")
            if apply_now:
                self.engine.act_on_sender(sender, lambda ids: rules.combine(ids, [rule], self.engine.roles),
                                          self._after_action)

        prompt_sender_rule(self, self.tree, self.config, sender, done)

    def categorise(self, category: str) -> None:
        """"Categorise as…" (#23): the user's word on the selected conversations, kept over
        the rules and used to train the learned layer."""
        ids = self._selected_email_ids()
        if not ids or category not in CATEGORY_NAMES:
            return
        self.db.set_category(ids, category)
        self.model.refresh_threads(ids)
        for t in self.selected:
            if t.thread_id == self.conversation.thread_id:
                self.conversation.refresh_thread(t)
        self.engine.retrain_bayes()
        self._schedule_view_refresh()
        self._toast(f"Sorted into {CATEGORY_NAMES[category]}; the app learns from it")

    def _on_thread_context_menu(self, thread: ThreadObject, x: int, y: int) -> None:
        threads = self.selected or [thread]
        many = len(threads) > 1
        sender = thread.summary.from_addresses[0] if thread.summary.from_addresses else {}
        menu = Gio.Menu()
        top = Gio.Menu()
        if not many:
            top.append("Open in new window", "win.open")
            top.append("Open beside", "win.open-beside")
            if sender.get("email"):
                item = Gio.MenuItem.new(f"Find all conversations with {sender.get('name') or sender['email']}", None)
                item.set_action_and_target_value("win.find-sender", GLib.Variant("s", sender["email"]))
                top.append_item(item)
                item = Gio.MenuItem.new("Always for this sender…", None)
                item.set_action_and_target_value("win.sender-rule", GLib.Variant("s", sender["email"]))
                top.append_item(item)
                if self._screener_pending(sender["email"]):
                    for label, name in (("Let through", "win.screen-allow"), ("Screen out", "win.screen-block")):
                        item = Gio.MenuItem.new(label, None)
                        item.set_action_and_target_value(name, GLib.Variant("s", sender["email"]))
                        top.append_item(item)
        menu.append_section(None, top)
        if not many:
            reply = Gio.Menu()
            reply.append("Reply", "win.reply")
            reply.append("Reply to all", "win.reply-all")
            reply.append("Forward", "win.forward")
            menu.append_section(None, reply)
        organise = Gio.Menu()
        organise.append("Archive", "win.archive")
        organise.append("Delete", "win.trash")
        labels = Gio.Menu()
        for mb in self.tree.labels():
            has = self._selection_has_label(mb.id)
            item = Gio.MenuItem.new(("● " if has else "   ") + "    " * mb.depth + mb.name, None)
            item.set_action_and_target_value("win.toggle-label", GLib.Variant("s", mb.id))
            labels.append_item(item)
        organise.append_submenu("Labels", labels)
        move = Gio.Menu()
        for mb in self.tree.all():
            item = Gio.MenuItem.new("    " * mb.depth + mb.name, None)
            item.set_action_and_target_value("win.move-to", GLib.Variant("s", mb.id))
            move.append_item(item)
        organise.append_submenu("Move to", move)
        categories = Gio.Menu()
        current = {t.category for t in threads}
        for cat, name in CATEGORY_NAMES.items():
            item = Gio.MenuItem.new(("● " if current == {cat} else "   ") + name, None)
            item.set_action_and_target_value("win.categorise", GLib.Variant("s", cat))
            categories.append_item(item)
        organise.append_submenu("Categorise as", categories)
        menu.append_section(None, organise)
        state = Gio.Menu()
        unread = any(t.unread for t in threads)
        state.append("Mark as read" if unread else "Mark as unread", "win.mark-read" if unread else "win.mark-unread")
        flagged = all(t.flagged for t in threads)
        state.append("Unflag" if flagged else "Flag", "win.flag")
        menu.append_section(None, state)
        danger = Gio.Menu()
        if self.current_mailbox and self.current_mailbox.role == ROLE_JUNK:
            danger.append("Not spam", "win.not-junk")
        else:
            danger.append("Report spam", "win.junk")
        if self.current_mailbox and self.current_mailbox.role == ROLE_TRASH:
            danger.append("Delete permanently", "win.delete-permanently")
        menu.append_section(None, danger)
        self.threadlist.popup_menu(menu, x, y)

    # ------------------------------------------------------- engine events

    def _on_mailboxes_changed(self, _engine) -> None:
        self.tree.update(self.db.get_mailboxes())
        self.sidebar.apply_expansion()
        self._schedule_view_refresh()
        if self.current_mailbox is None or self.tree.get(self.current_mailbox.id) is None:
            inbox = self.tree.by_role(ROLE_INBOX)
            if inbox is not None:
                self.sidebar.select_mailbox(inbox.id)
        else:
            self._update_list_title()

    def _on_query_updated(self, _engine, key: str) -> None:
        if key != self.query_key:
            return
        q = self.db.get_query(key)
        if not q:
            return
        self._timing_listed()
        ids = list(q["ids"])
        if self.threadlist.unread_only and self.selected:
            ids = self._keep_selected(ids)
        self._apply_ids(ids, q["total"], q["complete"])

    def _apply_ids(self, ids: list[str], total: int | None, complete: bool) -> None:
        """Give the list a query's (or a view's) representative email ids."""
        selected_ids = {t.thread_id for t in self.selected}
        self.model.loading = False
        self.model.set_email_ids(ids, total, complete)
        self._update_list_title()
        if self._pending_select_position is not None:
            pos = min(self._pending_select_position, self.model.get_n_items() - 1)
            self._pending_select_position = None
            if pos >= 0:
                self.threadlist.select_position(pos, step=1)
        elif selected_ids and not any(t in self.model.by_thread for t in selected_ids):
            self.conversation.clear()

    def _timing_listed(self) -> None:
        if self._timing_pending:
            timing.mark(f"{self._timing_pending}-listed")
            self._timing_pending = None

    def _keep_selected(self, ids: list[str]) -> list[str]:
        """With the unread filter on, reading a conversation would drop it from the list
        mid-read; the selected ones stay where they were until the user moves on."""
        present = {e["threadId"] for e in self.db.get_emails(ids).values()}
        for t in self.selected:
            if t.thread_id not in present and t.email_ids:
                pos = self.model.threads.index(t) if t in self.model.threads else len(ids)
                ids.insert(min(pos, len(ids)), t.email_ids[-1])
        return ids

    def _on_emails_changed(self, _engine, ids: list[str]) -> None:
        self.model.refresh_threads(ids)
        for t in self.selected:
            if t.thread_id == self.conversation.thread_id:
                self.conversation.refresh_thread(t)
        self._schedule_view_refresh()

    # ------------------------------------------------------------- views

    def _schedule_view_refresh(self) -> None:
        """Views are answered from the cache, so every change to it may move their
        counts and, for the one on screen, its rows; coalesced, as changes come in bursts."""
        if not self._view_timer and self.engine and (self.tree.show_views or self.config.get("screener")):
            self._view_timer = GLib.timeout_add(300, self._refresh_views)

    def _update_held(self) -> None:
        """The Inbox badge leaves out what the screener holds (#62); the Screener view counts it."""
        inbox = self.tree.by_role(ROLE_INBOX)
        if inbox is None or self.db is None:
            return
        inbox.set_held(self.db.screener_held_unread(inbox.id) if self.config.get("screener") else 0)

    def _refresh_views(self) -> bool:
        self._view_timer = 0
        if not self.engine or not self.db:
            return False
        self._sync_screened()
        self._update_held()
        for view_id, (total, unread) in views.all_counts(self.db, self.engine.trash_junk_ids()).items():
            obj = self.tree.get(view_id)
            if obj is not None:
                if obj.total != total:
                    obj.total = total
                if obj.unread != unread:
                    obj.unread = unread
        mb = self.current_mailbox
        if mb is not None and mb.is_view:
            ids = self._query_view(mb)
            if ids != self._view_ids:
                self._view_ids = ids
                self._show_view()
            else:
                self._update_list_title()
        return False

    def _query_view(self, mb: MailboxObject) -> list[str]:
        view = views.get_view(mb.id)
        s = self._sort_for_mailbox(mb)
        return views.list_ids(self.db, view, self.engine.trash_junk_ids(), s.get("key", "newest"),
                              bool(s.get("flagged_first")), bool(s.get("unread_first")),
                              unread_only=self.threadlist.unread_only, search=self._view_search)

    def _load_view(self, mb: MailboxObject, search: str = "") -> None:
        """Show a view: no server query, the cache answers at once (#19)."""
        if self.query_key:
            self.engine.release_query(self.query_key)
            self.query_key = None
        self.model.mailbox_id = None   # rows aggregate the whole thread, as an all-mail search does
        self.model.trash_junk = set(self.engine.trash_junk_ids())
        self.model.loading = True
        self.model.clear()
        self.model.set_screened(set())
        self.selected = []   # else _keep_selected would carry the last mailbox's thread into the view
        self.conversation.clear()
        self._view_search = search
        self._set_empty_text()
        s = self._sort_for_mailbox(mb)
        self.threadlist.set_sort(s["key"], bool(s["flagged_first"]), bool(s["unread_first"]))
        self._view_ids = self._query_view(mb)
        self._view_shown = self.engine.page_size
        self._show_view()
        self._timing_listed()
        self.threadlist.scroll_to_top()

    def _show_view(self) -> None:
        """Hand the list the first `_view_shown` rows of the view, the rest on scrolling."""
        ids = self._view_ids[:self._view_shown]
        if self.selected:
            ids = self._keep_selected(ids)   # a row that stopped matching stays while it is open
        self._apply_ids(ids, len(self._view_ids), len(ids) >= len(self._view_ids))

    def mark_all_read(self, mb: MailboxObject) -> None:
        if not mb.is_view:
            self.engine.mark_mailbox_read(mb.id)
            return
        ids = views.list_ids(self.db, views.get_view(mb.id), self.engine.trash_junk_ids(),
                             unread_only=True, collapse=False)
        if ids:
            self.engine.perform(actions.mark_read(ids, True))

    def set_sidebar_views(self, on: bool) -> None:
        """The Preferences switch: show or hide the Views section."""
        self.tree.show_views = on
        self.tree.refresh()
        if on:
            self._schedule_view_refresh()
        elif self.current_mailbox is not None and self.current_mailbox.is_view:
            self._goto_role(ROLE_INBOX)

    def _on_contacts_changed(self) -> None:
        self.avatars.forget_contacts()
        for row in list(self.threadlist._rows):
            row.refresh_avatar()
        for card in self.conversation.cards.values():
            card.refresh_avatar()

    def _on_sync_status(self, _engine, status: str, message: str) -> None:
        self.threadlist.set_syncing(status == "syncing")
        self._status_message = message
        self._update_status()

    def _update_status(self) -> None:
        if not self.engine:
            return
        msg = getattr(self, "_status_message", "")
        queued = self.engine.outbox_count()
        waiting = f"{queued} change{'s' if queued != 1 else ''} waiting" if queued else ""
        if msg:
            text = msg
        elif not self.engine.online:
            text = f"Offline — {waiting}, sent when back" if waiting else "Offline — showing cached mail"
        elif self.engine.push_connected:
            text = "Connected (push)"
        else:
            text = "Connected (polling)"
        assistant = self.assistant.status()
        self.sidebar.set_status(f"{text} · {assistant}" if assistant else text)

    def _on_new_mail(self, _engine, emails: list[dict]) -> None:
        if not self.config.get("notify_new_mail", True) or self.is_active():
            return
        for e in emails[:5]:
            sender = (e.get("from") or [{}])[0]
            # Wait briefly for the sender's logo so the notification can carry it.
            self.avatars.when_ready(sender.get("email"), lambda path, e=e, s=sender: self._notify(e, s, path))

    def _notify(self, e: dict, sender: dict, icon_path) -> None:
        app = self.get_application()
        if app is None:
            return
        if self.locked:   # nothing about the message while the mail is hidden
            n = Gio.Notification.new("New mail")
            n.set_default_action("app.activate")
            app.send_notification(f"mail-{e['id']}", n)
            return
        n = Gio.Notification.new(sender.get("name") or sender.get("email") or "New mail")
        n.set_body(e.get("subject") or "(no subject)")
        if icon_path is not None:
            n.set_icon(Gio.FileIcon.new(Gio.File.new_for_path(str(icon_path))))
        n.set_default_action_and_target("app.open-mail", GLib.Variant("s", e["id"]))
        app.send_notification(f"mail-{e['id']}", n)

    def open_mail(self, email_id: str) -> None:
        """A notification was clicked (#93): show that message's conversation, in the list
        when it is there, else in a thread window."""
        if not self.db or not self.engine or self.locked:
            return
        thread_id = self.db.thread_of_email(email_id)
        if not thread_id:
            return
        if thread_id in self.model.by_thread and not self.inner.get_collapsed():
            self.threadlist.select_thread(thread_id)
        else:
            self.open_thread_by_id(thread_id)

    def _resolve_cid(self, email_id: str, cid: str, done) -> None:
        body = self.db.get_email_body(email_id) if self.db else None
        att = find_inline_part(body or {}, cid)
        if att is None:
            done(None, None)
            return
        self.engine.fetch_blob(att["blobId"], att.get("name") or "inline", att.get("type"),
                               lambda p: done(p.read_bytes(), att.get("type")), lambda _m: done(None, None))

    # ---------------------------------------------------------- navigation

    def _on_select_mailbox(self, mb: MailboxObject) -> None:
        if self.current_mailbox is not None:  # the first selection belongs to the start-up pair
            timing.mark("switch-start")
            self._timing_pending = "switch"
        self.current_mailbox = mb
        log.debug("select mailbox %s", mb.name)
        self.threadlist.search_entry.set_text("")
        self.threadlist.search_bar.set_search_mode(False)
        self.threadlist.set_scope_label("This view" if mb.is_view else "This mailbox")
        self._load_mailbox(mb)
        self._update_banner()
        if self.main.get_collapsed():
            self.main.set_show_content(True)

    def _load_mailbox(self, mb: MailboxObject) -> None:
        if mb.is_view:
            self._load_view(mb)
            return
        if self.query_key:
            self.engine.release_query(self.query_key)
        self.model.mailbox_id = mb.id
        self.model.trash_junk = set(self.engine.trash_junk_ids())
        self.model.loading = True
        self.model.clear()
        self.selected = []   # GTK reports no selection change for an emptied model
        self._sync_screened()
        self.conversation.clear()
        self._set_empty_text()
        s = self._sort_for_mailbox(mb)
        self.threadlist.set_sort(s["key"], bool(s["flagged_first"]), bool(s["unread_first"]))
        self.query_key = self.engine.load_query(self._mailbox_query(mb))
        cached = self.db.get_query(self.query_key)
        if cached:
            self.model.set_email_ids(cached["ids"], cached["total"], cached["complete"])
            self._timing_listed()  # the list is usable now; the server's answer only refreshes it
        self._update_list_title()
        self.threadlist.scroll_to_top()

    def _mailbox_query(self, mb: MailboxObject) -> dict:
        return mailbox_query_spec(mb.id, self._current_sort(), self.threadlist.unread_only)

    def _reload_current(self) -> None:
        if self.current_mailbox:
            self._load_mailbox(self.current_mailbox)

    def _on_unread_filter(self, active: bool) -> None:
        self.config.set("unread_only", active)
        if not self.engine:
            return
        if self.threadlist.search_active:
            self.threadlist._fire_search()
        else:
            self._reload_current()

    def _on_category_filter(self, category: str | None) -> None:
        """Categories are local (#18), so the loaded list is filtered in place."""
        self.config.set("category_filter", category)
        self.model.set_category_filter(category)
        self._set_empty_text()
        self._update_list_title()
        self.threadlist._fill_filtered_list()
        if self.selected and not any(t.thread_id in self.model.by_thread and t in self.model.threads
                                     for t in self.selected):
            self.conversation.clear()

    def _set_empty_text(self) -> None:
        mb = self.current_mailbox
        where = mb.name if mb else "this list"
        category = self.model.category_filter
        if mb is not None and mb.is_view and not category and not self.threadlist.unread_only:
            if self._view_search:
                self.threadlist.set_empty_text("No results", f"Nothing in {where} matches the search.")
            else:
                self.threadlist.set_empty_text(f"Nothing in {where}", views.get_view(mb.id).empty)
        elif category:
            name = CATEGORY_NAMES.get(category, category)
            self.threadlist.set_empty_text(f"No {name} conversations",
                                           f"Nothing in {where} was sorted into {name}.")
        elif self.threadlist.unread_only:
            self.threadlist.set_empty_text("No unread conversations", f"Everything in {where} is read.")
        else:
            self.threadlist.set_empty_text("No conversations", f"{where} is empty.")

    def _update_list_title(self) -> None:
        category = self.model.category_filter
        if category:
            # the category is local, so the subtitle counts what is on screen
            shown = len(self.model.threads)
            sub = f"{CATEGORY_NAMES.get(category, category)} · {shown} shown"
            if not self.model.complete:
                sub += " so far"
            title = "Search" if self.threadlist.search_entry.get_text().strip() else (
                "All mail" if self.threadlist.search_active else
                ((self.tree.get(self.current_mailbox.id) or self.current_mailbox).name if self.current_mailbox else ""))
            self.threadlist.set_title(title, sub)
            return
        if self.threadlist.search_active:
            total = self.model.total
            if self.threadlist.search_entry.get_text().strip():
                self.threadlist.set_title("Search", f"{total} result{'s' if total != 1 else ''}")
            else:
                self.threadlist.set_title("All mail", f"{total} conversations")
            return
        mb = self.current_mailbox
        if mb is None:
            return
        live = self.tree.get(mb.id) or mb
        sub = f"{live.total} conversations" if live.total else ""
        if self.threadlist.unread_only:
            sub = f"{live.unread} unread" if live.unread else "No unread"
        elif live.unread and live.role not in (ROLE_TRASH, ROLE_JUNK):
            sub = f"{live.unread} unread · {sub}" if sub else f"{live.unread} unread"
        if self.model.hidden_by_screener:
            sub = f"{sub} · {self.model.hidden_by_screener} screened" if sub else f"{self.model.hidden_by_screener} screened"
        self.threadlist.set_title(live.name, sub)

    def _on_search(self, text: str, scope: str) -> None:
        if text:
            timing.mark("search-start")
            self._timing_pending = "search"
        if not text and scope == "mailbox":
            if self.current_mailbox:
                self._load_mailbox(self.current_mailbox)
                self._update_banner()
            return
        self._update_banner()
        if scope == "mailbox" and self.current_mailbox is not None and self.current_mailbox.is_view:
            self._load_view(self.current_mailbox, text)   # the view is local, so is a search within it
            return
        # An empty query over all mail lists everything outside Trash and Spam,
        # which is how "group by sender" works across the whole account.
        if self.query_key:
            self.engine.release_query(self.query_key)
        mailbox_id = self.current_mailbox.id if (scope == "mailbox" and self.current_mailbox) else None
        mailboxes = self.db.get_mailboxes()
        named, unknown, everywhere = search_mailboxes(text, mailboxes)
        if named or everywhere:
            # `in:`/`label:` replaces the folder scope; a single named mailbox scopes the thread rows too
            mailbox_id = named[0] if len(named) == 1 else None
        self.model.mailbox_id = mailbox_id
        self.model.trash_junk = set(self.engine.trash_junk_ids())
        self.conversation.clear()
        if unknown:
            self.query_key = None
            self.model.loading = False
            self.model.clear()
            self.threadlist.set_empty_text(f"No label called “{unknown[0]}”",
                                           "A search can name the labels and folders in the sidebar.")
            self.threadlist.set_title("Search", "0 results")
            return
        self.model.loading = True
        self.model.clear()
        self.threadlist.set_empty_text("No results", "Try different words, or search all mail.")
        self.query_key = self.engine.load_query(search_query_spec(text, None if named or everywhere else mailbox_id,
                                                                  self.engine.trash_junk_ids(), self._current_sort(),
                                                                  self.threadlist.unread_only, mailboxes=mailboxes))
        self.threadlist.set_title("All mail" if not text else "Search", "Loading…" if not text else "Searching…")
        self.threadlist.scroll_to_top()

    def _sort_for_mailbox(self, mb: MailboxObject | None) -> dict:
        """Per-mailbox sort: local override, else Fastmail's own per-mailbox `sort`, else the global choice."""
        if mb is not None:
            override = (self.config.get("mailbox_sort", {}) or {}).get(mb.id)
            if override:
                return override
            if mb.data.get("sort"):
                key, flagged, unread = parse_sort(mb.data["sort"])
                return {"key": key, "flagged_first": flagged, "unread_first": unread}
        return self.sort

    def _current_sort(self) -> list[dict]:
        s = self._sort_for_mailbox(self.current_mailbox if not self.threadlist.search_active else None)
        return build_sort(s.get("key", "newest"), bool(s.get("flagged_first")), bool(s.get("unread_first")))

    def _group_mode(self) -> str:
        mode = self.config.get("group_by_sender", "off")
        if mode is True:
            return "sender"
        return mode if mode in ("off", "sender", "domain") else "off"

    def _on_sort_changed(self, key: str, flagged_first: bool, unread_first: bool, group: str = "off") -> None:
        if group != self._group_mode():
            # Only the grouping toggle changed: a global view setting, applied client-side.
            self.config.set("group_by_sender", group)
            self.threadlist.set_grouped(group)
            self.threadlist.scroll_to_top()
            return
        choice = {"key": key, "flagged_first": flagged_first, "unread_first": unread_first}
        if self.current_mailbox and not self.threadlist.search_active:
            overrides = dict(self.config.get("mailbox_sort", {}) or {})
            overrides[self.current_mailbox.id] = choice
            self.config.set("mailbox_sort", overrides)
            if not self.current_mailbox.is_view:
                # Fastmail keeps a per-mailbox sort; try to share it with the web client (ignored if rejected).
                self.engine.mailbox_set(update={self.current_mailbox.id: {"sort": build_sort(key, flagged_first, unread_first)}},
                                        on_error=lambda m: log.info("per-mailbox sort not accepted by server: %s", m))
        else:
            self.sort = choice
            self.config.set("sort", choice)
        if self.threadlist.search_active:
            self.threadlist._fire_search()
        elif self.current_mailbox:
            self._load_mailbox(self.current_mailbox)

    def _on_load_more(self) -> None:
        if self.current_mailbox is not None and self.current_mailbox.is_view:
            if self._view_shown < len(self._view_ids):
                self._view_shown += self.engine.page_size
                self._show_view()
        elif self.query_key:
            self.engine.load_more(self.query_key)

    def _goto_role(self, role: str) -> None:
        mb = self.tree.by_role(role)
        if mb:
            self.sidebar.select_mailbox(mb.id)

    def _go_back(self) -> None:
        if self.inner.get_collapsed() and self.inner.get_show_content():
            self.inner.set_show_content(False)
        elif self.main.get_collapsed() and self.main.get_show_content():
            self.main.set_show_content(False)

    # ---------------------------------------------------------- selection

    def _on_selection(self, threads: list[ThreadObject]) -> None:
        self.selected = threads
        if len(threads) == 1:
            t = threads[0]
            timing.mark("open-start")
            self.conversation.show_thread(t, self.model.mailbox_id)
            if self.config.get("mark_read_on_open", True):
                unread = self.conversation.unread_email_ids()
                if unread:
                    self.engine.perform(actions.mark_read(unread, True))
        elif threads:
            self.conversation.show_multi(len(threads))
        else:
            self.conversation.clear()

    def _on_activate_thread(self, thread: ThreadObject) -> None:
        if thread.is_draft and self.current_mailbox and self.current_mailbox.role == ROLE_DRAFTS:
            drafts = self.conversation.draft_email_ids() or thread.email_ids
            self._compose_from("draft", drafts[-1])
            return
        if self.inner.get_collapsed():
            self.inner.set_show_content(True)
            return
        self.open_thread_window(thread)

    def open_thread_by_id(self, thread_id: str) -> None:
        """A thread named by id (the cleanup dialog's message rows), in its own window."""
        summary = self.db.thread_summary(thread_id, None, set(self.engine.trash_junk_ids()))
        if summary is not None:
            self.open_thread_window(ThreadObject(summary))

    def _beside_min_width(self) -> int:
        """How wide the window must be for a second column; a config key for other tastes and the smoke test."""
        return int(self.config.get("beside_min_width") or MIN_WINDOW_WIDTH)

    def open_beside(self) -> None:
        """Pin the selected conversation in the second column (#35); on a narrow window, a thread window."""
        if not self.selected:
            return
        thread = self.selected[-1]
        if self.get_width() < self._beside_min_width() or self.inner.get_collapsed():
            self.open_thread_window(thread)
            return
        self.beside.show(thread, self.model.mailbox_id)
        if self.config.get("mark_read_on_open", True):
            unread = self.beside.conversation.unread_email_ids()
            if unread:
                self.engine.perform(actions.mark_read(unread, True))

    def open_thread_window(self, thread: ThreadObject) -> None:
        for w in self.thread_windows:
            if w.thread.thread_id == thread.thread_id:
                w.present()
                return
        win = ThreadWindow(self, thread, self.model.mailbox_id)
        self.thread_windows.append(win)
        win.present()

    def _move_selection(self, delta: int) -> None:
        pos = self.threadlist.selected_position()
        target = 0 if pos < 0 else pos + delta
        self.threadlist.select_position(target, step=delta or 1)

    def _selected_email_ids(self) -> list[str]:
        return [eid for t in self.selected for eid in t.email_ids]

    # ------------------------------------------------------------ actions

    def _selection_action(self, kind: str) -> None:
        ids = self._selected_email_ids()
        if not ids:
            return
        self._email_action(kind, ids, from_selection=True)

    def _email_action(self, kind: str, ids: list[str], from_selection: bool = False,
                      threads: list[ThreadObject] | None = None) -> None:
        roles = self.engine.roles
        if threads is None:
            threads = self.selected if from_selection else []
        thread_ids = {t.thread_id for t in threads}
        removes_from_list = False
        mb = self.current_mailbox
        # A view is not a mailbox: archiving neither leaves it nor takes a label away (#19).
        in_mailbox = mb is not None and not mb.is_view
        if kind == "archive":
            act = actions.archive(ids, roles, mb.id if in_mailbox and not mb.is_system else None)
            removes_from_list = in_mailbox and (mb.role == ROLE_INBOX or not mb.is_system)
        elif kind == "trash":
            if mb and mb.role == ROLE_TRASH:
                confirm(self, "Delete permanently?", "These messages will be removed for good.", "Delete", True,
                        lambda: self._email_action("destroy", ids, from_selection, threads))
                return
            act = actions.trash(ids, roles)
            removes_from_list = mb is not None and mb.role != ROLE_TRASH
        elif kind == "junk":
            act = actions.junk(ids, roles)
            removes_from_list = mb is not None and mb.role != ROLE_JUNK
        elif kind == "not-junk":
            act = actions.not_junk(ids, roles)
            removes_from_list = mb is not None and mb.role == ROLE_JUNK
        elif kind == "destroy":
            act = actions.destroy(ids)
            removes_from_list = True
        elif kind == "flag":
            emails = self.db.get_emails(ids)
            all_flagged = all((e.get("keywords") or {}).get(KW_FLAGGED) for e in emails.values())
            act = actions.flag(ids, not all_flagged)
        elif kind == "mark-read":
            act = actions.mark_read(ids, True)
        elif kind == "mark-unread":
            act = actions.mark_read(ids, False)
        else:
            return
        if kind == "mark-unread" and threads:
            # Like Fastmail: marking unread from the toolbar only touches the latest message per thread.
            latest = [t.email_ids[-1] if t.email_ids else t.email_id for t in threads]
            act = actions.mark_read(latest, False)
        if removes_from_list and thread_ids and not self.threadlist.search_active:
            self._remove_from_list(thread_ids)
        self.engine.perform(act, self._after_action)

    def _remove_from_list(self, thread_ids: set[str]) -> None:
        visible = {t.thread_id for t in self.selected}
        pos = self.threadlist.selected_position()
        self.model.remove_threads(thread_ids)
        if visible & thread_ids:
            self.conversation.clear()
            self.threadlist.select_position(min(pos, self.model.get_n_items() - 1), step=1)

    def _after_action(self, record: UndoRecord | None) -> None:
        if record is None:
            return
        toast = Adw.Toast(title=record.description, timeout=6, button_label="Undo")
        toast.connect("button-clicked", lambda *_: self.engine.perform(record.to_action()))
        self.toast_overlay.add_toast(toast)

    def _label_toggle(self, mb: MailboxObject | None, active: bool, threads: list[ThreadObject] | None = None) -> None:
        threads = self.selected if threads is None else threads
        ids = [eid for t in threads for eid in t.email_ids]
        if mb is None or not ids:
            return
        act = actions.add_label(ids, mb.id, mb.name) if active else actions.remove_label(ids, mb.id, mb.name)
        current = self.current_mailbox
        if not active and current and current.id == mb.id and not self.threadlist.search_active:
            self._remove_from_list({t.thread_id for t in threads})
        self.engine.perform(act, self._after_action)

    def _present_labels(self, popover: MailboxPickerPopover, threads: list[ThreadObject]) -> None:
        sets = [t.summary.mailbox_ids for t in threads]
        if not sets:
            return
        union = set().union(*sets)
        inter = set.intersection(*sets)
        popover.present_for(inter, union - inter)

    def _on_labels_button(self, button: Gtk.MenuButton, _p) -> None:
        if button.get_active():
            self._present_labels(self.labels_popover, self.selected)

    def _create_label_and_apply(self, name: str, threads: list[ThreadObject] | None = None) -> None:
        threads = self.selected if threads is None else threads
        ids = [eid for t in threads for eid in t.email_ids]

        def created(res: dict) -> None:
            new_id = res["created"]["new"]["id"]
            self.engine.perform(actions.add_label(ids, new_id, name), self._after_action)

        self.engine.mailbox_set(create={"new": {"name": name, "parentId": None, "isSubscribed": True}},
                                on_done=created, on_error=lambda m: self._toast(f"Could not create label: {m}"))

    def _move_to(self, mb: MailboxObject, threads: list[ThreadObject] | None = None) -> None:
        threads = self.selected if threads is None else threads
        ids = [eid for t in threads for eid in t.email_ids]
        if not ids:
            return
        act = actions.move(ids, mb.id, mb.name)
        current = self.current_mailbox
        if current and current.id != mb.id and not current.is_view and not self.threadlist.search_active:
            self._remove_from_list({t.thread_id for t in threads})
        self.engine.perform(act, self._after_action)

    def _on_drop(self, mb: MailboxObject, email_ids: list[str], copy: bool) -> None:
        roles = self.engine.roles
        current = self.current_mailbox
        if mb.role in (ROLE_TRASH, ROLE_JUNK):
            act = actions.trash(email_ids, roles) if mb.role == ROLE_TRASH else actions.junk(email_ids, roles)
        elif mb.role == ROLE_ARCHIVE:
            act = actions.archive(email_ids, roles, current.id if current and not current.is_system else None)
        elif (copy or current is None or current.is_view or self.threadlist.search_active
                or current.role in (ROLE_TRASH, ROLE_JUNK)):
            act = actions.add_label(email_ids, mb.id, mb.name)
        else:
            act = actions.EmailAction(email_ids, f"Moved to {mb.name}", mailbox_add={mb.id},
                                      mailbox_remove={current.id} if current.id != mb.id else set())
        threads = {self.db.thread_of_email(e) for e in email_ids}
        if (not copy and current and current.id != mb.id and not current.is_view
                and not self.threadlist.search_active):
            self.model.remove_threads({t for t in threads if t})
            self.conversation.clear()
        self.engine.perform(act, self._after_action)

    def _label_names(self, mailbox_ids: set[str]) -> list[tuple[str, int]]:
        """Chips for a list row: Inbox (when not viewing it) plus labels by full path, never the current one."""
        labels = []
        for mid in mailbox_ids:
            mb = self.tree.get(mid)
            if mb is None or (self.current_mailbox and mid == self.current_mailbox.id):
                continue
            if mb.is_system:
                if mb.role == ROLE_INBOX:
                    labels.append(("", "Inbox", -1))
                continue
            labels.append((self.tree.path_name(mid), self.tree.path_name(mid), mb.color_index))
        labels.sort()
        out = [(name, color) for _key, name, color in labels]
        if len(out) > 3:
            out = [*out[:2], (f"+{len(out) - 2}", -1)]
        return out

    # ------------------------------------------------------------ labels

    def new_label(self, parent_id: str | None) -> None:
        parent_name = self.tree.path_name(parent_id) if parent_id else ""
        text_prompt(self, "New label", f"Inside “{parent_name}”" if parent_name else "Labels can hold conversations from any folder.",
                    "", "Create",
                    lambda name: self.engine.mailbox_set(
                        create={"new": {"name": name, "parentId": parent_id, "isSubscribed": True}},
                        on_error=lambda m: self._toast(f"Could not create label: {m}")))

    def rename_label(self, mb: MailboxObject) -> None:
        text_prompt(self, "Rename label", "", mb.name, "Rename",
                    lambda name: self.engine.mailbox_set(update={mb.id: {"name": name}},
                                                         on_error=lambda m: self._toast(f"Rename failed: {m}")))

    def delete_label(self, mb: MailboxObject) -> None:
        confirm(self, f"Delete “{mb.name}”?",
                "Conversations keep their other labels; messages only in this label move to Archive.",
                "Delete", True,
                lambda: self.engine.mailbox_set(destroy=[mb.id],
                                                on_error=lambda m: self._toast(f"Delete failed: {m}")))

    def refresh_mailbox(self, mb: MailboxObject) -> None:
        """Sync now and re-run the mailbox query from scratch (bypassing queryChanges)."""
        if self.current_mailbox and self.current_mailbox.id == mb.id and mb.is_view:
            self._load_view(mb, self._view_search)
        elif self.current_mailbox and self.current_mailbox.id == mb.id and self.query_key:
            self.engine.load_query(self._mailbox_query(mb))
        else:
            self.sidebar.select_mailbox(mb.id)
        self.engine.sync_now()
        self._toast(f"Refreshing {mb.name}")

    def set_label_color(self, mb: MailboxObject, index: int) -> None:
        overrides = dict(self.config.get("label_colors", {}) or {})
        if index < 0:
            overrides.pop(mb.id, None)
        else:
            overrides[mb.id] = index
        self.config.set("label_colors", overrides)
        self.tree.color_overrides = {k: int(v) for k, v in overrides.items()}
        self.tree.refresh()
        if self.query_key:
            self._on_query_updated(self.engine, self.query_key)
        for t in self.selected:
            if t.thread_id == self.conversation.thread_id:
                self.conversation.refresh_thread(t)

    def empty_mailbox(self, mb: MailboxObject) -> None:
        confirm(self, f"Empty {mb.name}?", f"All {mb.total} messages will be deleted permanently.", "Empty", True,
                lambda: self.engine.empty_mailbox(mb.id, lambda: self._toast(f"{mb.name} emptied")))

    # ----------------------------------------------------------- compose

    def compose(self, mode: str = "new", source: dict | None = None, mailto: dict | None = None) -> None:
        # Fastmail lets a folder choose the identity used when composing from it (identityRef).
        preferred = self.current_mailbox.data.get("identityRef") if self.current_mailbox else None
        win = ComposeWindow(self, self.engine, self.db, self.identities, mode, source, mailto,
                            on_closed=lambda w: w in self.compose_windows and self.compose_windows.remove(w),
                            preferred_identity_id=preferred,
                            default_identity_email=self.client.session.username if self.client else None,
                            config=self.config)
        win.set_transient_for(None)
        self.compose_windows.append(win)
        win.present()

    # ---------------------------------------------------------- undo send

    def schedule_send(self, email: dict, identity_id: str, draft_id: str | None, in_reply_to_id: str | None,
                      forwarded_id: str | None, seconds: int) -> None:
        """Submit `email` (already saved as draft `draft_id`) after `seconds`, unless undone."""
        pending = PendingSend(email, identity_id, draft_id, in_reply_to_id, forwarded_id, seconds)
        self.pending_sends.append(pending)
        pending.start(self.toast_overlay.add_toast, self._send_pending, self._undo_pending)

    def _send_pending(self, pending: PendingSend) -> None:
        self.pending_sends.remove(pending)
        self._sends_in_flight += 1

        def done(new_id: str) -> None:
            self._toast("Message sent" if new_id else "Offline: the message goes out when the connection is back", 6)
            self._send_finished()

        def failed(message: str) -> None:
            toast = Adw.Toast(title=f"Could not send: {message}", timeout=0, button_label="Open draft",
                              priority=Adw.ToastPriority.HIGH)
            toast.connect("button-clicked", lambda *_: self._compose_from("draft", pending.draft_id))
            self.toast_overlay.add_toast(toast)
            self._send_finished()

        self.engine.send_email(pending.email, pending.identity_id, pending.draft_id, done, failed,
                               in_reply_to_id=pending.in_reply_to_id, forwarded_id=pending.forwarded_id)

    def _send_finished(self) -> None:
        self._sends_in_flight -= 1
        if not self._sends_in_flight and not self.pending_sends and self._after_sends is not None:
            after, self._after_sends = self._after_sends, None
            after()

    def _undo_pending(self, pending: PendingSend) -> None:
        self.pending_sends.remove(pending)
        self._compose_from("draft", pending.draft_id)

    def flush_sends(self, then: Callable[[], None]) -> bool:
        """Send every waiting message now; True when `then` will run once they are out."""
        if not self.pending_sends and not self._sends_in_flight:
            return False
        self._after_sends = then
        for pending in list(self.pending_sends):
            pending.send_now()
        return True

    def _compose_from(self, mode: str, email_id: str | None) -> None:
        if not email_id:
            return
        body = self.db.get_email_body(email_id)
        if body:
            self.compose(mode, body)
            return

        def ready(_engine, eid):
            if eid == email_id:
                self.engine.disconnect(handler)
                full = self.db.get_email_body(email_id)
                if full:
                    self.compose(mode, full)

        handler = self.engine.connect("body-ready", ready)
        self.engine.fetch_body(email_id)

    # ------------------------------------------------------------ dialogs

    # ---------------------------------------------------------------- lock

    def lock(self) -> None:
        """Hide everything behind the lock page; compose and thread windows go too."""
        if not self.config.get("lock_enabled") or self.locked or self.stack.get_visible_child_name() in ("login", "loading"):
            return
        self.locked = True
        self._before_lock = self.stack.get_visible_child_name() or "main"
        self.stack.set_visible_child_name("locked")
        if lock.method(self.config) == lock.METHOD_KEYRING:
            threading.Thread(target=lock.keyring_lock, name="keyring-lock", daemon=True).start()
        for w in (*self.compose_windows, *self.thread_windows):
            w.set_visible(False)
        self.set_title(f"{APP_NAME} (locked)")

    def track_activity(self, window: Gtk.Window) -> None:
        """Keys and clicks in `window` count as activity for the idle lock; the compose
        and thread windows register themselves (#55)."""
        activity = Gtk.EventControllerLegacy()
        activity.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        activity.connect("event", lambda *_: (self.idle.touch(), False)[1])
        window.add_controller(activity)

    def lock_idle(self) -> None:
        if self.config.get("lock_enabled"):
            self.lock()

    def lock_from_session(self) -> None:
        if self.config.get("lock_enabled") and self.config.get("lock_with_session", True):
            self.lock()

    def unlock(self) -> None:
        """The method chosen in Preferences: the system prompt, the keyring daemon's prompt,
        or the passphrase or PIN dialog (see lock.method)."""
        if not self.locked:
            return
        method = lock.method(self.config)
        if method == lock.METHOD_SYSTEM:
            lock.polkit_check(lambda ok, err: self._unlocked() if ok else self._toast(
                f"Not unlocked{': ' + err if err else ''}", 5))
            return
        if method == lock.METHOD_KEYRING:
            lock.keyring_unlock(lambda ok, err: self._unlocked() if ok else self._toast(
                f"Not unlocked{': ' + err if err else ''}", 5))
            return
        stored = self.config.get("lock_passphrase") or ""
        if not stored:
            # No way to check anyone: the passphrase was removed after enabling. Open, and switch the lock off.
            self.config.set("lock_enabled", False)
            self.apply_lock_settings()
            self._unlocked()
            self._toast("No passphrase or PIN is set, so the lock is off; set one under Account in Preferences", 6)
            return
        pin = method == lock.METHOD_PIN
        entry = secret_entry(pin, activates_default=True)
        dlg = Adw.AlertDialog(heading="Unlock Den Mail", body="Your Den Mail PIN." if pin else "Your Den Mail passphrase.")
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Unlock")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("ok")
        dlg.set_close_response("cancel")

        def on_response(_d, response):
            if response != "ok":
                return
            if lock.check_passphrase(entry.get_text(), stored):
                self._unlocked()
            else:
                self._toast("Wrong PIN" if pin else "Wrong passphrase", 4)

        dlg.connect("response", on_response)
        dlg.present(self)
        entry.grab_focus()

    def _unlocked(self) -> None:
        self.locked = False
        self.stack.set_visible_child_name(self._before_lock)
        for w in (*self.compose_windows, *self.thread_windows):
            w.set_visible(True)
        self.set_title(APP_NAME)
        self.idle.touch()

    def apply_lock_settings(self) -> None:
        """After a change in Preferences: the idle timer and the session watch follow the switches."""
        enabled = bool(self.config.get("lock_enabled"))
        self.lookup_action("lock").set_enabled(enabled)
        self.idle.set_minutes(int(self.config.get("lock_idle_minutes", 0)) if enabled else 0)
        if enabled and not self._session_subs:
            self._session_subs = lock.watch_session_lock(self.lock_from_session)

    def show_preferences(self, page: str | None = None) -> None:
        self.preferences_dialog = PreferencesDialog(self.config, self.client.session if self.client else None,
                          on_sign_out=lambda: confirm(self, "Sign out?", "The local cache will be removed.",
                                                      "Sign out", True, lambda: self.sign_out(clear=True)),
                          on_clear_cache=lambda: self.engine.enqueue(0, self.engine.reset_cache, "reset"),
                          on_manage_identities=lambda: IdentitiesDialog(self.engine, self.db, self.config).present(self),
                          on_sidebar_views=self.set_sidebar_views,
                          on_screener=self.set_screener,
                          on_open=lambda name: self.lookup_action(name).activate(None),
                          on_lock_changed=self.apply_lock_settings,
                          assistant=self.assistant,
                          rules_count=len(rules.load_rules(self.config)),
                          contact_count=self.db.contact_count() if self.db else 0,
                          )
        if page:
            self.preferences_dialog.set_visible_page_name(page)
        self.preferences_dialog.present(self)

    def show_cleanup(self, category: str | None = None) -> None:
        if not self.config.get("cleanup_opened"):
            self.config.set("cleanup_opened", True)
            self._update_banner()   # the hints about Clean up have done their job
            self._update_banner()
        self.cleanup_dialog = CleanupDialog(self.engine, self.db, self.config, self.tree, self._after_action,
                                            self.open_thread_by_id, category=category, assistant=self.assistant)
        self.cleanup_dialog.present(self)

    def _update_banner(self) -> None:
        """The hint above the list: what a view is for, or, in the Inbox a few times, that Clean up exists."""
        mb = self.current_mailbox
        if mb is None or self.threadlist.search_active:
            self.threadlist.set_banner(None)
            return
        dismissed = set(self.config.get("banners_dismissed") or [])
        # The cleanup hints go for good once Clean up was opened or one of them was closed (#84).
        cleanup_done = self.config.get("cleanup_opened") or "cleanup" in dismissed
        if mb.is_view:
            view = views.get_view(mb.id)
            if mb.id == views.SCREENER and "screener" not in dismissed:
                self.threadlist.set_banner("Senders you have never heard from wait here. Open a conversation to let "
                                           "them through or screen them out.",
                                           on_dismiss=lambda: self._dismiss_banner("screener"))
            elif view is not None and (view.category or mb.id == views.NEVER_READ) and not cleanup_done:
                self.threadlist.set_banner(f"Tired of some of these? Clean up ranks the senders behind {view.name} "
                                           "and archives, deletes or unsubscribes in bulk.", "Clean up…",
                                           lambda: self.show_cleanup(view.category),
                                           on_dismiss=lambda: self._dismiss_banner("cleanup"))
            else:
                self.threadlist.set_banner(None)
            return
        if mb.role == ROLE_INBOX and not cleanup_done and self._tip_starts <= 3:
            self.threadlist.set_banner("New: Clean up ranks the senders you never read; archive, delete or "
                                       "unsubscribe in bulk. More under Inbox in the main menu.",
                                       "Clean up…", self.show_cleanup,
                                       on_dismiss=lambda: self._dismiss_banner("cleanup"))
            return
        self.threadlist.set_banner(None)

    def _dismiss_banner(self, name: str) -> None:
        dismissed = list(self.config.get("banners_dismissed") or [])
        if name not in dismissed:
            dismissed.append(name)
            self.config.set("banners_dismissed", dismissed)

    def show_shortcuts(self) -> None:
        dlg = Adw.ShortcutsDialog()
        for title, items in shortcuts.DIALOG:
            section = Adw.ShortcutsSection(title=title)
            for name, ref, *extra in items:
                section.add(Adw.ShortcutsItem(title=name, accelerator=shortcuts.dialog_accelerator(ref, *extra)))
            dlg.add(section)
        dlg.present(self)

    def _toast(self, text: str, timeout: int = 3) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=text, timeout=timeout))

    # ---------------------------------------------------------- lifecycle

    def _on_close_request(self, *_) -> bool:
        w, h = self.get_default_size()
        self.config.set("window", {"width": w, "height": h, "maximized": self.is_maximized()})
        # Secondary windows keep the application alive, so closing the main window closes them too;
        # a compose window with an unsaved draft asks first and keeps the session open meanwhile.
        for win in list(self.thread_windows):
            win.close()
        for win in list(self.compose_windows):
            win.close()
        if self.compose_windows:
            return True
        if self.config.get("run_in_background") and not self.quitting and self.engine:
            # Keep syncing and notifying with the window hidden (#2); Quit ends it.
            app = self.get_application()
            if app is not None and hasattr(app, "hide_to_background"):
                app.hide_to_background()
                return True
        # Messages still counting down go out first; the window closes when they are sent.
        return self.flush_sends(self.close)

    def shutdown(self) -> None:
        if getattr(self, "beside", None) is not None:
            self.beside.detach()
        for w in list(self.compose_windows):
            w.destroy()
        for w in list(self.thread_windows):
            w.close()
        self.avatars.shutdown()
        if self.engine:
            self.engine.stop()
        if self.db:
            self.db.close()
