"""API token storage in the Secret Service (GNOME Keyring / KeePassXC / 1Password)."""

from __future__ import annotations

import logging
import os

import gi

gi.require_version("Secret", "1")
from gi.repository import Secret  # noqa: E402

log = logging.getLogger(__name__)

SCHEMA = Secret.Schema.new(
    "io.github.felsenuboot.FastmailGtk",
    Secret.SchemaFlags.NONE,
    {"app": Secret.SchemaAttributeType.STRING, "account": Secret.SchemaAttributeType.STRING},
)
_ATTRS_BASE = {"app": "fastmail-gtk"}


def _attrs(account: str) -> dict:
    return {**_ATTRS_BASE, "account": account}


def load_token(account: str = "default") -> str | None:
    env = os.environ.get("FASTMAIL_GTK_TOKEN")
    if env:
        return env
    try:
        return Secret.password_lookup_sync(SCHEMA, _attrs(account), None)
    except Exception as e:  # noqa: BLE001 - no secret service running, etc.
        log.warning("secret lookup failed: %s", e)
        return None


def store_token(token: str, account: str = "default") -> bool:
    try:
        Secret.password_store_sync(
            SCHEMA, _attrs(account), Secret.COLLECTION_DEFAULT, f"Fastmail GTK API token ({account})", token, None
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
