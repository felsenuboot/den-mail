"""Constants and small value types for the JMAP protocol as Fastmail speaks it."""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_SESSION_URL = "https://api.fastmail.com/jmap/session"

CAP_CORE = "urn:ietf:params:jmap:core"
CAP_MAIL = "urn:ietf:params:jmap:mail"
CAP_SUBMISSION = "urn:ietf:params:jmap:submission"
CAP_MASKED_EMAIL = "https://www.fastmail.com/dev/maskedemail"

# Mailbox roles (RFC 8621 §2 + IANA registry). Fastmail uses all of these.
ROLE_INBOX = "inbox"
ROLE_ARCHIVE = "archive"
ROLE_DRAFTS = "drafts"
ROLE_SENT = "sent"
ROLE_JUNK = "junk"
ROLE_TRASH = "trash"
# Fastmail-specific roles
ROLE_SNOOZED = "snoozed"
ROLE_SCHEDULED = "scheduled"
ROLE_TEMPLATES = "xtemplates"
ROLE_ORDER = [ROLE_INBOX, ROLE_DRAFTS, ROLE_SENT, ROLE_ARCHIVE, ROLE_JUNK, ROLE_TRASH, ROLE_SNOOZED, ROLE_SCHEDULED,
              ROLE_TEMPLATES]
ROLE_ICONS = {
    ROLE_INBOX: "fm-inbox-symbolic",
    ROLE_ARCHIVE: "fm-archive-symbolic",
    ROLE_DRAFTS: "fm-drafts-symbolic",
    ROLE_SENT: "fm-sent-symbolic",
    ROLE_JUNK: "fm-junk-symbolic",
    ROLE_TRASH: "user-trash-symbolic",
    ROLE_SNOOZED: "fm-snoozed-symbolic",
    ROLE_SCHEDULED: "fm-scheduled-symbolic",
    ROLE_TEMPLATES: "fm-templates-symbolic",
    None: "folder-symbolic",
}

KW_SEEN = "$seen"
KW_FLAGGED = "$flagged"
KW_DRAFT = "$draft"
KW_ANSWERED = "$answered"
KW_FORWARDED = "$forwarded"
KW_JUNK = "$junk"
KW_NOTJUNK = "$notjunk"

# Properties needed to render a thread list row and compute thread aggregates.
EMAIL_LIST_PROPERTIES = [
    "id",
    "blobId",
    "threadId",
    "mailboxIds",
    "keywords",
    "size",
    "receivedAt",
    "sentAt",
    "from",
    "to",
    "cc",
    "bcc",
    "replyTo",
    "subject",
    "preview",
    "hasAttachment",
    "messageId",
    "inReplyTo",
    "references",
    # Presence of these decides the category of a message (#18); asRaw because
    # Fastmail rejects :asText for List-Unsubscribe.
    "header:List-Post:asRaw",
    "header:List-Id:asRaw",
    "header:List-Unsubscribe:asRaw",
    "header:Precedence:asRaw",
    "header:Auto-Submitted:asRaw",
    "header:Feedback-ID:asRaw",
]

# Everything needed to display and reply to a message.
EMAIL_BODY_PROPERTIES = [
    *EMAIL_LIST_PROPERTIES,
    "sender",
    "bodyStructure",
    "textBody",
    "htmlBody",
    "attachments",
    "bodyValues",
    "header:X-Delivered-To:asText",  # Fastmail's delivery address; Delivered-To only on forwarded mail
    "header:Delivered-To:asText",
    "header:List-Unsubscribe-Post:asRaw",  # List-Unsubscribe itself is a list property
]

MAX_BODY_VALUE_BYTES = 2_000_000

MASKED_STATES = ("pending", "enabled", "disabled", "deleted")


@dataclass
class Session:
    """The subset of the JMAP session resource this client uses."""

    username: str
    api_url: str
    download_url: str
    upload_url: str
    event_source_url: str | None
    state: str
    account_id: str
    submission_account_id: str | None
    masked_account_id: str | None
    accounts: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict) -> Session:
        primary = data.get("primaryAccounts", {})
        mail_account = primary.get(CAP_MAIL)
        if not mail_account:
            # Fall back to the first account that advertises mail.
            for acc_id, acc in data.get("accounts", {}).items():
                if CAP_MAIL in acc.get("accountCapabilities", {}):
                    mail_account = acc_id
                    break
        if not mail_account:
            raise ValueError("Session has no mail account")
        return cls(
            username=data.get("username", ""),
            api_url=data["apiUrl"],
            download_url=data["downloadUrl"],
            upload_url=data["uploadUrl"],
            event_source_url=data.get("eventSourceUrl"),
            state=data.get("state", ""),
            account_id=mail_account,
            submission_account_id=primary.get(CAP_SUBMISSION, mail_account),
            masked_account_id=primary.get(CAP_MASKED_EMAIL),
            accounts=data.get("accounts", {}),
            capabilities=data.get("capabilities", {}),
            raw=data,
        )

    @property
    def has_masked_email(self) -> bool:
        return self.masked_account_id is not None

    @property
    def account_name(self) -> str:
        acc = self.accounts.get(self.account_id, {})
        return acc.get("name") or self.username

    def max_upload_size(self) -> int:
        core = self.capabilities.get(CAP_CORE, {})
        return int(core.get("maxSizeUpload", 50_000_000))

    def max_objects_in_get(self) -> int:
        core = self.capabilities.get(CAP_CORE, {})
        return int(core.get("maxObjectsInGet", 500))


DELIVERED_TO_HEADERS = ("header:X-Delivered-To:asText", "header:Delivered-To:asText")


def delivered_to(email: dict) -> str | None:
    """The address a message was delivered to, from X-Delivered-To (Fastmail) or Delivered-To."""
    for key in DELIVERED_TO_HEADERS:
        value = (email.get(key) or "").strip()
        if value:
            return value
    return None


def address_display(addr: dict | None) -> str:
    """Render an EmailAddress object as 'Name' or the bare address."""
    if not addr:
        return ""
    name = (addr.get("name") or "").strip()
    return name or (addr.get("email") or "")


def address_full(addr: dict | None) -> str:
    """Render an EmailAddress object as 'Name <email>'."""
    if not addr:
        return ""
    name = (addr.get("name") or "").strip()
    email = addr.get("email") or ""
    return f"{name} <{email}>" if name else email
