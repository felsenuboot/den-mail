"""The lock screen (#28): passphrases, the policy check, the idle timer."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from den_mail import lock


def test_passphrases_are_salted_hashes_that_verify():
    stored = lock.hash_passphrase("correct horse")
    assert stored.startswith("pbkdf2$") and stored.count("$") == 3
    assert lock.check_passphrase("correct horse", stored)
    assert not lock.check_passphrase("wrong", stored)
    assert lock.hash_passphrase("correct horse") != stored          # a fresh salt each time
    assert lock.hash_passphrase("x", b"\x00" * 16) == lock.hash_passphrase("x", b"\x00" * 16)
    for bad in (None, "", "md5$1$00$00", "pbkdf2$notanumber$00$00", "garbage"):
        assert not lock.check_passphrase("anything", bad)


def test_policy_detection(tmp_path):
    assert not lock.policy_installed(str(tmp_path))
    (tmp_path / "io.github.felsenuboot.DenMail.policy").write_text("<policyconfig/>")
    assert lock.policy_installed(str(tmp_path))


def test_idle_timer_counts_from_the_last_touch():
    fired = []
    timer = lock.IdleTimer(lambda: fired.append(1))
    timer.set_minutes(5)
    assert timer.minutes == 5 and timer.idle_for() < 1
    timer._last -= 6 * 60 * 1_000_000   # six minutes ago
    assert timer.idle_for() > 300
    assert timer._tick() is True and fired == [1] and timer.idle_for() < 1
    timer.set_minutes(0)
    timer._last -= 60 * 60 * 1_000_000
    assert timer._tick() is True and fired == [1]                  # disabled: never fires
    assert lock.IDLE_CHOICES[0] == 0 and sorted(lock.IDLE_CHOICES) == lock.IDLE_CHOICES
