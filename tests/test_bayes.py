"""The learned layer (#23): tokens, the model, and how the cache uses it."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from den_mail.classify import bayes
from den_mail.classify.rules import (
    H_LIST_UNSUBSCRIBE,
    NEWSLETTERS,
    PRIMARY,
    SOURCE_BAYES,
    SOURCE_RULES,
    SOURCE_USER,
    UPDATES,
)
from den_mail.store.db import Database

from .test_views import TRASH_JUNK, db, email  # noqa: F401 - fixture and helper

UNSUB = {H_LIST_UNSUBSCRIBE: "<mailto:leave@lists.example>"}


def test_tokens_cover_sender_words_headers_and_behaviour():
    e = email("a", "Your weekly streak report", "hello@duolingo.example", "Duolingo", preview="Keep it up, 12 days", **UNSUB)
    t = bayes.tokens(e, {"count": 5, "unread": 5, "deleted_unread": 3, "replied": False})
    assert "from:hello@duolingo.example" in t and "domain:duolingo.example" in t and "local:hello" in t
    assert "dom:duolingo" in t and "name:duolingo" in t
    assert "s:weekly" in t and "s:streak" in t and "p:keep" in t and "p:days" in t
    assert "h:list-unsubscribe" in t
    assert "b:never-opened" in t and "b:deleted-unread" in t and "b:replied" not in t
    assert len(t) == len(set(t))
    assert "b:mostly-read" in bayes.tokens(e, {"count": 10, "unread": 1})
    assert "b:replied" in bayes.tokens(e, {"count": 1, "unread": 0, "replied": True})
    assert not [x for x in bayes.tokens({"subject": "hi"}) if x.startswith("from:")]


def test_model_learns_and_explains():
    m = bayes.BayesModel()
    assert m.predict(["s:hello"]) is None and not m.ready
    for i in range(12):
        m.add(NEWSLETTERS, [f"from:news{i}@lists.example", "h:list-unsubscribe", "s:digest", "s:weekly"])
        m.add(UPDATES, [f"from:hello{i}@app.example", "h:list-unsubscribe", "s:streak", "b:never-opened"])
    assert m.size == 24 and not m.ready          # no corrections yet
    m.corrections = 3
    assert m.ready
    p = m.predict(["from:hello99@app.example", "h:list-unsubscribe", "s:streak", "b:never-opened"])
    assert p.category == UPDATES and p.probability > 0.9
    assert p.evidence and p.evidence[0][0] in ("s:streak", "b:never-opened")
    assert "streak" in p.reason or "never-opened" in p.reason
    q = m.predict(["h:list-unsubscribe"])       # no distinguishing token: unsure
    assert q.probability < bayes.MIN_PROBABILITY
    again = bayes.BayesModel.from_rows(dict(m.docs), m.rows(), 3)
    assert again.predict(["s:digest", "s:weekly"]).category == NEWSLETTERS and again.ready


def test_corrections_train_the_model_and_override_unsure_verdicts(db):  # noqa: F811
    # "friendly" list mail the rules call Newsletters: the user says Updates, eight times
    corrected = [email(f"c{i}", f"Your streak day {i}", f"hello{i}@app.example", "Streak App", preview="Keep it up",
                       **UNSUB) for i in range(8)]
    # and a body of sure verdicts for the rules
    sure = [email(f"n{i}", f"Digest #{i}", f"news{i}@lists.example", "Weekly Digest", preview="This week", **UNSUB)
            for i in range(12)]
    db.upsert_emails(corrected + sure)
    assert db.get_categories(["c0"]) == {"c0": NEWSLETTERS}
    db.set_category([e["id"] for e in corrected], UPDATES)
    assert db.get_classification("c0")["source"] == SOURCE_USER and db.get_classification("c0")["reason"] == "your choice"
    size, corrections = db.retrain_bayes()
    assert corrections == 8 and size == 8 * bayes.CORRECTION_WEIGHT + 12 and db.bayes_ready
    # an unsure rules verdict (Primary for lack of signals) on similar mail: the model decides
    db.upsert_emails([email("x", "Your streak day 9", "hello9@app.example", "Streak App", preview="Keep it up")])
    row = db.get_classification("x")
    assert row["category"] == UPDATES and row["source"] == SOURCE_BAYES and "learned" in row["reason"]
    assert row["confidence"] >= bayes.MIN_PROBABILITY
    # a sure rules verdict is left alone, whatever the model thinks
    db.upsert_emails([email("y", "Your streak day 10", "hello10@app.example", "Streak App", preview="Keep it up", **UNSUB)])
    assert db.get_classification("y")["source"] == SOURCE_RULES
    # the user's word survives both the rules and the model
    db.upsert_emails([corrected[0]])
    assert db.get_classification("c0")["source"] == SOURCE_USER
    # the model is kept in the cache and comes back on the next start
    path = db.path
    db.close()
    again = Database(path)
    assert again.bayes_ready
    again.upsert_emails([email("z", "Your streak day 11", "hello11@app.example", "Streak App", preview="Keep it up")])
    assert again.get_classification("z")["source"] == SOURCE_BAYES
    assert set(again.unsure_ids()) >= {"x", "z"} or again.get_classification("x")["confidence"] >= 0.8
    again.clear_all()
    assert not Database(path).bayes_ready
    again.close()


def test_model_stays_silent_without_corrections(db):  # noqa: F811
    db.upsert_emails([email(f"n{i}", f"Digest #{i}", f"news{i}@lists.example", "Weekly Digest", **UNSUB) for i in range(30)])
    size, corrections = db.retrain_bayes()
    assert size == 30 and corrections == 0 and not db.bayes_ready
    db.upsert_emails([email("p", "Hello", "anna@example.net", "Anna")])
    assert db.get_classification("p")["source"] == SOURCE_RULES and db.get_categories(["p"]) == {"p": PRIMARY}
