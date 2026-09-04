"""The push listener's reconnect policy, against a scripted stream."""

from __future__ import annotations

import io
import threading

from den_mail.jmap.push import PushListener


class _Stream(io.BytesIO):
    """A response whose readline() ends the stream once the scripted lines are out."""

    def close(self) -> None:
        pass


class _Client:
    def __init__(self, scripts: list[bytes]):
        self.scripts = scripts
        self.opened = 0
        self.exhausted = threading.Event()

    def open_event_source(self, types):
        if not self.scripts:
            self.exhausted.set()
            raise ConnectionError("no more streams")
        self.opened += 1
        return _Stream(self.scripts.pop(0))


STATE = b'event: state\ndata: {"changed": {"acc": {"Email": "1"}}}\n\n'
CLOSE = b"event: close\ndata: {}\n\n"


def _run(scripts: list[bytes], healthy_seconds: float, waited: list[float]) -> list[dict]:
    client = _Client(scripts)
    changes: list[dict] = []
    listener = PushListener(client, changes.append)
    listener.HEALTHY_SECONDS = healthy_seconds
    original_wait = listener._stopping.wait

    def wait(timeout=None):
        waited.append(timeout)
        if client.exhausted.is_set():
            listener._stopping.set()
        return original_wait(0)

    listener._stopping.wait = wait
    listener.start()
    listener.join(5)
    assert not listener.is_alive()
    return changes


def test_state_events_reach_the_engine_and_a_close_reconnects():
    waited: list[float] = []
    changes = _run([STATE + CLOSE, STATE], healthy_seconds=0, waited=waited)
    assert changes == [{"acc": {"Email": "1"}}, {"acc": {"Email": "1"}}]


def test_backoff_grows_while_streams_die_young():
    waited: list[float] = []
    _run([CLOSE, CLOSE, CLOSE], healthy_seconds=1e9, waited=waited)
    assert waited[:3] == [1.0, 2.0, 4.0]


def test_backoff_resets_after_a_healthy_stream():
    waited: list[float] = []
    _run([CLOSE, CLOSE, STATE + CLOSE, CLOSE], healthy_seconds=0, waited=waited)
    # every stream counts as healthy here, so the delay never grows
    assert waited[:4] == [1.0, 1.0, 1.0, 1.0]
