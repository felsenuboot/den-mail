"""User actions on emails, expressed as local+remote patches with inverses."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..jmap.types import KW_FLAGGED, KW_SEEN, ROLE_ARCHIVE, ROLE_INBOX, ROLE_JUNK, ROLE_TRASH


@dataclass
class EmailAction:
    """A change to apply to a set of emails.

    keyword_changes: keyword -> True (set) / False (clear)
    mailbox_add / mailbox_remove: incremental label changes
    mailbox_replace: replace the full set (a "move")
    destroy: permanently delete
    """

    email_ids: list[str]
    description: str
    keyword_changes: dict[str, bool] = field(default_factory=dict)
    mailbox_add: set[str] = field(default_factory=set)
    mailbox_remove: set[str] = field(default_factory=set)
    mailbox_replace: set[str] | None = None
    destroy: bool = False
    undoable: bool = True

    def apply_to(self, email: dict, fallback_mailbox: str | None) -> tuple[dict, dict]:
        """Return (new_keywords, new_mailbox_ids) for one email."""
        keywords = dict(email.get("keywords") or {})
        for kw, on in self.keyword_changes.items():
            if on:
                keywords[kw] = True
            else:
                keywords.pop(kw, None)
        current = {m for m, on in (email.get("mailboxIds") or {}).items() if on}
        if self.mailbox_replace is not None:
            new = set(self.mailbox_replace)
        else:
            new = (current | self.mailbox_add) - self.mailbox_remove
        if not new and fallback_mailbox:
            new = {fallback_mailbox}
        return keywords, {m: True for m in new}


@dataclass
class UndoRecord:
    """Everything needed to restore emails after an action."""

    description: str
    originals: dict[str, tuple[dict, dict]]  # email_id -> (keywords, mailboxIds)

    def to_action(self) -> RestoreAction:
        return RestoreAction(self)


@dataclass
class RestoreAction:
    record: UndoRecord

    @property
    def email_ids(self) -> list[str]:
        return list(self.record.originals)

    @property
    def description(self) -> str:
        return f"Undid: {self.record.description}"


# ------------------------------------------------------------ constructors


def mark_read(ids: list[str], read: bool = True) -> EmailAction:
    return EmailAction(ids, "Marked as read" if read else "Marked as unread", keyword_changes={KW_SEEN: read},
                       undoable=False)


def flag(ids: list[str], flagged: bool = True) -> EmailAction:
    return EmailAction(ids, "Flagged" if flagged else "Unflagged", keyword_changes={KW_FLAGGED: flagged},
                       undoable=False)


def archive(ids: list[str], roles: dict[str, str], from_mailbox: str | None = None) -> EmailAction:
    remove = {roles[ROLE_INBOX]} if ROLE_INBOX in roles else set()
    if from_mailbox:
        remove.add(from_mailbox)
    add = {roles[ROLE_ARCHIVE]} if ROLE_ARCHIVE in roles else set()
    return EmailAction(ids, "Archived", mailbox_add=add, mailbox_remove=remove)


def trash(ids: list[str], roles: dict[str, str]) -> EmailAction:
    return EmailAction(ids, "Moved to Trash", mailbox_replace={roles[ROLE_TRASH]})


def junk(ids: list[str], roles: dict[str, str]) -> EmailAction:
    return EmailAction(ids, "Marked as spam", mailbox_replace={roles[ROLE_JUNK]},
                       keyword_changes={"$junk": True, "$notjunk": False})


def not_junk(ids: list[str], roles: dict[str, str]) -> EmailAction:
    return EmailAction(ids, "Marked as not spam", mailbox_replace={roles[ROLE_INBOX]},
                       keyword_changes={"$junk": False, "$notjunk": True})


def move(ids: list[str], target: str, name: str) -> EmailAction:
    return EmailAction(ids, f"Moved to {name}", mailbox_replace={target})


def add_label(ids: list[str], mailbox_id: str, name: str) -> EmailAction:
    return EmailAction(ids, f"Labelled {name}", mailbox_add={mailbox_id})


def remove_label(ids: list[str], mailbox_id: str, name: str) -> EmailAction:
    return EmailAction(ids, f"Removed label {name}", mailbox_remove={mailbox_id})


def set_labels(ids: list[str], add: set[str], remove: set[str]) -> EmailAction:
    return EmailAction(ids, "Labels changed", mailbox_add=set(add), mailbox_remove=set(remove))


def destroy(ids: list[str]) -> EmailAction:
    return EmailAction(ids, "Deleted permanently", destroy=True, undoable=False)
