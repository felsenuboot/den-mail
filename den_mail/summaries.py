"""Summaries through the assistant (#68): a conversation, or a sender's newest message.

On demand only. A summary is cached in the SQLite cache under a key with a
fingerprint of the message ids it covers, so a second look costs nothing and
a new reply invalidates it. The prompts strip quoted history and cap the
text, newest messages first, so a long thread still fits a small model.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from gi.repository import GLib

from .html.body import assemble_body
from .html.totext import html_to_text, split_quoted_text
from .llm import LLMError

log = logging.getLogger(__name__)

MAX_CHARS = 12000          # of message text in one prompt; older messages are cut first
MESSAGE_MIN = 600          # what an older message keeps when the thread is cut down

THREAD_SYSTEM = (
    "You summarise an email conversation for the person who owns the mailbox. Answer in the language the "
    "messages are written in. Write three to five short lines of plain text: what it is about, what was "
    "decided or asked, and what the reader has to do, with dates and amounts when there are any. No "
    "greeting, no preamble, no headings, no markdown."
)
SENDER_SYSTEM = (
    "You describe one email in a single line of at most twenty words, in the language it is written in, so "
    "the reader can decide without opening it whether mail from this sender is worth keeping. Say what it "
    "offers or announces; no preamble, no quotation marks."
)


@dataclass(frozen=True)
class Summary:
    text: str
    fingerprint: str
    model: str
    cached: bool


# ----------------------------------------------------------------- the text

def message_text(full: dict) -> str:
    """A message's own words: the text part, else the HTML as text, with the quoted history dropped."""
    content = assemble_body(full)
    text = content.text or (html_to_text(content.html, link_targets=False) if content.html else "")
    own, _quoted = split_quoted_text(text or "")
    return own.strip() or (full.get("preview") or "").strip()


def _who(email: dict) -> str:
    sender = (email.get("from") or [{}])[0]
    name, addr = sender.get("name") or "", sender.get("email") or ""
    return f"{name} <{addr}>" if name and addr else name or addr or "unknown sender"


def thread_prompt(emails: list[dict], bodies: dict[str, dict | None], limit: int = MAX_CHARS) -> str:
    """The conversation as text, oldest first; when it is too long, older messages are cut to a
    stub so the newest ones stay whole."""
    parts = []
    for e in emails:
        body = bodies.get(e["id"])
        text = message_text(body) if body else (e.get("preview") or "").strip()
        parts.append((f"From: {_who(e)}\nDate: {e.get('receivedAt') or ''}\n", text))
    total = sum(len(h) + len(t) for h, t in parts)
    for i in range(len(parts) - 1):     # never cut the newest message
        if total <= limit:
            break
        head, text = parts[i]
        if len(text) > MESSAGE_MIN:
            cut = text[:MESSAGE_MIN].rstrip() + " […]"
            total -= len(text) - len(cut)
            parts[i] = (head, cut)
    subject = next((e.get("subject") for e in reversed(emails) if e.get("subject")), "") or "(no subject)"
    out = [f"Subject: {subject}", f"Messages: {len(parts)}"]
    for n, (head, text) in enumerate(parts, 1):
        out.append(f"\n--- Message {n} of {len(parts)} ---\n{head}\n{text or '(empty message)'}")
    return "\n".join(out)


def sender_prompt(email: dict, body: dict | None) -> str:
    text = message_text(body) if body else (email.get("preview") or "").strip()
    return f"From: {_who(email)}\nSubject: {email.get('subject') or '(no subject)'}\n\n{text[:MAX_CHARS // 2]}"


def fingerprint(ids: list[str]) -> str:
    return ",".join(ids)


# ------------------------------------------------------------- the requests

class Summariser:
    """Asks the assistant on a thread and delivers on the main loop; results go to the cache.

    `spawn` and `deliver` exist for the tests, which run both inline.
    """

    def __init__(self, db, engine, assistant, spawn: Callable[[Callable[[], None]], None] | None = None,
                 deliver: Callable[[Callable[[], None]], None] | None = None) -> None:
        self.db = db
        self.engine = engine
        self.assistant = assistant
        self._spawn = spawn or (lambda fn: threading.Thread(target=fn, name="summarise", daemon=True).start())
        self._deliver = deliver or (lambda fn: GLib.idle_add(lambda: (fn(), False)[1]))

    @property
    def available(self) -> bool:
        return bool(self.assistant) and self.assistant.enabled

    # -- cache lookups that cost nothing

    def cached_thread(self, thread_id: str) -> Summary | None:
        ids = self.db.thread_email_ids(thread_id)
        return self._cached(f"thread:{thread_id}", fingerprint(ids))

    def cached_sender(self, address: str) -> Summary | None:
        email = self._newest_of(address)
        return self._cached(f"sender:{address.lower()}", email["id"]) if email else None

    def _cached(self, key: str, fp: str) -> Summary | None:
        row = self.db.get_summary(key)
        if row and row["fingerprint"] == fp:
            return Summary(row["text"], fp, row.get("model") or "", cached=True)
        return None

    # -- the two summaries

    def thread(self, thread_id: str, on_done: Callable[[Summary], None], on_error: Callable[[str], None],
               force: bool = False) -> None:
        emails = self.db.thread_emails(thread_id)
        if not emails:
            on_error("Nothing to summarise")
            return
        ids = [e["id"] for e in emails]
        key, fp = f"thread:{thread_id}", fingerprint(ids)
        if not force and (hit := self._cached(key, fp)):
            on_done(hit)
            return

        def work() -> None:
            bodies = {e["id"]: self.engine.fetch_body_now(e["id"]) for e in emails}
            self._ask(key, fp, THREAD_SYSTEM, thread_prompt(emails, bodies), on_done, on_error)

        self._spawn(work)

    def sender(self, address: str, on_done: Callable[[Summary], None], on_error: Callable[[str], None],
               force: bool = False) -> None:
        email = self._newest_of(address)
        if not email:
            on_error("No message from this sender in the cache")
            return
        key, fp = f"sender:{address.lower()}", email["id"]
        if not force and (hit := self._cached(key, fp)):
            on_done(hit)
            return

        def work() -> None:
            body = self.engine.fetch_body_now(email["id"])
            self._ask(key, fp, SENDER_SYSTEM, sender_prompt(email, body), on_done, on_error)

        self._spawn(work)

    # -- plumbing

    def _newest_of(self, address: str) -> dict | None:
        from . import senders

        rows = senders.messages_of(self.db, address, self.engine.trash_junk_ids(), limit=1)
        return self.db.get_email(rows[0]["id"]) if rows else None

    def _ask(self, key: str, fp: str, system: str, user: str, on_done, on_error) -> None:
        try:
            text = self.assistant.ask(system, user).strip()
        except LLMError as e:
            message = str(e)
            self._deliver(lambda: on_error(message))
            return
        except Exception as e:
            log.exception("summary failed")
            message = f"Summary failed: {e}"
            self._deliver(lambda: on_error(message))
            return
        model = getattr(self.assistant, "model_name", "") or ""
        self.db.set_summary(key, text, fp, model)
        self._deliver(lambda: on_done(Summary(text, fp, model, cached=False)))
