"""The deterministic categoriser (#18): rules, cache plumbing and the list filter."""

from __future__ import annotations

import gi
import pytest

gi.require_version("Gtk", "4.0")

from den_mail.classify.rules import (
    CATEGORIES,
    H_AUTO_SUBMITTED,
    H_FEEDBACK_ID,
    H_LIST_ID,
    H_LIST_POST,
    H_LIST_UNSUBSCRIBE,
    H_PRECEDENCE,
    LISTS,
    NEWSLETTERS,
    PRIMARY,
    PROMOTIONS,
    SECURITY,
    SOURCE_RULES,
    SOURCE_USER,
    TRANSACTIONS,
    UPDATES,
    classify,
    is_noreply,
)
from den_mail.jmap.types import EMAIL_BODY_PROPERTIES, EMAIL_LIST_PROPERTIES
from den_mail.models.thread import ThreadListModel
from den_mail.store.db import Database


def mail(subject: str, preview: str = "", frm: str = "anna@example.net", **headers) -> dict:
    return {"id": "M1", "subject": subject, "preview": preview, "from": [{"email": frm}], **headers}


def cat(subject: str, preview: str = "", frm: str = "anna@example.net", **headers) -> str:
    return classify(mail(subject, preview, frm, **headers)).category


# ------------------------------------------------------------------ rules


def test_headers_outrank_content():
    unsub = {H_LIST_UNSUBSCRIBE: "<mailto:leave@lists.example>"}
    assert cat("Widget lifecycle", **{H_LIST_POST: "<mailto:gtk-devel@lists.example>"}, **unsub) == LISTS
    assert cat("Sale ends soon", **{H_LIST_POST: "<mailto:gtk-devel@lists.example>"}) == LISTS
    assert cat("Digest #40", **{H_LIST_POST: "NO", **unsub}) == NEWSLETTERS  # RFC 2369: no posting
    assert cat("Arch Linux news: kernel 7.2", frm="news@archlinux.org", **unsub) == NEWSLETTERS
    assert cat("Weekly update", **{H_PRECEDENCE: "bulk"}) == NEWSLETTERS
    assert cat("Weekly update", **{H_LIST_ID: "<news.example>"}) == NEWSLETTERS
    # Feedback-ID alone is what big senders put on everything: automated, not a newsletter
    assert cat("Ordered: 'Blum Manufaktur 250ml...'", frm="bestellbestaetigung@shop.example",
               **{H_FEEDBACK_ID: "a:b:c:mailer"}) == TRANSACTIONS
    assert cat("Someone commented on your document", frm="comments-noreply@docs.example",
               **{H_FEEDBACK_ID: "a:b:c:mailer"}) == UPDATES
    assert cat("Weekly update", **{H_FEEDBACK_ID: "a:b:c:mailer"}) == UPDATES
    assert cat("Weekly update", **{H_FEEDBACK_ID: "a:b:c:mailer", **unsub}) == NEWSLETTERS


def test_list_post_from_a_machine_is_a_notice_and_list_tags_are_lists():
    post = {H_LIST_POST: "<mailto:reply+abc@reply.github.example>", H_PRECEDENCE: "list",
            H_LIST_UNSUBSCRIBE: "<mailto:unsub@github.example>"}
    assert cat("[org/repo] Run failed: CI - master", frm="notifications@github.example", **post) == UPDATES
    assert cat("[gtk-devel] Widget lifecycle", frm="erin@example.org", **post) == LISTS
    assert cat("Re: [gsba-bkyomu:35917] About the programme", frm="office@uni.example") == LISTS
    assert cat("[VUB#03338685] Re: Confirmation", frm="office@uni.example") != LISTS  # a ticket number
    assert cat("Invoice [acct:12345]", frm="office@uni.example") == TRANSACTIONS  # wording first


def test_list_mail_from_noreply_needs_a_newsletter_cue():
    unsub = {H_LIST_UNSUBSCRIBE: "<https://x.example/u>"}
    assert cat("Analytics Manager @ Grüns", "Hi Felix, it looks like your background", frm="donotreply@match.jobs.example", **unsub) == UPDATES
    assert cat("Ihr monatlicher Bericht für Ihre FRITZ!Box", frm="noreply@router.example", **unsub) == UPDATES
    assert cat("Nowhere to hide", "Morning Briefing", frm="noreply@news.paper.example", **unsub) == NEWSLETTERS
    assert cat("Hidden Worlds IRL", "Digital camo", frm="404-media@ghost.example", **unsub) == NEWSLETTERS
    assert cat("Issue #42", "Read in browser", frm="noreply@paper.example", **unsub) == NEWSLETTERS
    assert cat("Hello there", "Wird diese Nachricht nicht richtig dargestellt?", frm="noreply@x.example", **unsub) == NEWSLETTERS
    assert cat("Thank You For Applying!", frm="acme@myworkday.example", **unsub) == UPDATES
    assert cat("Vielen Dank für deine Bewerbung", frm="jobs@firma.example") == UPDATES


