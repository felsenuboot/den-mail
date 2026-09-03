"""Sender logos for the conversation list (BIMI first, favicon fallback).

Lookups happen per sender *domain*, never per message, and are cached on
disk. They still contact the sender's web server for the favicon, so the
feature can be switched off in Preferences.
"""

from __future__ import annotations

import logging
import re
import socket
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import time

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject  # noqa: E402

from .config import cache_dir  # noqa: E402

log = logging.getLogger(__name__)

NEGATIVE_TTL = 7 * 24 * 3600
MAX_BYTES = 1_000_000
MIN_SIZE = 16
TARGET_SIZE = 128
DARK_LOGO_LUMA = 0.35   # mean luminance below which a logo gets a light plate on the dark theme
USER_AGENT = "Mozilla/5.0 (X11; Linux) den-mail avatar fetcher"
BIMI_URL_RE = re.compile(r"\bl=([^;\s]+)")
# second-level public suffixes where the registrable domain has three labels
SECOND_LEVEL = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.jp", "or.jp", "ne.jp",
                "co.nz", "com.br", "com.mx", "co.za", "com.sg", "com.hk", "co.in"}


def sender_key(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower().strip(".") or None


def registrable_domain(domain: str) -> str:
    parts = domain.split(".")
    if len(parts) <= 2:
        return domain
    if ".".join(parts[-2:]) in SECOND_LEVEL and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


class AvatarService(GObject.Object):
    __gsignals__ = {"avatar-ready": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.dir: Path = cache_dir() / "avatars"
        self.dir.mkdir(parents=True, exist_ok=True)
        # key -> (texture, plated texture for dark themes or None)
        self._mem: dict[str, tuple[Gdk.Texture, Gdk.Texture | None] | None] = {}
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="avatar")

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("sender_avatars", True))

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ public

    def get(self, email: str | None) -> Gdk.Texture | None:
        """Cached texture for the sender's domain, or None (and start a fetch)."""
        if not self.enabled:
            return None
        key = sender_key(email)
        if key is None:
            return None
        if key in self._mem:
            return self._pick(self._mem[key])
        with self._lock:
            if key in self._pending:
                return None
            self._pending.add(key)
        self._pool.submit(self._fetch, key)
        return None

    @staticmethod
    def _pick(entry):
        if entry is None:
            return None
        plain, plated = entry
        if plated is not None and Adw.StyleManager.get_default().get_dark():
            return plated
        return plain

    def cached_path(self, email: str | None) -> Path | None:
        """Path of the cached logo file for the sender's domain, if any."""
        if not self.enabled:
            return None
        key = sender_key(email)
        if key is None:
            return None
        path = self.dir / f"{key}.png"
        return path if path.exists() else None

    def when_ready(self, email: str | None, done, timeout_ms: int = 3000) -> None:
        """Call done(path_or_None) once the sender's logo is cached, after
        at most timeout_ms.  Used for notifications, which cannot be
        updated after the fact."""
        path = self.cached_path(email)
        if path is not None or not self.enabled or sender_key(email) is None:
            done(path)
            return
        key = sender_key(email)
        state = {"fired": False}

        def finish(*_):
            if state["fired"]:
                return
            state["fired"] = True
            self.disconnect(handler)
            GLib.source_remove(timer)
            done(self.cached_path(email))

        def on_ready(_svc, ready_key):
            if ready_key == key:
                finish()

        handler = self.connect("avatar-ready", on_ready)
        timer = GLib.timeout_add(timeout_ms, finish)
        if self.get(email) is not None:  # was already in memory
            finish()

    # ------------------------------------------------------------ worker

    def _fetch(self, key: str) -> None:
        entry = None
        try:
            pixbuf = self._load_cached(key)
            if pixbuf is None and not self._negative_cached(key):
                pixbuf = self._lookup(key)
                if pixbuf is not None:
                    self._store(key, pixbuf)
                else:
                    (self.dir / f"{key}.none").touch()
            if pixbuf is not None:
                plated = None
                if self._luminance(pixbuf) < DARK_LOGO_LUMA:
                    plated = Gdk.Texture.new_for_pixbuf(self._plate(pixbuf))
                entry = (Gdk.Texture.new_for_pixbuf(pixbuf), plated)
        except Exception as e:  # noqa: BLE001 - never let a logo break the list
            log.debug("avatar %s failed: %s", key, e)
        GLib.idle_add(self._done, key, entry)

    def _done(self, key: str, entry) -> bool:
        self._mem[key] = entry
        with self._lock:
            self._pending.discard(key)
        self.emit("avatar-ready", key)
        return False

    def _load_cached(self, key: str):
        path = self.dir / f"{key}.png"
        if path.exists():
            try:
                return GdkPixbuf.Pixbuf.new_from_file(str(path))
            except GLib.Error:
                path.unlink(missing_ok=True)
        return None

    def _negative_cached(self, key: str) -> bool:
        marker = self.dir / f"{key}.none"
        if marker.exists():
            if time() - marker.stat().st_mtime < NEGATIVE_TTL:
                return True
            marker.unlink(missing_ok=True)
        return False

    def _store(self, key: str, pixbuf) -> None:
        try:
            pixbuf.savev(str(self.dir / f"{key}.png"), "png", [], [])
        except GLib.Error as e:
            log.debug("avatar cache write failed: %s", e)

    def _lookup(self, domain: str):
        root = registrable_domain(domain)
        candidates: list[str] = []
        for d in dict.fromkeys([domain, root]):
            url = self._bimi(d)
            if url:
                candidates.append(url)
        for d in dict.fromkeys([root, domain, f"www.{root}"]):
            candidates.append(f"https://{d}/apple-touch-icon.png")
            candidates.append(f"https://{d}/favicon.ico")
        for url in candidates:
            data = self._download(url)
            if not data:
                continue
            pixbuf = self._decode(data)
            if pixbuf is not None:
                return pixbuf
        return None

    @staticmethod
    def _bimi(domain: str) -> str | None:
        try:
            records = Gio.Resolver.get_default().lookup_records(f"default._bimi.{domain}",
                                                                Gio.ResolverRecordType.TXT, None)
        except GLib.Error:
            return None
        for rec in records:
            text = "".join(rec.unpack()[0]) if rec.get_type_string() == "(as)" else str(rec)
            if "v=BIMI1" not in text:
                continue
            m = BIMI_URL_RE.search(text)
            if m and m.group(1).startswith("https://"):
                return m.group(1)
        return None

    @staticmethod
    def _luminance(pixbuf) -> float:
        """Mean relative luminance (0..1) of the logo's opaque pixels."""
        small = pixbuf.scale_simple(24, 24, GdkPixbuf.InterpType.BILINEAR)
        data, stride = small.get_pixels(), small.get_rowstride()
        channels, has_alpha = small.get_n_channels(), small.get_has_alpha()
        total = weight = 0.0
        for y in range(24):
            row = y * stride
            for x in range(24):
                o = row + x * channels
                a = data[o + 3] / 255 if has_alpha else 1.0
                if a < 0.5:
                    continue
                total += (0.2126 * data[o] + 0.7152 * data[o + 1] + 0.0722 * data[o + 2]) / 255 * a
                weight += a
        return total / weight if weight else 1.0

    @staticmethod
    def _plate(pixbuf, size: int = TARGET_SIZE, inset: float = 0.14):
        """Centre the logo on a white plate.  Used only for dark logos on the
        dark theme (Lufthansa, banks...), which otherwise disappear."""
        plate = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
        plate.fill(0xFFFFFFFF)
        inner = size * (1 - 2 * inset)
        scale = min(inner / pixbuf.get_width(), inner / pixbuf.get_height())
        w, h = max(1, round(pixbuf.get_width() * scale)), max(1, round(pixbuf.get_height() * scale))
        ox, oy = (size - w) / 2, (size - h) / 2
        pixbuf.composite(plate, round(ox), round(oy), w, h, ox, oy, scale, scale,
                         GdkPixbuf.InterpType.BILINEAR, 255)
        return plate

    @staticmethod
    def _download(url: str) -> bytes | None:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*;q=0.5"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if "text/html" in ctype:
                    return None
                return resp.read(MAX_BYTES + 1)[:MAX_BYTES]
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError, ValueError):
            return None

    @staticmethod
    def _decode(data: bytes):
        loader = GdkPixbuf.PixbufLoader()
        try:
            loader.set_size(TARGET_SIZE, TARGET_SIZE) if data[:4] == b"<svg" or b"<svg" in data[:300] else None
            loader.write(data)
            loader.close()
        except GLib.Error:
            try:
                loader.close()
            except GLib.Error:
                pass
            return None
        pixbuf = loader.get_pixbuf()
        if pixbuf is None or pixbuf.get_width() < MIN_SIZE or pixbuf.get_height() < MIN_SIZE:
            return None
        if pixbuf.get_width() > TARGET_SIZE or pixbuf.get_height() > TARGET_SIZE:
            scale = TARGET_SIZE / max(pixbuf.get_width(), pixbuf.get_height())
            pixbuf = pixbuf.scale_simple(max(1, int(pixbuf.get_width() * scale)),
                                         max(1, int(pixbuf.get_height() * scale)), GdkPixbuf.InterpType.BILINEAR)
        return pixbuf
