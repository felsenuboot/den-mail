"""Main window: sidebar | conversation list | conversation, wired to the sync engine."""

from __future__ import annotations

import logging
import threading

from gi.repository import Adw, Gio, GLib, Gtk

from .. import secrets
from ..config import Config, database_path
from ..jmap.client import AuthError, JMAPClient, JMAPError
from ..jmap.types import KW_FLAGGED, KW_SEEN, ROLE_ARCHIVE, ROLE_DRAFTS, ROLE_INBOX, ROLE_JUNK, ROLE_TRASH
from ..models.mailbox import MailboxObject, MailboxTree
from ..models.thread import ThreadListModel, ThreadObject
from ..store import actions
from ..store.actions import UndoRecord
from ..store.db import Database
from ..store.sync import SyncEngine, mailbox_query_spec, search_query_spec
from .compose import ComposeWindow
from .conversation import ConversationView
from .identities import IdentitiesDialog
from .labels import MailboxPickerPopover
from .login import LoginPage
from .masked import MaskedEmailDialog
from .message_body import set_cid_resolver
from .preferences import PreferencesDialog
from .sidebar import Sidebar
from .threadlist import ThreadList
from .widgets import confirm, text_prompt

log = logging.getLogger(__name__)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, config: Config):
        super().__init__(application=app, title="Fastmail")
        self.config = config
        win = config.get("window", {})
        self.set_default_size(int(win.get("width", 1400)), int(win.get("height", 900)))
        if win.get("maximized"):
            self.maximize()
        self.client: JMAPClient | None = None
        self.db: Database | None = None
        self.engine: SyncEngine | None = None
        self.tree = MailboxTree()
        self.current_mailbox: MailboxObject | None = None
        self.query_key: str | None = None
        self.selected: list[ThreadObject] = []
        self.compose_windows: list[ComposeWindow] = []
        self.identities: list[dict] = []
        self._pending_select_position: int | None = None

        self.toast_overlay = Adw.ToastOverlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toast_overlay.set_child(self.stack)
        self.set_content(self.toast_overlay)
        self.login = LoginPage(self._on_login_submit)
        self.stack.add_named(self.login, "login")
        self.loading = Adw.StatusPage(title="Connecting…")
        self.loading.set_child(Adw.Spinner())
        self.stack.add_named(self.loading, "loading")
        self.main: Adw.NavigationSplitView | None = None
        self._install_actions()
        self.connect("close-request", self._on_close_request)

    # ------------------------------------------------------------ account

    def start(self) -> None:
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
        e.connect("body-ready", lambda _e, eid: self.conversation.on_body_ready(eid))
        e.connect("body-failed", lambda _e, eid, msg: self.conversation.on_body_failed(eid, msg))
        e.connect("sync-status", self._on_sync_status)
        e.connect("push-status", lambda _e, on: self._update_status())
        e.connect("new-mail", self._on_new_mail)
        e.connect("action-failed", lambda _e, msg: self._toast(msg, 6))
        e.connect("auth-failed", lambda _e: self._auth_failed())
        e.connect("cache-reset", lambda _e: self._reload_current())
        e.connect("identities-changed", lambda _e: setattr(self, "identities", self.db.get_identities()))
        set_cid_resolver(self._resolve_cid)
        e.start()
        self.stack.set_visible_child_name("main")
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
        section.append("Masked Email…", "win.masked")
        section.append("Identities & Aliases…", "win.identities")
        primary.append_section(None, section)
        section = Gio.Menu()
        section.append("Preferences", "win.preferences")
        section.append("Keyboard Shortcuts", "win.shortcuts")
        section.append("About Fastmail GTK", "app.about")
        primary.append_section(None, section)

        self.sidebar = Sidebar(self.tree, self.client.session.account_name if self.client and self.client.session else "",
                               self._on_select_mailbox, self._on_drop, primary)
        self.sidebar.on_new_label = self.new_label
        self.sidebar.on_rename = self.rename_label
        self.sidebar.on_delete = self.delete_label
        self.sidebar.on_mark_read = lambda mb: self.engine.mark_mailbox_read(mb.id)
        self.sidebar.on_empty = self.empty_mailbox

        self.model = ThreadListModel(self.db)
        self.model.label_namer = self._label_names
        self.threadlist = ThreadList(self.model, self._on_selection, self._on_activate_thread, self._on_search,
                                     self._on_load_more, lambda: self.engine.sync_now())
        self.conversation = ConversationView(self.db, self.engine, self.tree, self.config, self._compose_from,
                                             self._email_action)
        self.conversation.on_remove_label = lambda mid: self._label_toggle(self.tree.get(mid), False)
        self.labels_popover = MailboxPickerPopover(self.tree, "labels", on_toggle=self._label_toggle,
                                                   on_create=self._create_label_and_apply)
        self.conversation.labels_button.set_popover(self.labels_popover)
        self.conversation.labels_button.connect("notify::active", self._on_labels_button)
        self.move_popover = MailboxPickerPopover(self.tree, "move", on_pick=self._move_to)
        self.conversation.move_button.set_popover(self.move_popover)
        self.conversation.move_button.connect("notify::active", lambda b, _p: self.move_popover._rebuild() if b.get_active() else None)

        self.inner = Adw.NavigationSplitView(sidebar=self.threadlist, content=self.conversation,
                                             min_sidebar_width=300, max_sidebar_width=520,
                                             sidebar_width_fraction=0.36)
        inner_page = Adw.NavigationPage(child=self.inner, title="Mail", tag="mail")
        self.main = Adw.NavigationSplitView(sidebar=self.sidebar, content=inner_page, min_sidebar_width=200,
                                            max_sidebar_width=300, sidebar_width_fraction=0.2)
        self.stack.add_named(self.main, "main")
        bp1 = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 1100sp"))
        bp1.add_setter(self.main, "collapsed", True)
        bp2 = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        bp2.add_setter(self.main, "collapsed", True)
        bp2.add_setter(self.inner, "collapsed", True)
        self.add_breakpoint(bp1)
        self.add_breakpoint(bp2)

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
            "back": self._go_back,
            "masked": lambda: MaskedEmailDialog(self.engine, self.db).present(self),
            "identities": lambda: IdentitiesDialog(self.engine, self.db).present(self),
            "preferences": self.show_preferences,
            "shortcuts": self.show_shortcuts,
            "goto-inbox": lambda: self._goto_role(ROLE_INBOX),
            "goto-drafts": lambda: self._goto_role(ROLE_DRAFTS),
        }
        for name, fn in specs.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=fn: self.engine and fn())
            self.add_action(action)

    # ------------------------------------------------------- engine events

    def _on_mailboxes_changed(self, _engine) -> None:
        self.tree.update(self.db.get_mailboxes())
        if self.current_mailbox is None:
            inbox = self.tree.by_role(ROLE_INBOX)
            if inbox is not None:
                self.sidebar.select_mailbox(inbox.id)
        elif self.tree.get(self.current_mailbox.id) is None:
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
        selected_ids = {t.thread_id for t in self.selected}
        self.model.loading = False
        self.model.set_email_ids(q["ids"], q["total"], q["complete"])
        self._update_list_title()
        if self._pending_select_position is not None:
            pos = min(self._pending_select_position, self.model.get_n_items() - 1)
            self._pending_select_position = None
            if pos >= 0:
                self.threadlist.select_position(pos)
        elif selected_ids and not any(t in self.model.by_thread for t in selected_ids):
            self.conversation.clear()

    def _on_emails_changed(self, _engine, ids: list[str]) -> None:
        self.model.refresh_threads(ids)
        for t in self.selected:
            if t.thread_id == self.conversation.thread_id:
                self.conversation.refresh_thread(t)

    def _on_sync_status(self, _engine, status: str, message: str) -> None:
        self.threadlist.set_syncing(status == "syncing")
        self._status_message = message
        self._update_status()

    def _update_status(self) -> None:
        if not self.engine:
            return
        msg = getattr(self, "_status_message", "")
        if msg:
            self.sidebar.set_status(msg)
        elif not self.engine.online:
            self.sidebar.set_status("Offline — showing cached mail")
        elif self.engine.push_connected:
            self.sidebar.set_status("Connected (push)")
        else:
            self.sidebar.set_status("Connected (polling)")

    def _on_new_mail(self, _engine, emails: list[dict]) -> None:
        if not self.config.get("notify_new_mail", True) or self.is_active():
            return
        app = self.get_application()
        for e in emails[:5]:
            sender = (e.get("from") or [{}])[0]
            n = Gio.Notification.new(sender.get("name") or sender.get("email") or "New mail")
            n.set_body(e.get("subject") or "(no subject)")
            n.set_default_action("app.activate")
            app.send_notification(f"mail-{e['id']}", n)

    def _resolve_cid(self, email_id: str, cid: str, done) -> None:
        body = self.db.get_email_body(email_id) if self.db else None
        att = None
        for a in (body or {}).get("attachments") or []:
            if (a.get("cid") or "").strip("<>") == cid:
                att = a
                break
        if att is None:
            done(None, None)
            return
        self.engine.fetch_blob(att["blobId"], att.get("name") or "inline", att.get("type"),
                               lambda p: done(p.read_bytes(), att.get("type")), lambda _m: done(None, None))

    # ---------------------------------------------------------- navigation

    def _on_select_mailbox(self, mb: MailboxObject) -> None:
        self.current_mailbox = mb
        self.threadlist.search_entry.set_text("")
        self.threadlist.search_bar.set_search_mode(False)
        self._load_mailbox(mb)
        if self.main.get_collapsed():
            self.main.set_show_content(True)

    def _load_mailbox(self, mb: MailboxObject) -> None:
        if self.query_key:
            self.engine.release_query(self.query_key)
        self.model.mailbox_id = mb.id
        self.model.trash_junk = set(self.engine.trash_junk_ids())
        self.model.loading = True
        self.model.clear()
        self.conversation.clear()
        self.threadlist.set_empty_text("No conversations", f"{mb.name} is empty.")
        self.query_key = self.engine.load_query(mailbox_query_spec(mb.id))
        cached = self.db.get_query(self.query_key)
        if cached:
            self.model.set_email_ids(cached["ids"], cached["total"], cached["complete"])
        self._update_list_title()
        self.threadlist.scroll_to_top()

    def _reload_current(self) -> None:
        if self.current_mailbox:
            self._load_mailbox(self.current_mailbox)

    def _update_list_title(self) -> None:
        if self.threadlist.search_active:
            total = self.model.total
            self.threadlist.set_title("Search", f"{total} result{'s' if total != 1 else ''}")
            return
        mb = self.current_mailbox
        if mb is None:
            return
        live = self.tree.get(mb.id) or mb
        sub = f"{live.total} conversations" if live.total else ""
        if live.unread and live.role not in (ROLE_TRASH, ROLE_JUNK):
            sub = f"{live.unread} unread · {sub}" if sub else f"{live.unread} unread"
        self.threadlist.set_title(live.name, sub)

    def _on_search(self, text: str, scope: str) -> None:
        if not text:
            if self.current_mailbox:
                self._load_mailbox(self.current_mailbox)
            return
        if self.query_key:
            self.engine.release_query(self.query_key)
        mailbox_id = self.current_mailbox.id if (scope == "mailbox" and self.current_mailbox) else None
        self.model.mailbox_id = mailbox_id
        self.model.trash_junk = set(self.engine.trash_junk_ids())
        self.model.loading = True
        self.model.clear()
        self.conversation.clear()
        self.threadlist.set_empty_text("No results", "Try different words, or search all mail.")
        self.query_key = self.engine.load_query(search_query_spec(text, mailbox_id, self.engine.trash_junk_ids()))
        self.threadlist.set_title("Search", "Searching…")

    def _on_load_more(self) -> None:
        if self.query_key:
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

    def _move_selection(self, delta: int) -> None:
        pos = self.threadlist.selected_position()
        target = 0 if pos < 0 else pos + delta
        self.threadlist.select_position(target)

    def _selected_email_ids(self) -> list[str]:
        return [eid for t in self.selected for eid in t.email_ids]

    # ------------------------------------------------------------ actions

    def _selection_action(self, kind: str) -> None:
        ids = self._selected_email_ids()
        if not ids:
            return
        self._email_action(kind, ids, from_selection=True)

    def _email_action(self, kind: str, ids: list[str], from_selection: bool = False) -> None:
        roles = self.engine.roles
        thread_ids = {t.thread_id for t in self.selected} if from_selection else set()
        removes_from_list = False
        mb = self.current_mailbox
        if kind == "archive":
            act = actions.archive(ids, roles, mb.id if mb and not mb.is_system else None)
            removes_from_list = mb is not None and mb.role in (ROLE_INBOX,) or (mb is not None and not mb.is_system)
        elif kind == "trash":
            if mb and mb.role == ROLE_TRASH:
                confirm(self, "Delete permanently?", "These messages will be removed for good.", "Delete", True,
                        lambda: self._email_action("destroy", ids, from_selection))
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
            if not from_selection:
                pass
        else:
            return
        if kind == "mark-unread" and from_selection:
            # Like Fastmail: marking unread from the toolbar only touches the latest message per thread.
            latest = []
            for t in self.selected:
                latest.append(t.email_ids[-1] if t.email_ids else t.email_id)
            act = actions.mark_read(latest, False)
        if removes_from_list and thread_ids and not self.threadlist.search_active:
            pos = self.threadlist.selected_position()
            self.model.remove_threads(thread_ids)
            self.conversation.clear()
            self.threadlist.select_position(min(pos, self.model.get_n_items() - 1))
        self.engine.perform(act, self._after_action)

    def _after_action(self, record: UndoRecord | None) -> None:
        if record is None:
            return
        toast = Adw.Toast(title=record.description, timeout=6, button_label="Undo")
        toast.connect("button-clicked", lambda *_: self.engine.perform(record.to_action()))
        self.toast_overlay.add_toast(toast)

    def _label_toggle(self, mb: MailboxObject | None, active: bool) -> None:
        ids = self._selected_email_ids()
        if mb is None or not ids:
            return
        act = actions.add_label(ids, mb.id, mb.name) if active else actions.remove_label(ids, mb.id, mb.name)
        current = self.current_mailbox
        if not active and current and current.id == mb.id and not self.threadlist.search_active:
            pos = self.threadlist.selected_position()
            self.model.remove_threads({t.thread_id for t in self.selected})
            self.conversation.clear()
            self.threadlist.select_position(min(pos, self.model.get_n_items() - 1))
        self.engine.perform(act, self._after_action)

    def _on_labels_button(self, button: Gtk.MenuButton, _p) -> None:
        if not button.get_active():
            return
        sets = [t.summary.mailbox_ids for t in self.selected]
        if not sets:
            return
        union = set().union(*sets)
        inter = set.intersection(*sets) if sets else set()
        self.labels_popover.present_for(inter, union - inter)

    def _create_label_and_apply(self, name: str) -> None:
        def created(res: dict) -> None:
            new_id = res["created"]["new"]["id"]
            self.engine.perform(actions.add_label(self._selected_email_ids(), new_id, name), self._after_action)

        self.engine.mailbox_set(create={"new": {"name": name, "parentId": None, "isSubscribed": True}},
                                on_done=created, on_error=lambda m: self._toast(f"Could not create label: {m}"))

    def _move_to(self, mb: MailboxObject) -> None:
        ids = self._selected_email_ids()
        if not ids:
            return
        act = actions.move(ids, mb.id, mb.name)
        if self.current_mailbox and self.current_mailbox.id != mb.id and not self.threadlist.search_active:
            pos = self.threadlist.selected_position()
            self.model.remove_threads({t.thread_id for t in self.selected})
            self.conversation.clear()
            self.threadlist.select_position(min(pos, self.model.get_n_items() - 1))
        self.engine.perform(act, self._after_action)

    def _on_drop(self, mb: MailboxObject, email_ids: list[str], copy: bool) -> None:
        roles = self.engine.roles
        current = self.current_mailbox
        if mb.role in (ROLE_TRASH, ROLE_JUNK):
            act = actions.trash(email_ids, roles) if mb.role == ROLE_TRASH else actions.junk(email_ids, roles)
        elif mb.role == ROLE_ARCHIVE:
            act = actions.archive(email_ids, roles, current.id if current and not current.is_system else None)
        elif copy or current is None or self.threadlist.search_active or current.role in (ROLE_TRASH, ROLE_JUNK):
            act = actions.add_label(email_ids, mb.id, mb.name)
        else:
            act = actions.EmailAction(email_ids, f"Moved to {mb.name}", mailbox_add={mb.id},
                                      mailbox_remove={current.id} if current.id != mb.id else set())
        threads = {self.db.thread_of_email(e) for e in email_ids}
        if not copy and current and current.id != mb.id and not self.threadlist.search_active:
            self.model.remove_threads({t for t in threads if t})
            self.conversation.clear()
        self.engine.perform(act, self._after_action)

    def _label_names(self, mailbox_ids: set[str]) -> str:
        names = []
        for mid in mailbox_ids:
            mb = self.tree.get(mid)
            if mb is None or mb.is_system or (self.current_mailbox and mid == self.current_mailbox.id):
                continue
            names.append(mb.name)
        return "  ".join(sorted(names)[:3])

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

    def empty_mailbox(self, mb: MailboxObject) -> None:
        confirm(self, f"Empty {mb.name}?", f"All {mb.total} messages will be deleted permanently.", "Empty", True,
                lambda: self.engine.empty_mailbox(mb.id, lambda: self._toast(f"{mb.name} emptied")))

    # ----------------------------------------------------------- compose

    def compose(self, mode: str = "new", source: dict | None = None, mailto: dict | None = None) -> None:
        win = ComposeWindow(self, self.engine, self.db, self.identities, mode, source, mailto,
                            on_closed=lambda w: w in self.compose_windows and self.compose_windows.remove(w))
        win.set_transient_for(None)
        self.compose_windows.append(win)
        win.present()

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

    def show_preferences(self) -> None:
        PreferencesDialog(self.config, self.client.session if self.client else None,
                          on_sign_out=lambda: confirm(self, "Sign out?", "The local cache will be removed.",
                                                      "Sign out", True, lambda: self.sign_out(clear=True)),
                          on_clear_cache=lambda: self.engine.enqueue(0, self.engine.reset_cache, "reset")).present(self)

    def show_shortcuts(self) -> None:
        dlg = Adw.ShortcutsDialog()
        for title, items in (
            ("Navigation", [("Next conversation", "j Down"), ("Previous conversation", "k Up"),
                            ("Open conversation", "Return"), ("Back", "Escape"), ("Search", "slash <Control>f"),
                            ("Go to Inbox", "g+i"), ("Go to Drafts", "g+d"), ("Refresh", "F5")]),
            ("Conversations", [("Archive", "e"), ("Delete", "numbersign Delete"), ("Mark as spam", "exclam"),
                               ("Flag", "s"), ("Mark unread", "<Shift>u"), ("Mark read", "<Shift>i"),
                               ("Labels", "l"), ("Move to…", "v"), ("Select all", "<Control>a")]),
            ("Compose", [("New message", "c <Control>n"), ("Reply", "r"), ("Reply all", "a"), ("Forward", "f"),
                         ("Send", "<Control>Return"), ("Save draft", "<Control>s")]),
        ):
            section = Adw.ShortcutsSection(title=title)
            for name, accel in items:
                section.add(Adw.ShortcutsItem(title=name, accelerator=accel))
            dlg.add(section)
        dlg.present(self)

    def _toast(self, text: str, timeout: int = 3) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=text, timeout=timeout))

    # ---------------------------------------------------------- lifecycle

    def _on_close_request(self, *_) -> bool:
        w, h = self.get_default_size()
        self.config.set("window", {"width": w, "height": h, "maximized": self.is_maximized()})
        return False

    def shutdown(self) -> None:
        for w in list(self.compose_windows):
            w.destroy()
        if self.engine:
            self.engine.stop()
        if self.db:
            self.db.close()
