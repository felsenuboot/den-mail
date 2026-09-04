"""What a provider looks like from the outside: the protocol and its registry entry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class Provider(Protocol):
    """One call is all a feature gets. `json_schema` asks for a JSON object of
    that shape when the server can promise it; the caller still parses."""

    name: str

    def complete(self, system: str, user: str, json_schema: dict | None = None) -> str: ...

    def check(self) -> str:
        """Reach the server without spending a request; a one-line verdict or LLMError."""
        ...


@dataclass(frozen=True)
class Spec:
    """A registry entry: what Preferences needs to know to offer the provider."""

    key: str            # in the config file
    title: str          # in the Preferences combo
    default_url: str
    default_model: str
    needs_key: bool     # a key is required unless the server is on this machine
    factory: Callable[[str, str, str | None], Provider]   # (url, model, key)
