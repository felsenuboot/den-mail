"""Paths and user preferences (a small JSON file; no GSettings schema to install)."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
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
    "undo_send_seconds": 10,  # how long a sent message waits with an Undo toast; 0 sends at once
    "group_by_sender": "off",  # "off" | "sender" | "domain": a row per sender/organisation above its conversations
    "notify_new_mail": True,
    "run_in_background": False,  # closing the window hides it and keeps syncing for notifications (#2)
    "poll_interval_seconds": 300,
    "thread_page_size": 50,
    "sidebar_width": 260,
    "beside_min_width": 2200,  # window width (sp) from which "Open beside" pins a second column (#35)
    "avatar_source": "",  # "direct" | "proxy" | "bimi" | "off": where sender logos come from (#63); "" = sender_avatars
    "chip_colours": True,  # label and category chips in colour, or plain (#105)
    "sidebar_views": True,  # the Views section (#19): local lists such as Newsletters and Never read
    "screener": False,  # first-time senders wait in the Screener view until let through (#24)
    "label_suggestions": True,  # chips offering a label the learned models are sure about (#60)
    "cleanup_tip_starts": 0,  # how often the Inbox has shown the "Clean up" tip; it stops after a few starts
    "cleanup_opened": False,  # the tip also stops once Clean up has been opened
    "banners_dismissed": [],  # banners closed with their X, by name ("cleanup", "screener"); they stay away (#84)
    "tip_index": 0,  # which tip the empty conversation pane shows; advances with every start
    "lock_enabled": False,  # the lock screen (#28)
    "lock_idle_minutes": 0,  # lock after this long without activity; 0 = never
    "lock_with_session": True,  # lock when the desktop session locks
    "lock_passphrase": "",  # salted PBKDF2 hash; used where no polkit policy is installed
    "lock_kind": "passphrase",  # before #66: "passphrase" or "pin"; read when lock_method is unset
    "lock_method": "",  # "system" | "passphrase" | "pin" | "keyring": how Unlock asks (see lock.method)
    "assistant_enabled": False,  # the assistant layer (#69): features may ask a language model
    "assistant_provider": "ollama",  # a key of den_mail.llm.PROVIDERS
    "assistant_url": "",  # blank = the provider's default server
    "assistant_model": "",  # blank = the provider's default model
    "assistant_daily_limit": 200,  # requests per day, all features together
    "assistant_usage": {},  # {"date": ISO day, "count": n}: today's requests
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
    def __init__(self, path: Path | None = None) -> None:
        """`path` overrides the config file (tests: GLib caches the user config
        directory per process, so an environment override only works once)."""
        self.path = path or config_dir() / "config.json"
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
            data[addr] = datetime.now(UTC).date().isoformat()
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
