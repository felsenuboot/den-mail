"""An in-process JMAP server that speaks enough of RFC 8620/8621 (plus Fastmail's
Masked Email extension) to exercise the whole client without a real account.

Run standalone:  python -m tests.fake_server   (prints the session URL)
Then:            DEN_MAIL_SESSION_URL=<url> DEN_MAIL_TOKEN=fake-token den-mail
"""

from __future__ import annotations

import html as html_module
import json
import random
import re
import threading
import time
import uuid
import zlib
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

TOKEN = "fake-token"
ACCOUNT = "u12345"
CAP_CORE = "urn:ietf:params:jmap:core"
CAP_MAIL = "urn:ietf:params:jmap:mail"
CAP_SUBMISSION = "urn:ietf:params:jmap:submission"
CAP_MASKED = "https://www.fastmail.com/dev/maskedemail"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _preview(text: str | None, html: str | None) -> str:
    """Like a real server: the words of the message, without markup, entities or quoted history."""
    from den_mail.html.totext import split_quoted_text

    if text is None:
        stripped = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", html or "", flags=re.DOTALL | re.IGNORECASE)
        stripped = re.sub(r"<[^>]+?class=\"[^\"]*gmail_quote[^\"]*\"[^>]*>.*$", " ", stripped, flags=re.DOTALL)
        text = html_module.unescape(re.sub(r"<[^>]+>", " ", stripped))
    else:
        text = split_quoted_text(text)[0]
    return re.sub(r"\s+", " ", text).strip()[:200]


