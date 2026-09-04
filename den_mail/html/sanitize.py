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

from .darkmode import COLOR_ATTRS, declares_dark_support, flip_attr, flip_css

DROP_WITH_CONTENT = {"script", "style_", "iframe", "object", "embed", "applet", "noscript", "template", "head_", "title"}
DROP_TAG_KEEP_CONTENT = {"form", "html", "body", "head", "meta", "link", "base", "input", "button", "select", "textarea",
                         "svg", "math", "video", "audio", "source", "track", "picture", "canvas", "frame", "frameset"}
VOID = {"br", "hr", "img", "area", "col", "wbr"}
URL_ATTRS = {"src", "background", "poster", "srcset", "longdesc"}
REMOTE_RE = re.compile(r"^\s*(https?:)?//", re.IGNORECASE)
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)\s*(https?:)?//[^)]*\)", re.IGNORECASE)
CSS_IMPORT_RE = re.compile(r"@import[^;]*;", re.IGNORECASE)
BLOCKED_PIXEL = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"

# Quoted history (#9). Containers hold the history inside them; a plain <blockquote>
# only counts when an attribution line ("On …, X wrote:") precedes it, so a
# newsletter's pull quote stays visible. Outlook's markers are cut points instead:
# the header div holds just "From:/Sent:/To:", the history follows as siblings.
QUOTE_CLASSES = {"gmail_quote", "gmail_quote_container", "x_gmail_quote", "yahoo_quoted", "protonmail_quote",
                 "zmail_extra", "moz-cite-prefix"}
