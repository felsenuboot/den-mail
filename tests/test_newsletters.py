"""The newsletter scan: who sends list mail, how much, and how to leave."""

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from den_mail.jmap.client import JMAPClient
from den_mail.newsletters import fetch_list_mail, group_senders
from den_mail.unsubscribe import identity_for

from .fake_server import FakeJMAPServer


@pytest.fixture
def server():
    srv = FakeJMAPServer().start()
    yield srv
    srv.stop()


def test_scan_groups_senders_busiest_first(server):
    client = JMAPClient(server.token, server.session_url)
    client.fetch_session()
    trash = next(m["id"] for m in server.data.mailboxes.values() if m.get("role") == "trash")
    junk = next(m["id"] for m in server.data.mailboxes.values() if m.get("role") == "junk")
    emails = fetch_list_mail(client, [trash, junk])
    assert emails and all("header:List-Unsubscribe:asRaw" in e for e in emails)
    assert not any(trash in e["mailboxIds"] for e in emails)
    senders = group_senders(emails, {"promo@shop.example": "2026-08-01"})
    by_email = {s.email: s for s in senders}
    digest = by_email["digest@lists.example.com"]
    assert senders[0] is digest and digest.count == 3 and digest.unread == 1
    assert digest.plan.kind == "mailto" and digest.method == "by mail"
    newest = max((e for e in server.data.emails.values() if e["from"][0]["email"] == digest.email
                  and "Old issue." not in e["preview"]), key=lambda e: e["receivedAt"])
    assert digest.sample["id"] == newest["id"] and len(digest.email_ids) == 3
    arch = by_email["news@archlinux.org"]
    assert arch.plan.kind == "one-click" and arch.unsubscribed_on is None
    promo = by_email["promo@shop.example"]
    assert promo.plan.kind == "browser" and promo.unsubscribed_on == "2026-08-01"
    # a small page still finds the busiest sender
    assert group_senders(fetch_list_mail(client, [], limit=2))[0].count <= 2


def test_scan_without_list_mail_is_empty(server):
    client = JMAPClient(server.token, server.session_url)
    client.fetch_session()
    for e in server.data.emails.values():
        e["_headers"].pop("List-Unsubscribe", None)
    assert fetch_list_mail(client, []) == []
    assert group_senders([]) == []


def test_identity_for_prefers_the_delivered_address():
    identities = [{"id": "a", "email": "me@example.org"}, {"id": "b", "email": "alias@example.org"},
                  {"id": "w", "email": "*@wild.example.org"}]
    assert identity_for(identities, "me@example.org", {"to": [{"email": "Alias@example.org"}]})["id"] == "b"
    assert identity_for(identities, "me@example.org", {"header:Delivered-To:asText": "alias@example.org",
                                                       "to": [{"email": "me@example.org"}]})["id"] == "b"
    # Fastmail writes X-Delivered-To, in whatever case the sender used
    assert identity_for(identities, "me@example.org", {"header:X-Delivered-To:asText": "ALIAS@Example.org",
                                                       "to": [{"email": "me@example.org"}]})["id"] == "b"
    wild = identity_for(identities, "me@example.org", {"header:X-Delivered-To:asText": "Shop@wild.example.org"})
    assert wild["id"] == "w" and wild["email"] == "shop@wild.example.org"
    wild = identity_for(identities, "me@example.org", {"to": [{"email": "shop@wild.example.org"}]})
    assert wild["id"] == "w" and wild["email"] == "shop@wild.example.org"
    assert identity_for(identities, "me@example.org", {"to": [{"email": "nobody@else.org"}]})["id"] == "a"
    assert identity_for([], "me@example.org", {}) is None
