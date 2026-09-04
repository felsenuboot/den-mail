"""Undo send: the countdown, the toast and the two ways it ends."""

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import GLib

from den_mail.ui.outbox import PendingSend


def pump(seconds: float) -> None:
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.02)


def _pending(seconds: int):
    events = []
    toasts = []
    p = PendingSend({"subject": "x"}, "I1", "M9", seconds=seconds)
    p.start(toasts.append, lambda s: events.append(("send", s)), lambda s: events.append(("undo", s)))
    return p, events, toasts


def test_countdown_sends_once_and_updates_the_toast():
    p, events, toasts = _pending(2)
    assert toasts and toasts[0].get_title() == "Sending in 2 s" and toasts[0].get_button_label() == "Undo"
    pump(1.3)
    assert toasts[0].get_title() == "Sending…" and events == []
    pump(1.2)
    assert events == [("send", p)]
    p.send_now()  # a flush after the fact does nothing
    assert events == [("send", p)]


def test_undo_stops_the_countdown():
    p, events, toasts = _pending(5)
    toasts[0].emit("button-clicked")
    assert events == [("undo", p)]
    pump(1.5)
    assert events == [("undo", p)]  # the timer is gone


def test_flush_sends_at_once():
    p, events, _toasts = _pending(30)
    p.send_now()
    assert events == [("send", p)]
    p.undo()
    assert events == [("send", p)]
