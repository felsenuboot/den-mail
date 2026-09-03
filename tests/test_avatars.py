"""Sender logo lookups used by notifications."""
from __future__ import annotations

import pytest
from gi.repository import GdkPixbuf, GLib

from den_mail.avatars import AvatarService, sender_key
from den_mail.config import Config


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    svc = AvatarService(Config())
    yield svc
    svc.shutdown()


def run_until(pred, timeout_ms=2000):
    loop = GLib.MainLoop()
    GLib.timeout_add(timeout_ms, loop.quit)
    GLib.timeout_add(10, lambda: (loop.quit() if pred() else True) and not pred())
    loop.run()


def test_cached_path_uses_domain_file(service):
    assert service.cached_path("someone@example.org") is None
    pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 32, 32)
    pix.savev(str(service.dir / "example.org.png"), "png", [], [])
    assert service.cached_path("someone@example.org") == service.dir / "example.org.png"
    assert service.cached_path("bogus") is None


def test_when_ready_immediate_when_cached(service):
    (service.dir / "cached.test.png").write_bytes(b"")
    got = []
    service.when_ready("a@cached.test", got.append)
    assert got == [service.dir / "cached.test.png"]


def test_when_ready_waits_for_fetch_then_gives_none(service, monkeypatch):
    monkeypatch.setattr(service, "_lookup", lambda domain: None)
    got = []
    service.when_ready("a@nowhere.test", got.append, timeout_ms=5000)
    run_until(lambda: bool(got))
    assert got == [None]
    assert (service.dir / "nowhere.test.none").exists()


def test_when_ready_delivers_fetched_logo(service, monkeypatch):
    pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 32, 32)
    monkeypatch.setattr(service, "_lookup", lambda domain: pix)
    got = []
    service.when_ready("a@logo.test", got.append, timeout_ms=5000)
    run_until(lambda: bool(got))
    assert got == [service.dir / "logo.test.png"]


def test_when_ready_disabled(service):
    service.config.set("sender_avatars", False)
    got = []
    service.when_ready("a@x.test", got.append)
    assert got == [None]
    assert sender_key("x") is None
