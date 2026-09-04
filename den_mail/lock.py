"""The lock screen (#28): hide the mail until the user proves it is them.

Three ways to unlock.  Where the polkit policy file is installed (the AUR
package does that; `install.sh` says how), the system's own authentication
agent asks, the way it does for privileged actions: password, fingerprint,
whatever PAM is set up for.  A keyring collection of the app's own ("Den
Mail"), which the keyring daemon locks with the app and unlocks with its own
prompt; nothing else in the keyring is touched, so no other app sees a
prompt, and it works inside a Flatpak (#66).  Or a local passphrase or PIN
kept as a salted PBKDF2 hash in the config file.  This is a privacy screen,
not a security boundary: the cache and the token are not encrypted by it.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
from collections.abc import Callable

import gi

gi.require_version("Secret", "1")
from gi.repository import Gio, GLib, Secret

log = logging.getLogger(__name__)

ACTION_ID = "io.github.felsenuboot.DenMail.unlock"
POLICY_DIR = "/usr/share/polkit-1/actions"
PBKDF2_ROUNDS = 200_000
IDLE_CHOICES = [0, 1, 5, 15, 30, 60]   # minutes; 0 = never
KEYRING_LABEL = "Den Mail"

METHOD_SYSTEM, METHOD_PASSPHRASE, METHOD_PIN, METHOD_KEYRING = "system", "passphrase", "pin", "keyring"
METHOD_TITLES = {
    METHOD_SYSTEM: "The system prompt",
    METHOD_PASSPHRASE: "A passphrase",
    METHOD_PIN: "A PIN",
    METHOD_KEYRING: "The keyring (a Den Mail collection)",
}


def method(config, policy_dir: str = POLICY_DIR) -> str:
    """How this configuration unlocks. `lock_method` when set and possible here; else the
    system prompt where the policy is installed, else what `lock_kind` said before #66."""
    chosen = config.get("lock_method") or ""
    if chosen in METHOD_TITLES and (chosen != METHOD_SYSTEM or policy_installed(policy_dir)):
        return chosen
    if policy_installed(policy_dir):
        return METHOD_SYSTEM
    return METHOD_PIN if config.get("lock_kind") == "pin" else METHOD_PASSPHRASE


def method_ready(config, m: str) -> bool:
    """Whether enabling the lock with this method has what it needs."""
    if m in (METHOD_PASSPHRASE, METHOD_PIN):
        return bool(config.get("lock_passphrase"))
    if m == METHOD_KEYRING:
        return keyring_exists()
    return True


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

    threading.Thread(target=call, name="polkit-unlock", daemon=True).start()


# --------------------------------------------------------------- keyring


def _service() -> Secret.Service:
    return Secret.Service.get_sync(Secret.ServiceFlags.LOAD_COLLECTIONS, None)


def keyring_collection(service=None):
    """The app's own collection, found by its label; None while it does not exist."""
    service = service or _service()
    for c in service.get_collections() or []:
        if c.get_label() == KEYRING_LABEL:
            return c
    return None


def keyring_available() -> bool:
    """A Secret Service is running (GNOME Keyring, KeePassXC, KWallet's bridge ...)."""
    try:
        _service()
        return True
    except GLib.Error as e:
        log.debug("no secret service: %s", e.message)
        return False


def keyring_exists() -> bool:
    try:
        return keyring_collection() is not None
    except GLib.Error:
        return False


def keyring_create() -> None:
    """Create the collection; the daemon asks for its new password. Raises GLib.Error."""
    service = _service()
    if keyring_collection(service) is None:
        Secret.Collection.create_sync(service, KEYRING_LABEL, None, Secret.CollectionCreateFlags.NONE, None)


def keyring_lock() -> None:
    """Lock the collection; the daemon needs no prompt for that."""
    try:
        service = _service()
        c = keyring_collection(service)
        if c is not None:
            service.lock_sync([c], None)
    except GLib.Error as e:
        log.warning("keyring lock failed: %s", e.message)


def keyring_unlock(on_result: Callable[[bool, str | None], None]) -> None:
    """Ask the daemon to unlock the collection; it shows its own prompt. `on_result(ok, error)`
    on the main loop."""
    def call() -> None:
        try:
            service = _service()
            c = keyring_collection(service)
            if c is None:
                GLib.idle_add(on_result, False, "The Den Mail keyring does not exist any more")
                return
            _count, unlocked = service.unlock_sync([c], None)
            ok = any(u.get_object_path() == c.get_object_path() for u in unlocked or []) or not c.get_locked()
            GLib.idle_add(on_result, bool(ok), None if ok else "The keyring stayed locked")
        except GLib.Error as e:
            log.warning("keyring unlock failed: %s", e.message)
            GLib.idle_add(on_result, False, e.message)

    threading.Thread(target=call, name="keyring-unlock", daemon=True).start()


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
