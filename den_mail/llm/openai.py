"""Any OpenAI-compatible server: OpenAI itself, Mistral, Groq, OpenRouter, LM Studio,
llama.cpp's server, vLLM and most others speak `/chat/completions`."""

from __future__ import annotations

from .errors import LLMError
from .http import is_local_url, request_json
from .spec import Spec

MAX_TOKENS = 1024   # a summary is a few lines; this stops a runaway model


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
            "max_tokens": MAX_TOKENS,
        }
        if json_schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "answer", "schema": json_schema}}
        if is_local_url(self.url):
            # llama.cpp, LM Studio and friends: a thinking model (Qwen3 …) would otherwise spend
            # the whole answer on hidden reasoning and hand back empty content (#98). Remote
            # APIs may reject unknown fields, so only local servers get it.
            body["chat_template_kwargs"] = {"enable_thinking": False}
        answer = request_json(f"{self.url}/chat/completions", body, self._headers())
        try:
            message = answer["choices"][0]["message"]
            text = (message.get("content") or "").strip()
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"{self.model} sent an answer without a message") from e
        if not text:
            if (message.get("reasoning_content") or message.get("reasoning") or "").strip():
                raise LLMError(f"{self.model} spent its whole answer on thinking; pick a model without a thinking "
                               "mode, or a server that lets it be turned off")
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
