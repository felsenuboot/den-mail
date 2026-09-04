"""Anthropic's Messages API. https://docs.anthropic.com/en/api/messages"""

from __future__ import annotations

import json

from .errors import LLMError
from .http import request_json
from .spec import Spec

API_VERSION = "2023-06-01"


class Anthropic:
    name = "anthropic"

    def __init__(self, url: str, model: str, key: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.key = key or ""

    def _headers(self) -> dict:
        return {"x-api-key": self.key, "anthropic-version": API_VERSION}

    def complete(self, system: str, user: str, json_schema: dict | None = None) -> str:
        if json_schema is not None:
            system += ("\n\nAnswer with a single JSON object matching this schema and nothing else:\n"
                       + json.dumps(json_schema))
        body = {
            "model": self.model,
            "max_tokens": 1024,
            "temperature": 0.2,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        answer = request_json(f"{self.url}/v1/messages", body, self._headers())
        blocks = answer.get("content") if isinstance(answer, dict) else None
        text = "".join(b.get("text", "") for b in (blocks or []) if b.get("type") == "text").strip()
        if not text:
            raise LLMError(f"{self.model} answered nothing")
        return text

    def check(self) -> str:
        answer = request_json(f"{self.url}/v1/models", headers=self._headers())
        ids = [m.get("id", "") for m in (answer.get("data") or [])] if isinstance(answer, dict) else []
        if ids and self.model not in ids:
            raise LLMError(f"Anthropic lists {len(ids)} models but not {self.model}")
        return f"Anthropic lists {self.model}" if ids else "Anthropic is reachable"


SPEC = Spec(key="anthropic", title="Anthropic", default_url="https://api.anthropic.com",
            default_model="claude-haiku-4-5-20251001", needs_key=True, factory=Anthropic)
