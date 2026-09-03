"""API token storage in the Secret Service (GNOME Keyring / KeePassXC / 1Password)."""

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


def _attrs(account: str) -> dict:
    return {**_ATTRS_BASE, "account": account}


def _migrate_legacy(account: str) -> str | None:
    token = Secret.password_lookup_sync(LEGACY_SCHEMA, {**_LEGACY_ATTRS_BASE, "account": account}, None)
    if token and store_token(token, account):
        Secret.password_clear_sync(LEGACY_SCHEMA, {**_LEGACY_ATTRS_BASE, "account": account}, None)
        log.info("moved the API token to the Den Mail keyring entry")
    return token


def load_token(account: str = "default") -> str | None:
    env = os.environ.get("DEN_MAIL_TOKEN")
    if env:
        return env
    try:
        return Secret.password_lookup_sync(SCHEMA, _attrs(account), None) or _migrate_legacy(account)
    except Exception as e:  # noqa: BLE001 - no secret service running, etc.
        log.warning("secret lookup failed: %s", e)
        return None


def store_token(token: str, account: str = "default") -> bool:
    try:
        Secret.password_store_sync(
            SCHEMA, _attrs(account), Secret.COLLECTION_DEFAULT, f"Den Mail API token ({account})", token, None
        )
        return True
    except Exception as e:  # noqa: BLE001
        log.error("secret store failed: %s", e)
        return False


def clear_token(account: str = "default") -> None:
    try:
        Secret.password_clear_sync(SCHEMA, _attrs(account), None)
    except Exception as e:  # noqa: BLE001
        log.warning("secret clear failed: %s", e)
