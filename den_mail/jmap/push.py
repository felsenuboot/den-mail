"""EventSource (Server-Sent Events) listener for JMAP push.

Fastmail's push stream sends a JSON StateChange whenever any type's state
changes.  We do not interpret the states here; we hand the `changed` map to
the sync engine, which compares them with what it has and fetches deltas.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from collections.abc import Callable

from .client import AuthError, JMAPClient, JMAPError

log = logging.getLogger(__name__)


def _abort_response(resp) -> None:
    """Interrupt a blocking readline() in another thread.

    Closing the response would block on the buffered reader's lock, so shut
    the socket down instead; the reader then sees EOF and exits.
    """
    import socket

    try:
        sock = resp.fp.raw._sock  # http.client wraps the socket in a BufferedReader
        sock.shutdown(socket.SHUT_RDWR)
    except Exception:  # noqa: BLE001 - best-effort unblock of a reader thread
        pass


class PushListener(threading.Thread):
    def __init__(
        self,
        client: JMAPClient,
        on_change: Callable[[dict], None],
        on_status: Callable[[bool], None] | None = None,
        types: str = "*",
    ) -> None:
        super().__init__(name="jmap-push", daemon=True)
        self.client = client
        self.on_change = on_change
        self.on_status = on_status
        self.types = types
        self._stop = threading.Event()
        self._resp = None
        self._lock = threading.Lock()
        self.connected = False

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            resp = self._resp
        if resp is not None:
            _abort_response(resp)

    def _set_status(self, connected: bool) -> None:
        if connected != self.connected:
            self.connected = connected
            if self.on_status:
                self.on_status(connected)

    def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                resp = self.client.open_event_source(self.types)
            except AuthError:
                log.warning("push: authentication rejected; giving up on push")
                self._set_status(False)
                return
            except JMAPError as e:
                log.info("push: connect failed (%s); retrying in %.0fs", e, backoff)
                self._set_status(False)
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 120)
                continue
            with self._lock:
                self._resp = resp
            try:
                self._read_stream(resp)
                backoff = 1.0
            except Exception as e:  # noqa: BLE001 - any read failure means reconnect
                if not self._stop.is_set():
                    log.info("push: stream ended (%s); reconnecting in %.0fs", e, backoff)
            finally:
                with self._lock:
                    self._resp = None
                _abort_response(resp)
                with contextlib.suppress(Exception):  # closing a dead stream
                    resp.close()
                self._set_status(False)
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, 120)

    def _read_stream(self, resp) -> None:
        self._set_status(True)
        event_type = None
        data_lines: list[str] = []
        last_activity = time.monotonic()
        while not self._stop.is_set():
            raw = resp.readline()
            if not raw:
                raise ConnectionError("EOF")
            last_activity = time.monotonic()
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line == "":
                if data_lines:
                    self._dispatch(event_type, "\n".join(data_lines))
                event_type = None
                data_lines = []
                continue
            if line.startswith(":"):
                continue  # comment / keep-alive
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                event_type = value
            elif field == "data":
                data_lines.append(value)
            # id / retry fields are ignored
        _ = last_activity

    def _dispatch(self, event_type: str | None, data: str) -> None:
        try:
            payload = json.loads(data)
        except ValueError:
            log.debug("push: non-JSON event %r", data[:100])
            return
        # Fastmail sends {"type": "connect"|"change", "changed": {...}} (and the
        # spec form {"@type": "StateChange", "changed": {...}}); both carry `changed`.
        changed = payload.get("changed")
        if isinstance(changed, dict):
            self.on_change(changed)
