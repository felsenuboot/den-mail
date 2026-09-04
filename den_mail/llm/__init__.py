"""The assistant layer (#69): features ask `Assistant.ask()`, never a provider.

A provider is one file that exposes a `SPEC` (see `spec.py`); listing it in
`PROVIDERS` below is the whole registration. Preferences reads the registry
for its combo and defaults, `build()` turns the config into the chosen
provider, and `Assistant` wraps it with the daily budget and the indicator
that the features share. Keys live in the keyring, never in the config file.
"""

from __future__ import annotations

import logging
import os
import threading
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime

from .. import secrets
from . import anthropic, ollama, openai
from .errors import BudgetExceeded, LLMError, NotConfigured
from .spec import Provider, Spec

log = logging.getLogger(__name__)

PROVIDERS: dict[str, Spec] = {s.key: s for s in (ollama.SPEC, openai.SPEC, anthropic.SPEC)}
DEFAULT_PROVIDER = ollama.SPEC.key
DEFAULT_DAILY_LIMIT = 200
KEY_ENV = "DEN_MAIL_ASSISTANT_KEY"   # overrides the keyring; for scripts and the smoke test

__all__ = [
    "PROVIDERS",
    "Assistant",
    "BudgetExceeded",
    "LLMError",
    "NotConfigured",
    "Provider",
    "Spec",
    "build",
    "clear_key",
    "is_local",
    "load_key",
    "settings",
    "store_key",
]


# ------------------------------------------------------------------ settings

def settings(config) -> tuple[Spec, str, str]:
    """The chosen spec with the URL and model in force (the spec's defaults fill blanks)."""
    spec = PROVIDERS.get(str(config.get("assistant_provider") or DEFAULT_PROVIDER), PROVIDERS[DEFAULT_PROVIDER])
    url = (config.get("assistant_url") or "").strip() or spec.default_url
    model = (config.get("assistant_model") or "").strip() or spec.default_model
    return spec, url, model


def is_local(url: str) -> bool:
    """True when the server is this machine, so mail text never leaves it."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in ("localhost", "::1") or host.startswith("127.") or host.endswith(".localhost")


# ---------------------------------------------------------------------- keys

def _account(provider_key: str) -> str:
    return f"assistant:{provider_key}"


def load_key(provider_key: str) -> str | None:
    return os.environ.get(KEY_ENV) or secrets.load_secret(_account(provider_key))


def store_key(provider_key: str, key: str) -> bool:
    title = PROVIDERS[provider_key].title if provider_key in PROVIDERS else provider_key
    return secrets.store_secret(_account(provider_key), key, f"Den Mail assistant key ({title})")


def clear_key(provider_key: str) -> None:
    secrets.clear_secret(_account(provider_key))


# --------------------------------------------------------------------- build

def build(config, key: str | None = None) -> Provider:
    """The provider the config describes; `key` overrides the keyring (the Test button
    tries a key before it is stored). Raises NotConfigured when something is missing."""
    spec, url, model = settings(config)
    if not url.startswith(("http://", "https://")):
        raise NotConfigured(f"The server URL must start with http:// or https:// (it is {url!r})")
    if key is None and spec.needs_key:
        key = load_key(spec.key)
    if spec.needs_key and not key and not is_local(url):
        raise NotConfigured(f"{spec.title} needs an API key; set it in Preferences → Assistant")
    return spec.factory(url, model, key or None)


# ----------------------------------------------------------------- assistant

class Assistant:
    """What every feature holds: the configured provider behind a daily budget.

    `ask()` is safe to call from a worker thread; `listeners` are called on the
    calling thread after each request, so the window wraps them in idle_add.
    """

    def __init__(self, config) -> None:
        self.config = config
        self.listeners: list[Callable[[Assistant], None]] = []
        self.last_error: str = ""
        self._lock = threading.Lock()
        self._provider: Provider | None = None
        self._built_for: tuple | None = None

    # -- state

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("assistant_enabled"))

    @property
    def limit(self) -> int:
        return max(1, int(self.config.get("assistant_daily_limit") or DEFAULT_DAILY_LIMIT))

    def used_today(self) -> int:
        usage = self.config.get("assistant_usage") or {}
        return int(usage.get("count") or 0) if usage.get("date") == _today() else 0

    def remaining(self) -> int:
        return max(0, self.limit - self.used_today())

    def describe(self) -> str:
        """One line for Preferences and the status bar."""
        if not self.enabled:
            return "Off"
        _spec, url, model = settings(self.config)
        where = "on this machine" if is_local(url) else "leaves this machine"
        return f"{model} at {url} ({where}); {self.used_today()} of {self.limit} requests used today"

    def status(self) -> str:
        """The status-bar fragment: empty until a request was made today."""
        used = self.used_today()
        if self.last_error:
            return f"Assistant: {self.last_error}"
        return f"Assistant: {used} of {self.limit} today" if used else ""

    def reset(self) -> None:
        """Settings changed: the next ask builds a fresh provider."""
        with self._lock:
            self._provider = None
            self._built_for = None
            self.last_error = ""

    # -- the call

    def provider(self) -> Provider:
        spec, url, model = settings(self.config)
        signature = (spec.key, url, model)
        with self._lock:
            if self._provider is None or self._built_for != signature:
                self._provider = build(self.config)
                self._built_for = signature
            return self._provider

    def ask(self, system: str, user: str, json_schema: dict | None = None) -> str:
        if not self.enabled:
            raise NotConfigured("The assistant is off; turn it on in Preferences → Assistant")
        if self.used_today() >= self.limit:
            raise BudgetExceeded(f"Today's {self.limit} assistant requests are used up; raise the limit in "
                                 "Preferences → Assistant or wait for tomorrow")
        try:
            text = self.provider().complete(system, user, json_schema)
        except LLMError as e:
            self.last_error = str(e)
            self._notify()
            raise
        self._record()
        return text

    # -- bookkeeping

    def _record(self) -> None:
        with self._lock:
            self.last_error = ""
            self.config.set("assistant_usage", {"date": _today(), "count": self.used_today() + 1})
        self._notify()

    def _notify(self) -> None:
        for fn in list(self.listeners):
            try:
                fn(self)
            except Exception:
                log.exception("assistant listener failed")


def _today() -> str:
    return datetime.now(UTC).date().isoformat()
