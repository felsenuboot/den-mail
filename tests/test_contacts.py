"""The address book (#4) and contact photos (#14)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from den_mail.jmap.types import CAP_CONTACTS, Session, contact_emails, contact_name, contact_photo

from .test_engine import engine, pump, server  # noqa: F401 - fixtures


def test_card_helpers():
    card = {"id": "c1", "name": {"components": [{"kind": "given", "value": "Anna"}, {"kind": "separator", "value": " "},
                                                {"kind": "surname", "value": "Berger"}]},
            "emails": {"a": {"address": "Anna@Example.net"}, "b": {"address": "anna.berger@work.example"}, "c": {"address": "nope"}},
            "media": {"m": {"kind": "photo", "blobId": "B1", "mediaType": "image/png"}, "s": {"kind": "sound", "blobId": "B2"}}}
    assert contact_name(card) == "Anna Berger"
    assert contact_name({"name": {"full": " Dr. Anna ", "components": [{"kind": "given", "value": "x"}]}}) == "Dr. Anna"
    assert contact_name({}) == ""
    assert contact_emails(card) == ["anna@example.net", "anna.berger@work.example"]
    assert contact_photo(card) == ("B1", "image/png") and contact_photo({"media": {"s": {"kind": "sound"}}}) is None
    s = Session.from_json({"apiUrl": "a", "downloadUrl": "d", "uploadUrl": "u", "primaryAccounts": {"urn:ietf:params:jmap:mail": "A"},
                           "accounts": {"A": {"accountCapabilities": {"urn:ietf:params:jmap:mail": {}}}}})
    assert not s.has_contacts
    s = Session.from_json({"apiUrl": "a", "downloadUrl": "d", "uploadUrl": "u", "capabilities": {CAP_CONTACTS: {}},
                           "primaryAccounts": {"urn:ietf:params:jmap:mail": "A", CAP_CONTACTS: "A"},
                           "accounts": {"A": {"accountCapabilities": {"urn:ietf:params:jmap:mail": {}}}}})
    assert s.has_contacts and s.contacts_account_id == "A"


def test_engine_caches_the_address_book_and_completion_prefers_it(engine, server):  # noqa: F811
    pump(lambda: engine.db.contact_count() == 3, timeout=10)
    anna = engine.db.contact_for("Anna@Example.net")
    assert anna and contact_name(anna) == "Anna Berger"
    assert engine.db.contact_for("anna.berger@work.example")["id"] == anna["id"]
    assert engine.db.contact_for("nobody@example.net") is None
    cid, blob, mtype = engine.db.contact_photo_for("anna@example.net")
    assert cid == anna["id"] and blob and mtype == "image/png"
    assert engine.db.contact_photo_for("ben@example.net") is None
    # completion: the address book first, cached mail after, no duplicates
    hits = engine.db.search_addresses("an")
    assert hits[0] == {"email": "anna.berger@work.example", "name": "Anna Berger"} or hits[0]["name"] == "Anna Berger"
    assert len({h["email"] for h in hits}) == len(hits)
    assert engine.db.search_addresses("okafor")[0]["email"] == "ben@example.net"
    # a change on the server arrives with the next sync
    server.data.add_contact("Dana", "Ito", ["dana@example.net"])
    engine.sync_now()
    pump(lambda: engine.db.contact_for("dana@example.net") is not None, timeout=10)
    assert engine.db.contact_count() == 4
    with server.data.lock:
        del server.data.contacts[cid]
        server.data.bump("ContactCard", cid, "destroyed")
    engine.sync_now()
    pump(lambda: engine.db.contact_for("anna@example.net") is None, timeout=10)
    assert engine.db.contact_count() == 3
    # a token without the scope: nothing is fetched and nothing breaks
    engine.client.session.contacts_account_id = None
    engine.db.clear_all()
    engine._sync_contacts(full=True)
    assert engine.db.contact_count() == 0


def test_avatar_keys_prefer_a_contact_with_a_photo(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    from den_mail.avatars import AvatarService
    from den_mail.config import Config

    svc = AvatarService(Config(tmp_path / "c.json"))
    assert svc.key_for("anna@example.net") == "example.net"
    svc.contact_photo = lambda email: ("c1", "B1", "image/png") if email == "anna@example.net" else None
    assert svc.key_for("anna@example.net") == "contact-c1" and svc.key_for("ben@example.net") == "example.net"
    assert svc.key_for(None) is None
    fetched = []
    svc.download_blob = lambda blob, name, mtype, done, err: fetched.append(blob)
    assert svc.get("anna@example.net") is None and fetched == ["B1"]   # the photo is being fetched
    svc.shutdown()
