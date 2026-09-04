"""Client-side rules (#22): the dialog that lists them and the prompt that creates one."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk

from .. import rules
from ..classify.rules import CATEGORIES, CATEGORY_NAMES
from ..models.mailbox import MailboxTree
from .a11y import watch as _a11y_watch
from .widgets import open_uri, toast

ACTION_LABELS = ["Label as…", "Archive", "Mark as read", "Delete"]
KIND_LABELS = ["Sender address", "Sender domain", "List id", "Category"]


class _RuleForm:
    """The rows shared by the prompt and the dialog: match, action, label."""

    def __init__(self, tree: MailboxTree, kinds: list[str], values: dict[str, str], parent_list: Gtk.Widget):
        self.tree = tree
        self.kinds = kinds
        self.values = values
        self.labels = tree.labels()
        self.group = Adw.PreferencesGroup()
        self.kind = Adw.ComboRow(title="When", model=Gtk.StringList.new(self._kind_titles()))
        self.kind.connect("notify::selected", lambda *_: self._sync())
        self.group.add(self.kind)
        self.value = Adw.EntryRow(title="Address, domain or list id")
        self.group.add(self.value)
        self.category = Adw.ComboRow(title="Category", model=Gtk.StringList.new([CATEGORY_NAMES[c] for c in CATEGORIES]))
        self.group.add(self.category)
        self.action = Adw.ComboRow(title="Then", model=Gtk.StringList.new(ACTION_LABELS))
        self.action.connect("notify::selected", lambda *_: self._sync())
        self.group.add(self.action)
        names = [tree.path_name(m.id) for m in self.labels] or ["(no labels yet)"]
        self.label = Adw.ComboRow(title="Label", model=Gtk.StringList.new(names), sensitive=bool(self.labels))
        self.group.add(self.label)
        self._sync()

    def _kind_titles(self) -> list[str]:
        titles = []
        for k in self.kinds:
            v = self.values.get(k)
            titles.append(f"{KIND_LABELS[rules.MATCH_KINDS.index(k)]}: {v}" if v else KIND_LABELS[rules.MATCH_KINDS.index(k)])
        return titles

    @property
    def selected_kind(self) -> str:
        return self.kinds[self.kind.get_selected()]

    def _sync(self) -> None:
        kind = self.selected_kind
        fixed = kind in self.values
        self.value.set_visible(not fixed and kind != "category")
        self.category.set_visible(not fixed and kind == "category")
        self.label.set_visible(self.action.get_selected() == 0)

    def rule(self) -> rules.Rule | None:
        kind = self.selected_kind
        if kind in self.values:
            value = self.values[kind]
        elif kind == "category":
            value = CATEGORIES[self.category.get_selected()]
        else:
            value = self.value.get_text().strip().lower().lstrip("@")
        action = rules.ACTIONS[self.action.get_selected()]
        label_id = label_name = None
        if action == "label":
            if not self.labels:
                return None
            mb = self.labels[self.label.get_selected()]
            label_id, label_name = mb.id, self.tree.path_name(mb.id)
        if not value or (kind == "list" and "@" in value) or (kind == "domain" and "@" in value):
            return None
        return rules.Rule(kind, value, action, label_id, label_name or "")


def prompt_sender_rule(parent: Gtk.Widget, tree: MailboxTree, config, sender: str,
                       on_done: Callable[[rules.Rule, bool], None], can_apply_now: bool = True) -> None:
    """"Always for this sender…": choose sender or domain, the action, a label;
    `on_done(rule, apply_now)` gets the stored rule and whether existing mail should get it too."""
    sender = sender.strip().lower()
    domain = rules.domain_of(sender)
    values = {"sender": sender}
    kinds = ["sender"]
    if domain:
        values["domain"] = domain
        kinds.append("domain")
    form = _RuleForm(tree, kinds, values, parent)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.append(form.group)
    apply_now = Adw.SwitchRow(title="Also apply to their mail now", subtitle="Everything from them outside Trash and Spam",
                              active=False)
    if can_apply_now:
        extra = Adw.PreferencesGroup()
        extra.add(apply_now)
        box.append(extra)
    dlg = Adw.AlertDialog(heading="Always for this sender",
                          body="A Den Mail rule: runs on this computer while the app is open, on mail that lands in the "
                               "Inbox. For a rule that runs on the server for every device, use Fastmail's settings "
                               "(Rules… in the main menu links there).")
    dlg.set_extra_child(box)
    dlg.add_response("cancel", "Cancel")
    dlg.add_response("ok", "Add rule")
    dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("ok")
    dlg.set_close_response("cancel")

    def on_response(_d, response):
        if response != "ok":
            return
        rule = form.rule()
        if rule is None:
            toast(parent, "The rule needs a label; create one first")
            return
        rules.add_rule(config, rule)
        on_done(rule, can_apply_now and apply_now.get_active())

    dlg.connect("response", on_response)
    dlg.present(parent)


class RuleRow(Adw.ActionRow):
    def __init__(self, rule: rules.Rule, tree: MailboxTree, on_delete: Callable[[rules.Rule], None]):
        super().__init__(title=GLib.markup_escape_text(rule.describe_match()))
        self.rule = rule
        label = tree.path_name(rule.label_id) if rule.label_id and tree.get(rule.label_id) else None
        parts = [rule.describe_action(label).capitalize()]
        if rule.hits:
            parts.append(f"applied {rule.hits} time{'s' if rule.hits != 1 else ''}")
        if rule.created:
            parts.append(f"since {rule.created}")
        self.set_subtitle(GLib.markup_escape_text(" · ".join(parts)))
        if rule.action == "label" and label is None:
            self.add_css_class("error")
            self.set_tooltip_text("The label no longer exists; the rule does nothing until it is recreated")
        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove rule")
        remove.add_css_class("flat")
        remove.connect("clicked", lambda *_: on_delete(rule))
        self.add_suffix(remove)


class RulesDialog(Adw.Dialog):
    """Every client-side rule, a form for a new one, and the way to Fastmail's server-side rules."""

    def __init__(self, engine, db, config, tree: MailboxTree):
        super().__init__(title="Rules", content_width=620, content_height=680)
        self.engine = engine
        self.config = config
        self.tree = tree
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        self.list_group = Adw.PreferencesGroup(
            title="Rules in Den Mail",
            description="Den Mail's own: run on this computer while the app is open, on mail landing in the Inbox.")
        page.add(self.list_group)
        self.empty = Adw.ActionRow(title="No rules yet", subtitle="Right-click a conversation and choose “Always for this sender…”, or add one below.")
        self.empty.add_css_class("dim-label")
        self.rows: list[RuleRow] = []

        self.form = _RuleForm(tree, list(rules.MATCH_KINDS), {}, self)
        self.form.group.set_title("New rule")
        add = Adw.ButtonRow(title="Add rule")
        add.connect("activated", lambda *_: self._add())
        self.form.group.add(add)
        page.add(self.form.group)

        server = Adw.PreferencesGroup(
            title="Rules in Fastmail",
            description="Made in Fastmail's settings, run on the server for every app; not shown here.")
        session = getattr(engine.client, "session", None)
        sieve = bool(session and rules.CAP_SIEVE in (session.capabilities or {}))
        link = Adw.ActionRow(title="Open Fastmail's rules settings", activatable=True,
                             subtitle="In the browser; for a rule that should work on the server, make it there"
                                      + (". This account advertises JMAP Sieve, so they could be managed here later" if sieve else ""))
        link.add_suffix(Gtk.Image(icon_name="external-link-symbolic"))
        link.connect("activated", lambda *_: open_uri(rules.FASTMAIL_RULES_URL, self.get_root()))
        server.add(link)
        page.add(server)
        view.set_content(page)
        self.toast_overlay = Adw.ToastOverlay(child=view)
        self.set_child(self.toast_overlay)
        self.reload()
        _a11y_watch(self)   # icon-only buttons get their tooltip as accessible name (#123)

    def reload(self) -> None:
        for row in self.rows:
            self.list_group.remove(row)
        self.rows = []
        if self.empty.get_parent() is not None:
            self.list_group.remove(self.empty)
        current = rules.load_rules(self.config)
        for rule in current:
            row = RuleRow(rule, self.tree, self._delete)
            self.rows.append(row)
            self.list_group.add(row)
        if not current:
            self.list_group.add(self.empty)

    def _delete(self, rule: rules.Rule) -> None:
        rules.remove_rule(self.config, rule.id)
        self.reload()
        toast(self, f"Rule removed: {rule.describe_match()}")

    def _add(self) -> None:
        rule = self.form.rule()
        if rule is None:
            toast(self, "Fill in what to match; a label rule needs a label")
            return
        rules.add_rule(self.config, rule)
        self.form.value.set_text("")
        self.reload()
        toast(self, f"Rule added: {rule.describe_match()} → {rule.describe_action()}")
