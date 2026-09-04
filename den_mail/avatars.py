"""Sender logos for the conversation list (BIMI first, favicon fallback).

Lookups happen per sender *domain*, never per message, and are cached on
disk. Where they look is the "Sender logos" choice in Preferences (#63):
directly at the sender's site (BIMI record, then the usual icon paths, then
the icons the home page links to), through DuckDuckGo's icon service so
only one third party sees the domains, BIMI only (a DNS query, no web
contact), or not at all.
"""

from __future__ import annotations

import contextlib
import html
import logging
import math
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import time
from typing import ClassVar

import cairo
import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject

from .config import cache_dir

log = logging.getLogger(__name__)

NEGATIVE_TTL = 7 * 24 * 3600
MAX_BYTES = 1_000_000
MIN_SIZE = 16
TARGET_SIZE = 128
DARK_LOGO_LUMA = 0.35   # mean luminance below which a logo gets a light plate on the dark theme
USER_AGENT = "Mozilla/5.0 (X11; Linux) den-mail avatar fetcher"
BIMI_URL_RE = re.compile(r"\bl=([^;\s]+)")
SOURCES = ["direct", "proxy", "bimi", "off"]   # the "Sender logos" choice (#63)
PROXY_URL = "https://icons.duckduckgo.com/ip3/{domain}.ico"
MAX_HTML = 300_000
LINK_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
ATTR_RE = re.compile(r"""([a-zA-Z-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
# second-level public suffixes where the registrable domain has three labels
SECOND_LEVEL = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.jp", "or.jp", "ne.jp",
                "co.nz", "com.br", "com.mx", "co.za", "com.sg", "com.hk", "co.in"}


def logo_source(config) -> str:
    """The configured source; the switch from before #63 still counts as off."""
    source = config.get("avatar_source") or ""
    if source in SOURCES:
        return source
    return "direct" if config.get("sender_avatars", True) else "off"


def icon_links(page: str, base_url: str) -> list[str]:
    """The icons a page links to (<link rel="icon" …>), largest and touch icons first,
    https only, resolved against the page's URL."""
    found: list[tuple[int, int, str]] = []
    for n, tag in enumerate(LINK_RE.findall(page[:MAX_HTML])):
        attrs = {m.group(1).lower(): html.unescape(m.group(2) or m.group(3) or m.group(4) or "")
                 for m in ATTR_RE.finditer(tag)}
        rel = attrs.get("rel", "").lower().split()
        href = attrs.get("href", "").strip()
        if not href or not ({"icon", "apple-touch-icon", "apple-touch-icon-precomposed"} & set(rel)):
            continue
        url = urllib.parse.urljoin(base_url, href)
        if not url.startswith("https://"):
            continue
        size = 0
        with contextlib.suppress(ValueError):
            size = max(int(s.split("x")[0]) for s in attrs.get("sizes", "").lower().split() if "x" in s)
        touch = 1 if "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel else 0
        found.append((-(size or (180 if touch else 0)), n, url))
    seen: set[str] = set()
    return [u for _s, _n, u in sorted(found) if not (u in seen or seen.add(u))]


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
    __gsignals__: ClassVar[dict] = {"avatar-ready": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

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
        # Contact photos (#14): the window provides a lookup from an address to the
        # contact's photo blob and a way to download it; a photo beats the domain's logo.
        self.contact_photo = lambda email: None   # -> (contact id, blob id, media type) | None
        self.download_blob = None                  # (blob id, name, type, on_done(path), on_error(msg))

    @property
    def enabled(self) -> bool:
        return logo_source(self.config) != "off"

    @property
    def source(self) -> str:
        return logo_source(self.config)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ public

    def key_for(self, email: str | None) -> str | None:
        """What an address's avatar is keyed by: the contact when it has a photo, else the domain."""
        photo = self.contact_photo(email) if email else None
        if photo:
            return f"contact-{photo[0]}"
        return sender_key(email)

    def forget_contacts(self) -> None:
        """The address book changed: photos looked up again on the next request."""
        for key in [k for k in self._mem if k.startswith("contact-")]:
            self._mem.pop(key, None)

    def get(self, email: str | None) -> Gdk.Texture | None:
        """Cached texture for the sender (the contact's photo, else the domain's logo),
        or None (and start a fetch)."""
        if not self.enabled:
            return None
        key = self.key_for(email)
        if key is None:
            return None
        if key.startswith("contact-"):
            return self._get_contact(key, email)
        if key in self._mem:
            return self._pick(self._mem[key])
        with self._lock:
            if key in self._pending:
                return None
            self._pending.add(key)
        self._pool.submit(self._fetch, key)
        return None

    def _get_contact(self, key: str, email: str) -> Gdk.Texture | None:
        if key in self._mem:
            return self._pick(self._mem[key])
        with self._lock:
            if key in self._pending:
                return None
            self._pending.add(key)
        pixbuf = self._load_cached(key)
        if pixbuf is not None:
            self._done(key, (Gdk.Texture.new_for_pixbuf(pixbuf), None))
            return self._pick(self._mem[key])
        photo = self.contact_photo(email)
        if photo is None or self.download_blob is None:
            self._done(key, None)
            return None

        def got(path: Path) -> None:
            try:
                pixbuf = self._decode(path.read_bytes())
            except OSError:
                pixbuf = None
            if pixbuf is not None:
                self._store(key, pixbuf)
                self._done(key, (Gdk.Texture.new_for_pixbuf(pixbuf), None))
            else:
                self._done(key, None)

        self.download_blob(photo[1], "photo", photo[2], got, lambda _m: self._done(key, None))
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
        """Path of the cached photo or logo file for the sender, if any."""
        if not self.enabled:
            return None
        key = self.key_for(email)
        if key is None:
            return None
        path = self.dir / f"{key}.png"
        return path if path.exists() else None

    def when_ready(self, email: str | None, done, timeout_ms: int = 3000) -> None:
        """Call done(path_or_None) once the sender's logo is cached, after
        at most timeout_ms.  Used for notifications, which cannot be
        updated after the fact."""
        path = self.cached_path(email)
        if path is not None or not self.enabled or self.key_for(email) is None:
            done(path)
            return
        key = self.key_for(email)
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

    def _candidates(self, domain: str, source: str | None = None) -> list[str]:
        """Where a domain's logo is looked for, in order, for the configured source."""
        source = source or self.source
        root = registrable_domain(domain)
        candidates: list[str] = []
        if source == "off":
            return candidates
        for d in dict.fromkeys([domain, root]):
            url = self._bimi(d)
            if url:
                candidates.append(url)
        if source == "bimi":
            return candidates
        if source == "proxy":
            candidates += [PROXY_URL.format(domain=d) for d in dict.fromkeys([root, domain])]
            return candidates
        for d in dict.fromkeys([root, domain, f"www.{root}"]):
            candidates.append(f"https://{d}/apple-touch-icon.png")
            candidates.append(f"https://{d}/favicon.ico")
        return candidates

    def _lookup(self, domain: str):
        source = self.source
        for url in self._candidates(domain, source):
            pixbuf = self._decode_download(url)
            if pixbuf is not None:
                return pixbuf
        if source != "direct":
            return None
        # No icon at the usual paths: the home page may link to one (#63); one page, once per domain.
        root = registrable_domain(domain)
        for base in dict.fromkeys([f"https://{root}/", f"https://www.{root}/"]):
            page = self._download(base, html_ok=True)
            if not page:
                continue
            for url in icon_links(page.decode("utf-8", "replace"), base)[:4]:
                pixbuf = self._decode_download(url)
                if pixbuf is not None:
                    return pixbuf
            break
        return None

    def _decode_download(self, url: str):
        data = self._download(url)
        return self._decode(data) if data else None

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
    def _plate(pixbuf, size: int = TARGET_SIZE, ring: float = 0.08):
        """Round badge for dark logos on the dark theme: the logo clipped to a
        disc, with a thin white ring around it so it stands out (a square
        logo such as Vercel's must not end up as a square inside a circle)."""
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        r = size / 2
        cr.arc(r, r, r, 0, 2 * math.pi)
        cr.set_source_rgb(1, 1, 1)
        cr.fill()
        inner = r * (1 - ring)
        cr.arc(r, r, inner, 0, 2 * math.pi)
        cr.clip()
        scale = 2 * inner / max(pixbuf.get_width(), pixbuf.get_height())
        cr.translate(r - pixbuf.get_width() * scale / 2, r - pixbuf.get_height() * scale / 2)
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
        cr.paint()
        surface.flush()
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    @staticmethod
    def _download(url: str, html_ok: bool = False) -> bytes | None:
        """The bytes at an https URL: an image, or with `html_ok` a page (for its icon links)."""
        if not url.startswith("https://"):
            return None
        accept = "text/html;q=0.9,*/*;q=0.5" if html_ok else "image/*,*/*;q=0.5"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
        try:
            # https:// only, checked above (BIMI record, the sender domain, the icon service)
            with urllib.request.urlopen(req, timeout=8) as resp:  # nosec B310
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if ("text/html" in ctype) != html_ok:
                    return None
                limit = MAX_HTML if html_ok else MAX_BYTES
                return resp.read(limit + 1)[:limit]
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError):
            return None

    @staticmethod
    def _decode(data: bytes):
        loader = GdkPixbuf.PixbufLoader()
        try:
            loader.set_size(TARGET_SIZE, TARGET_SIZE) if data[:4] == b"<svg" or b"<svg" in data[:300] else None
            loader.write(data)
            loader.close()
        except GLib.Error:
            with contextlib.suppress(GLib.Error):
                loader.close()
            return None
        pixbuf = loader.get_pixbuf()
        if pixbuf is None or pixbuf.get_width() < MIN_SIZE or pixbuf.get_height() < MIN_SIZE:
            return None
        if pixbuf.get_width() > TARGET_SIZE or pixbuf.get_height() > TARGET_SIZE:
            scale = TARGET_SIZE / max(pixbuf.get_width(), pixbuf.get_height())
            pixbuf = pixbuf.scale_simple(max(1, int(pixbuf.get_width() * scale)),
                                         max(1, int(pixbuf.get_height() * scale)), GdkPixbuf.InterpType.BILINEAR)
        return pixbuf
