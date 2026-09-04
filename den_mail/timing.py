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
