"""A small, dependency-free JMAP client (RFC 8620) tuned for Fastmail.

Only the transport lives here: session discovery, batched method calls with
back-references, blob upload/download and the raw EventSource connection.
Everything that knows about mailboxes and emails lives in the store package.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .. import APP_NAME, VERSION
from .types import CAP_CORE, CAP_MAIL, CAP_MASKED_EMAIL, CAP_SUBMISSION, DEFAULT_SESSION_URL, Session

log = logging.getLogger(__name__)

USER_AGENT = f"{APP_NAME}/{VERSION} (+https://github.com/felsenuboot/den-mail)"
DEFAULT_TIMEOUT = 60


class JMAPError(Exception):
    """Base class for everything this module raises."""


class TransportError(JMAPError):
    """Network-level failure: DNS, TLS, timeouts, 5xx."""


class AuthError(JMAPError):
    """The token was rejected (HTTP 401/403)."""


class RateLimited(TransportError):
    def __init__(self, retry_after: float):
        super().__init__(f"rate limited, retry after {retry_after:.0f}s")
        self.retry_after = retry_after


class MethodError(JMAPError):
    """A method-level error response (RFC 8620 §3.6.2)."""

    def __init__(self, call_id: str, error_type: str, description: str | None = None, extra: dict | None = None):
        self.call_id = call_id
        self.type = error_type
        self.description = description
        self.extra = extra or {}
        super().__init__(f"{error_type} in call {call_id}: {description or ''}".strip())


class SetError(JMAPError):
    """A per-object failure inside a Foo/set response."""

    def __init__(self, object_id: str, error: dict):
        self.object_id = object_id
        self.type = error.get("type", "unknown")
        self.description = error.get("description")
        self.error = error
        super().__init__(f"{self.type} for {object_id}: {self.description or ''}".strip())


@dataclass
class Invocation:
    name: str
    args: dict
    call_id: str


class Request:
    """Builder for a batch of method calls with automatic call ids."""

    def __init__(self) -> None:
        self.calls: list[Invocation] = []

    def add(self, name: str, args: dict, call_id: str | None = None) -> str:
        cid = call_id or f"c{len(self.calls)}"
        self.calls.append(Invocation(name, args, cid))
        return cid

    @staticmethod
    def ref(call_id: str, name: str, path: str) -> dict:
        """Build a ResultReference (RFC 8620 §3.7)."""
        return {"resultOf": call_id, "name": name, "path": path}

    def to_json(self, using: list[str]) -> dict:
        return {"using": using, "methodCalls": [[c.name, c.args, c.call_id] for c in self.calls]}


class Response:
    def __init__(self, data: dict):
        self.raw = data
        self.session_state: str = data.get("sessionState", "")
        self.responses: list[tuple[str, dict, str]] = [tuple(r) for r in data.get("methodResponses", [])]

    def all(self, call_id: str) -> list[tuple[str, dict]]:
        """All responses for a call id (a call may yield several, e.g. Foo/set + implicit)."""
        return [(name, result) for name, result, cid in self.responses if cid == call_id]

    def get(self, call_id: str, expect: str | None = None) -> dict:
        """First non-error response for a call id; raises MethodError on error."""
        for name, result, cid in self.responses:
            if cid != call_id:
                continue
            if name == "error":
                raise MethodError(cid, result.get("type", "unknown"), result.get("description"), result)
            if expect and name != expect:
                continue
            return result
        raise MethodError(call_id, "missingResponse", f"no response for {call_id}")

    def get_optional(self, call_id: str, expect: str | None = None) -> dict | None:
        try:
            return self.get(call_id, expect)
        except MethodError as e:
            if e.type == "missingResponse":
                return None
            raise


def check_set_response(result: dict, kind: str = "created") -> None:
    """Raise SetError for the first notCreated/notUpdated/notDestroyed entry."""
    errors = result.get(f"not{kind.capitalize()}") or {}
    for obj_id, err in errors.items():
        raise SetError(obj_id, err)


class JMAPClient:
    """Thread-safe (no shared mutable state beyond the session) JMAP transport."""

    def __init__(self, token: str, session_url: str = DEFAULT_SESSION_URL, timeout: int = DEFAULT_TIMEOUT):
        self.token = token
        self.session_url = session_url
        self.timeout = timeout
        self.session: Session | None = None
        self._opener = urllib.request.build_opener()

    # ------------------------------------------------------------------ HTTP

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.token}", "User-Agent": USER_AGENT, "Accept": "application/json"}
        if extra:
            h.update(extra)
        return h

    def _http(self, url: str, data: bytes | None = None, headers: dict | None = None, method: str | None = None,
              timeout: int | None = None):
        req = urllib.request.Request(url, data=data, headers=self._headers(headers), method=method)
        try:
            return self._opener.open(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthError(f"HTTP {e.code}: authentication rejected") from e
            if e.code == 429:
                retry = e.headers.get("Retry-After")
                try:
                    retry_after = float(retry) if retry else 30.0
                except ValueError:
                    retry_after = 30.0
                raise RateLimited(retry_after) from e
            body = ""
            with contextlib.suppress(Exception):  # the body is only decoration for the error
                body = e.read(2000).decode("utf-8", "replace")
            raise TransportError(f"HTTP {e.code} for {url}: {body[:300]}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise TransportError(f"{e.__class__.__name__}: {e}") from e

    # --------------------------------------------------------------- session

    def fetch_session(self) -> Session:
        with self._http(self.session_url) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.session = Session.from_json(data)
        return self.session

    def load_session(self, data: dict) -> Session:
        """Use a cached session object without a network round trip."""
        self.session = Session.from_json(data)
        return self.session

    def _require_session(self) -> Session:
        if self.session is None:
            return self.fetch_session()
        return self.session

    # --------------------------------------------------------------- methods

    def send(self, request: Request, using: list[str] | None = None) -> Response:
        session = self._require_session()
        if using is None:
            using = [CAP_CORE, CAP_MAIL, CAP_SUBMISSION]
            if session.has_masked_email:
                using.append(CAP_MASKED_EMAIL)
        payload = json.dumps(request.to_json(using)).encode("utf-8")
        t0 = time.monotonic()
        with self._http(session.api_url, data=payload, headers={"Content-Type": "application/json"}) as resp:
            body = resp.read()
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise TransportError(f"invalid JSON from server: {e}") from e
        log.debug("JMAP %s -> %d bytes in %.0fms", [c.name for c in request.calls], len(body),
                  (time.monotonic() - t0) * 1000)
        response = Response(data)
        if response.session_state and response.session_state != session.state:
            log.info("session state changed; refreshing session")
            try:
                self.fetch_session()
            except JMAPError as e:  # keep working with the old session
                log.warning("session refresh failed: %s", e)
        return response

    def call(self, name: str, args: dict, using: list[str] | None = None) -> dict:
        """Convenience: send a single method call and return its result."""
        req = Request()
        cid = req.add(name, args)
        return self.send(req, using).get(cid)

    # ----------------------------------------------------------------- blobs

    def upload(self, data: bytes, content_type: str = "application/octet-stream") -> dict:
        session = self._require_session()
        url = session.upload_url.replace("{accountId}", urllib.parse.quote(session.account_id, safe=""))
        with self._http(url, data=data, headers={"Content-Type": content_type}, timeout=max(self.timeout, 300)) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def download_url(self, blob_id: str, name: str = "blob", content_type: str | None = None) -> str:
        session = self._require_session()
        url = session.download_url
        url = url.replace("{accountId}", urllib.parse.quote(session.account_id, safe=""))
        url = url.replace("{blobId}", urllib.parse.quote(blob_id, safe=""))
        url = url.replace("{name}", urllib.parse.quote(name or "blob", safe=""))
        return url.replace("{type}", urllib.parse.quote(content_type or "application/octet-stream", safe=""))

    def download(self, blob_id: str, name: str = "blob", content_type: str | None = None) -> bytes:
        with self._http(self.download_url(blob_id, name, content_type), timeout=max(self.timeout, 300)) as resp:
            return resp.read()

    # ------------------------------------------------------------------ push

    def open_event_source(self, types: str = "*", ping: int = 30):
        """Open the EventSource stream. Returns a file-like response; caller reads lines."""
        session = self._require_session()
        if not session.event_source_url:
            raise TransportError("server advertises no eventSourceUrl")
        url = session.event_source_url
        url = url.replace("{types}", types).replace("{closeafter}", "no").replace("{ping}", str(ping))
        if "{" in url:  # template variables we do not know
            url = url.split("{", 1)[0]
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
        return self._http(url, headers=headers, timeout=ping * 3 + 15)


def account_args(session: Session, **kwargs: Any) -> dict:
    """Shortcut for args that start with the mail accountId."""
    return {"accountId": session.account_id, **kwargs}
