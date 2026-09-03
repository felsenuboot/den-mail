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
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject  # noqa: E402

from .config import cache_dir  # noqa: E402

log = logging.getLogger(__name__)

NEGATIVE_TTL = 7 * 24 * 3600
MAX_BYTES = 1_000_000
MIN_SIZE = 16
TARGET_SIZE = 128
USER_AGENT = "Mozilla/5.0 (X11; Linux) fastmail-gtk avatar fetcher"
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
        self._mem: dict[str, Gdk.Texture | None] = {}
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
            return self._mem[key]
        with self._lock:
            if key in self._pending:
                return None
            self._pending.add(key)
        self._pool.submit(self._fetch, key)
        return None

    # ------------------------------------------------------------ worker

    def _fetch(self, key: str) -> None:
        texture = None
        try:
            pixbuf = self._load_cached(key)
            if pixbuf is None and not self._negative_cached(key):
                pixbuf = self._lookup(key)
                if pixbuf is not None:
                    self._store(key, pixbuf)
                else:
                    (self.dir / f"{key}.none").touch()
            if pixbuf is not None:
                texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        except Exception as e:  # noqa: BLE001 - never let a logo break the list
            log.debug("avatar %s failed: %s", key, e)
        GLib.idle_add(self._done, key, texture)

    def _done(self, key: str, texture) -> bool:
        self._mem[key] = texture
        with self._lock:
            self._pending.discard(key)
        if texture is not None:
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