def test_own_mail_is_primary():
    own = {"me@example.com"}.__contains__
    m = mail("den-mail send test", frm="me@example.com", **{H_FEEDBACK_ID: "i6ec1497d:Provider"})
    assert classify(m, None, own).category == PRIMARY
    assert classify(m).category == UPDATES


def test_bulk_mail_splits_into_promotions_by_wording():
    unsub = {H_LIST_UNSUBSCRIBE: "<https://shop.example/u>"}
    assert cat("Sale ends soon", "Everything must go.", frm="promo@shop.example", **unsub) == PROMOTIONS
    assert cat("20% off everything this weekend", **unsub) == PROMOTIONS
    assert cat("Nur heute: 30 % Rabatt auf alles", **unsub) == PROMOTIONS
    assert cat("Kostenloser Versand bis Sonntag", **unsub) == PROMOTIONS
    assert cat("Neu eingetroffen", "Jetzt shoppen und sparen", **unsub) == PROMOTIONS
    assert cat("Der Newsletter im September", "Liebe Leserin, lieber Leser", **unsub) == NEWSLETTERS
    # a receipt that carries list headers is still a receipt
    assert cat("Your order has shipped", frm="ship-confirm@shop.example", **unsub) == TRANSACTIONS
    assert cat("New sign-in to your account", **unsub) == SECURITY


@pytest.mark.parametrize("subject", [
    "Security alert: new sign-in", "123456 is your verification code", "Your one-time password",
    "Reset your password", "Verify your email address", "Unusual activity on your account",
    "Neue Anmeldung bei Ihrem Konto", "Ihr Bestätigungscode", "Passwort zurücksetzen",
    "Bitte bestätigen Sie Ihre E-Mail-Adresse", "Sicherheitswarnung: unbekanntes Gerät",
])
def test_security_wording(subject):
    assert cat(subject, frm="noreply@service.example") == SECURITY
    assert cat(subject) == SECURITY  # the subject alone is enough


@pytest.mark.parametrize("subject", [
    "Invoice 2026-08 for hosting", "Your package is on its way", "Concert tickets confirmed",
    "Your ticket: Berlin → München", "Payment received", "Your receipt from Apple", "Order #4711 confirmed",
    "Ihre Bestellung #4711 wurde versandt", "Rechnung 2026-09-001", "Ihr Paket kommt heute",
    "Buchungsbestätigung: Hotel Sonne", "Zahlungseingang bestätigt", "Ihre Fahrkarte", "Retoure eingegangen",
])
def test_transaction_wording(subject):
    assert cat(subject, frm="noreply@shop.example") == TRANSACTIONS
    assert cat(subject) == TRANSACTIONS


def test_automated_senders_are_updates_unless_the_text_says_otherwise():
    assert cat("[repo] Sync engine review (#12)", "Comment 1: looks good", frm="noreply@github.com") == UPDATES
    assert cat("Backup completed", frm="donotreply@nas.example") == UPDATES
    assert cat("Your weekly report", frm="no-reply@app.example") == UPDATES
    assert cat("Out of office", "I am away until Monday", **{H_AUTO_SUBMITTED: "auto-replied"}) == UPDATES
    assert cat("Hello", frm="anna@example.net", **{H_AUTO_SUBMITTED: "no"}) == PRIMARY
    # the preview of automated mail is trusted more than that of a person's
    assert cat("Your code", "Use 482913 to sign in.", frm="noreply@service.example") == SECURITY
    assert cat("Reminder: renew domain", "example.com is due for renewal", frm="noreply@registrar.example") == TRANSACTIONS
    assert cat("Reminder: renew domain", "example.com is due for renewal") == TRANSACTIONS
    assert cat("Notes", "renewal of our chat", frm="anna@example.net") == TRANSACTIONS  # weak, preview-only
    assert cat("Neues Passwort", frm="abeasyinfo@example.net") == SECURITY
    assert cat("A withdrawal was made from an unfamiliar device") == SECURITY
    assert cat("Neue 1Password-Anmeldewarnung", frm="hello@1password.example") == SECURITY
    assert cat("Verify your candidate account", frm="acme@otp.workday.example") == SECURITY
    assert cat("Ihr Brunobett.de-Auftrag 26041101: Terminbestätigung", frm="auftragsinfo@courier.example") == TRANSACTIONS
    assert cat("Pegasus Pre-Flight Reminders", frm="pegasus@fly.example") == TRANSACTIONS
    assert cat("SmartLife 登録検証コード", frm="system@notice.example") == SECURITY
    assert cat("ご注文ありがとうございます", frm="shop@store.example") == TRANSACTIONS
    assert cat("Your Classique membership has been renewed", frm="loyalty@rail.example") == TRANSACTIONS
    assert cat("Sie haben 2 ungelesene Nachrichten in Ihrem Postfach", frm="postfach@versicherung.example") == UPDATES
    assert cat("Updated invitation: Felix x Mirela | Call", frm="mirela@people.example") == UPDATES
    assert cat("Welcome! Your Careers account has been created.", frm="talent@firm.example") == UPDATES


