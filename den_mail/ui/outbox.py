"""Undo send (#7): a message waits a few seconds after Send, with a toast to take it back.

The compose window saves the message as a draft and closes; the main window keeps
a PendingSend that counts down in a toast and then submits the draft. Undo reopens
the draft instead. Quitting or closing the window sends whatever is still waiting.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from gi.repository import Adw, GLib


@dataclass
class PendingSend:
    email: dict
    identity_id: str
    draft_id: str | None
    in_reply_to_id: str | None = None
    forwarded_id: str | None = None
    seconds: int = 10
    remaining: int = field(default=0, init=False)
    toast: Adw.Toast | None = field(default=None, init=False)
    _timer: int = field(default=0, init=False)
    _settled: bool = field(default=False, init=False)

    def start(self, show_toast: Callable[[Adw.Toast], None], on_send: Callable[[PendingSend], None],
              on_undo: Callable[[PendingSend], None]) -> None:
        self.remaining = self.seconds
        self._on_send, self._on_undo = on_send, on_undo
        self.toast = Adw.Toast(title=self._title(), button_label="Undo", timeout=0,
                               priority=Adw.ToastPriority.HIGH)
        self.toast.connect("button-clicked", lambda *_: self.undo())
        show_toast(self.toast)
        self._timer = GLib.timeout_add(1000, self._tick)  # not timeout_add_seconds: its ticks may come late

    def _title(self) -> str:
        return f"Sending in {self.remaining} s" if self.remaining > 1 else "Sending…"

    def _tick(self) -> bool:
        self.remaining -= 1
        if self.remaining <= 0:
            self._timer = 0
            self.send_now()
            return False
        if self.toast is not None:
            self.toast.set_title(self._title())
        return True

    def _settle(self) -> bool:
        if self._settled:
            return False
        self._settled = True
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if self.toast is not None:
            self.toast.dismiss()
        return True

    def send_now(self) -> None:
        """Submit right away (the countdown ended, or the app is closing)."""
        if self._settle():
            self._on_send(self)

    def undo(self) -> None:
        if self._settle():
            self._on_undo(self)
