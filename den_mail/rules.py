"""Client-side rules (#22): what to do with new mail from a sender, a domain, a list or a category.

A rule matches on one thing and does one thing: label, archive, mark as
read, or move to Trash.  Rules live in the config file and run in the sync
engine when new mail lands in the Inbox while the app is open; they never
touch the backlog on their own.  Fastmail's own rules run on the server
whether the app is open or not, and the rules dialog links to them.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .classify.rules import CATEGORY_NAMES, H_LIST_ID
from .jmap.types import KW_SEEN, ROLE_ARCHIVE, ROLE_INBOX, ROLE_TRASH
from .store.actions import EmailAction

MATCH_KINDS = ("sender", "domain", "list", "category")
MATCH_NAMES = {"sender": "Sender", "domain": "Domain", "list": "List", "category": "Category"}
ACTIONS = ("label", "archive", "mark_read", "trash")
ACTION_NAMES = {"label": "Label as…", "archive": "Archive", "mark_read": "Mark as read", "trash": "Delete"}
FASTMAIL_RULES_URL = "https://app.fastmail.com/settings/rules"
CAP_SIEVE = "urn:ietf:params:jmap:sieve"

_LIST_ID_RE = re.compile(r"<([^<>]+)>")


@dataclass
class Rule:
    kind: str                 # one of MATCH_KINDS
    value: str                # address, domain, list id or category, lowercased
    action: str               # one of ACTIONS
    label_id: str | None = None
    label_name: str = ""      # shown when the label no longer exists
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: str = field(default_factory=lambda: datetime.now(UTC).date().isoformat())
    hits: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> Rule | None:
        kind, value, action = d.get("kind"), (d.get("value") or "").strip().lower(), d.get("action")
        if kind not in MATCH_KINDS or action not in ACTIONS or not value:
            return None
        if action == "label" and not d.get("label_id"):
            return None
        return cls(kind, value, action, d.get("label_id"), d.get("label_name") or "",
                   d.get("id") or uuid.uuid4().hex[:12], d.get("created") or "", int(d.get("hits") or 0))

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "value": self.value, "action": self.action,
                "label_id": self.label_id, "label_name": self.label_name, "created": self.created, "hits": self.hits}

    def matches(self, email: dict, category: str | None) -> bool:
        if self.kind == "sender":
            return sender_of(email) == self.value
        if self.kind == "domain":
            addr = sender_of(email)
            return addr.endswith(("@" + self.value, "." + self.value))
        if self.kind == "list":
            return list_id_of(email) == self.value
        return (category or "").lower() == self.value

    def describe_match(self) -> str:
        if self.kind == "sender":
            return f"Mail from {self.value}"
        if self.kind == "domain":
            return f"Mail from anyone at {self.value}"
        if self.kind == "list":
            return f"Mail on the list {self.value}"
        return f"{CATEGORY_NAMES.get(self.value, self.value)} mail"

    def describe_action(self, label_name: str | None = None) -> str:
        if self.action == "label":
            return f"label as {label_name or self.label_name or 'a label that no longer exists'}"
        return {"archive": "archive", "mark_read": "mark as read", "trash": "move to Trash"}[self.action]


def sender_of(email: dict) -> str:
    frm = email.get("from") or []
    first = frm[0] if frm and isinstance(frm[0], dict) else {}
    return (first.get("email") or "").strip().lower()


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[1].strip().lower() if "@" in address else ""


def list_id_of(email: dict) -> str:
    """The id inside a List-Id header: "GTK development <gtk-devel.lists.example>" -> gtk-devel.lists.example."""
    raw = email.get(H_LIST_ID)
    if not isinstance(raw, str) or not raw.strip():
        return ""
    m = _LIST_ID_RE.search(raw)
    return (m.group(1) if m else raw).strip().lower()


def load_rules(config) -> list[Rule]:
    out: list[Rule] = []
    for d in config.get("rules") or []:
        if isinstance(d, dict) and (rule := Rule.from_dict(d)) is not None:
            out.append(rule)
    return out


def save_rules(config, rules: list[Rule]) -> None:
    config.set("rules", [r.to_dict() for r in rules])


def add_rule(config, rule: Rule) -> Rule:
    """Store a rule; one that matches and does the same as an existing rule replaces it."""
    rules = [r for r in load_rules(config)
             if not (r.kind == rule.kind and r.value == rule.value and r.action == rule.action
                     and r.label_id == rule.label_id)]
    rules.append(rule)
    save_rules(config, rules)
    return rule


def remove_rule(config, rule_id: str) -> None:
    save_rules(config, [r for r in load_rules(config) if r.id != rule_id])


def bump_hits(config, hits: dict[str, int]) -> None:
    """Count how often each rule fired (the engine reports it on the main thread)."""
    current = load_rules(config)
    for r in current:
        r.hits += hits.get(r.id, 0)
    if any(hits.get(r.id) for r in current):
        save_rules(config, current)


def matching_rules(rules: list[Rule], email: dict, category: str | None) -> list[Rule]:
    return [r for r in rules if r.matches(email, category)]


def combine(email_ids: list[str], rules: list[Rule], roles: dict[str, str]) -> EmailAction | None:
    """One Email/set for everything the matched rules ask of these messages.

    Trash wins over the others (a trashed message needs no label); archive
    removes the Inbox and adds Archive; labels add up; mark-as-read sets $seen.
    Not undoable: the user asked for it in advance."""
    if not rules or not email_ids:
        return None
    keywords: dict[str, bool] = {}
    add: set[str] = set()
    remove: set[str] = set()
    replace: set[str] | None = None
    what: list[str] = []
    for r in rules:
        if r.action == "mark_read":
            keywords[KW_SEEN] = True
        elif r.action == "archive":
            if ROLE_INBOX in roles:
                remove.add(roles[ROLE_INBOX])
            if ROLE_ARCHIVE in roles:
                add.add(roles[ROLE_ARCHIVE])
        elif r.action == "trash" and ROLE_TRASH in roles:
            replace = {roles[ROLE_TRASH]}
        elif r.action == "label" and r.label_id:
            add.add(r.label_id)
        what.append(r.describe_action())
    if replace is not None:
        add, remove = set(), set()
    if not keywords and not add and not remove and replace is None:
        return None
    return EmailAction(email_ids, "Rule: " + ", ".join(dict.fromkeys(what)), keyword_changes=keywords,
                       mailbox_add=add, mailbox_remove=remove, mailbox_replace=replace, undoable=False)


def plan(rules: list[Rule], emails: list[dict], categories: dict[str, str], roles: dict[str, str],
         inbox_id: str | None) -> tuple[list[EmailAction], dict[str, int]]:
    """The actions to run for a batch of new mail, grouped by the set of rules
    that fired, and how often each rule fired (by rule id)."""
    groups: dict[tuple[str, ...], tuple[list[Rule], list[str]]] = {}
    hits: dict[str, int] = {}
    for e in emails:
        if inbox_id and not (e.get("mailboxIds") or {}).get(inbox_id):
            continue   # only mail that lands in the Inbox; sent mail and drafts pass by
        fired = matching_rules(rules, e, categories.get(e["id"]))
        if not fired:
            continue
        key = tuple(sorted(r.id for r in fired))
        groups.setdefault(key, (fired, []))[1].append(e["id"])
        for r in fired:
            hits[r.id] = hits.get(r.id, 0) + 1
    out: list[EmailAction] = []
    for fired, ids in groups.values():
        act = combine(ids, fired, roles)
        if act is not None:
            out.append(act)
    return out, hits
