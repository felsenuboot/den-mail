"""Any OpenAI-compatible server: OpenAI itself, Mistral, Groq, OpenRouter, LM Studio,
llama.cpp's server, vLLM and most others speak `/chat/completions`."""

from __future__ import annotations

from .errors import LLMError
from .http import request_json
from .spec import Spec


class OpenAICompatible:
    name = "openai"

    def __init__(self, url: str, model: str, key: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.key = key or ""

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    def complete(self, system: str, user: str, json_schema: dict | None = None) -> str:
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
        }
        if json_schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "answer", "schema": json_schema}}
        answer = request_json(f"{self.url}/chat/completions", body, self._headers())
        try:
            text = (answer["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"{self.model} sent an answer without a message") from e
        if not text:
            raise LLMError(f"{self.model} answered nothing")
        return text

    def check(self) -> str:
        answer = request_json(f"{self.url}/models", headers=self._headers())
        ids = [m.get("id", "") for m in (answer.get("data") or [])] if isinstance(answer, dict) else []
        if ids and self.model not in ids:
            raise LLMError(f"{self.url} lists {len(ids)} models but not {self.model}")
        return (f"{self.url} lists {self.model}" if ids else
                f"{self.url} is reachable (it lists no models; {self.model} is not verified)")


SPEC = Spec(key="openai", title="OpenAI-compatible", default_url="https://api.openai.com/v1",
            default_model="gpt-4o-mini", needs_key=True, factory=OpenAICompatible)
