"""API token storage in the Secret Service (GNOME Keyring / KeePassXC / 1Password).

Secrets go into the ``login`` keyring, which PAM unlocks when the user logs in,
so they can be read at start-up without a prompt. The ``default`` alias is only
used where no login keyring exists (KeePassXC, the KWallet bridge): a default
keyring that is not the login one is locked at every boot, and a lookup then
needs an unlock prompt that races with other apps' prompts or gets dismissed,
after which the app asked for the token again (#160).
"""

from __future__ import annotations

import logging
import os

import gi

gi.require_version("Secret", "1")
from gi.repository import Secret

log = logging.getLogger(__name__)

SCHEMA = Secret.Schema.new(
    "io.github.felsenuboot.DenMail",
    Secret.SchemaFlags.NONE,
    {"app": Secret.SchemaAttributeType.STRING, "account": Secret.SchemaAttributeType.STRING},
)
_ATTRS_BASE = {"app": "den-mail"}
# Schema used before the app was renamed; tokens found there are moved over.
LEGACY_SCHEMA = Secret.Schema.new(
    "io.github.felsenuboot.FastmailGtk",
    Secret.SchemaFlags.NONE,
    {"app": Secret.SchemaAttributeType.STRING, "account": Secret.SchemaAttributeType.STRING},
)
_LEGACY_ATTRS_BASE = {"app": "fastmail-gtk"}
LOGIN_COLLECTION = "login"


def _attrs(account: str) -> dict:
    return {**_ATTRS_BASE, "account": account}


def _store(account: str, secret: str, label: str) -> bool:
    """Store into the login keyring, or the default collection where there is none."""
    for collection in (LOGIN_COLLECTION, Secret.COLLECTION_DEFAULT):
        try:
            Secret.password_store_sync(SCHEMA, _attrs(account), collection, label, secret, None)
            return True
        except Exception as e:  # noqa: BLE001
            log.log(logging.DEBUG if collection == LOGIN_COLLECTION else logging.ERROR,
                    "secret store in %s failed: %s", collection, e)
    return False


def _settle_in_login(account: str, secret: str, label: str) -> None:
    """Move a secret that lives in another collection into the login keyring, once.

    The secret has just been read, so its collection is unlocked and the copies
    elsewhere can be deleted after the login copy is written."""
    service = Secret.Service.get_sync(Secret.ServiceFlags.OPEN_SESSION, None)
    login = Secret.Collection.for_alias_sync(service, LOGIN_COLLECTION, Secret.CollectionFlags.NONE, None)
    if login is None:
        return
    prefix = login.get_object_path() + "/"
    items = service.search_sync(SCHEMA, _attrs(account), Secret.SearchFlags.ALL, None) or []
    elsewhere = [i for i in items if not i.get_object_path().startswith(prefix)]
    if not elsewhere or len(elsewhere) < len(items):
        return
    try:
        Secret.password_store_sync(SCHEMA, _attrs(account), LOGIN_COLLECTION, label, secret, None)
    except Exception as e:  # noqa: BLE001
        log.warning("could not move the secret to the login keyring: %s", e)
        return
    for item in elsewhere:
        item.delete_sync(None)
    log.info("moved %s to the login keyring", label)


def _migrate_legacy(account: str) -> str | None:
    token = Secret.password_lookup_sync(LEGACY_SCHEMA, {**_LEGACY_ATTRS_BASE, "account": account}, None)
    if token and store_token(token, account):
        Secret.password_clear_sync(LEGACY_SCHEMA, {**_LEGACY_ATTRS_BASE, "account": account}, None)
        log.info("moved the API token to the Den Mail keyring entry")
    return token


def _token_label(account: str) -> str:
    return f"Den Mail API token ({account})"


def load_token(account: str = "default") -> str | None:
    env = os.environ.get("DEN_MAIL_TOKEN")
    if env:
        return env
    try:
        token = Secret.password_lookup_sync(SCHEMA, _attrs(account), None) or _migrate_legacy(account)
    except Exception as e:  # noqa: BLE001 - no secret service running, etc.
        log.warning("secret lookup failed: %s", e)
        return None
    if token:
        try:
            _settle_in_login(account, token, _token_label(account))
        except Exception as e:  # noqa: BLE001 - the token was read; where it lives is secondary
            log.debug("could not check the token's keyring: %s", e)
    return token


def store_token(token: str, account: str = "default") -> bool:
    return _store(account, token, _token_label(account))


def load_secret(account: str) -> str | None:
    """Any other secret of the app under the same schema (the assistant's API keys, #69)."""
    try:
        return Secret.password_lookup_sync(SCHEMA, _attrs(account), None)
    except Exception as e:  # noqa: BLE001
        log.warning("secret lookup failed: %s", e)
        return None


def store_secret(account: str, secret: str, label: str) -> bool:
    return _store(account, secret, label)


def clear_secret(account: str) -> None:
    try:
        Secret.password_clear_sync(SCHEMA, _attrs(account), None)
    except Exception as e:  # noqa: BLE001
        log.warning("secret clear failed: %s", e)


def clear_token(account: str = "default") -> None:
    try:
        Secret.password_clear_sync(SCHEMA, _attrs(account), None)
    except Exception as e:  # noqa: BLE001
        log.warning("secret clear failed: %s", e)