def test_noreply_address_shapes():
    for addr in ("noreply@a.example", "no-reply@a.example", "no_reply@a.example", "donotreply@a.example",
                 "do-not-reply@a.example", "newsletter-noreply@a.example", "notifications@a.example",
                 "alerts@a.example", "mailer-daemon@a.example", "NoReply@A.Example"):
        assert is_noreply(addr), addr
    for addr in ("anna@example.net", "reply-to-me@a.example", "notes@a.example", "", "bogus"):
        assert not is_noreply(addr), addr


def test_person_mail_is_primary():
    for subject in ("GTK meetup on Thursday", "Re: dentist appointment", "Lunch tomorrow?", "Photos from the weekend",
                    "Quarterly planning notes", "Welcome to the mailing list", "Bike repair quote"):
        assert cat(subject, "hi, see attached") == PRIMARY, subject
    assert classify(mail("Hi")).confidence < classify(mail("Reset your password")).confidence


def test_written_to_sender_stays_primary_but_not_over_headers():
    written = {"anna@example.net"}.__contains__
    assert classify(mail("Notes", "here is the invoice for the trip"), written).category == PRIMARY
    assert classify(mail("Notes", "here is the invoice for the trip")).category == TRANSACTIONS
    assert classify(mail("Out of office", **{H_AUTO_SUBMITTED: "auto-replied"}), written).category == PRIMARY
    # a list or a receipt is what it is, whoever sends it
    assert classify(mail("Digest", **{H_LIST_UNSUBSCRIBE: "<mailto:x>"}), written).category == NEWSLETTERS
    assert classify(mail("Invoice 42"), written).category == TRANSACTIONS


def test_every_category_is_reachable_and_named():
    assert set(CATEGORIES) == {PRIMARY, TRANSACTIONS, SECURITY, UPDATES, NEWSLETTERS, LISTS, PROMOTIONS}
    assert classify({}).category == PRIMARY  # nothing to go on


def test_headers_are_list_properties():
    for h in (H_LIST_POST, H_LIST_ID, H_LIST_UNSUBSCRIBE, H_PRECEDENCE, H_AUTO_SUBMITTED, H_FEEDBACK_ID):
        assert h in EMAIL_LIST_PROPERTIES
        assert EMAIL_BODY_PROPERTIES.count(h) == 1


# ------------------------------------------------------------------ cache


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.sqlite3")
    d.upsert_mailboxes([{"id": "mb-inbox", "name": "Inbox", "role": "inbox"}, {"id": "mb-sent", "name": "Sent", "role": "sent"}])
    d.set_identities([{"id": "i1", "email": "me@example.com", "name": "Me"}, {"id": "i2", "email": "*@example.org"}])
    yield d
    d.close()


def email(eid: str, subject: str, frm: str, to: str = "me@example.com", mailbox: str = "mb-inbox",
          thread: str | None = None, when: str = "2026-09-01T10:00:00Z", preview: str = "", **headers) -> dict:
    return {"id": eid, "threadId": thread or f"T-{eid}", "subject": subject, "preview": preview,
            "from": [{"email": frm}], "to": [{"email": to}], "receivedAt": when, "keywords": {},
            "mailboxIds": {mailbox: True}, **headers}


def test_upsert_classifies_and_thread_summary_carries_the_category(db):
    db.upsert_emails([
        email("a", "Digest #1", "digest@lists.example", **{H_LIST_UNSUBSCRIBE: "<mailto:x>"}),
        email("b", "Lunch?", "anna@example.net"),
        email("c", "Your order has shipped", "noreply@shop.example"),
    ])
    assert db.get_categories(["a", "b", "c"]) == {"a": NEWSLETTERS, "b": PRIMARY, "c": TRANSACTIONS}
    row = db.get_classification("a")
    assert row["source"] == SOURCE_RULES and row["confidence"] > 0.5 and "List-Unsubscribe" in row["reason"]
    assert db.thread_summary("T-a", None, set()).category == NEWSLETTERS
    assert db.thread_summary("T-b", "mb-inbox", set()).category == PRIMARY
    # the latest message of a thread decides
    db.upsert_emails([email("c2", "Did it arrive?", "anna@example.net", thread="T-c",
                            when="2026-09-02T10:00:00Z", preview="just checking")])
    assert db.thread_summary("T-c", None, set()).category == PRIMARY
    db.delete_emails(["a"])
    assert db.get_categories(["a"]) == {}


