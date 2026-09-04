"""Label suggestions (#60): the per-label models, their training from the cache, the suggestions."""

from __future__ import annotations

from den_mail.classify import bayes, labels
from den_mail.store.db import Database

from .test_views import db, email  # noqa: F401 - fixture and helper


def work_mail(i: int, labelled: bool = True, seen: bool = True) -> dict:
    boxes = ("mb-inbox", "mb-work") if labelled else ("mb-inbox",)
    return email(f"w{i}", f"Sprint planning {i}", "pm@corp.example", "Project Manager", seen=seen,
                 mailboxes=boxes, preview="Agenda for the standup and the roadmap review",
                 when=f"2026-08-{10 + i % 18:02d}T10:00:00Z")


def shop_mail(i: int) -> dict:
    return email(f"s{i}", f"Sale {i}% off shoes", "news@shop.example", "Shop", seen=False,
                 mailboxes=("mb-inbox",), preview="Discount on sneakers this weekend only",
                 when=f"2026-08-{1 + i % 27:02d}T12:00:00Z")


def test_model_learns_a_label_and_stays_silent_without_enough_examples():
    examples = [(bayes.tokens(work_mail(i)), {"mb-work"}) for i in range(12)]
    examples += [(bayes.tokens(shop_mail(i)), set()) for i in range(20)]
    model = labels.LabelModel.train(examples, ["mb-work", "mb-rare"])
    assert model.labels == ["mb-work"] and model.ready
    s = model.suggest(bayes.tokens(work_mail(99)))
    assert s and s[0].label_id == "mb-work" and s[0].probability >= labels.MIN_PROBABILITY
    assert any(t.startswith("from:pm@") or t == "domain:corp.example" for t in s[0].evidence)
    assert model.suggest(bayes.tokens(shop_mail(99))) == []
    assert model.suggest(bayes.tokens(work_mail(99)), present={"mb-work"}) == []   # already labelled
    # too few positives: no model for that label
    few = labels.LabelModel.train([(bayes.tokens(work_mail(i)), {"mb-work"}) for i in range(3)]
                                  + [(bayes.tokens(shop_mail(i)), set()) for i in range(5)], ["mb-work"])
    assert not few.ready and few.suggest(bayes.tokens(work_mail(9))) == []
    # storage round trip keeps the verdicts
    again = labels.LabelModel.from_rows(model.doc_rows(), model.token_rows())
    assert again.labels == ["mb-work"]
    assert again.suggest(bayes.tokens(work_mail(99)))[0].label_id == "mb-work"


def test_cache_trains_from_labelled_mail_and_suggests_for_new_mail(db):  # noqa: F811
    db.upsert_emails([work_mail(i) for i in range(12)] + [shop_mail(i) for i in range(20)])
    assert not db.labels_ready
    size = db.retrain_labels()
    assert size["mb-work"] == 12 and db.labels_ready
    fresh = work_mail(50, labelled=False)
    db.upsert_emails([fresh])
    got = db.label_suggestions(db.get_email("w50"))
    assert [s.label_id for s in got] == ["mb-work"]
    assert db.label_suggestions(db.get_email("w1")) == []          # carries the label already
    assert db.label_suggestions(db.get_email("s3")) == []
    # a fresh Database reads the stored model back
    other = Database(db.path)
    assert other.labels_ready and [s.label_id for s in other.label_suggestions(other.get_email("w50"))] == ["mb-work"]
    other.close()
    # folders never get a model, even with many messages
    db.upsert_emails([email(f"t{i}", f"Trash {i}", "x@example.net", mailboxes=("mb-trash",)) for i in range(12)])
    db.retrain_labels()
    assert "mb-trash" not in db.retrain_labels()
