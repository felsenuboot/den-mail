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


def test_method_follows_the_choice_the_policy_and_the_old_kind(tmp_path):
    from den_mail.config import Config

    cfg = Config(tmp_path / "c.json")
    assert lock.method(cfg, str(tmp_path)) == lock.METHOD_PASSPHRASE
    cfg.set("lock_kind", "pin")
    assert lock.method(cfg, str(tmp_path)) == lock.METHOD_PIN          # before #66, the kind decided
    (tmp_path / "io.github.felsenuboot.DenMail.policy").write_text("<policyconfig/>")
    assert lock.method(cfg, str(tmp_path)) == lock.METHOD_SYSTEM       # the policy wins over the old kind
    cfg.set("lock_method", "keyring")
    assert lock.method(cfg, str(tmp_path)) == lock.METHOD_KEYRING      # an explicit choice wins over the policy
    cfg.set("lock_method", "system")
    assert lock.method(cfg, str(tmp_path)) == lock.METHOD_SYSTEM
    (tmp_path / "io.github.felsenuboot.DenMail.policy").unlink()
    assert lock.method(cfg, str(tmp_path)) == lock.METHOD_PIN          # "system" without the policy falls back
    cfg.set("lock_method", "nonsense")
    assert lock.method(cfg, str(tmp_path)) == lock.METHOD_PIN
    assert not lock.method_ready(cfg, lock.METHOD_PIN)
    cfg.set("lock_passphrase", lock.hash_passphrase("1234"))
    assert lock.method_ready(cfg, lock.METHOD_PIN) and lock.method_ready(cfg, lock.METHOD_SYSTEM)


def test_keyring_collection_is_found_by_label():
    class Coll:
        def __init__(self, label):
            self.label = label

        def get_label(self):
            return self.label

    class Service:
        def __init__(self, *labels):
            self.colls = [Coll(x) for x in labels]

        def get_collections(self):
            return self.colls

    assert lock.keyring_collection(Service("login", "Den Mail")).get_label() == "Den Mail"
    assert lock.keyring_collection(Service("login")) is None
    assert lock.keyring_collection(Service()) is None


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
