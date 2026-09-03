"""Sanitise incoming HTML mail for display.

The output is a complete HTML document meant for a WebKit view that has
JavaScript disabled.  Sanitising is still needed to (a) gate remote content
until the user allows it, (b) neutralise forms, scripts, frames and event
handlers, and (c) route `cid:` references to the message's inline parts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from urllib.parse import quote

DROP_WITH_CONTENT = {"script", "style_", "iframe", "object", "embed", "applet", "noscript", "template", "head_", "title"}
DROP_TAG_KEEP_CONTENT = {"form", "html", "body", "head", "meta", "link", "base", "input", "button", "select", "textarea",
                         "svg", "math", "video", "audio", "source", "track", "picture", "canvas", "frame", "frameset"}
VOID = {"br", "hr", "img", "area", "col", "wbr"}
URL_ATTRS = {"src", "background", "poster", "srcset", "longdesc"}
REMOTE_RE = re.compile(r"^\s*(https?:)?//", re.I)
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)\s*(https?:)?//[^)]*\)", re.I)
CSS_IMPORT_RE = re.compile(r"@import[^;]*;", re.I)
BLOCKED_PIXEL = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"

BASE_CSS = """
:root { color-scheme: light; }
html, body { margin: 0; padding: 0; background: #ffffff; color: #1a1a1a; }
body { font-family: -apple-system, "Cantarell", "Inter", system-ui, sans-serif; font-size: 15px; line-height: 1.45;
       padding: 12px 16px; overflow-wrap: anywhere; word-wrap: break-word; }
img { max-width: 100% !important; height: auto; }
table { max-width: 100% !important; }
pre { white-space: pre-wrap; }
blockquote { margin: 0.5em 0 0.5em 0; padding-left: 0.8em; border-left: 3px solid #b0b0b0; color: #444; }
a { color: #1c71d8; }
"""


@dataclass
class SanitizedHtml:
    html: str
    has_remote_content: bool = False
    cids: list[str] = field(default_factory=list)


class _Sanitizer(HTMLParser):
    def __init__(self, allow_remote: bool, cid_scheme: str):
        super().__init__(convert_charrefs=False)
        self.allow_remote = allow_remote
        self.cid_scheme = cid_scheme
        self.out: list[str] = []
        self.skip_depth = 0
        self.skip_tag: str | None = None
        self.in_style = False
        self.has_remote = False
        self.cids: list[str] = []
        self.open_tags: list[str] = []

    # --- helpers
    def _fix_url(self, attr: str, value: str) -> str | None:
        v = value.strip()
        low = v.lower()
        if low.startswith("javascript:") or low.startswith("vbscript:"):
            return None
        if low.startswith("cid:"):
            cid = v[4:].strip("<>")
            self.cids.append(cid)
            return f"{self.cid_scheme}{quote(cid, safe='')}"
        if low.startswith("data:"):
            return v
        if REMOTE_RE.match(v):
            if self.allow_remote:
                return v
            self.has_remote = True
            return BLOCKED_PIXEL if attr == "src" else None
        return v

    def _fix_style(self, value: str) -> str:
        if self.allow_remote:
            return value
        if CSS_URL_RE.search(value) or CSS_IMPORT_RE.search(value):
            self.has_remote = True
        value = CSS_IMPORT_RE.sub("", value)
        return CSS_URL_RE.sub("none", value)

    def _attrs(self, tag: str, attrs) -> str:
        parts = []
        for name, value in attrs:
            n = name.lower()
            if n.startswith("on") or n in ("formaction", "xlink:href", "ping"):
                continue
            if value is None:
                parts.append(f" {escape(n)}")
                continue
            if n == "srcset":
                if not self.allow_remote and REMOTE_RE.search(value):
                    self.has_remote = True
                    continue
            if n in URL_ATTRS:
                fixed = self._fix_url(n, value)
                if fixed is None:
                    continue
                value = fixed
            elif n == "href":
                low = value.strip().lower()
                if low.startswith(("javascript:", "vbscript:", "data:")):
                    continue
            elif n == "style":
                value = self._fix_style(value)
            elif n == "target":
                continue
            parts.append(f' {escape(n)}="{escape(value, quote=True)}"')
        if tag == "a":
            parts.append(' target="_blank" rel="noopener noreferrer"')
        return "".join(parts)

    # --- parser callbacks
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.skip_depth:
            if tag not in VOID:
                self.skip_depth += 1
            return
        if tag in ("script", "iframe", "object", "embed", "applet", "noscript", "template", "title"):
            self.skip_depth = 1
            return
        if tag == "style":
            self.in_style = True
            self.out.append("<style>")
            return
        if tag in DROP_TAG_KEEP_CONTENT:
            return
        self.out.append(f"<{tag}{self._attrs(tag, attrs)}>")
        if tag not in VOID:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self.skip_depth:
            return
        if tag in DROP_TAG_KEEP_CONTENT or tag in ("script", "iframe", "object", "embed", "style", "title"):
            return
        self.out.append(f"<{tag}{self._attrs(tag, attrs)}/>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip_depth:
            if tag not in VOID:
                self.skip_depth -= 1
            return
        if tag == "style":
            self.in_style = False
            self.out.append("</style>")
            return
        if tag in DROP_TAG_KEEP_CONTENT or tag in VOID:
            return
        if tag in self.open_tags:
            # close any unclosed inner tags for well-formedness
            while self.open_tags:
                t = self.open_tags.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self.in_style:
            self.out.append(self._fix_style(data))
        else:
            self.out.append(data)

    def handle_entityref(self, name):
        if not self.skip_depth:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self.skip_depth:
            self.out.append(f"&#{name};")

    def handle_comment(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    def unknown_decl(self, data):
        pass

    def finish(self) -> str:
        while self.open_tags:
            self.out.append(f"</{self.open_tags.pop()}>")
        return "".join(self.out)


def sanitize_html(html: str, allow_remote: bool = False, cid_scheme: str = "cid:") -> SanitizedHtml:
    """Return a full document safe to load in a JS-less WebKit view.

    `cid_scheme` is prefixed to each (URL-quoted) Content-ID, e.g. "fmcid://M123/".
    """
    parser = _Sanitizer(allow_remote, cid_scheme)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - never let a malformed mail break the viewer
        body = f"<pre>{escape(html)}</pre>"
        return SanitizedHtml(_wrap(body), False, [])
    return SanitizedHtml(_wrap(parser.finish()), parser.has_remote, parser.cids)


def _wrap(body: str) -> str:
    return f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{BASE_CSS}</style></head><body>{body}</body></html>'


def plain_text_to_html_document(text: str) -> str:
    """Render text/plain mail as HTML with quotes styled and links clickable."""
    from .compose import text_to_html  # local import: compose depends on nothing here

    return _wrap(f'<div class="plain">{text_to_html(text)}</div>')
