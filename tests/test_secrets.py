"""The API token is kept in the login keyring, which is unlocked at login (#160)."""

from __future__ import annotations

from den_mail import secrets


def test_store_prefers_the_login_keyring(monkeypatch):
    calls = []
    monkeypatch.setattr(secrets.Secret, "password_store_sync",
                        lambda schema, attrs, collection, label, secret, cancel: calls.append(collection))
    assert secrets.store_token("tok") is True
    assert calls == [secrets.LOGIN_COLLECTION]


def test_store_falls_back_to_the_default_collection(monkeypatch):
    calls = []

    def store(schema, attrs, collection, label, secret, cancel):
        calls.append(collection)
        if collection == secrets.LOGIN_COLLECTION:
            raise RuntimeError("no such collection")

    monkeypatch.setattr(secrets.Secret, "password_store_sync", store)
    assert secrets.store_secret("llm-key", "k", "LLM key") is True
    assert calls == [secrets.LOGIN_COLLECTION, secrets.Secret.COLLECTION_DEFAULT]


def test_store_reports_failure(monkeypatch):
    def store(*_a):
        raise RuntimeError("no secret service")

    monkeypatch.setattr(secrets.Secret, "password_store_sync", store)
    assert secrets.store_token("tok") is False


def test_load_token_survives_a_failed_move(monkeypatch):
    monkeypatch.delenv("DEN_MAIL_TOKEN", raising=False)
    monkeypatch.setattr(secrets.Secret, "password_lookup_sync", lambda *_a: "tok")

    def settle(*_a):
        raise RuntimeError("keyring gone")

    monkeypatch.setattr(secrets, "_settle_in_login", settle)
    assert secrets.load_token() == "tok"


def test_load_token_env_wins(monkeypatch):
    monkeypatch.setenv("DEN_MAIL_TOKEN", "from-env")
    assert secrets.load_token() == "from-env"
