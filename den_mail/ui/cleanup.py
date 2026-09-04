"""Cleanup dialog (#21): every sender in the cache ranked by how pointless their
mail looks, with bulk actions per sender and rules for the future."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gio, GLib, Gtk

from .. import rules, senders
from ..classify.rules import CATEGORIES, CATEGORY_NAMES, H_LIST_UNSUBSCRIBE, PRIMARY
from ..models.thread import format_date
from ..store import actions
from ..store.actions import UndoRecord
from ..summaries import Summariser
from ..unsubscribe import parse_list_unsubscribe
from .a11y import watch as _a11y_watch
from .newsletters import run_unsubscribe
from .widgets import avatar, chip, confirm, human_size, toast

SORTS = ("score", "count", "unread", "newest", "size")
SORT_LABELS = ["Pointless first", "Most mail", "Most unread", "Newest", "Largest"]
UNSUBSCRIBE_POST = "header:List-Unsubscribe-Post:asRaw"


class SenderRow(Adw.ExpanderRow):
    """One sender: the numbers in the subtitle, the newest messages inside."""

    def __init__(self, stats: senders.SenderStats, on_toggle: Callable[[], None],
                 fill: Callable[[SenderRow], None]):
        super().__init__(title=GLib.markup_escape_text(stats.name))
        self.stats = stats
        self.filled = False
        self.check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        self.check.connect("toggled", lambda *_: on_toggle())
        self.add_prefix(self.check)
        self.add_prefix(avatar(stats.name, 32))
        self.score = chip(f"{stats.score:.0f}", "chip-neutral")
        self.score.set_valign(Gtk.Align.CENTER)
        self.score.set_tooltip_text("Pointless score: volume, unread and deleted-unread shares, an unsubscribe header;"
                                    " below zero once you have written to the sender")
        self.add_suffix(self.score)
        if stats.category != PRIMARY:
            c = chip(CATEGORY_NAMES.get(stats.category, stats.category), "chip")
            c.add_css_class("chip-category")
            c.add_css_class(f"category-{stats.category}")
            c.set_valign(Gtk.Align.CENTER)
            self.add_suffix(c)
        self.refresh()
        self.connect("notify::expanded", lambda *_: self.get_expanded() and not self.filled and fill(self))

    def refresh(self, note: str | None = None) -> None:
        s = self.stats
        parts = [s.email, f"{s.count} message{'s' if s.count != 1 else ''}"
                 + (f", {s.unread} unread" if s.unread else ", all read")]
        if s.last_at:
            parts.append(f"last {format_date(s.last_at)}")
        parts.append(note or s.score_text)
        self.set_subtitle(GLib.markup_escape_text(" · ".join(parts)))
        self.set_tooltip_text(f"{human_size(s.size)} in the cache" + (f", newest: {s.newest_subject}" if s.newest_subject else ""))


class CleanupDialog(Adw.Dialog):
    def __init__(self, engine, db, config, tree, on_action: Callable[[UndoRecord | None], None] | None = None,
                 on_open_thread: Callable[[str], None] | None = None, category: str | None = None,
                 assistant=None):
        super().__init__(title="Clean up", content_width=720, content_height=720)
        self.engine = engine
        self.db = db
        self.summariser = Summariser(db, engine, assistant) if assistant is not None else None
        self.config = config
        self.tree = tree
        self.on_action = on_action
        self.on_open_thread = on_open_thread
        self.rows: list[SenderRow] = []
        self.running = False

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.select_all = Gtk.CheckButton(label="All", tooltip_text="Select every sender shown")
        self.select_all.connect("toggled", self._on_select_all)
        header.pack_start(self.select_all)
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Count again")
        self.refresh_button.connect("clicked", lambda *_: self.reload())
        header.pack_end(self.refresh_button)
        view.add_top_bar(header)
        filters = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_bottom=6)
        self.category = Gtk.DropDown.new_from_strings(["All categories", *(CATEGORY_NAMES[c] for c in CATEGORIES)])
        if category in CATEGORIES:
            self.category.set_selected(CATEGORIES.index(category) + 1)
        self.category.connect("notify::selected", lambda *_: self.reload())
        filters.append(self.category)
        self.sort = Gtk.DropDown.new_from_strings(SORT_LABELS)
        self.sort.connect("notify::selected", lambda *_: self._resort())
        filters.append(self.sort)
        self.search = Gtk.SearchEntry(placeholder_text="Filter senders", hexpand=True)
        self.search.connect("search-changed", lambda *_: self.listbox.invalidate_filter())
        filters.append(self.search)
        view.add_top_bar(filters)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_filter_func(self._filter)
        for side in ("start", "end", "bottom"):
            getattr(self.listbox, f"set_margin_{side}")(12)
        self.empty = Adw.StatusPage(icon_name="fm-mail-read-symbolic", title="Nothing to clean up",
                                    description="No cached mail outside Trash and Spam matches.")
        self.empty.add_css_class("compact")
        self.stack = Gtk.Stack()
        self.stack.add_named(Gtk.ScrolledWindow(child=self.listbox, hscrollbar_policy=Gtk.PolicyType.NEVER), "list")
        self.stack.add_named(self.empty, "empty")
        view.set_content(self.stack)

        bar = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_top=6, margin_bottom=12)
        self.summary = Gtk.Label(hexpand=True, xalign=0, ellipsize=3, css_classes=["dim-label"])
        bar.append(self.summary)
        self.buttons: list[Gtk.Widget] = []
        for label, kind, tip in (("Mark read", "mark_read", "Mark every message from the selected senders as read"),
                                 ("Archive", "archive", "Archive every message from the selected senders"),
                                 ("Unsubscribe", "unsubscribe", "Send the senders' unsubscribe requests")):
            b = Gtk.Button(label=label, tooltip_text=tip)
            b.connect("clicked", lambda _b, k=kind: self.run(k))
            bar.append(b)
            self.buttons.append(b)
        delete = Gtk.Button(label="Delete", tooltip_text="Move every message from the selected senders to Trash",
                            css_classes=["destructive-action"])
        delete.connect("clicked", lambda *_: self.run("trash"))
        bar.append(delete)
        self.buttons.append(delete)
        always = Gio.Menu()
        for label, action in (("Label as…", "label"), ("Archive", "archive"), ("Mark as read", "mark_read"),
                              ("Delete", "trash")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("cleanup.always", GLib.Variant("s", action))
            always.append_item(item)
        group = Gio.SimpleActionGroup()
        act = Gio.SimpleAction.new("always", GLib.VariantType.new("s"))
        act.connect("activate", lambda _a, p: self.always(p.get_string()))
        group.add_action(act)
        self.insert_action_group("cleanup", group)
        self.always_button = Gtk.MenuButton(label="Always…", menu_model=always,
                                            tooltip_text="A rule for the selected senders' future mail, applied to their mail now")
        bar.append(self.always_button)
        self.buttons.append(self.always_button)
        view.add_bottom_bar(bar)
        self.toast_overlay = Adw.ToastOverlay(child=view)
        self.set_child(self.toast_overlay)
        self.reload()
        _a11y_watch(self)   # icon-only buttons get their tooltip as accessible name (#123)

    # ------------------------------------------------------------- loading

    @property
    def category_filter(self) -> str | None:
        i = self.category.get_selected()
        return CATEGORIES[i - 1] if i > 0 else None

    def reload(self) -> None:
        if self.running:
            return
        stats = senders.sender_stats(self.db, self.engine.trash_junk_ids(), self.category_filter)
        while child := self.listbox.get_first_child():
            self.listbox.remove(child)
        self.rows = [SenderRow(s, self._update_summary, self._fill) for s in stats]
        self._resort()
        self.select_all.set_active(False)
        self._update_summary()

    def _resort(self) -> None:
        key = SORTS[self.sort.get_selected()]
        keys = {"score": lambda s: (-s.score, -s.count), "count": lambda s: (-s.count, -s.score),
                "unread": lambda s: (-s.unread, -s.count), "newest": lambda s: s.last_at and -_stamp(s.last_at),
                "size": lambda s: (-s.size, -s.count)}[key]
        self.rows.sort(key=lambda r: keys(r.stats))
        while child := self.listbox.get_first_child():
            self.listbox.remove(child)
        for row in self.rows:
            self.listbox.append(row)
        self.stack.set_visible_child_name("list" if self.rows else "empty")

    def _fill(self, row: SenderRow) -> None:
        row.filled = True
        if self.summariser is not None and self.summariser.available:
            self._summarise_row(row)
        for m in senders.messages_of(self.db, row.stats.email, self.engine.trash_junk_ids()):
            r = Adw.ActionRow(title=GLib.markup_escape_text(m["subject"] or "(no subject)"),
                              subtitle=GLib.markup_escape_text(format_date(m["received_at"] or "")),
                              activatable=self.on_open_thread is not None)
            if not m["seen"]:
                r.add_css_class("unread")
                r.add_prefix(Gtk.Image(icon_name="media-record-symbolic", pixel_size=8, css_classes=["unread-dot"]))
            if self.on_open_thread is not None:
                r.connect("activated", lambda _r, tid=m["thread_id"]: self.on_open_thread(tid))
            row.add_row(r)

    def _summarise_row(self, row: SenderRow) -> None:
        """Their newest message in one line (#68), so one can decide without opening anything."""
        line = Adw.ActionRow(title="Their newest message, in one line", subtitle="Summarising…")
        line.add_prefix(Gtk.Image(icon_name="fm-assistant-symbolic"))
        line.add_css_class("summary-line")
        row.add_row(line)
        self.summariser.sender(row.stats.email, lambda s: line.set_subtitle(GLib.markup_escape_text(s.text)),
                               lambda m: (line.set_subtitle(GLib.markup_escape_text(m)), line.add_css_class("error")))

    def _filter(self, row: Gtk.ListBoxRow) -> bool:
        text = self.search.get_text().strip().lower()
        return not text or text in f"{row.stats.name} {row.stats.email}".lower()

    def _on_select_all(self, button: Gtk.CheckButton) -> None:
        on = button.get_active()
        for row in self.rows:
            row.check.set_active(on and row.get_visible() and self._filter(row))

    def selected(self) -> list[SenderRow]:
        return [r for r in self.rows if r.check.get_active()]

    def _update_summary(self) -> None:
        rows = self.selected()
        n, total = len(rows), len(self.rows)
        for b in self.buttons:
            b.set_sensitive(n > 0 and not self.running)
        if n:
            messages = sum(r.stats.count for r in rows)
            unread = sum(r.stats.unread for r in rows)
            self.summary.set_label(f"{n} of {total} senders selected: {messages} messages, {unread} unread")
        else:
            self.summary.set_label(f"{total} sender{'s' if total != 1 else ''} in the cache"
                                   + (f", {CATEGORY_NAMES[self.category_filter]} only" if self.category_filter else ""))

    # ------------------------------------------------------------- actions

    def run(self, kind: str) -> None:
        rows = self.selected()
        if not rows or self.running:
            return
        if kind == "trash":
            n = sum(r.stats.count for r in rows)
            confirm(self, f"Delete {n} messages?", f"Everything from {len(rows)} sender{'s' if len(rows) != 1 else ''}"
                    " outside Trash and Spam moves to Trash, messages the cache has not listed included.",
                    "Delete", True, lambda: self._run_rows(rows, kind))
            return
        if kind == "unsubscribe":
            self._unsubscribe(rows)
            return
        self._run_rows(rows, kind)

    def _start(self) -> None:
        self.running = True
        self.select_all.set_sensitive(False)
        self.refresh_button.set_sensitive(False)
        self._update_summary()

    def _finish(self, text: str, records: list[UndoRecord]) -> None:
        self.running = False
        self.select_all.set_sensitive(True)
        self.refresh_button.set_sensitive(True)
        originals = {k: v for rec in records for k, v in rec.originals.items()}
        if originals:
            t = Adw.Toast(title=text, timeout=8, button_label="Undo")
            merged = UndoRecord(text, originals)
            t.connect("button-clicked", lambda *_: self.engine.perform(merged.to_action(), lambda *_: self.reload()))
            self.toast_overlay.add_toast(t)
        else:
            toast(self, text, 5)
        self.reload()

    def _run_rows(self, rows: list[SenderRow], kind: str, rule: rules.Rule | None = None, verb: str = "") -> None:
        """Apply one action to every selected sender, one server round trip each."""
        queue = list(rows)
        records: list[UndoRecord] = []
        touched = [0]
        roles = self.engine.roles
        self._start()

        def make(ids: list[str], addr: str) -> actions.EmailAction:
            if rule is not None:
                r = rules.Rule(rule.kind, addr if rule.kind == "sender" else rule.value, rule.action,
                               rule.label_id, rule.label_name)
                return rules.combine(ids, [r], roles)
            return {"archive": lambda: actions.archive(ids, roles),
                    "trash": lambda: actions.trash(ids, roles),
                    "mark_read": lambda: actions.mark_read(ids, True)}[kind]()

        def step() -> None:
            if not queue:
                what = verb or {"archive": "Archived", "trash": "Deleted", "mark_read": "Marked as read"}[kind]
                self._finish(f"{what} {touched[0]} messages from {len(rows)} sender{'s' if len(rows) != 1 else ''}",
                             records)
                return
            row = queue.pop(0)
            row.refresh("working…")

            def done(record: UndoRecord | None) -> None:
                if record is not None:
                    records.append(record)
                    touched[0] += len(record.originals)
                step()

            addr = row.stats.email
            self.engine.act_on_sender(addr, lambda ids, a=addr: make(ids, a), done)

        step()

    def always(self, action: str) -> None:
        """A rule per selected sender, applied to their mail now (#22)."""
        rows = self.selected()
        if not rows or self.running:
            return
        if action != "label":
            self._always(rows, action, None, "")
            return
        labels = self.tree.labels()
        if not labels:
            toast(self, "Create a label first")
            return
        names = [self.tree.path_name(m.id) for m in labels]
        pick = Adw.ComboRow(title="Label", model=Gtk.StringList.new(names))
        group = Adw.PreferencesGroup()
        group.add(pick)
        dlg = Adw.AlertDialog(heading="Always label their mail", body=f"Applies to the {len(rows)} selected sender{'s' if len(rows) != 1 else ''}, now and in future.")
        dlg.set_extra_child(group)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Add rules")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("ok")
        dlg.set_close_response("cancel")

        def on_response(_d, response):
            if response == "ok":
                mb = labels[pick.get_selected()]
                self._always(rows, "label", mb.id, self.tree.path_name(mb.id))

        dlg.connect("response", on_response)
        dlg.present(self)

    def _always(self, rows: list[SenderRow], action: str, label_id: str | None, label_name: str) -> None:
        template = rules.Rule("sender", "*", action, label_id, label_name)
        for row in rows:
            rules.add_rule(self.config, rules.Rule("sender", row.stats.email, action, label_id, label_name))
        verb = {"label": f"Labelled as {label_name}", "archive": "Archived", "trash": "Deleted",
                "mark_read": "Marked as read"}[action]
        self._run_rows(rows, action, template, f"{len(rows)} rule{'s' if len(rows) != 1 else ''} added. {verb}")

    # --------------------------------------------------------- unsubscribe

    def _unsubscribe(self, rows: list[SenderRow]) -> None:
        queue = [r for r in rows if r.stats.unsubscribe]
        skipped = len(rows) - len(queue)
        if not queue:
            toast(self, "None of the selected senders offers an unsubscribe method")
            return
        done: list[SenderRow] = []
        failed: list[tuple[SenderRow, str]] = []
        self._start()

        def step() -> None:
            if not queue:
                n = len(done)
                text = f"Unsubscribed from {n} sender{'s' if n != 1 else ''}"
                if failed:
                    text += f", {len(failed)} failed: " + "; ".join(f"{r.stats.name}: {m}" for r, m in failed[:3])
                if skipped:
                    text += f", {skipped} without an unsubscribe method"
                self._finish(text, [])
                return
            row = queue.pop(0)
            row.refresh("unsubscribing…")
            sample = senders.newest_list_mail(self.db, row.stats.email, self.engine.trash_junk_ids())
            if sample is None:
                failed.append((row, "no message with the header"))
                step()
                return

            def go(email: dict) -> None:
                plan = parse_list_unsubscribe(email.get(H_LIST_UNSUBSCRIBE), email.get(UNSUBSCRIBE_POST))
                if plan is None:
                    failed.append((row, "header unusable"))
                    step()
                    return

                def ok(_plan) -> None:
                    self.config.mark_unsubscribed(row.stats.email)
                    done.append(row)
                    step()

                def err(message: str) -> None:
                    failed.append((row, message))
                    step()

                run_unsubscribe(self.engine, self.db, email, plan, self.get_root(), ok, err)

            if UNSUBSCRIBE_POST in sample:
                go(sample)
            else:   # the -Post header is a body property: one small fetch
                self.engine.fetch_email_headers(sample["id"], [H_LIST_UNSUBSCRIBE, UNSUBSCRIBE_POST],
                                                lambda headers: go({**sample, **headers}),
                                                lambda message: (failed.append((row, message)), step()))

        step()


def _stamp(iso: str) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0