QUOTE_IDS = {"yahoo_quoted"}
CUT_IDS = {"divrplyfwdmsg", "x_divrplyfwdmsg", "appendonsend", "x_appendonsend"}
QUOTE_CLASS = "den-quote"
ATTRIBUTION_RE = re.compile(
    r"\b(wrote|writes|schrieb|a écrit|escribió|scrisse|schreef|skrev|napisał|написал[аи]?)\b[^:]{0,80}:\s*$"
    r"|-{3,}\s*(Original|Forwarded) Message\s*-{3,}\s*$", re.IGNORECASE)

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
.den-quote { display: none !important; }
body.den-show-quotes .den-quote { display: revert !important; }
"""

DARK_CSS = """
:root { color-scheme: dark; }
html, body { background: #1e1e1e; color: #e6e6e6; }
blockquote { border-left-color: #555; color: #bbb; }
a { color: #78aeed; }
"""


@dataclass
class SanitizedHtml:
    html: str
    has_remote_content: bool = False  # outside the quoted history
    cids: list[str] = field(default_factory=list)
    has_quotes: bool = False
    has_remote_in_quotes: bool = False


class _Sanitizer(HTMLParser):
    def __init__(self, allow_remote: bool, cid_scheme: str, dark: bool = False, fold_quotes: bool = True):
        super().__init__(convert_charrefs=False)
        self.allow_remote = allow_remote
        self.cid_scheme = cid_scheme
        self.dark = dark
        self.fold_quotes = fold_quotes
        self.out: list[str] = []
        self.skip_depth = 0
        self.skip_tag: str | None = None
        self.in_style = False
        self.has_remote = False
        self.cids: list[str] = []
        self.open_tags: list[str] = []
        self.has_remote_in_quotes = False
        self.has_own_text = False  # words outside the quoted history
        self._tagged = 0  # elements carrying the quote class
        self._recent_text = ""  # the last words seen, to spot "… wrote:" before a blockquote
        # The open quoted elements, innermost last: open_tags depth, kind ("blockquote",
        # "container" for a client's wrapper div, "sibling" after an Outlook cut), the
        # index of the start tag in self.out, and what was seen inside.
        self._quotes: list[dict] = []
        self._cut_depth: int | None = None  # after an Outlook marker: siblings at this depth or above are history
        self._in_quote = False  # while the current start tag's attributes are processed

    @property
    def has_quotes(self) -> bool:
        return self._tagged > 0

    # --- helpers
    def _fix_url(self, attr: str, value: str) -> str | None:
        v = value.strip()
        low = v.lower()
        if low.startswith(("javascript:", "vbscript:")):
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
            self._note_remote()
            return BLOCKED_PIXEL if attr == "src" else None
        return v

    def _note_remote(self) -> None:
        if self._in_quote or self._quotes:
            self.has_remote_in_quotes = True
            if self._quotes:
                self._quotes[-1]["remote"] = True
        else:
            self.has_remote = True

    def _fix_style(self, value: str) -> str:
        if not self.allow_remote:
            if CSS_URL_RE.search(value) or CSS_IMPORT_RE.search(value):
                self._note_remote()
            value = CSS_IMPORT_RE.sub("", value)
            value = CSS_URL_RE.sub("none", value)
        if self.dark:
            value = flip_css(value)
        return value

    def _quote_kind(self, tag: str, attrs) -> str | None:
        """"container" for an element holding quoted history, "cut" for an Outlook marker."""
        if not self.fold_quotes:
            return None
        classes = ids = ""
        for name, value in attrs:
            n = name.lower()
            if n == "class" and value:
                classes = value.lower()
            elif n == "id" and value:
                ids = value.lower()
            elif n == "type" and tag == "blockquote" and value and value.lower() == "cite":
                return "container"
        if ids in CUT_IDS:
            return "cut"
        if QUOTE_CLASSES & set(classes.split()) or ids in QUOTE_IDS:
            return "container"
        return "container" if tag == "blockquote" and ATTRIBUTION_RE.search(self._recent_text) else None

    def _start_quote(self, tag: str, attrs) -> tuple[list, str | None]:
        """Tag `attrs` with the quote class when the element is quoted history; returns its kind."""
        kind = self._quote_kind(tag, attrs)
        if kind == "cut" and self._cut_depth is None:
            self._cut_depth = len(self.open_tags)
            self._hide_trailing_hr()
        if kind == "cut" or (self._cut_depth is not None and len(self.open_tags) <= self._cut_depth):
            kind = "sibling"
        elif kind == "container" and tag == "blockquote":
            kind = "blockquote"
        if kind is None:
            return list(attrs), None
        self._tagged += 1
        attrs = [(n, f"{v} {QUOTE_CLASS}" if n.lower() == "class" and v else v) for n, v in attrs]
        if not any(n.lower() == "class" for n, _v in attrs):
            attrs = [*attrs, ("class", QUOTE_CLASS)]
        return attrs, kind

    def _untag(self, entry: dict) -> None:
        """A wrapper turned out to hold the sender's own answers: show it (its blockquotes stay folded)."""
        start_tag = self.out[entry["index"]]
        self.out[entry["index"]] = start_tag.replace(f' class="{QUOTE_CLASS}"', "", 1).replace(f' {QUOTE_CLASS}"', '"', 1)
        self._tagged -= 1
        self.has_own_text = True
        if entry["remote"]:
            self.has_remote = True

    def _hide_trailing_hr(self) -> None:
        """Outlook draws a rule above its "From:" header; it belongs to the history."""
        for i in range(len(self.out) - 1, -1, -1):
            chunk = self.out[i]
            if not chunk.strip():
                continue
            if chunk.lower().startswith("<hr") and QUOTE_CLASS not in chunk:
                self.out[i] = chunk[:3] + f' class="{QUOTE_CLASS}"' + chunk[3:]
            return

    def _attrs(self, tag: str, attrs) -> str:
        parts = []
        for name, value in attrs:
            n = name.lower()
            if n.startswith("on") or n in ("formaction", "xlink:href", "ping"):
                continue
            if value is None:
                parts.append(f" {escape(n)}")
                continue
            if n == "srcset" and not self.allow_remote and REMOTE_RE.search(value):
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
            elif n in COLOR_ATTRS and self.dark:
                value = flip_attr(value, n)
            elif n == "target":
                continue
            parts.append(f' {escape(n)}="{escape(value, quote=True)}"')
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
        attrs, kind = self._start_quote(tag, attrs)
        self._in_quote = kind is not None
        self.out.append(f"<{tag}{self._attrs(tag, attrs)}>")
        self._in_quote = False
        if tag not in VOID:
            self.open_tags.append(tag)
            if kind:
                self._quotes.append({"depth": len(self.open_tags), "kind": kind, "index": len(self.out) - 1,
                                     "after_blockquote": False, "inline": False, "remote": False})

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self.skip_depth:
            return
        if tag in DROP_TAG_KEEP_CONTENT or tag in ("script", "iframe", "object", "embed", "style", "title"):
            return
        attrs, kind = self._start_quote(tag, attrs)
        self._in_quote = kind is not None
        self.out.append(f"<{tag}{self._attrs(tag, attrs)}/>")
        self._in_quote = False

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
            while self._quotes and self._quotes[-1]["depth"] > len(self.open_tags):
                entry = self._quotes.pop()
                # Gmail writes inline answers between the blockquotes of its wrapper div:
                # text directly in the wrapper after a blockquote closed is the sender's.
                if entry["kind"] == "blockquote" and self._quotes and self._quotes[-1]["kind"] == "container":
                    self._quotes[-1]["after_blockquote"] = True
                if entry["kind"] == "container" and entry["inline"]:
                    self._untag(entry)

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self.in_style:
            self.out.append(self._fix_style(data))
        else:
            self.out.append(data)
            if data.strip():
                self._recent_text = (self._recent_text + " " + data.strip())[-200:]
                if not self._quotes:
                    self.has_own_text = True
                elif self._quotes[-1]["kind"] == "container" and self._quotes[-1]["after_blockquote"]:
                    self._quotes[-1]["inline"] = True

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


def sanitize_html(html: str, allow_remote: bool = False, cid_scheme: str = "cid:",
                  dark: bool = False, show_quotes: bool = False, fold_quotes: bool = True) -> SanitizedHtml:
    """Return a full document safe to load in a JS-less WebKit view.

    `cid_scheme` is prefixed to each (URL-quoted) Content-ID, e.g. "fmcid://M123/".
    With `dark`, colours the message specifies are lightness-flipped (images untouched)
    unless the message declares its own dark-mode support, in which case WebKit's
    native `color-scheme: dark` handling is used. With `fold_quotes`, quoted history
    is tagged with the den-quote class and hidden unless `show_quotes`; the viewer
    toggles it by adding den-show-quotes to the body.
    """
    native_dark = dark and declares_dark_support(html)
    try:
        parser = _Sanitizer(allow_remote, cid_scheme, dark=dark and not native_dark, fold_quotes=fold_quotes)
        parser.feed(html)
        parser.close()
        if parser.has_quotes and not parser.has_own_text:
            # Nothing outside the quote (an inline reply typed into it, a bare forward):
            # folding would leave a blank message, so show it whole.
            parser = _Sanitizer(allow_remote, cid_scheme, dark=dark and not native_dark, fold_quotes=False)
            parser.feed(html)
            parser.close()
    except Exception:  # noqa: BLE001 - never let a malformed mail break the viewer
        body = f"<pre>{escape(html)}</pre>"
        return SanitizedHtml(_wrap(body, dark), False, [])
    return SanitizedHtml(_wrap(parser.finish(), dark, show_quotes), parser.has_remote, parser.cids,
                         parser.has_quotes, parser.has_remote_in_quotes)


def _wrap(body: str, dark: bool = False, show_quotes: bool = False) -> str:
    css = BASE_CSS + (DARK_CSS if dark else "")
    body_tag = '<body class="den-show-quotes">' if show_quotes else "<body>"
    return f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{css}</style></head>{body_tag}{body}</body></html>'


def plain_text_to_html_document(text: str, dark: bool = False) -> str:
    """Render text/plain mail as HTML with quotes styled and links clickable."""
    from .compose import text_to_html  # local import: compose depends on nothing here

    return _wrap(f'<div class="plain">{text_to_html(text)}</div>', dark)
