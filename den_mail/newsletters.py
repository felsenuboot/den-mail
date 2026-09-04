"""Mass unsubscribe: find every sender whose mail carries a List-Unsubscribe header, one row per sender."""

from __future__ import annotations

from dataclasses import dataclass, field

from .jmap.client import JMAPClient, Request
from .jmap.types import DELIVERED_TO_HEADERS, KW_SEEN, address_display
from .unsubscribe import UnsubscribePlan, parse_list_unsubscribe

HEADER_UNSUBSCRIBE = "header:List-Unsubscribe:asRaw"
HEADER_POST = "header:List-Unsubscribe-Post:asRaw"
# Enough to group by sender, pick an unsubscribe method and the identity to answer from.
LIST_MAIL_PROPERTIES = ["id", "threadId", "from", "to", "cc", "receivedAt", "keywords", "mailboxIds",
                        HEADER_UNSUBSCRIBE, HEADER_POST, *DELIVERED_TO_HEADERS]
GET_BATCH = 500


def fetch_list_mail(client: JMAPClient, trash_junk: list[str], limit: int = 2000) -> list[dict]:
    """The newest `limit` messages outside Trash and Spam that carry a List-Unsubscribe header."""
    acc = client.session.account_id
    filt: dict = {"header": ["List-Unsubscribe"]}
    if trash_junk:
        filt = {"operator": "AND", "conditions": [filt, {"inMailboxOtherThan": trash_junk}]}
    res = client.call("Email/query", {"accountId": acc, "filter": filt, "collapseThreads": False, "limit": limit,
                                      "sort": [{"property": "receivedAt", "isAscending": False}]})
    ids = res.get("ids") or []
    if not ids:
        return []
    req = Request()
    calls = [req.add("Email/get", {"accountId": acc, "ids": ids[i:i + GET_BATCH], "properties": LIST_MAIL_PROPERTIES})
             for i in range(0, len(ids), GET_BATCH)]
    resp = client.send(req)
    return [e for c in calls for e in resp.get(c)["list"]]


@dataclass
class Sender:
    """One newsletter sender: who, how much they send, and how to unsubscribe."""

    email: str
    name: str = ""
    count: int = 0
    unread: int = 0
    last_at: str = ""
    plan: UnsubscribePlan | None = None
    sample: dict = field(default_factory=dict)   # the newest message, for the identity to answer from
    email_ids: list[str] = field(default_factory=list)
    unsubscribed_on: str | None = None

    @property
    def method(self) -> str:
        return {"one-click": "one-click", "mailto": "by mail", "browser": "web page"}[self.plan.kind] if self.plan else ""


def group_senders(emails: list[dict], unsubscribed: dict[str, str] | None = None) -> list[Sender]:
    """Fold list mail into one Sender per From address, busiest first; the newest parsable header sets the plan."""
    unsubscribed = unsubscribed or {}
    by_addr: dict[str, Sender] = {}
    for e in sorted(emails, key=lambda e: e.get("receivedAt") or "", reverse=True):
        frm = (e.get("from") or [{}])[0]
        addr = (frm.get("email") or "").strip().lower()
        if not addr:
            continue
        s = by_addr.get(addr)
        if s is None:
            s = by_addr[addr] = Sender(email=addr, name=address_display(frm) or addr, last_at=e.get("receivedAt") or "",
                                       sample=e, unsubscribed_on=unsubscribed.get(addr))
        s.count += 1
        if not (e.get("keywords") or {}).get(KW_SEEN):
            s.unread += 1
        s.email_ids.append(e["id"])
        if s.plan is None:
            s.plan = parse_list_unsubscribe(e.get(HEADER_UNSUBSCRIBE), e.get(HEADER_POST))
            if s.plan is not None:
                s.sample = e
    return sorted(by_addr.values(), key=lambda s: (-s.count, s.name.lower()))
