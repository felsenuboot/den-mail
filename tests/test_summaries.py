"""Summaries (#68): the prompt text, the cache with its fingerprint, the sender line."""

from __future__ import annotations

import pytest

from den_mail import summaries
from den_mail.llm import LLMError
from den_mail.store.db import Database

from .test_views import TRASH_JUNK, db, email  # noqa: F401 - fixture and helper


def body(eid: str, text: str | None = None, html: str | None = None, **extra) -> dict:
    values, parts = {}, {}
    if text is not None:
        values["t"] = {"value": text}
        parts["textBody"] = [{"partId": "t", "type": "text/plain"}]
    if html is not None:
        values["h"] = {"value": html}
        parts["htmlBody"] = [{"partId": "h", "type": "text/html"}]
    return {"id": eid, "bodyValues": values, **parts, **extra}


class FakeAssistant:
    def __init__(self, answer="A summary.", enabled=True):
        self.answer, self.enabled, self.calls, self.model_name = answer, enabled, [], "fake/model"

    def ask(self, system, user, json_schema=None):
        self.calls.append((system, user))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class FakeEngine:
    def __init__(self, db: Database):  # noqa: F811
        self.db, self.fetched = db, []

    def trash_junk_ids(self):
        return list(TRASH_JUNK)

    def fetch_body_now(self, email_id):
        self.fetched.append(email_id)
        return self.db.get_email_body(email_id)


def inline(fn):
    fn()


def make(db, assistant) -> tuple[summaries.Summariser, FakeEngine]:  # noqa: F811
    engine = FakeEngine(db)
    return summaries.Summariser(db, engine, assistant, spawn=inline, deliver=inline), engine


# ------------------------------------------------------------------- text

def test_message_text_drops_quoted_history_and_falls_back_to_html_then_preview():
    plain = body("e", text="Yes, Tuesday works.\n\nOn Mon, Anna wrote:\n> Does Tuesday work?\n> Anna")
    assert summaries.message_text(plain) == "Yes, Tuesday works."
    html = body("e", html="<p>Hello <b>there</b></p><p>Second line</p>")
    assert "Hello there" in summaries.message_text(html) and "Second line" in summaries.message_text(html)
    assert summaries.message_text({"id": "e", "preview": "just a preview"}) == "just a preview"


def test_thread_prompt_keeps_the_newest_message_whole_and_cuts_older_ones():
    emails = [email("e1", "Plan", "anna@example.com", "Anna", when="2026-09-01T10:00:00Z"),
              email("e2", "Re: Plan", "me@example.com", "Me", when="2026-09-02T10:00:00Z")]
    bodies = {"e1": body("e1", text="old " * 1000), "e2": body("e2", text="new " * 1000)}
    prompt = summaries.thread_prompt(emails, bodies, limit=5000)
    assert prompt.startswith("Subject: Re: Plan\nMessages: 2")
    assert "--- Message 1 of 2 ---\nFrom: Anna <anna@example.com>" in prompt
    old_part, new_part = prompt.split("--- Message 2 of 2 ---")
    assert old_part.count("old") < 1000 and "[…]" in old_part
    assert new_part.count("new") == 1000
    short = summaries.thread_prompt(emails, {"e1": None, "e2": bodies["e2"]})
    assert "(empty message)" in short and "[…]" not in short


# ------------------------------------------------------------------ thread

def test_thread_summary_is_cached_until_a_reply_arrives(db):  # noqa: F811
    db.upsert_emails([email("e1", "Plan", "anna@example.com", "Anna", thread="T1"),
                      email("e2", "Re: Plan", "me@example.com", "Me", thread="T1", when="2026-09-02T10:00:00Z")])
    db.set_email_body(body("e1", text="Does Tuesday work?"))
    assistant = FakeAssistant("Anna asks about Tuesday; you agreed.")
    s, engine = make(db, assistant)
    assert s.available and s.cached_thread("T1") is None
    got, errors = [], []
    s.thread("T1", got.append, errors.append)
    assert not errors and got[-1].text == "Anna asks about Tuesday; you agreed." and not got[-1].cached
    assert got[-1].model == "fake/model"
    assert engine.fetched == ["e1", "e2"]                       # the missing body was asked for
    system, user = assistant.calls[-1]
    assert system == summaries.THREAD_SYSTEM and "Does Tuesday work?" in user and "Messages: 2" in user
    # A second look is free.
    s.thread("T1", got.append, errors.append)
    assert len(assistant.calls) == 1 and got[-1].cached
    assert s.cached_thread("T1").text == got[-1].text
    # A new reply makes the cached one stale; the next request asks again.
    db.upsert_emails([email("e3", "Re: Plan", "anna@example.com", "Anna", thread="T1", when="2026-09-03T10:00:00Z")])
    assert s.cached_thread("T1") is None
    s.thread("T1", got.append, errors.append)
    assert len(assistant.calls) == 2 and "Messages: 3" in assistant.calls[-1][1]
    # Force asks even with a fresh cache.
    s.thread("T1", got.append, errors.append, force=True)
    assert len(assistant.calls) == 3


def test_thread_summary_reports_errors_and_missing_threads(db):  # noqa: F811
    db.upsert_emails([email("e1", "Plan", "anna@example.com", thread="T1")])
    s, _ = make(db, FakeAssistant(LLMError("Could not reach localhost:11434")))
    got, errors = [], []
    s.thread("T1", got.append, errors.append)
    assert errors == ["Could not reach localhost:11434"] and not got
    assert db.get_summary("thread:T1") is None
    s.thread("nope", got.append, errors.append)
    assert errors[-1] == "Nothing to summarise"
    off, _ = make(db, FakeAssistant(enabled=False))
    assert not off.available
    assert not summaries.Summariser(db, FakeEngine(db), None).available


# ------------------------------------------------------------------ sender

def test_sender_summary_uses_the_newest_message_outside_trash(db):  # noqa: F811
    db.upsert_emails([
        email("n1", "Sale 20%", "news@shop.example", "Shop", when="2026-09-01T10:00:00Z"),
        email("n2", "Sale 30%", "news@shop.example", "Shop", when="2026-09-02T10:00:00Z"),
        email("n3", "Sale 99%", "news@shop.example", "Shop", when="2026-09-03T10:00:00Z", mailboxes=("mb-trash",)),
    ])
    db.set_email_body(body("n2", html="<p>Thirty percent off shoes until Sunday</p>"))
    assistant = FakeAssistant("Thirty percent off shoes until Sunday.")
    s, _ = make(db, assistant)
    got, errors = [], []
    s.sender("News@Shop.example", got.append, errors.append)
    assert not errors and got[-1].text.startswith("Thirty")
    system, user = assistant.calls[-1]
    assert system == summaries.SENDER_SYSTEM and "Subject: Sale 30%" in user and "shoes" in user
    assert s.cached_sender("news@shop.example").cached
    s.sender("news@shop.example", got.append, errors.append)
    assert len(assistant.calls) == 1
    s.sender("nobody@example.com", got.append, errors.append)
    assert errors[-1] == "No message from this sender in the cache"


def test_summaries_are_cleared_with_the_cache(db):  # noqa: F811
    db.set_summary("thread:T1", "text", "e1,e2", "m")
    assert db.get_summary("thread:T1")["fingerprint"] == "e1,e2"
    db.clear_all()
    assert db.get_summary("thread:T1") is None


@pytest.mark.parametrize("ids", [["a"], ["a", "b"]])
def test_fingerprint_is_the_id_list(ids):
    assert summaries.fingerprint(ids) == ",".join(ids)
