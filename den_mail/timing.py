"""Timing marks for the benchmark in docs/BENCHMARK.md.

With DEN_MAIL_TIMING=1 every mark is logged as
    timing: <event> at=<ms since the process started> took=<ms since its start mark>
so a run's log holds the numbers the bench scripts collect. Marks come in pairs,
"<name>-start" and "<name>-<done>": inbox (start-up until the first list),
switch (a mailbox change until its list), search (typing until the results),
open (selecting a conversation until its body is shown, and painted for HTML).
Without the variable nothing is logged and the marks cost a dictionary lookup.
"""

from __future__ import annotations

import logging
import os
import time

log = logging.getLogger(__name__)

ENABLED = bool(os.environ.get("DEN_MAIL_TIMING"))
_T0 = time.perf_counter()
_starts: dict[str, float] = {}


def mark(event: str) -> None:
    """Record `event`; "x-start" opens the pair that any later "x-…" mark closes."""
    if not ENABLED:
        return
    now = time.perf_counter()
    name, _, phase = event.rpartition("-")
    if phase == "start":
        _starts[name] = now
        log.info("timing: %s at=%d", event, (now - _T0) * 1000)
        return
    started = _starts.get(name)
    took = f" took={(now - started) * 1000:.0f}" if started is not None else ""
    log.info("timing: %s at=%d%s", event, (now - _T0) * 1000, took)


def install_watchdog(threshold_ms: int = 1000) -> None:
    """Log when the main loop stops turning for longer than `threshold_ms` (DEN_MAIL_TIMING=1).

    A thread posts a heartbeat to the main loop every 250 ms and measures how late it
    runs; a late heartbeat means the main thread was busy (or blocked) that long, which
    is what the compositor's "not responding" dialog reacts to."""
    if not ENABLED:
        return
    import threading

    from gi.repository import GLib

    last_mark = {"name": "start"}
    original_mark = globals()["mark"]

    def mark_and_note(event: str) -> None:
        last_mark["name"] = event
        original_mark(event)

    globals()["mark"] = mark_and_note

    def beat(sent: float) -> bool:
        late = (time.perf_counter() - sent) * 1000
        if late > threshold_ms:
            log.warning("timing: main loop stalled %.0f ms (last mark %s)", late, last_mark["name"])
        return False

    def pulse() -> None:
        while True:
            GLib.idle_add(beat, time.perf_counter(), priority=GLib.PRIORITY_HIGH)
            time.sleep(0.25)

    threading.Thread(target=pulse, name="timing-watchdog", daemon=True).start()
