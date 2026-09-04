"""The lock screen (#28): hide the mail until the user proves it is them.

Two ways to unlock.  Where the polkit policy file is installed (the AUR
package does that; `install.sh` says how), the system's own authentication
agent asks, the way it does for privileged actions: password, fingerprint,
whatever PAM is set up for.  Elsewhere, a Flatpak in particular, a local
passphrase kept as a salted PBKDF2 hash in the config file.  This is a
privacy screen, not a security boundary: the cache and the token are not
encrypted by it.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from collections.abc import Callable

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

ACTION_ID = "io.github.felsenuboot.DenMail.unlock"
POLICY_DIR = "/usr/share/polkit-1/actions"
PBKDF2_ROUNDS = 200_000
IDLE_CHOICES = [0, 1, 5, 15, 30, 60]   # minutes; 0 = never


# ------------------------------------------------------------ passphrase


def hash_passphrase(passphrase: str, salt: bytes | None = None) -> str:
    """`pbkdf2$<rounds>$<salt hex>$<hash hex>`, ready for the config file."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def check_passphrase(passphrase: str, stored: str | None) -> bool:
    try:
        algo, rounds, salt_hex, digest_hex = (stored or "").split("$")
        if algo != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds))
        return secrets.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- polkit


def policy_installed(policy_dir: str = POLICY_DIR) -> bool:
    return os.path.exists(os.path.join(policy_dir, "io.github.felsenuboot.DenMail.policy"))


def polkit_check(on_result: Callable[[bool, str | None], None], action_id: str = ACTION_ID) -> None:
    """Ask polkit to authenticate the current user for `action_id` through the session's
    agent; `on_result(authorized, error)` on the main loop.  Interactive, so the call
    waits as long as the prompt is open."""
    def call() -> None:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            subject = GLib.Variant("(sa{sv})", ("unix-process", {"pid": GLib.Variant("u", os.getpid()),
                                                                 "start-time": GLib.Variant("t", 0)}))
            result = bus.call_sync(
                "org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority",
                "org.freedesktop.PolicyKit1.Authority", "CheckAuthorization",
                GLib.Variant("((sa{sv})sa{ss}us)", (subject.unpack(), action_id, {}, 1, "")),
                GLib.VariantType("((bba{ss}))"), Gio.DBusCallFlags.NONE, 5 * 60 * 1000, None)
            authorized, _challenge, _details = result.unpack()[0]
            GLib.idle_add(on_result, bool(authorized), None)
        except GLib.Error as e:
            log.warning("polkit check failed: %s", e.message)
            GLib.idle_add(on_result, False, e.message)

    import threading

    threading.Thread(target=call, name="polkit-unlock", daemon=True).start()


# --------------------------------------------------------------- session


def watch_session_lock(on_lock: Callable[[], None]) -> list[int]:
    """Call `on_lock` when the desktop session locks: login1's Lock signal on the system
    bus, or the screensaver reporting itself active on the session bus.  Returns the
    subscription ids (bus, id) for unsubscribing; either bus may be missing."""
    subs: list[int] = []
    try:
        system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        subs.append(system.signal_subscribe("org.freedesktop.login1", "org.freedesktop.login1.Session", "Lock", None,
                                            None, Gio.DBusSignalFlags.NONE, lambda *_: on_lock()))
    except GLib.Error as e:
        log.debug("no system bus for the lock signal: %s", e.message)
    try:
        session = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        def on_active(_c, _s, _p, _i, _sig, params):
            if params.unpack()[0]:
                on_lock()

        subs.append(session.signal_subscribe(None, "org.freedesktop.ScreenSaver", "ActiveChanged", None, None,
                                             Gio.DBusSignalFlags.NONE, on_active))
    except GLib.Error as e:
        log.debug("no session bus for the screensaver signal: %s", e.message)
    return subs


class IdleTimer:
    """Fires `on_idle` once no activity was reported for `minutes`; 0 disables."""

    def __init__(self, on_idle: Callable[[], None]):
        self.on_idle = on_idle
        self.minutes = 0
        self._last = GLib.get_monotonic_time()
        self._source = 0

    def set_minutes(self, minutes: int) -> None:
        self.minutes = max(0, int(minutes))
        if self._source:
            GLib.source_remove(self._source)
            self._source = 0
        if self.minutes:
            self._source = GLib.timeout_add_seconds(15, self._tick)
        self.touch()

    def touch(self) -> None:
        self._last = GLib.get_monotonic_time()

    def _tick(self) -> bool:
        if self.minutes and (GLib.get_monotonic_time() - self._last) >= self.minutes * 60 * 1_000_000:
            self.on_idle()
            self.touch()
        return True

    def idle_for(self) -> float:
        return (GLib.get_monotonic_time() - self._last) / 1_000_000
