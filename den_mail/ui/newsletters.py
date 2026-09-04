"""Newsletters dialog: every sender with a List-Unsubscribe header, unsubscribe from many at once."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk

from ..models.thread import format_date
from ..newsletters import Sender, fetch_list_mail, group_senders
from ..store import actions
from ..unsubscribe import UnsubscribePlan, identity_for
from .a11y import watch as _a11y_watch
from .widgets import open_uri, toast

Done = Callable[[UnsubscribePlan], None]
Failed = Callable[[str], None]


def run_unsubscribe(engine, db, email: dict, plan: UnsubscribePlan, parent: Gtk.Window | None,
                    on_done: Done, on_error: Failed, on_fallback: Failed | None = None) -> None:
    """Carry out `plan` for the sender of `email`, falling back through the sender's other methods.

    `on_done` gets the plan that went through; `on_error` a message when none did; `on_fallback`
    a note each time one method fails and the next is tried.
    """
    def failed(message: str) -> None:
        nxt = plan.fallback()
        if nxt is None:
            on_error(f"Unsubscribe via {plan.target} failed: {message}")
            return
        how = {"mailto": f"sending a message to {nxt.target}", "browser": f"opening the page at {nxt.target}"}[nxt.kind]
        if on_fallback:
            on_fallback(f"Unsubscribe via {plan.target} failed ({message}); {how} instead")
        run_unsubscribe(engine, db, email, nxt, parent, on_done, on_error, on_fallback)

    if plan.kind == "one-click":
        engine.unsubscribe_one_click(plan.url, lambda *_: on_done(plan), failed)
    elif plan.kind == "browser":
        open_uri(plan.url, parent)
        on_done(plan)
    else:
        session = getattr(engine.client, "session", None)
        ident = identity_for(db.get_identities(), getattr(session, "username", "") or "", email)
        if ident is None:
            failed("no identity to send from")
            return
        draft = {"from": [{"name": ident.get("name") or None, "email": ident["email"]}],
                 "to": [{"name": None, "email": plan.to}],
                 "subject": plan.subject or "unsubscribe",
                 "textBody": [{"partId": "t", "type": "text/plain"}],
                 "bodyValues": {"t": {"value": plan.body or "unsubscribe"}}}
        engine.send_email(draft, ident["id"], None, lambda *_: on_done(plan), failed)


def done_text(plan: UnsubscribePlan) -> str:
    return {"one-click": f"Unsubscribe request sent to {plan.target}",
            "browser": f"Opened the unsubscribe page at {plan.target}",
            "mailto": f"Unsubscribe message sent to {plan.target}"}[plan.kind]


class SenderRow(Adw.ActionRow):
    def __init__(self, sender: Sender, on_toggle: Callable[[], None]):
        super().__init__(title=GLib.markup_escape_text(sender.name), activatable=True)
        self.sender = sender
        self.check = Gtk.CheckButton(valign=Gtk.Align.CENTER, sensitive=sender.plan is not None)
        self.check.connect("toggled", lambda *_: on_toggle())
        self.add_prefix(self.check)
        self.connect("activated", lambda *_: self.check.set_active(not self.check.get_active()))
        self.status = Gtk.Label(xalign=1, css_classes=["dim-label", "caption"])
        self.add_suffix(self.status)
        self.refresh()

    def refresh(self, note: str | None = None) -> None:
        s = self.sender
        parts = [s.email, f"{s.count} message{'s' if s.count != 1 else ''}"
                 + (f", {s.unread} unread" if s.unread else "")]
        if s.last_at:
            parts.append(f"last {format_date(s.last_at)}")
        self.set_subtitle(GLib.markup_escape_text(" · ".join(parts)))
        if note:
            self.status.set_label(note)
        elif s.unsubscribed_on:
            self.status.set_label(f"Unsubscribed {s.unsubscribed_on}")
        else:
            self.status.set_label(s.method or "no unsubscribe link")
        self.set_tooltip_text({"one-click": f"Sends an unsubscribe request to {s.plan.target}",
                               "browser": f"Opens the unsubscribe page at {s.plan.target} in your browser",
                               "mailto": f"Sends an unsubscribe message to {s.plan.target}"}[s.plan.kind]
                              if s.plan else "The sender offers no unsubscribe method Den Mail can use")


class NewslettersDialog(Adw.Dialog):
    """One row per newsletter sender; tick some, unsubscribe from all of them, optionally clearing their mail."""

    def __init__(self, engine, db, config, on_action: Callable | None = None):
        super().__init__(title="Newsletters", content_width=640, content_height=700)
        self.engine = engine
        self.db = db
        self.config = config
        self.on_action = on_action
        self.rows: list[SenderRow] = []
        self.running = False

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.select_all = Gtk.CheckButton(label="All", tooltip_text="Select every sender with an unsubscribe method")
        self.select_all.connect("toggled", self._on_select_all)
        header.pack_start(self.select_all)
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Scan again")
        self.refresh_button.connect("clicked", lambda *_: self.reload())
        header.pack_end(self.refresh_button)
        view.add_top_bar(header)
        self.search = Gtk.SearchEntry(placeholder_text="Filter senders")
        for side in ("start", "end"):
            getattr(self.search, f"set_margin_{side}")(12)
        self.search.set_margin_bottom(6)
        self.search.connect("search-changed", lambda *_: self.listbox.invalidate_filter())
        view.add_top_bar(self.search)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_filter_func(self._filter)
        for side in ("start", "end", "bottom"):
            getattr(self.listbox, f"set_margin_{side}")(12)
        self.empty = Adw.StatusPage(icon_name="fm-mail-read-symbolic", title="No newsletters",
                                    description="No message outside Trash and Spam carries an unsubscribe header.")
        self.empty.add_css_class("compact")
        self.loading = Adw.StatusPage(title="Scanning…", description="Looking for mail with an unsubscribe header.")
        self.loading.add_css_class("compact")
        self.stack = Gtk.Stack()
        self.stack.add_named(self.loading, "loading")
        self.stack.add_named(Gtk.ScrolledWindow(child=self.listbox, hscrollbar_policy=Gtk.PolicyType.NEVER), "list")
        self.stack.add_named(self.empty, "empty")
        view.set_content(self.stack)

        bar = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=6, margin_bottom=12)
        self.cleanup = Gtk.DropDown.new_from_strings(["Keep their mail", "Archive their mail", "Delete their mail"])
        self.cleanup.set_tooltip_text("What to do with the messages of the senders you unsubscribe from")
        bar.append(self.cleanup)
        self.summary = Gtk.Label(hexpand=True, xalign=0, css_classes=["dim-label"])
        bar.append(self.summary)
        self.go = Gtk.Button(label="Unsubscribe", css_classes=["suggested-action"], sensitive=False)
        self.go.connect("clicked", lambda *_: self.start())
        bar.append(self.go)
        view.add_bottom_bar(bar)
        self.toast_overlay = Adw.ToastOverlay(child=view)
        self.set_child(self.toast_overlay)
        _a11y_watch(self)   # icon-only buttons get their tooltip as accessible name (#123)
        self.reload()

    # ------------------------------------------------------------- loading

    def reload(self) -> None:
        if self.running:
            return
        self.stack.set_visible_child_name("loading")
        self.refresh_button.set_sensitive(False)
        trash_junk = self.engine.trash_junk_ids()
        self.engine.run(lambda: fetch_list_mail(self.engine.client, trash_junk), self._loaded, self._failed)

    def _loaded(self, emails: list[dict]) -> None:
        self.refresh_button.set_sensitive(True)
        while child := self.listbox.get_first_child():
            self.listbox.remove(child)
        self.rows = [SenderRow(s, self._update_summary) for s in group_senders(emails, self.config.unsubscribed())]
        for row in self.rows:
            self.listbox.append(row)
        self.stack.set_visible_child_name("list" if self.rows else "empty")
        self.select_all.set_active(False)
        self._update_summary()

    def _failed(self, message: str) -> None:
        self.refresh_button.set_sensitive(True)
        self.stack.set_visible_child_name("empty")
        toast(self, f"Could not scan for newsletters: {message}", 6)

    def _filter(self, row: Gtk.ListBoxRow) -> bool:
        text = self.search.get_text().strip().lower()
        return not text or text in f"{row.sender.name} {row.sender.email}".lower()

    def _on_select_all(self, button: Gtk.CheckButton) -> None:
        on = button.get_active()
        for row in self.rows:
            if row.check.get_sensitive():
                row.check.set_active(on and row.get_visible())

    def selected(self) -> list[SenderRow]:
        return [r for r in self.rows if r.check.get_active()]

    def _update_summary(self) -> None:
        rows = self.selected()
        n = len(rows)
        messages = sum(r.sender.count for r in rows)
        self.go.set_sensitive(n > 0 and not self.running)
        self.go.set_label(f"Unsubscribe ({n})" if n else "Unsubscribe")
        total = len(self.rows)
        self.summary.set_label(
            f"{n} of {total} sender{'s' if total != 1 else ''} selected, {messages} messages" if n
            else f"{total} sender{'s' if total != 1 else ''} with an unsubscribe header")

    # ------------------------------------------------------------- running

    def start(self) -> None:
        queue = self.selected()
        if not queue or self.running:
            return
        self.running = True
        self.go.set_sensitive(False)
        self.select_all.set_sensitive(False)
        self.refresh_button.set_sensitive(False)
        done: list[SenderRow] = []
        failed: list[tuple[SenderRow, str]] = []

        def step() -> None:
            if not queue:
                self._finished(done, failed)
                return
            row = queue.pop(0)
            row.refresh("Unsubscribing…")
            sender = row.sender

            def ok(plan: UnsubscribePlan) -> None:
                self.config.mark_unsubscribed(sender.email)
                sender.unsubscribed_on = self.config.unsubscribed().get(sender.email)
                row.check.set_active(False)
                row.refresh()
                done.append(row)
                step()

            def err(message: str) -> None:
                row.refresh("failed")
                failed.append((row, message))
                step()

            run_unsubscribe(self.engine, self.db, sender.sample, sender.plan, self.get_root(), ok, err)

        step()

    def _finished(self, done: list[SenderRow], failed: list[tuple[SenderRow, str]]) -> None:
        self.running = False
        self.select_all.set_sensitive(True)
        self.refresh_button.set_sensitive(True)
        self._update_summary()
        n = len(done)
        text = f"Unsubscribed from {n} sender{'s' if n != 1 else ''}"
        if failed:
            text += f", {len(failed)} failed: " + "; ".join(f"{r.sender.name}: {m}" for r, m in failed[:3])
        toast(self, text, 8 if failed else 4)
        what = self.cleanup.get_selected()
        ids = [i for r in done for i in r.sender.email_ids]
        if what and ids:
            act = actions.archive(ids, self.engine.roles) if what == 1 else actions.trash(ids, self.engine.roles)
            self.engine.perform(act, self.on_action)
