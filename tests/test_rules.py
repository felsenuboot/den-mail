"""Client-side rules (#22): matching, combining, storage, and their run in the engine."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from den_mail import rules
from den_mail.classify.rules import H_LIST_ID
from den_mail.config import Config
from den_mail.jmap.types import KW_SEEN, ROLE_ARCHIVE, ROLE_INBOX, ROLE_TRASH
from den_mail.store.sync import mailbox_query_spec

from .test_engine import engine, pump, server  # noqa: F401 - fixtures

ROLES = {"inbox": "in", "archive": "ar", "trash": "tr"}


def mail(eid: str, frm: str, mailbox: str = "in", **extra) -> dict:
    return {"id": eid, "from": [{"email": frm, "name": ""}], "mailboxIds": {mailbox: True}, **extra}


def test_rules_match_sender_domain_list_and_category():
    e = mail("a", "News@Shop.example", **{H_LIST_ID: "Shop news <news.shop.example>"})
    assert rules.Rule("sender", "news@shop.example", "archive").matches(e, None)
    assert not rules.Rule("sender", "other@shop.example", "archive").matches(e, None)
    assert rules.Rule("domain", "shop.example", "archive").matches(e, None)
    assert rules.Rule("domain", "example", "archive").matches(e, None)          # a parent domain counts
    assert not rules.Rule("domain", "hop.example", "archive").matches(e, None)  # but not a suffix of a label
    assert rules.Rule("list", "news.shop.example", "archive").matches(e, None)
    assert rules.list_id_of({H_LIST_ID: "bare.list.example"}) == "bare.list.example"
    assert rules.list_id_of({}) == "" and not rules.Rule("list", "x", "archive").matches({}, None)
    assert rules.Rule("category", "newsletters", "archive").matches(e, "newsletters")
    assert not rules.Rule("category", "newsletters", "archive").matches(e, "primary")


def test_combine_merges_the_actions_of_the_rules_that_fired():
    label = rules.Rule("sender", "a@b.example", "label", "L1", "Work")
    archive = rules.Rule("domain", "b.example", "archive")
    read = rules.Rule("category", "updates", "mark_read")
    trash = rules.Rule("list", "l.example", "trash")
    act = rules.combine(["m1", "m2"], [label, archive, read], ROLES)
    assert act.email_ids == ["m1", "m2"] and not act.undoable
    assert act.mailbox_add == {"L1", "ar"} and act.mailbox_remove == {"in"} and act.mailbox_replace is None
    assert act.keyword_changes == {KW_SEEN: True}
    assert act.description == "Rule: label as Work, archive, mark as read"
    act = rules.combine(["m1"], [label, trash], ROLES)
    assert act.mailbox_replace == {"tr"} and not act.mailbox_add and not act.mailbox_remove
    assert rules.combine([], [label], ROLES) is None
    assert rules.combine(["m1"], [rules.Rule("sender", "x", "trash")], {"inbox": "in"}) is None  # no Trash to move to


def test_plan_groups_new_inbox_mail_by_the_rules_that_fired():
    r1 = rules.Rule("domain", "shop.example", "archive")
    r2 = rules.Rule("category", "newsletters", "mark_read")
    emails = [mail("a", "news@shop.example"), mail("b", "promo@shop.example"), mail("c", "anna@example.net"),
              mail("d", "news@shop.example", mailbox="sent")]
    acts, hits = rules.plan([r1, r2], emails, {"a": "newsletters", "d": "newsletters"}, ROLES, "in")
    assert hits == {r1.id: 2, r2.id: 1}
    by_ids = {tuple(a.email_ids): a for a in acts}
    assert set(by_ids) == {("a",), ("b",)}
    assert by_ids[("a",)].keyword_changes == {KW_SEEN: True} and by_ids[("b",)].keyword_changes == {}
    assert rules.plan([], emails, {}, ROLES, "in") == ([], {})


def test_rules_round_trip_through_the_config(tmp_path):
    config = Config(tmp_path / "config.json")
    assert rules.load_rules(config) == []
    rule = rules.add_rule(config, rules.Rule("sender", "A@B.example", "label", "L1", "Work"))
    again = rules.add_rule(config, rules.Rule("sender", "a@b.example", "label", "L1", "Work"))  # replaces
    other = rules.add_rule(config, rules.Rule("sender", "a@b.example", "archive"))
    loaded = rules.load_rules(Config(tmp_path / "config.json"))
    assert [r.id for r in loaded] == [again.id, other.id] and loaded[0].value == "a@b.example"
    assert rule.id != again.id and loaded[0].created and loaded[0].hits == 0
    rules.bump_hits(config, {again.id: 3, "unknown": 1})
    assert [r.hits for r in rules.load_rules(config)] == [3, 0]
    rules.remove_rule(config, again.id)
    assert [r.id for r in rules.load_rules(config)] == [other.id]
    # rows that make no sense are skipped, not crashed on
    config.set("rules", [{"kind": "sender", "value": "x", "action": "label"}, {"kind": "nope"}, "junk",
                         {"kind": "domain", "value": "b.example", "action": "trash"}])
    assert [r.value for r in rules.load_rules(config)] == ["b.example"]


def test_describe_texts():
    assert rules.Rule("domain", "b.example", "archive").describe_match() == "Mail from anyone at b.example"
    assert rules.Rule("category", "newsletters", "trash").describe_match() == "Newsletters mail"
    assert rules.Rule("sender", "a@b", "label", "L1", "Work").describe_action() == "label as Work"
    assert rules.Rule("sender", "a@b", "label", "L1", "Work").describe_action("Renamed") == "label as Renamed"
    assert rules.Rule("sender", "a@b", "label", "L1").describe_action() == "label as a label that no longer exists"


# ------------------------------------------------------------ in the engine


def test_new_mail_is_archived_and_read_by_a_rule_before_it_is_announced(engine, server):  # noqa: F811
    pump(lambda: engine.push_connected, timeout=10)
    inbox, archive = engine.roles[ROLE_INBOX], engine.roles[ROLE_ARCHIVE]
    rule = rules.add_rule(engine.config, rules.Rule("domain", "shop.example", "archive"))
    read = rules.add_rule(engine.config, rules.Rule("sender", "promo@shop.example", "mark_read"))
    fired = []
    engine.connect("rules-applied", lambda _e, hits: fired.append(hits))
    quiet = server.deliver("Sale", frm={"name": "Shop", "email": "promo@shop.example"})
    loud = server.deliver("Hello", frm={"name": "Anna", "email": "anna@example.net"})
    pump(lambda: engine.db.get_email(loud) is not None and engine.db.get_email(quiet) is not None, timeout=15)
    pump(lambda: fired, timeout=10)
    assert fired[0] == {rule.id: 1, read.id: 1}
    local = engine.db.get_email(quiet)
    assert inbox not in local["mailboxIds"] and archive in local["mailboxIds"] and local["keywords"].get(KW_SEEN)
    remote = server.data.emails[quiet]
    assert inbox not in remote["mailboxIds"] and remote["keywords"].get(KW_SEEN)
    assert inbox in engine.db.get_email(loud)["mailboxIds"]
    # only the message the rules left alone is announced as new mail
    pump(lambda: engine.events.get("new-mail"), timeout=10)
    announced = {e["id"] for args in engine.events["new-mail"] for e in args[0]}
    assert loud in announced and quiet not in announced


def test_act_on_sender_reaches_mail_the_cache_never_saw(engine, server):  # noqa: F811
    inbox = engine.roles[ROLE_INBOX]
    key = engine.load_query(mailbox_query_spec(inbox))
    pump(lambda: any(a[0] == key for a in engine.events.get("query-updated", [])))
    digest = "digest@lists.example.com"
    theirs = [e["id"] for e in server.data.emails.values() if e["from"][0]["email"] == digest]
    trash = engine.roles[ROLE_TRASH]
    outside = [i for i in theirs if trash not in server.data.emails[i]["mailboxIds"]]
    assert len(outside) == 3
    done = []
    engine.act_on_sender(digest, lambda ids: rules.combine(ids, [rules.Rule("sender", digest, "archive")], engine.roles),
                         lambda rec: done.append(rec))
    pump(lambda: done, timeout=10)
    for i in outside:
        assert inbox not in server.data.emails[i]["mailboxIds"]
    trashed = [i for i in theirs if i not in outside]
    assert trashed and all(server.data.emails[i]["mailboxIds"] == {trash: True} for i in trashed)  # left alone
    # a sender with no mail outside Trash and Spam is a no-op
    done.clear()
    engine.act_on_sender("nobody@example.net", lambda ids: rules.combine(ids, [], engine.roles), lambda rec: done.append(rec))
    pump(lambda: done, timeout=10)
    assert done == [None]