def test_sent_mail_records_correspondents_and_reclassifies_their_mail(db):
    db.upsert_emails([email("a", "Notes", "paul@example.net", preview="the invoice for the trip is attached")])
    assert db.get_categories(["a"]) == {"a": TRANSACTIONS}
    assert not db.is_correspondent("paul@example.net")
    # a reply in the Sent folder, or from an identity (wildcard domains included)
    db.upsert_emails([email("s1", "Re: Notes", "me@example.com", to="paul@example.net", mailbox="mb-sent")])
    assert db.is_correspondent("paul@example.net") and db.is_correspondent("Paul@Example.net")
    assert db.get_categories(["a"]) == {"a": PRIMARY}
    db.upsert_emails([email("s2", "Hi", "someone@example.org", to="kim@example.net", mailbox="mb-inbox")])
    assert db.is_correspondent("kim@example.net")
    assert not db.is_correspondent("me@example.com")  # never oneself
    # within one batch the sent mail counts first, whatever the order
    db.upsert_emails([email("b", "Notes", "lee@example.net", preview="invoice attached"),
                      email("s3", "Hello", "me@example.com", to="lee@example.net", mailbox="mb-sent")])
    assert db.get_categories(["b"]) == {"b": PRIMARY}


def test_user_choice_survives_the_rules(db):
    db.upsert_emails([email("a", "Digest #1", "digest@lists.example", **{H_LIST_UNSUBSCRIBE: "<mailto:x>"})])
    db.set_category(["a"], PROMOTIONS)
    db.upsert_emails([email("a", "Digest #1", "digest@lists.example", **{H_LIST_UNSUBSCRIBE: "<mailto:x>"})])
    db.reclassify()
    assert db.get_classification("a")["category"] == PROMOTIONS
    assert db.get_classification("a")["source"] == SOURCE_USER


def test_header_backfill_for_mail_cached_before(db):
    db.upsert_emails([email("old", "Digest #1", "digest@lists.example"), email("gone", "x", "a@b.example"),
                      email("new", "Digest #2", "digest@lists.example", **{H_LIST_POST: None, H_LIST_UNSUBSCRIBE: None})])
    assert db.get_categories(["old"]) == {"old": PRIMARY}
    assert set(db.emails_missing_headers()) == {"old", "gone"}
    db.merge_headers(["old", "gone"], [{"id": "old", H_LIST_UNSUBSCRIBE: "<mailto:x>", H_LIST_POST: None}])
    assert db.get_categories(["old"]) == {"old": NEWSLETTERS}
    assert db.get_email("old")[H_LIST_UNSUBSCRIBE] == "<mailto:x>"
    assert db.emails_missing_headers() == []  # the id the server no longer knows is not retried
    db.clear_all()
    assert db.get_categories(["old"]) == {} and not db.is_correspondent("x@y.example")


def test_model_category_filter(db):
    db.upsert_emails([
        email("a", "Digest #1", "digest@lists.example", when="2026-09-03T10:00:00Z", **{H_LIST_UNSUBSCRIBE: "<mailto:x>"}),
        email("b", "Lunch?", "anna@example.net", when="2026-09-02T10:00:00Z"),
        email("c", "Your order has shipped", "noreply@shop.example", when="2026-09-01T10:00:00Z"),
    ])
    model = ThreadListModel(db)
    model.set_email_ids(["a", "b", "c"], 3, True)
    assert [t.category for t in model.items] == [NEWSLETTERS, PRIMARY, TRANSACTIONS]
    model.set_category_filter(TRANSACTIONS)
    assert [t.thread_id for t in model.items] == ["T-c"] and model.hidden_by_filter == 2
    assert model.index_of("T-a") == -1 and "T-a" in model.by_thread
    model.set_email_ids(["a", "b", "c"], 3, True)  # a refresh keeps the filter
    assert [t.thread_id for t in model.items] == ["T-c"]
    # a message reclassified into the category appears, one moved out disappears
    db.set_category(["b"], TRANSACTIONS)
    model.refresh_threads(["b"])
    assert [t.thread_id for t in model.items] == ["T-b", "T-c"]
    db.set_category(["c"], UPDATES)
    model.refresh_threads(["c"])
    assert [t.thread_id for t in model.items] == ["T-b"]
    model.remove_threads({"T-b"})
    assert model.get_n_items() == 0 and len(model.all_threads) == 2
    model.set_category_filter(None)
    assert [t.thread_id for t in model.items] == ["T-a", "T-c"]
