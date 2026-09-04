"""The one HTTP door every provider uses: JSON in, JSON out, errors as LLMError.

Kept apart from the JMAP client on purpose: that one carries the account
token in every request and retries on the server's terms; these requests go
to whatever server the user configured and never carry the mail token.
"""

from __future__ import annotations

import contextlib
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .. import APP_NAME, VERSION
from .errors import LLMError

DEFAULT_TIMEOUT = 300   # a local model on a laptop can take a while over a long thread


def host_of(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc or url


def is_local_url(url: str) -> bool:
    """True when the server is this machine (den_mail.llm.is_local says the same; this one
    lives here so the providers can ask without an import cycle)."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in ("localhost", "::1") or host.startswith("127.") or host.endswith(".localhost")


def request_json(url: str, body: dict | None = None, headers: dict | None = None,
                 timeout: float = DEFAULT_TIMEOUT) -> Any:
    """POST `body` as JSON (GET when None) and return the decoded answer."""
    scheme = urllib.parse.urlsplit(url).scheme
    if scheme not in ("http", "https"):
        raise LLMError(f"Not a web address: {url}")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET", headers={
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": f"{APP_NAME}/{VERSION}",
        **(headers or {}),
    })
    host = host_of(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - scheme checked above
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = ""
        with contextlib.suppress(Exception):
            detail = e.read(2000).decode("utf-8", "replace")
        raise LLMError(f"{host} answered {e.code}: {_message(detail) or e.reason}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"Could not reach {host}: {e.reason}") from e
    except TimeoutError as e:
        raise LLMError(f"{host} did not answer within {timeout:.0f} s") from e
    except OSError as e:
        raise LLMError(f"Could not reach {host}: {e}") from e
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise LLMError(f"{host} sent something that is not JSON") from e


def _message(detail: str) -> str:
    """The human part of an error body, when the server bothered to send one."""
    try:
        data = json.loads(detail)
    except ValueError:
        return detail.strip()[:200]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or "")[:200]
    if isinstance(err, str):
        return err[:200]
    return detail.strip()[:200]
