"""Ollama: a local server, `/api/chat`, no key. https://github.com/ollama/ollama/blob/main/docs/api.md"""

from __future__ import annotations

from .errors import LLMError
from .http import request_json
from .spec import Spec


class Ollama:
    name = "ollama"

    def __init__(self, url: str, model: str, key: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.model = model

    def complete(self, system: str, user: str, json_schema: dict | None = None) -> str:
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "think": False,   # thinking models (Qwen3, DeepSeek-R1 …) would answer with reasoning only (#98)
            "options": {"temperature": 0.2, "num_predict": 1024},
        }
        if json_schema is not None:
            body["format"] = json_schema   # structured output, Ollama 0.5+
        answer = request_json(f"{self.url}/api/chat", body)
        text = ((answer.get("message") or {}).get("content") or "").strip() if isinstance(answer, dict) else ""
        if not text:
            raise LLMError(f"{self.model} answered nothing")
        return text

    def check(self) -> str:
        """Names the models the server has; the configured one must be among them."""
        answer = request_json(f"{self.url}/api/tags")
        names = [m.get("name", "") for m in (answer.get("models") or [])] if isinstance(answer, dict) else []
        if not names:
            raise LLMError(f"Ollama at {self.url} has no models yet; run `ollama pull {self.model}`")
        if self.model not in names and f"{self.model}:latest" not in names:
            raise LLMError(f"Ollama at {self.url} has no model {self.model}; it has {', '.join(sorted(names))}")
        return f"Ollama at {self.url} has {self.model}"


SPEC = Spec(key="ollama", title="Ollama (local)", default_url="http://localhost:11434",
            default_model="llama3.2", needs_key=False, factory=Ollama)
