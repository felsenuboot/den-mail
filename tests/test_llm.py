"""The assistant layer (#69): providers against a canned HTTP server, the registry, keys, the budget."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from den_mail import llm
from den_mail.config import Config
from den_mail.llm import anthropic, ollama, openai


class Server:
    """Answers each path with a canned JSON body and remembers what it received."""

    def __init__(self):
        self.answers: dict[str, tuple[int, dict]] = {}
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                outer.requests.append({"method": self.command, "path": self.path,
                                       "headers": {k.lower(): v for k, v in self.headers.items()}, "body": json.loads(raw) if raw else None})
                status, body = outer.answers.get(self.path, (404, {"error": {"message": "no such path"}}))
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _serve

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    s = Server()
    yield s
    s.close()


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.delenv(llm.KEY_ENV, raising=False)
    return Config(tmp_path / "config.json")


# ---------------------------------------------------------------- registry

def test_registry_has_the_three_providers_with_defaults():
    assert list(llm.PROVIDERS) == ["ollama", "openai", "anthropic"]
    for spec in llm.PROVIDERS.values():
        assert spec.default_url.startswith("http") and spec.default_model and spec.title
    assert not llm.PROVIDERS["ollama"].needs_key
    assert llm.PROVIDERS["openai"].needs_key and llm.PROVIDERS["anthropic"].needs_key


def test_settings_fill_blanks_from_the_spec(config):
    spec, url, model = llm.settings(config)
    assert spec.key == "ollama" and url == "http://localhost:11434" and model == "llama3.2"
    config.set("assistant_provider", "openai")
    config.set("assistant_model", "  gpt-4.1  ")
    spec, url, model = llm.settings(config)
    assert (spec.key, url, model) == ("openai", "https://api.openai.com/v1", "gpt-4.1")
    config.set("assistant_provider", "nonsense")
    assert llm.settings(config)[0].key == "ollama"


def test_is_local():
    assert llm.is_local("http://localhost:11434")
    assert llm.is_local("http://127.0.0.1:8080/v1")
    assert llm.is_local("http://[::1]:11434")
    assert llm.is_local("http://ollama.localhost")
    assert not llm.is_local("https://api.openai.com/v1")
    assert not llm.is_local("http://192.168.1.20:11434")


# --------------------------------------------------------------- providers

def test_ollama_chat_and_check(server):
    server.answers["/api/chat"] = (200, {"message": {"role": "assistant", "content": "  Three lines.  "}})
    server.answers["/api/tags"] = (200, {"models": [{"name": "llama3.2:latest"}, {"name": "phi4:latest"}]})
    p = ollama.Ollama(server.url + "/", "llama3.2")
    assert p.complete("be brief", "summarise", {"type": "object"}) == "Three lines."
    sent = server.requests[-1]
    assert sent["path"] == "/api/chat" and sent["body"]["stream"] is False
    assert sent["body"]["format"] == {"type": "object"}
    assert [m["role"] for m in sent["body"]["messages"]] == ["system", "user"]
    assert "authorization" not in sent["headers"]
    assert "llama3.2" in p.check()
    with pytest.raises(llm.LLMError, match="no model gemma"):
        ollama.Ollama(server.url, "gemma").check()
    server.answers["/api/tags"] = (200, {"models": []})
    with pytest.raises(llm.LLMError, match="ollama pull"):
        p.check()


def test_openai_compatible_sends_the_key_and_the_schema(server):
    server.answers["/v1/chat/completions"] = (200, {"choices": [{"message": {"content": "ok"}}]})
    server.answers["/v1/models"] = (200, {"data": [{"id": "gpt-4o-mini"}]})
    p = openai.OpenAICompatible(server.url + "/v1", "gpt-4o-mini", "sk-test")
    assert p.complete("s", "u", {"type": "object"}) == "ok"
    sent = server.requests[-1]
    assert sent["headers"]["authorization"] == "Bearer sk-test"
    assert sent["body"]["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert p.complete("s", "u") == "ok"
    assert "response_format" not in server.requests[-1]["body"]
    assert "gpt-4o-mini" in p.check()
    with pytest.raises(llm.LLMError, match="not gpt-5"):
        openai.OpenAICompatible(server.url + "/v1", "gpt-5", "k").check()
    server.answers["/v1/models"] = (200, {"data": []})
    assert "not verified" in p.check()


def test_anthropic_messages(server):
    server.answers["/v1/messages"] = (200, {"content": [{"type": "text", "text": "One. "}, {"type": "text", "text": "Two."}]})
    server.answers["/v1/models"] = (200, {"data": [{"id": "claude-haiku-4-5-20251001"}]})
    p = anthropic.Anthropic(server.url, "claude-haiku-4-5-20251001", "sk-ant")
    assert p.complete("sys", "user", {"type": "object"}) == "One. Two."
    sent = server.requests[-1]
    assert sent["headers"]["x-api-key"] == "sk-ant" and sent["headers"]["anthropic-version"]
    assert sent["body"]["messages"] == [{"role": "user", "content": "user"}]
    assert "JSON object" in sent["body"]["system"] and sent["body"]["system"].startswith("sys")
    assert "claude-haiku" in p.check()


def test_http_errors_become_llm_errors(server):
    server.answers["/api/chat"] = (401, {"error": {"message": "invalid api key"}})
    with pytest.raises(llm.LLMError, match="401: invalid api key"):
        ollama.Ollama(server.url, "m").complete("s", "u")
    server.answers["/api/chat"] = (200, {"message": {"content": ""}})
    with pytest.raises(llm.LLMError, match="answered nothing"):
        ollama.Ollama(server.url, "m").complete("s", "u")
    with pytest.raises(llm.LLMError, match="Could not reach"):
        ollama.Ollama("http://127.0.0.1:1", "m").complete("s", "u")
    with pytest.raises(llm.LLMError, match="Not a web address"):
        ollama.Ollama("ftp://x", "m").complete("s", "u")


# ---------------------------------------------------------------- build/keys

def test_build_needs_a_key_only_away_from_this_machine(config, monkeypatch, server):
    monkeypatch.setattr(llm, "load_key", lambda _k: None)
    config.set("assistant_provider", "openai")
    with pytest.raises(llm.NotConfigured, match="needs an API key"):
        llm.build(config)
    config.set("assistant_url", server.url + "/v1")     # LM Studio, llama.cpp: no key
    assert isinstance(llm.build(config), openai.OpenAICompatible)
    config.set("assistant_url", "https://api.example.com/v1")
    assert llm.build(config, key="typed").key == "typed"
    monkeypatch.setattr(llm, "load_key", lambda _k: "from-keyring")
    assert llm.build(config).key == "from-keyring"
    config.set("assistant_url", "localhost:1234")
    with pytest.raises(llm.NotConfigured, match="http://"):
        llm.build(config)


def test_key_env_overrides_the_keyring(monkeypatch):
    monkeypatch.setattr(llm.secrets, "load_secret", lambda account: f"stored:{account}")
    assert llm.load_key("openai") == "stored:assistant:openai"
    monkeypatch.setenv(llm.KEY_ENV, "env-key")
    assert llm.load_key("openai") == "env-key"


# --------------------------------------------------------------- assistant

def test_assistant_budget_and_indicator(config, server):
    server.answers["/api/chat"] = (200, {"message": {"content": "answer"}})
    config.set("assistant_url", server.url)
    config.set("assistant_daily_limit", 2)
    a = llm.Assistant(config)
    seen = []
    a.listeners.append(lambda x: seen.append(x.used_today()))
    with pytest.raises(llm.NotConfigured, match="off"):
        a.ask("s", "u")
    config.set("assistant_enabled", True)
    assert a.status() == ""
    assert a.ask("s", "u") == "answer"
    assert a.ask("s", "u") == "answer"
    assert a.used_today() == 2 and a.remaining() == 0 and seen == [1, 2]
    assert a.status() == "Assistant: 2 of 2 today"
    with pytest.raises(llm.BudgetExceeded):
        a.ask("s", "u")
    assert len([r for r in server.requests if r["path"] == "/api/chat"]) == 2
    assert Config(config.path).get("assistant_usage")["count"] == 2      # survives a restart
    config.set("assistant_usage", {"date": "2000-01-01", "count": 99})   # yesterday's count is not today's
    assert a.used_today() == 0 and a.remaining() == 2
    server.answers["/api/chat"] = (500, {"error": "boom"})
    with pytest.raises(llm.LLMError):
        a.ask("s", "u")
    assert a.status().startswith("Assistant: ") and "500" in a.status()
    assert "on this machine" in a.describe() and "0 of 2" in a.describe()


def test_assistant_rebuilds_when_settings_change(config, server):
    config.set("assistant_enabled", True)
    config.set("assistant_url", server.url)
    a = llm.Assistant(config)
    first = a.provider()
    assert a.provider() is first
    config.set("assistant_model", "phi4")
    assert a.provider() is not first and a.provider().model == "phi4"
