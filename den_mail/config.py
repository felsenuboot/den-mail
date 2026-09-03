"""Paths and user preferences (a small JSON file; no GSettings schema to install)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from typing import Any

from gi.repository import GLib

log = logging.getLogger(__name__)

APP_DIR_NAME = "den-mail"
LEGACY_DIR_NAME = "fastmail-gtk"   # name before the app became Den Mail

DEFAULTS: dict[str, Any] = {
    "load_remote_images": "ask",  # "ask" | "always" | "never"
    "open_links_new_window": False,  # start the browser with its new-window switch (see launch.py)
    "mark_read_on_open": True,
    "group_by_sender": False,  # thread list shows a row per sender above its conversations
    "notify_new_mail": True,
    "poll_interval_seconds": 300,
    "thread_page_size": 50,
    "sidebar_width": 260,
    "window": {"width": 1400, "height": 900, "maximized": False},
    "signature_position": "below",
}


def _app_dir(base: str) -> Path:
    p = Path(base) / APP_DIR_NAME
    legacy = Path(base) / LEGACY_DIR_NAME
    if not p.exists() and legacy.is_dir():
        try:
            legacy.rename(p)
            log.info("migrated %s to %s", legacy, p)
        except OSError as e:
            log.warning("could not migrate %s: %s", legacy, e)
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    return _app_dir(GLib.get_user_config_dir())


def data_dir() -> Path:
    return _app_dir(GLib.get_user_data_dir())


def cache_dir() -> Path:
    return _app_dir(GLib.get_user_cache_dir())


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

    # ------------------------------------------------------- unsubscribed

    def unsubscribed(self) -> dict[str, str]:
        """sender address -> ISO date of the last unsubscribe request."""
        return dict(self._data.get("unsubscribed") or {})

    def mark_unsubscribed(self, email: str) -> None:
        addr = email.strip().lower()
        if addr:
            data = self.unsubscribed()
            data[addr] = datetime.now(timezone.utc).date().isoformat()
            self.set("unsubscribed", data)

    # ------------------------------------------------- favourite identities

    def favorite_identities(self) -> list[str]:
        return list(self._data.get("favorite_identities") or [])

    def set_favorite_identity(self, identity_id: str, favorite: bool) -> None:
        favs = self.favorite_identities()
        if favorite and identity_id not in favs:
            favs.append(identity_id)
        elif not favorite and identity_id in favs:
            favs.remove(identity_id)
        self.set("favorite_identities", favs)

    @property
    def session_url(self) -> str:
        return os.environ.get("DEN_MAIL_SESSION_URL") or self._data.get(
            "session_url", "https://api.fastmail.com/jmap/session"
        )