def _png_1x1(rgb=(255, 120, 0)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = tag + data
        return len(data).to_bytes(4, "big") + c + zlib.crc32(c).to_bytes(4, "big")

    raw = b"\x00" + bytes(rgb)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class MethodError(Exception):
    def __init__(self, error_type: str, description: str | None = None):
        super().__init__(error_type)
        self.type = error_type
        self.description = description


class FakeData:
    """All mutable state, guarded by one lock."""

    def __init__(self, seed: int = 7):
        self.lock = threading.RLock()
        self.cv = threading.Condition(self.lock)
        self.counter = 1
        self.states = {"Email": 1, "Mailbox": 1, "Thread": 1, "Identity": 1, "MaskedEmail": 1}
        self.changes: dict[str, list[tuple[int, str, str]]] = {k: [] for k in self.states}
        self.mailboxes: dict[str, dict] = {}
        self.emails: dict[str, dict] = {}
        self.threads: dict[str, list[str]] = {}
        self.identities: dict[str, dict] = {}
        self.masked: dict[str, dict] = {}
        self.blobs: dict[str, tuple[bytes, str, str]] = {}
        self.submissions: dict[str, dict] = {}
        self.query_snapshots: dict[tuple[str, str], list[str]] = {}
        self.rng = random.Random(seed)
        self._seq = 0
        self._build_fixture()

    # ------------------------------------------------------------ utilities

    def new_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq:04d}"

    def bump(self, kind: str, obj_id: str, change: str) -> None:
        self.counter += 1
        self.states[kind] = self.counter
        self.changes[kind].append((self.counter, obj_id, change))
        self.cv.notify_all()

    def state(self, kind: str) -> str:
        return str(self.states[kind])

    def changes_since(self, kind: str, since: str, ids_alive) -> dict:
        try:
            since_n = int(since)
        except (TypeError, ValueError):
            raise MethodError("cannotCalculateChanges", "unknown state") from None
        created, updated, destroyed = [], [], []
        seen: dict[str, str] = {}
        for n, obj_id, change in self.changes[kind]:
            if n <= since_n:
                continue
            if change == "created":
                seen[obj_id] = "created"
            elif change == "destroyed":
                seen[obj_id] = "destroyed" if seen.get(obj_id) != "created" else "gone"
            else:
                seen.setdefault(obj_id, "updated")
        for obj_id, kind_ in seen.items():
            if kind_ == "created" and ids_alive(obj_id):
                created.append(obj_id)
            elif kind_ == "updated" and ids_alive(obj_id):
                updated.append(obj_id)
            elif kind_ == "destroyed":
                destroyed.append(obj_id)
        return {"oldState": since, "newState": self.state(kind), "hasMoreChanges": False,
                "created": created, "updated": updated, "destroyed": destroyed}

    # ---------------------------------------------------------------- fixture

    def add_mailbox(self, name: str, role: str | None = None, parent: str | None = None, sort: int = 0) -> str:
        mid = self.new_id("mb")
        self.mailboxes[mid] = {
            "id": mid, "name": name, "parentId": parent, "role": role, "sortOrder": sort,
            "totalEmails": 0, "unreadEmails": 0, "totalThreads": 0, "unreadThreads": 0, "isSubscribed": True,
            "myRights": {"mayReadItems": True, "mayAddItems": True, "mayRemoveItems": True, "maySetSeen": True,
                         "maySetKeywords": True, "mayCreateChild": True, "mayRename": role is None,
                         "mayDelete": role is None, "maySubmit": True},
        }
        return mid

    def add_blob(self, data: bytes, ctype: str, name: str) -> str:
        bid = "G" + uuid.uuid4().hex[:20]
        self.blobs[bid] = (data, ctype, name)
        return bid

    def add_email(self, *, frm, to, subject, text=None, html=None, mailboxes, keywords=None, when=None,
                  thread=None, cc=None, attachments=None, inline_png=False, in_reply_to=None, headers=None,
                  sequence=None) -> dict:
        """`sequence`: an Apple-Mail-style body, a list of str (text/plain) and
        (name, mime, bytes) tuples (inline images) with no text/html part;
        textBody and htmlBody then both list the whole sequence."""
        eid = self.new_id("M")
        when = when or datetime.now(UTC)
        values, text_body, html_body, atts = {}, [], [], []
        preview_text = text
        if sequence:
            for i, item in enumerate(sequence, start=1):
                pid = str(i)
                if isinstance(item, str):
                    values[pid] = {"value": item, "isEncodingProblem": False, "isTruncated": False}
                    text_body.append({"partId": pid, "type": "text/plain", "size": len(item), "name": None,
                                      "cid": None, "disposition": None, "charset": "us-ascii"})
                else:
                    name, mime, data = item
                    bid = self.add_blob(data, mime, name)
                    text_body.append({"partId": pid, "blobId": bid, "type": mime, "size": len(data), "name": name,
                                      "cid": None, "disposition": "inline", "charset": None})
            html_body = list(text_body)
            preview_text = "".join(i for i in sequence if isinstance(i, str))
        if text is not None:
            values["1"] = {"value": text, "isEncodingProblem": False, "isTruncated": False}
            text_body.append({"partId": "1", "type": "text/plain", "size": len(text), "name": None, "cid": None,
                              "disposition": None, "charset": "utf-8"})
        if html is not None:
            values["2"] = {"value": html, "isEncodingProblem": False, "isTruncated": False}
            html_body.append({"partId": "2", "type": "text/html", "size": len(html), "name": None, "cid": None,
                              "disposition": None, "charset": "utf-8"})
        if inline_png:
            bid = self.add_blob(_png_1x1(), "image/png", "logo.png")
            atts.append({"partId": "3", "blobId": bid, "type": "image/png", "size": 70, "name": "logo.png",
                         "cid": "logo@fake", "disposition": "inline", "charset": None})
        for name, ctype, data in attachments or []:
            bid = self.add_blob(data, ctype, name)
            atts.append({"partId": f"a{len(atts)}", "blobId": bid, "type": ctype, "size": len(data), "name": name,
                         "cid": None, "disposition": "attachment", "charset": None})
        if thread is None:
            thread = self.new_id("T")
        msg_id = f"<{eid}@fake.example>"
        e = {
            "id": eid, "blobId": self.add_blob(b"raw message", "message/rfc822", "message.eml"), "threadId": thread,
            "mailboxIds": {m: True for m in mailboxes}, "keywords": keywords or {},
            "size": 1000 + len(text or "") + len(html or ""), "receivedAt": _iso(when), "sentAt": _iso(when),
            "messageId": [msg_id], "inReplyTo": [in_reply_to] if in_reply_to else None,
            "references": [in_reply_to] if in_reply_to else None,
            "from": [frm], "sender": None, "to": to, "cc": cc, "bcc": None, "replyTo": None, "subject": subject,
            "preview": _preview(preview_text, html),
            "hasAttachment": any(a["disposition"] == "attachment" for a in atts),
            "textBody": text_body, "htmlBody": html_body, "attachments": atts, "bodyValues": values,
            "bodyStructure": {"partId": None, "type": "multipart/mixed", "subParts": text_body + html_body + atts},
            "_headers": headers or {},
        }
        self.emails[eid] = e
        self.threads.setdefault(thread, []).append(eid)
        return e

    def _build_fixture(self) -> None:
        inbox = self.add_mailbox("Inbox", "inbox", sort=1)
        drafts = self.add_mailbox("Drafts", "drafts", sort=2)
        sent = self.add_mailbox("Sent", "sent", sort=3)
        archive = self.add_mailbox("Archive", "archive", sort=4)
        junk = self.add_mailbox("Spam", "junk", sort=5)
        trash = self.add_mailbox("Trash", "trash", sort=6)
        self.add_mailbox("Scheduled", "scheduled", sort=7)
        receipts = self.add_mailbox("Receipts", sort=10)
        work = self.add_mailbox("Work", sort=11)
        projects = self.add_mailbox("Projects", parent=work, sort=12)
        newsletters = self.add_mailbox("Newsletters", sort=13)
        self.add_mailbox("Family", sort=14)

        me = {"name": "Felix Test", "email": "felix@example.com"}
        shop = {"name": "Felix (shop)", "email": "shop@example.com"}
        self.identities = {
            "id1": {"id": "id1", "name": "Felix Test", "email": "felix@example.com", "replyTo": None, "bcc": None,
                    "textSignature": "Cheers,\nFelix", "htmlSignature": "<p>Cheers,<br>Felix</p>", "mayDelete": False},
            "id2": {"id": "id2", "name": "Felix (shop)", "email": "shop@example.com", "replyTo": None, "bcc": None,
                    "textSignature": "", "htmlSignature": "", "mayDelete": False},
            "id3": {"id": "id3", "name": "Felix", "email": "*@example.org", "replyTo": None, "bcc": None,
                    "textSignature": "", "htmlSignature": "", "mayDelete": False},
        }
        now = datetime.now(UTC)
        for i, (state, dom, desc) in enumerate([
            ("enabled", "https://shop.example", "Online shop"),
            ("disabled", "https://forum.example", "Old forum account"),
            ("deleted", "https://spammy.example", "Was spammy"),
            ("pending", "https://new.example", ""),
        ]):
            mid = f"masked-{i + 1}"
            self.masked[mid] = {
                "id": mid, "email": f"{desc.split()[0].lower() if desc else 'quiet'}.{self.rng.randint(1000, 9999)}@fastmail.com",
                "state": state, "forDomain": dom, "description": desc, "url": None,
                "createdBy": "Fake seed", "createdAt": _iso(now - timedelta(days=30 + i)),
                "lastMessageAt": _iso(now - timedelta(days=i)) if state == "enabled" else None, "emailPrefix": "",
            }

        people = [
            {"name": "Anna Berger", "email": "anna@example.net"},
            {"name": "Ben Okafor", "email": "ben@example.net"},
            {"name": "Chiara Rossi", "email": "chiara@example.net"},
            {"name": "GitHub", "email": "noreply@github.com"},
            {"name": "Arch Linux Newsletter", "email": "news@archlinux.org"},
            {"name": "Bahn Tickets", "email": "tickets@bahn.example"},
        ]
        t = now - timedelta(hours=1)
        # A conversation thread of four messages (text)
        thread = None
        prev = None
        # The replies carry the quoted history the way real clients write it (text with an
        # attribution line, and a Gmail-style HTML quote), which the viewer folds away (#9).
        gmail_reply = ('<div dir="ltr">Room 2.04, 18:00. See you both there!<div><br></div><div>Anna</div></div><br>'
                       '<div class="gmail_quote"><div dir="ltr" class="gmail_attr">On Tue, 1 Sep 2026 at 09:12, '
                       'Ben Okafor &lt;<a href="mailto:ben@example.net">ben@example.net</a>&gt; wrote:</div>'
                       '<blockquote class="gmail_quote" style="margin:0 0 0 .8ex;border-left:1px solid #ccc;'
                       'padding-left:1ex">Count me in as well. Which room?<br><br>Ben</blockquote></div>')
        for idx, (who, body, html) in enumerate([
            (people[0], "Hi Felix,\n\nare we still on for the GTK meetup on Thursday?\n\nAnna", None),
            (me, "Hi Anna,\n\nyes! I'll bring the demo laptop.\n\nOn Mon, 31 Aug 2026 at 15:04, Anna Berger wrote:\n"
                 "> Hi Felix,\n>\n> are we still on for the GTK meetup on Thursday?\n>\n> Anna\n", None),
            (people[1], "Count me in as well. Which room?\n\nBen\n\nOn Mon, 31 Aug 2026 at 18:10, Felix Test\n"
                        "<felix@example.com> wrote:\n> Hi Anna,\n>\n> yes! I'll bring the demo laptop.\n", None),
            (people[0], None, gmail_reply),
        ]):
            e = self.add_email(frm=who, to=[me] if who != me else [people[0]], cc=[people[1]] if idx else None,
                               subject="GTK meetup on Thursday" if idx == 0 else "Re: GTK meetup on Thursday",
                               text=body, html=html, mailboxes=[sent if who == me else inbox],
                               keywords={"$seen": True} if idx < 3 else {}, when=t - timedelta(days=3, hours=-idx * 3),
                               thread=thread, in_reply_to=prev)
            thread, prev = e["threadId"], e["messageId"][0]
        # HTML newsletter with remote images
        # Apple-Mail-style: text, a photo, text -- and no text/html part at all
        self.add_email(frm={"name": "Dana Ito", "email": "dana@example.net"}, to=[me], subject="Photo from the workshop", mailboxes=[inbox],
                       when=t - timedelta(hours=2),
                       sequence=["\r\n", ("IMG_0001.png", "image/png", _png_1x1()), "Sent from my iPhone\r\n"])
        self.add_email(frm=people[4], to=[me], subject="Arch Linux news: kernel 7.2 and Plasma 7",
                       html="""<html><head><style>body{font-family:sans-serif}</style></head><body>
<h1 style="color:#1793d1">Arch Linux Newsletter</h1>
<p>This month: <b>kernel 7.2</b> lands in core, <i>Plasma 7</i> hits extra.</p>
<img src="https://example.org/tracking.gif" width="1" height="1">
<p><a href="https://archlinux.org/news/">Read the full announcement</a></p>
<blockquote>Remember to read the news before upgrading.</blockquote>
<ul><li>pacman 8 release candidate</li><li>Repository signing changes</li></ul>
<script>alert('nope')</script></body></html>""",
                       mailboxes=[inbox, newsletters], keywords={}, when=t - timedelta(hours=5),
                       headers={"List-Unsubscribe": "<mailto:unsubscribe@archlinux.org?subject=unsubscribe%20news>, "
                                                    "<{base}unsubscribe/arch-news>",
                                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
        # More list mail: a mailto-only sender with several issues, a web-page-only one, and one in Trash
        weekly = {"name": "Weekly Digest", "email": "digest@lists.example.com"}
        for i in range(3):
            self.add_email(frm=weekly, to=[me], subject=f"Digest #{40 + i}", text=f"Issue {40 + i}.",
                           mailboxes=[inbox, newsletters], keywords={"$seen": True} if i else {},
                           when=t - timedelta(days=7 * i + 1),
                           headers={"List-Unsubscribe": "<mailto:leave@lists.example.com?subject=unsubscribe>"})
        self.add_email(frm={"name": "Shop Promotions", "email": "promo@shop.example"}, to=[me], subject="Sale ends soon",
                       text="Everything must go.", mailboxes=[inbox], when=t - timedelta(days=2),
                       headers={"List-Unsubscribe": "<https://shop.example/unsubscribe?u=42>"})
        self.add_email(frm=weekly, to=[me], subject="Digest #39", text="Old issue.", mailboxes=[trash],
                       keywords={"$seen": True}, when=t - timedelta(days=30),
                       headers={"List-Unsubscribe": "<mailto:leave@lists.example.com?subject=unsubscribe>"})
        # A discussion list (List-Post) and an automated notice (Auto-Submitted), for the categoriser (#18)
        self.add_email(frm={"name": "Erin Walsh", "email": "erin@example.co"}, to=[{"email": "gtk-devel@lists.example"}],
                       subject="[gtk-devel] Widget lifecycle question", text="Is unparent() enough?",
                       mailboxes=[inbox], keywords={"$seen": True}, when=t - timedelta(days=1, hours=5),
                       headers={"List-Id": "GTK development <gtk-devel.lists.example>",
                                "List-Post": "<mailto:gtk-devel@lists.example>",
                                "List-Unsubscribe": "<mailto:gtk-devel-leave@lists.example>", "Precedence": "list"})
        self.add_email(frm={"name": "Backup Bot", "email": "backup@nas.example"}, to=[me],
                       subject="Nightly backup finished", text="All 3 jobs completed without errors.",
                       mailboxes=[inbox], keywords={"$seen": True}, when=t - timedelta(days=1, hours=7),
                       headers={"Auto-Submitted": "auto-generated"})
        # Message with inline image and PDF attachment
        self.add_email(frm=people[5], to=[shop], subject="Your ticket: Berlin → München",
                       html='<p>Thanks for booking. Your ticket is attached.</p><p><img src="cid:logo@fake" alt="logo"></p>',
                       text="Thanks for booking. Your ticket is attached.",
                       mailboxes=[inbox, receipts], keywords={"$seen": True, "$flagged": True},
                       when=t - timedelta(days=1, hours=2), inline_png=True,
                       attachments=[("ticket.pdf", "application/pdf", b"%PDF-1.4 fake ticket\n%%EOF")],
                       headers={"X-Delivered-To": "shop@example.com", "Delivered-To": "shop@example.com"})
        # GitHub notifications thread
        gh_thread = None
        for k in range(3):
            e = self.add_email(frm=people[3], to=[me], subject="[felsenuboot/den-mail] Sync engine review (#12)",
                               text=f"Comment {k + 1}: looks good, one nit about the queue priorities.\n\n-- \nReply to this email directly or view it on GitHub.",
                               mailboxes=[inbox, work, projects] if k == 2 else [archive, work, projects],
                               keywords={"$seen": k < 2}, when=t - timedelta(days=2, hours=-k * 4), thread=gh_thread)
            gh_thread = e["threadId"]
        # Assorted inbox mail
        subjects = ["Invoice 2026-08 for hosting", "Photos from the weekend", "Re: dentist appointment",
                    "Your package is on its way", "Security alert: new sign-in", "Lunch tomorrow?",
                    "Quarterly planning notes", "Welcome to the mailing list", "Reminder: renew domain",
                    "Draft agenda for Monday", "Bike repair quote", "Concert tickets confirmed"]
        for i, subj in enumerate(subjects):
            who = people[i % 3]
            labels = [inbox]
            if "Invoice" in subj or "tickets" in subj:
                labels.append(receipts)
            if "planning" in subj or "agenda" in subj:
                labels.append(work)
            self.add_email(frm=who, to=[me], subject=subj,
                           text=f"Hello Felix,\n\nthis is message {i + 1} about '{subj}'.\n\nBest,\n{who['name']}",
                           html=f"<p>Hello Felix,</p><p>this is message {i + 1} about <b>{subj}</b>.</p><p>Best,<br>{who['name']}</p>",
                           mailboxes=labels, keywords={"$seen": True} if i % 3 else {},
                           when=t - timedelta(days=3 + i, hours=i * 2))
        # Archived and older mail
        for i in range(12):
            who = people[(i + 1) % 3]
            self.add_email(frm=who, to=[me], subject=f"Older thread {i + 1}", text=f"Archived message {i + 1}.",
                           mailboxes=[archive] + ([newsletters] if i % 4 == 0 else []),
                           keywords={"$seen": True}, when=t - timedelta(days=20 + i))
        # Spam, trash, draft, sent
        self.add_email(frm={"name": "Prize Dept", "email": "win@spam.example"}, to=[me], subject="You WON!!!",
                       text="Click here to claim.", mailboxes=[junk], keywords={"$junk": True},
                       when=t - timedelta(days=1))
        self.add_email(frm=people[2], to=[me], subject="Old newsletter", text="Bye.", mailboxes=[trash],
                       keywords={"$seen": True}, when=t - timedelta(days=9))
        self.add_email(frm=me, to=[people[2]], subject="Draft: trip planning", text="Let's plan the trip...",
                       mailboxes=[drafts], keywords={"$draft": True, "$seen": True}, when=t - timedelta(hours=3))
        self.add_email(frm=me, to=[people[1]], subject="Slides from today", text="Attached the slides.",
                       mailboxes=[sent], keywords={"$seen": True}, when=t - timedelta(days=4),
                       attachments=[("slides.pdf", "application/pdf", b"%PDF-1.4 slides\n%%EOF")])
        self.recount()

    # ------------------------------------------------------------- counting

    def recount(self) -> None:
        for m in self.mailboxes.values():
            m["totalEmails"] = m["unreadEmails"] = m["totalThreads"] = m["unreadThreads"] = 0
        threads_in: dict[str, set] = {}
        unread_threads: dict[str, set] = {}
        for e in self.emails.values():
            for mid in e["mailboxIds"]:
                m = self.mailboxes.get(mid)
                if not m:
                    continue
                m["totalEmails"] += 1
                threads_in.setdefault(mid, set()).add(e["threadId"])
                if not e["keywords"].get("$seen"):
                    m["unreadEmails"] += 1
                    unread_threads.setdefault(mid, set()).add(e["threadId"])
        for mid, m in self.mailboxes.items():
            m["totalThreads"] = len(threads_in.get(mid, ()))
            m["unreadThreads"] = len(unread_threads.get(mid, ()))

    def touch_mailboxes(self, ids) -> None:
        self.recount()
        for mid in ids:
            if mid in self.mailboxes:
                self.bump("Mailbox", mid, "updated")

    # ------------------------------------------------------------ Email/query

    def _matches(self, e: dict, f: dict) -> bool:
        if not f:
            return True
        op = f.get("operator")
        if op:
            conds = f.get("conditions") or []
            if op == "AND":
                return all(self._matches(e, c) for c in conds)
            if op == "OR":
                return any(self._matches(e, c) for c in conds)
            if op == "NOT":
                return not any(self._matches(e, c) for c in conds)
            raise MethodError("unsupportedFilter", f"operator {op}")
        for key, value in f.items():
            if key == "inMailbox":
                if value not in e["mailboxIds"]:
                    return False
            elif key == "inMailboxOtherThan":
                if not (set(e["mailboxIds"]) - set(value)):
                    return False
            elif key == "hasKeyword":
                if not e["keywords"].get(value):
                    return False
            elif key == "notKeyword":
                if e["keywords"].get(value):
                    return False
            elif key == "hasAttachment":
                if bool(e["hasAttachment"]) != bool(value):
                    return False
            elif key in ("from", "to", "cc", "bcc"):
                addrs = " ".join(f"{a.get('name') or ''} {a.get('email')}" for a in (e.get(key) or [])).lower()
                if value.lower() not in addrs:
                    return False
            elif key == "subject":
                if value.lower() not in (e["subject"] or "").lower():
                    return False
            elif key == "body":
                if value.lower() not in " ".join(v["value"] for v in e["bodyValues"].values()).lower():
                    return False
            elif key == "text":
                hay = " ".join([e["subject"] or "", " ".join(v["value"] for v in e["bodyValues"].values())] + [
                    f"{a.get('name') or ''} {a.get('email')}" for k in ("from", "to", "cc") for a in (e.get(k) or [])
                ]).lower()
                if not all(w in hay for w in value.lower().split()):
                    return False
            elif key == "header":
                raw = e["_headers"].get(value[0])
                if raw is None or (len(value) > 1 and value[1].lower() not in raw.lower()):
                    return False
            elif key == "before":
                if e["receivedAt"] >= value:
                    return False
            elif key == "after":
                if e["receivedAt"] < value:
                    return False
            else:
                raise MethodError("unsupportedFilter", key)
        return True

    def _sort_value(self, e: dict, comparator: dict):
        prop = comparator.get("property", "receivedAt")
        if prop == "from":
            a = (e.get("from") or [{}])[0]
            return ((a.get("name") or a.get("email") or "").lower(), e["id"])
        if prop == "subject":
            return (re.sub(r"^((re|fwd?)\s*:\s*)+", "", (e.get("subject") or "").lower()), e["id"])
        if prop == "size":
            return (e.get("size") or 0, e["id"])
        if prop == "hasKeyword":
            return (1 if e["keywords"].get(comparator.get("keyword")) else 0, e["id"])
        if prop in ("someInThreadHaveKeyword", "allInThreadHaveKeyword"):
            kws = [self.emails[i]["keywords"].get(comparator.get("keyword"), False) for i in self.threads[e["threadId"]]]
            return (1 if (any(kws) if prop.startswith("some") else all(kws)) else 0, e["id"])
        if prop in ("receivedAt", "sentAt"):
            return (e.get(prop) or "", e["id"])
        raise MethodError("unsupportedSort", prop)

    def run_query(self, args: dict) -> list[str]:
        f = args.get("filter") or {}
        sort = args.get("sort") or [{"property": "receivedAt", "isAscending": False}]
        matched = [e for e in self.emails.values() if self._matches(e, f)]
        for comparator in reversed(sort):  # stable multi-key sort: apply least significant first
            matched.sort(key=lambda e, c=comparator: self._sort_value(e, c), reverse=not comparator.get("isAscending", False))
        if args.get("collapseThreads"):
            seen, out = set(), []
            for e in matched:
                if e["threadId"] in seen:
                    continue
                seen.add(e["threadId"])
                out.append(e)
            matched = out
        return [e["id"] for e in matched]

    @staticmethod
    def _spec_key(args: dict) -> str:
        return json.dumps({"filter": args.get("filter"), "sort": args.get("sort"),
                           "collapseThreads": args.get("collapseThreads", False)}, sort_keys=True)

    def email_query(self, args: dict) -> dict:
        ids = self.run_query(args)
        state = self.state("Email")
        self.query_snapshots[(state, self._spec_key(args))] = ids
        pos = int(args.get("position") or 0)
        if pos < 0:
            pos = max(0, len(ids) + pos)
        limit = args.get("limit")
        page = ids[pos:] if limit is None else ids[pos:pos + int(limit)]
        res = {"accountId": ACCOUNT, "queryState": state, "canCalculateChanges": True, "position": pos, "ids": page}
        if args.get("calculateTotal"):
            res["total"] = len(ids)
        return res

    def email_query_changes(self, args: dict) -> dict:
        since = args.get("sinceQueryState")
        old = self.query_snapshots.get((since, self._spec_key(args)))
        if old is None:
            raise MethodError("cannotCalculateChanges", "no snapshot for that state")
        new = self.run_query(args)
        state = self.state("Email")
        self.query_snapshots[(state, self._spec_key(args))] = new
        old_set, new_set = set(old), set(new)
        removed = [i for i in old if i not in new_set]
        added = [{"id": i, "index": idx} for idx, i in enumerate(new) if i not in old_set]
        max_changes = args.get("maxChanges")
        if max_changes and len(removed) + len(added) > int(max_changes):
            raise MethodError("tooManyChanges")
        res = {"accountId": ACCOUNT, "oldQueryState": since, "newQueryState": state, "removed": removed, "added": added}
        if args.get("calculateTotal"):
            res["total"] = len(new)
        return res

    # -------------------------------------------------------------- Email/get

    def email_get(self, args: dict) -> dict:
        ids = args.get("ids")
        props = args.get("properties")
        want_values = bool(args.get("fetchAllBodyValues") or args.get("fetchTextBodyValues")
                           or args.get("fetchHTMLBodyValues"))
        found, not_found = [], []
        if ids is None:
            raise MethodError("requestTooLarge", "ids must be given")
        for idx, p in enumerate(props or []):
            if p == "header:List-Unsubscribe:asText":  # Fastmail behaviour observed 2026-09-03
                err = MethodError("invalidArguments")
                err.arguments = [f"properties[{idx}:{p}]"]
                raise err
        for i in ids:
            e = self.emails.get(i)
            if not e:
                not_found.append(i)
                continue
            found.append(self._project(e, props, want_values))
        return {"accountId": ACCOUNT, "state": self.state("Email"), "list": found, "notFound": not_found}

    def _project(self, e: dict, props, want_values: bool) -> dict:
        if props is None:
            props = [k for k in e if not k.startswith("_") and k != "bodyValues"]
        out = {"id": e["id"]}
        for p in props:
            if p.startswith("header:"):
                parts = p.split(":")
                value = e["_headers"].get(parts[1])
                out[p] = value.replace("{base}", getattr(self, "base_url", "http://127.0.0.1/")) if value else value
            elif p == "bodyValues":
                out[p] = e["bodyValues"] if want_values else {}
            elif p in e:
                out[p] = e[p]
        return out

    # -------------------------------------------------------------- Email/set

    def _apply_patch(self, e: dict, patch: dict) -> None:
        for key, value in patch.items():
            if key == "keywords":
                e["keywords"] = dict(value)
            elif key == "mailboxIds":
                e["mailboxIds"] = {k: True for k, v in value.items() if v}
            elif key.startswith("keywords/"):
                kw = key[len("keywords/"):]
                if value:
                    e["keywords"][kw] = True
                else:
                    e["keywords"].pop(kw, None)
            elif key.startswith("mailboxIds/"):
                mb = key[len("mailboxIds/"):]
                if mb not in self.mailboxes:
                    raise MethodError("invalidProperties", f"unknown mailbox {mb}")
                if value:
                    e["mailboxIds"][mb] = True
                else:
                    e["mailboxIds"].pop(mb, None)
            else:
                raise MethodError("invalidProperties", f"immutable property {key}")
        if not e["mailboxIds"]:
            raise MethodError("invalidProperties", "email must be in at least one mailbox")

    def email_set(self, args: dict, created_refs: dict) -> dict:
        res = {"accountId": ACCOUNT, "oldState": self.state("Email"), "created": {}, "updated": {}, "destroyed": [],
               "notCreated": {}, "notUpdated": {}, "notDestroyed": {}}
        touched: set[str] = set()
        for cid, obj in (args.get("create") or {}).items():
            try:
                e = self._create_email(obj)
            except MethodError as err:
                res["notCreated"][cid] = {"type": err.type, "description": err.description}
                continue
            created_refs[f"#{cid}"] = e["id"]
            res["created"][cid] = {"id": e["id"], "blobId": e["blobId"], "threadId": e["threadId"], "size": e["size"]}
            touched |= set(e["mailboxIds"])
            self.bump("Email", e["id"], "created")
            self.bump("Thread", e["threadId"], "updated" if len(self.threads[e["threadId"]]) > 1 else "created")
        for eid, patch in (args.get("update") or {}).items():
            eid = created_refs.get(eid, eid)
            e = self.emails.get(eid)
            if not e:
                res["notUpdated"][eid] = {"type": "notFound"}
                continue
            before = set(e["mailboxIds"])
            snapshot = json.dumps({"k": e["keywords"], "m": e["mailboxIds"]}, sort_keys=True)
            try:
                self._apply_patch(e, patch)
            except MethodError as err:
                e.update(json.loads(snapshot) and {"keywords": json.loads(snapshot)["k"],
                                                   "mailboxIds": json.loads(snapshot)["m"]})
                res["notUpdated"][eid] = {"type": err.type, "description": err.description}
                continue
            res["updated"][eid] = None
            touched |= before | set(e["mailboxIds"])
            self.bump("Email", eid, "updated")
        for eid in args.get("destroy") or []:
            e = self.emails.pop(eid, None)
            if not e:
                res["notDestroyed"][eid] = {"type": "notFound"}
                continue
            touched |= set(e["mailboxIds"])
            self.threads[e["threadId"]].remove(eid)
            if not self.threads[e["threadId"]]:
                del self.threads[e["threadId"]]
                self.bump("Thread", e["threadId"], "destroyed")
            else:
                self.bump("Thread", e["threadId"], "updated")
            res["destroyed"].append(eid)
            self.bump("Email", eid, "destroyed")
        if touched:
            self.touch_mailboxes(touched)
        res["newState"] = self.state("Email")
        return res

    def _create_email(self, obj: dict) -> dict:
        if not obj.get("mailboxIds"):
            raise MethodError("invalidProperties", "mailboxIds required")
        for mb in obj["mailboxIds"]:
            if mb not in self.mailboxes:
                raise MethodError("invalidProperties", f"unknown mailbox {mb}")
        values = obj.get("bodyValues") or {}
        text = html = None
        for part in obj.get("textBody") or []:
            text = values.get(part["partId"], {}).get("value")
        for part in obj.get("htmlBody") or []:
            html = values.get(part["partId"], {}).get("value")
        atts = []
        for a in obj.get("attachments") or []:
            blob = self.blobs.get(a.get("blobId"))
            if not blob:
                raise MethodError("blobNotFound", a.get("blobId"))
            atts.append((a.get("name") or blob[2], a.get("type") or blob[1], blob[0]))
        thread = None
        for ref in obj.get("inReplyTo") or []:
            for e in self.emails.values():
                if ref in (e.get("messageId") or []):
                    thread = e["threadId"]
        frm = (obj.get("from") or [{"email": "unknown@example.com"}])[0]
        e = self.add_email(frm=frm, to=obj.get("to") or [], cc=obj.get("cc"), subject=obj.get("subject") or "",
                           text=text, html=html, mailboxes=list(obj["mailboxIds"]), keywords=dict(obj.get("keywords") or {}),
                           thread=thread, attachments=atts, in_reply_to=(obj.get("inReplyTo") or [None])[0])
        e["bcc"] = obj.get("bcc")
        e["replyTo"] = obj.get("replyTo")
        if obj.get("references"):
            e["references"] = obj["references"]
        return e

    # ---------------------------------------------------------- Mailbox/set

    def mailbox_set(self, args: dict) -> dict:
        res = {"accountId": ACCOUNT, "oldState": self.state("Mailbox"), "created": {}, "updated": {}, "destroyed": [],
               "notCreated": {}, "notUpdated": {}, "notDestroyed": {}}
        for cid, obj in (args.get("create") or {}).items():
            name = (obj.get("name") or "").strip()
            if not name:
                res["notCreated"][cid] = {"type": "invalidProperties", "description": "name required"}
                continue
            parent = obj.get("parentId")
            if parent and parent not in self.mailboxes:
                res["notCreated"][cid] = {"type": "invalidProperties", "description": "unknown parent"}
                continue
            mid = self.add_mailbox(name, obj.get("role"), parent, obj.get("sortOrder", 0))
            res["created"][cid] = {k: v for k, v in self.mailboxes[mid].items() if k not in obj}
            self.bump("Mailbox", mid, "created")
        for mid, patch in (args.get("update") or {}).items():
            m = self.mailboxes.get(mid)
            if not m:
                res["notUpdated"][mid] = {"type": "notFound"}
                continue
            for k, v in patch.items():
                if k in ("name", "parentId", "sortOrder", "isSubscribed"):
                    m[k] = v
                else:
                    res["notUpdated"][mid] = {"type": "invalidProperties", "description": k}
                    break
            else:
                res["updated"][mid] = None
                self.bump("Mailbox", mid, "updated")
        for mid in args.get("destroy") or []:
            m = self.mailboxes.get(mid)
            if not m:
                res["notDestroyed"][mid] = {"type": "notFound"}
                continue
            if any(mb["parentId"] == mid for mb in self.mailboxes.values()):
                res["notDestroyed"][mid] = {"type": "mailboxHasChild"}
                continue
            holders = [e for e in self.emails.values() if mid in e["mailboxIds"]]
            if holders and not args.get("onDestroyRemoveEmails"):
                res["notDestroyed"][mid] = {"type": "mailboxHasEmail"}
                continue
            for e in holders:
                del e["mailboxIds"][mid]
                if not e["mailboxIds"]:
                    del self.emails[e["id"]]
                    self.bump("Email", e["id"], "destroyed")
                else:
                    self.bump("Email", e["id"], "updated")
            del self.mailboxes[mid]
            res["destroyed"].append(mid)
            self.bump("Mailbox", mid, "destroyed")
        self.recount()
        res["newState"] = self.state("Mailbox")
        return res

    # ------------------------------------------------------------- Identity

    def identity_set(self, args: dict) -> dict:
        res = {"accountId": ACCOUNT, "oldState": self.state("Identity"), "created": {}, "updated": {}, "destroyed": [],
               "notCreated": {}, "notUpdated": {}, "notDestroyed": {}}
        for cid in args.get("create") or {}:
            res["notCreated"][cid] = {"type": "forbidden", "description": "Identities are managed in Settings"}
        for iid, patch in (args.get("update") or {}).items():
            ident = self.identities.get(iid)
            if not ident:
                res["notUpdated"][iid] = {"type": "notFound"}
                continue
            bad = [k for k in patch if k not in ("name", "replyTo", "bcc", "textSignature", "htmlSignature")]
            if bad:
                res["notUpdated"][iid] = {"type": "invalidProperties", "properties": bad}
                continue
            ident.update(patch)
            res["updated"][iid] = None
            self.bump("Identity", iid, "updated")
        res["newState"] = self.state("Identity")
        return res

    # ------------------------------------------------------ EmailSubmission

    def submission_set(self, args: dict, created_refs: dict) -> tuple[dict, dict | None]:
        res = {"accountId": ACCOUNT, "oldState": "0", "newState": "1", "created": {}, "updated": {}, "destroyed": [],
               "notCreated": {}, "notUpdated": {}, "notDestroyed": {}}
        implicit_update: dict = {}
        for cid, obj in (args.get("create") or {}).items():
            email_id = created_refs.get(obj.get("emailId"), obj.get("emailId"))
            if email_id not in self.emails:
                res["notCreated"][cid] = {"type": "emailNotFound"}
                continue
            if obj.get("identityId") not in self.identities:
                res["notCreated"][cid] = {"type": "invalidProperties", "description": "unknown identity"}
                continue
            sid = self.new_id("S")
            send_at = obj.get("sendAt") or _now()
            pending = send_at > _now()   # a future sendAt is held (#6)
            self.submissions[sid] = {"id": sid, "emailId": email_id, "identityId": obj["identityId"],
                                     "sendAt": send_at, "undoStatus": "pending" if pending else "final"}
            res["created"][cid] = {"id": sid, "sendAt": send_at, "undoStatus": "pending" if pending else "final"}
            patch = (args.get("onSuccessUpdateEmail") or {}).get(f"#{cid}")
            if patch:
                implicit_update[email_id] = patch
        for sid, patch in (args.get("update") or {}).items():
            sub = self.submissions.get(sid)
            if sub is None:
                res["notUpdated"][sid] = {"type": "notFound"}
                continue
            if patch.get("undoStatus") == "canceled" and sub["undoStatus"] == "pending":
                sub["undoStatus"] = "canceled"
                res["updated"][sid] = None
                after = (args.get("onSuccessUpdateEmail") or {}).get(sid)
                if after:
                    implicit_update[sub["emailId"]] = after
            else:
                res["notUpdated"][sid] = {"type": "cannotUnsend"}
        email_res = None
        if implicit_update:
            email_res = self.email_set({"update": implicit_update}, created_refs)
        return res, email_res

    # ---------------------------------------------------------- MaskedEmail

    def masked_set(self, args: dict) -> dict:
        res = {"accountId": ACCOUNT, "oldState": self.state("MaskedEmail"), "created": {}, "updated": {},
               "destroyed": [], "notCreated": {}, "notUpdated": {}, "notDestroyed": {}}
        for cid, obj in (args.get("create") or {}).items():
            prefix = (obj.get("emailPrefix") or "").strip()
            if prefix and not re.fullmatch(r"[a-z0-9_]{1,64}", prefix):
                res["notCreated"][cid] = {"type": "invalidProperties", "properties": ["emailPrefix"]}
                continue
            word = self.rng.choice(["quiet", "amber", "brisk", "lunar", "cedar", "velvet"])
            local = f"{prefix or word}.{self.rng.randint(1000, 9999)}"
            mid = self.new_id("masked-")
            item = {"id": mid, "email": f"{local}@fastmail.com", "state": obj.get("state") or "enabled",
                    "forDomain": obj.get("forDomain") or "", "description": obj.get("description") or "",
                    "url": obj.get("url"), "createdBy": "Den Mail (fake)", "createdAt": _now(),
                    "lastMessageAt": None, "emailPrefix": prefix}
            self.masked[mid] = item
            res["created"][cid] = {k: v for k, v in item.items() if k not in obj}
            self.bump("MaskedEmail", mid, "created")
        for mid, patch in (args.get("update") or {}).items():
            item = self.masked.get(mid)
            if not item:
                res["notUpdated"][mid] = {"type": "notFound"}
                continue
            bad = [k for k in patch if k not in ("state", "description", "forDomain", "url")]
            if bad or patch.get("state") not in (None, "pending", "enabled", "disabled", "deleted"):
                res["notUpdated"][mid] = {"type": "invalidProperties", "properties": bad or ["state"]}
                continue
            item.update(patch)
            res["updated"][mid] = None
            self.bump("MaskedEmail", mid, "updated")
        for mid in args.get("destroy") or []:
            item = self.masked.get(mid)
            if not item:
                res["notDestroyed"][mid] = {"type": "notFound"}
            elif item.get("lastMessageAt"):
                res["notDestroyed"][mid] = {"type": "forbidden", "description": "address has received mail"}
            else:
                del self.masked[mid]
                res["destroyed"].append(mid)
                self.bump("MaskedEmail", mid, "destroyed")
        res["newState"] = self.state("MaskedEmail")
        return res


# ---------------------------------------------------------------- dispatch


def _resolve_pointer(value, path: str):
    """JSON pointer with the JMAP '*' extension (RFC 8620 §3.7)."""
    tokens = [t.replace("~1", "/").replace("~0", "~") for t in path.split("/")[1:]] if path else []

    def walk(v, toks):
        if not toks:
            return v
        t, rest = toks[0], toks[1:]
        if t == "*":
            if not isinstance(v, list):
                raise MethodError("invalidResultReference", "'*' on non-array")
            out = []
            for item in v:
                r = walk(item, rest)
                if isinstance(r, list):
                    out.extend(r)
                else:
                    out.append(r)
            return out
        if isinstance(v, list):
            return walk(v[int(t)], rest)
        if isinstance(v, dict) and t in v:
            return walk(v[t], rest)
        raise MethodError("invalidResultReference", f"path {path} not found")

    return walk(value, tokens)


class Dispatcher:
    def __init__(self, data: FakeData):
        self.data = data

    def handle(self, body: dict) -> dict:
        responses: list[list] = []
        created_refs: dict[str, str] = {}
        with self.data.lock:
            for name, args, cid in body.get("methodCalls", []):
                try:
                    args = self._resolve_refs(args, responses)
                    for resp in self._call(name, args, created_refs):
                        responses.append([resp[0], resp[1], cid])
                except MethodError as e:
                    err = {"type": e.type}
                    if e.description:
                        err["description"] = e.description
                    if getattr(e, "arguments", None):
                        err["arguments"] = e.arguments
                    responses.append(["error", err, cid])
        return {"methodResponses": responses, "sessionState": "s1"}

    def _resolve_refs(self, args: dict, responses: list) -> dict:
        out = {}
        for k, v in args.items():
            if k.startswith("#"):
                ref = v
                for name, result, cid in responses:
                    if cid == ref["resultOf"] and name == ref["name"]:
                        out[k[1:]] = _resolve_pointer(result, ref["path"])
                        break
                else:
                    raise MethodError("invalidResultReference", f"no result {ref['resultOf']}/{ref['name']}")
            else:
                out[k] = v
        return out

    def _call(self, name: str, args: dict, created_refs: dict) -> list[tuple[str, dict]]:
        d = self.data
        if args.get("accountId") not in (None, ACCOUNT):
            raise MethodError("accountNotFound")
        if name == "Mailbox/get":
            ids = args.get("ids")
            items = list(d.mailboxes.values()) if ids is None else [d.mailboxes[i] for i in ids if i in d.mailboxes]
            nf = [] if ids is None else [i for i in ids if i not in d.mailboxes]
            return [(name, {"accountId": ACCOUNT, "state": d.state("Mailbox"), "list": items, "notFound": nf})]
        if name == "Mailbox/changes":
            r = d.changes_since("Mailbox", args.get("sinceState"), lambda i: i in d.mailboxes)
            r["updatedProperties"] = None
            return [(name, r)]
        if name == "Mailbox/set":
            return [(name, d.mailbox_set(args))]
        if name == "Email/get":
            return [(name, d.email_get(args))]
        if name == "Email/query":
            return [(name, d.email_query(args))]
        if name == "Email/queryChanges":
            return [(name, d.email_query_changes(args))]
        if name == "Email/changes":
            return [(name, d.changes_since("Email", args.get("sinceState"), lambda i: i in d.emails))]
        if name == "Email/set":
            return [(name, d.email_set(args, created_refs))]
        if name == "Thread/get":
            ids = args.get("ids") or []
            items = [{"id": t, "emailIds": sorted(d.threads[t], key=lambda i: d.emails[i]["receivedAt"])}
                     for t in ids if t in d.threads]
            return [(name, {"accountId": ACCOUNT, "state": d.state("Thread"), "list": items,
                            "notFound": [t for t in ids if t not in d.threads]})]
        if name == "Thread/changes":
            return [(name, d.changes_since("Thread", args.get("sinceState"), lambda i: i in d.threads))]
        if name == "Identity/get":
            ids = args.get("ids")
            items = list(d.identities.values()) if ids is None else [d.identities[i] for i in ids if i in d.identities]
            return [(name, {"accountId": ACCOUNT, "state": d.state("Identity"), "list": items, "notFound": []})]
        if name == "Identity/set":
            return [(name, d.identity_set(args))]
        if name == "EmailSubmission/set":
            res, email_res = d.submission_set(args, created_refs)
            out = [(name, res)]
            if email_res:
                out.append(("Email/set", email_res))
            return out
        if name == "MaskedEmail/get":
            ids = args.get("ids")
            items = list(d.masked.values()) if ids is None else [d.masked[i] for i in ids if i in d.masked]
            return [(name, {"accountId": ACCOUNT, "state": d.state("MaskedEmail"), "list": items, "notFound": []})]
        if name == "MaskedEmail/set":
            return [(name, d.masked_set(args))]
        if name == "Core/echo":
            return [(name, args)]
        raise MethodError("unknownMethod", name)


# ------------------------------------------------------------------ HTTP


class Handler(BaseHTTPRequestHandler):
    server: FakeJMAPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        if self.server.verbose:
            super().log_message(fmt, *args)

    def _authed(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {self.server.token}":
            return True
        self._send(401, {"type": "urn:ietf:params:jmap:error:notAuthenticated"}, "application/problem+json")
        return False

    def _send(self, code: int, payload, ctype: str = "application/json") -> None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._authed():
            return
        path = urlparse(self.path).path
        if path == "/session":
            self._send(200, self.server.session())
        elif path.startswith("/download/"):
            parts = path.split("/")
            blob = self.server.data.blobs.get(unquote(parts[3]) if len(parts) > 3 else "")
            if not blob:
                self._send(404, {"error": "not found"})
            else:
                self._send(200, blob[0], blob[1])
        elif path == "/event":
            self._event_stream()
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith("/unsubscribe/"):  # RFC 8058 endpoint: no JMAP auth, records the request
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode()
            self.server.unsubscribes.append((path, body, self.headers.get("Content-Type")))
            self._send(200, {"ok": True})
            return
        if not self._authed():
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if path == "/deliver":  # test hook: a new message arrives (the body is its subject)
            self._send(200, {"id": self.server.deliver(body.decode() or "New mail arrived")})
            return
        if path == "/api":
            self.server.requests.append(json.loads(body))
            self._send(200, self.server.dispatcher.handle(json.loads(body)))
        elif path.startswith("/upload/"):
            ctype = self.headers.get("Content-Type") or "application/octet-stream"
            with self.server.data.lock:
                bid = self.server.data.add_blob(body, ctype, "upload")
            self._send(201, {"accountId": ACCOUNT, "blobId": bid, "type": ctype, "size": len(body)})
        else:
            self._send(404, {"error": "not found"})

    def _event_stream(self):
        d = self.server.data
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        q = parse_qs(urlparse(self.path).query)
        ping = int(q.get("ping", ["30"])[0]) or 30
        last = None
        try:
            while not self.server.stopping:
                with d.cv:
                    snapshot = {k: str(v) for k, v in d.states.items()}
                    if snapshot != last:
                        payload = {"type": "connect" if last is None else "change", "changed": {ACCOUNT: snapshot}}
                        last = snapshot
                        self.wfile.write(f"event: state\ndata: {json.dumps(payload)}\n\n".encode())
                        self.wfile.flush()
                    d.cv.wait(timeout=ping)
                    if self.server.stopping:
                        break
                    if {k: str(v) for k, v in d.states.items()} == last:
                        self.wfile.write(b"event: ping\ndata: {}\n\n")
                        self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


class FakeJMAPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def __init__(self, host: str = "127.0.0.1", port: int = 0, token: str = TOKEN, verbose: bool = False):
        super().__init__((host, port), Handler)
        self.token = token
        self.verbose = verbose
        self.data = FakeData()
        self.data.base_url = f"{self.base_url}/"
        self.dispatcher = Dispatcher(self.data)
        self.requests: list[dict] = []
        self.unsubscribes: list[tuple[str, str, str | None]] = []  # (path, body, content-type)
        self._thread: threading.Thread | None = None
        self.stopping = False

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def session_url(self) -> str:
        return f"{self.base_url}/session"

    def session(self) -> dict:
        b = self.base_url
        return {
            "capabilities": {
                CAP_CORE: {"maxSizeUpload": 50_000_000, "maxConcurrentUpload": 4, "maxSizeRequest": 10_000_000,
                           "maxConcurrentRequests": 4, "maxCallsInRequest": 50, "maxObjectsInGet": 1000,
                           "maxObjectsInSet": 500, "collationAlgorithms": ["i;ascii-numeric"]},
                CAP_MAIL: {}, CAP_SUBMISSION: {}, CAP_MASKED: {},
            },
            "accounts": {ACCOUNT: {"name": "felix@example.com", "isPersonal": True, "isReadOnly": False,
                                   "accountCapabilities": {CAP_MAIL: {"maxMailboxDepth": 10, "maxSizeAttachmentsPerEmail": 50_000_000,
                                                                      "mayCreateTopLevelMailbox": True},
                                                           CAP_SUBMISSION: {"submissionExtensions": {}, "maxDelayedSend": 44236800},
                                                           CAP_MASKED: {}}}},
            "primaryAccounts": {CAP_MAIL: ACCOUNT, CAP_SUBMISSION: ACCOUNT, CAP_MASKED: ACCOUNT},
            "username": "felix@example.com",
            "apiUrl": f"{b}/api",
            "downloadUrl": f"{b}/download/{{accountId}}/{{blobId}}/{{name}}?type={{type}}",
            "uploadUrl": f"{b}/upload/{{accountId}}/",
            "eventSourceUrl": f"{b}/event?types={{types}}&closeafter={{closeafter}}&ping={{ping}}",
            "state": "s1",
        }

    def start(self) -> FakeJMAPServer:
        self._thread = threading.Thread(target=self.serve_forever, name="fake-jmap", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.stopping = True
        with self.data.cv:
            self.data.cv.notify_all()
        self.shutdown()
        self.server_close()

    # Helpers for tests: simulate server-side events.
    def deliver(self, subject: str = "New mail arrived", text: str = "Hello from the fake server.",
                mailbox_role: str = "inbox", frm: dict | None = None) -> str:
        d = self.data
        with d.lock:
            mb = next(m["id"] for m in d.mailboxes.values() if m.get("role") == mailbox_role)
            e = d.add_email(frm=frm or {"name": "Fake Sender", "email": "fake@example.net"},
                            to=[{"name": "Felix Test", "email": "felix@example.com"}], subject=subject, text=text,
                            mailboxes=[mb])
            d.bump("Email", e["id"], "created")
            d.bump("Thread", e["threadId"], "created")
            d.touch_mailboxes([mb])
            return e["id"]


if __name__ == "__main__":
    import sys

    srv = FakeJMAPServer(port=int(sys.argv[1]) if len(sys.argv) > 1 else 0, verbose="-v" in sys.argv).start()
    print(f"DEN_MAIL_SESSION_URL={srv.session_url} DEN_MAIL_TOKEN={srv.token}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.stop()
