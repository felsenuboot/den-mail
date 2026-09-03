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


def test_luminance_tells_dark_from_light_logos(service):
    dark = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 20, 20)
    dark.fill(0x102040FF)
    light = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 20, 20)
    light.fill(0xF0F0F0FF)
    assert service._luminance(dark) < 0.35 < service._luminance(light)
    # transparent pixels do not count
    ghost = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 20, 20)
    ghost.fill(0x00000000)
    assert service._luminance(ghost) == 1.0


def test_plate_makes_a_round_badge_with_a_white_ring(service):
    logo = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 20, 20)
    logo.fill(0x102040FF)  # a solid dark square, like Vercel's
    plate = service._plate(logo, size=128)
    assert (plate.get_width(), plate.get_height()) == (128, 128)
    px, stride = plate.get_pixels(), plate.get_rowstride()

    def at(x, y):
        o = y * stride + x * 4
        return tuple(px[o:o + 3])

    assert at(64, 64) == (0x10, 0x20, 0x40)   # logo fills the inner disc
    assert at(64, 3) == (255, 255, 255)       # white ring at the rim
    assert at(108, 20) == (255, 255, 255)     # ring also on the diagonal: corner clipped away
    assert at(103, 25) == (0x10, 0x20, 0x40)  # just inside the ring the logo is there
    assert plate.get_pixels()[3] == 0         # outside the disc is transparent
