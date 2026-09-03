"""Paths and user preferences (a small JSON file; no GSettings schema to install)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from gi.repository import GLib

log = logging.getLogger(__name__)

APP_DIR_NAME = "fastmail-gtk"

DEFAULTS: dict[str, Any] = {
    "load_remote_images": "ask",  # "ask" | "always" | "never"
    "mark_read_on_open": True,
    "notify_new_mail": True,
    "poll_interval_seconds": 300,
    "thread_page_size": 50,
    "sidebar_width": 260,
    "window": {"width": 1400, "height": 900, "maximized": False},
    "signature_position": "below",
}


def config_dir() -> Path:
    p = Path(GLib.get_user_config_dir()) / APP_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    p = Path(GLib.get_user_data_dir()) / APP_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = Path(GLib.get_user_cache_dir()) / APP_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def attachments_dir() -> Path:
    p = cache_dir() / "attachments"
    p.mkdir(parents=True, exist_ok=True)
    return p


def database_path(account_key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_.@" else "_" for c in account_key)
    return data_dir() / f"{safe}.sqlite3"


class Config:
    def __init__(self) -> None:
        self.path = config_dir() / "config.json"
        self._data: dict[str, Any] = json.loads(json.dumps(DEFAULTS))
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            log.warning("config unreadable (%s); using defaults", e)
            return
        if isinstance(loaded, dict):
            self._data.update(loaded)

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("config not saved: %s", e)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    # ---------------------------------------------------- trusted senders

    def trusted_senders(self) -> list[str]:
        return list(self._data.get("trusted_senders") or [])

    def is_trusted(self, email: str | None) -> bool:
        return bool(email) and email.strip().lower() in self.trusted_senders()

    def trust_sender(self, email: str) -> None:
        addr = email.strip().lower()
        senders = self.trusted_senders()
        if addr and addr not in senders:
            senders.append(addr)
            self.set("trusted_senders", senders)

    def untrust_sender(self, email: str) -> None:
        addr = email.strip().lower()
        senders = [s for s in self.trusted_senders() if s != addr]
        self.set("trusted_senders", senders)

    @property
    def session_url(self) -> str:
        return os.environ.get("FASTMAIL_GTK_SESSION_URL") or self._data.get(
            "session_url", "https://api.fastmail.com/jmap/session"
        )
