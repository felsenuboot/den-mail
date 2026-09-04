"""HTML → plain text and HTML → Pango markup.

Plain text is used for quoting in replies and for previews; Pango markup is
the fallback renderer when WebKitGTK is not installed.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

BLOCK = {"p", "div", "section", "article", "header", "footer", "aside", "main", "nav", "table", "tr", "ul", "ol",
         "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "hr", "br", "dd", "dt", "dl", "address",
         "figure", "figcaption", "center", "form", "fieldset"}
SKIP = {"script", "style", "head", "title", "noscript", "template", "iframe", "object", "svg"}
WS_RE = re.compile(r"[ \t\r\n\f\v]+")


class _Converter(HTMLParser):
    def __init__(self, markup: bool):
        super().__init__(convert_charrefs=True)
        self.markup = markup
        self.parts: list[str] = []
        self.skip = 0
        self.pre = 0
        self.quote = 0
        self.list_stack: list[tuple[str, int]] = []
        self.inline: list[str] = []  # open pango tags
        self.href: str | None = None
        self.at_line_start = True

    def _emit(self, text: str) -> None:
        if not text:
            return
        if self.at_line_start and self.quote and not text.startswith("\n"):
            prefix = "> " * self.quote
            self.parts.append(prefix)
        self.parts.append(text)
        self.at_line_start = text.endswith("\n")

    def _newline(self, count: int = 1) -> None:
        # collapse multiple blank lines
        joined = "".join(self.parts[-3:])
        trailing = len(joined) - len(joined.rstrip("\n"))
        need = count - trailing
        if need > 0:
            self.parts.append("\n" * need)
            self.at_line_start = True

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        a = dict(attrs)
        if tag in ("p", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "dl", "address", "figure"):
            self._newline(2 if tag in ("p", "h1", "h2", "h3") else 1)
        elif tag == "br":
            self.parts.append("\n")
            self.at_line_start = True
        elif tag == "hr":
            self._newline(1)
            self._emit("—" * 20 if self.markup else "-" * 20)
            self._newline(1)
        elif tag in ("ul", "ol"):
            self._newline(1)
            self.list_stack.append((tag, 0))
        elif tag == "li":
            self._newline(1)
            if self.list_stack:
                kind, n = self.list_stack[-1]
                n += 1
                self.list_stack[-1] = (kind, n)
                self._emit(f"{n}. " if kind == "ol" else "• ")
            else:
                self._emit("• ")
        elif tag == "blockquote":
            self._newline(1)
            self.quote += 1
        elif tag == "pre":
            self._newline(1)
            self.pre += 1
            if self.markup:
                self._open("tt")
        elif tag in ("td", "th") and not self.at_line_start:
            self._emit("\t")
        if self.markup:
            if tag in ("b", "strong"):
                self._open("b")
            elif tag in ("i", "em", "cite", "var"):
                self._open("i")
            elif tag == "u":
                self._open("u")
            elif tag in ("s", "strike", "del"):
                self._open("s")
            elif tag in ("code", "kbd", "samp"):
                self._open("tt")
            elif tag in ("h1", "h2", "h3"):
                self._open("b")
            elif tag == "a":
                href = (a.get("href") or "").strip()
                if href and not href.lower().startswith(("javascript:", "data:")):
                    self.href = href
                    self.parts.append(f'<a href="{escape(href, quote=True)}">')
                    self.inline.append("a")
        elif tag == "a":
            self.href = (a.get("href") or "").strip()
        elif tag == "img":
            alt = (a.get("alt") or "").strip()
            if alt:
                self._emit(f"[{escape(alt) if self.markup else alt}]")

    def _open(self, pango_tag: str) -> None:
        self.parts.append(f"<{pango_tag}>")
        self.inline.append(pango_tag)

    def _close(self, pango_tag: str) -> None:
        if pango_tag in self.inline:
            while self.inline:
                t = self.inline.pop()
                self.parts.append(f"</{t}>")
                if t == pango_tag:
                    break

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in ("p", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "li", "dl", "dd", "dt", "address",
                   "figure", "figcaption"):
            if self.markup and tag in ("h1", "h2", "h3"):
                self._close("b")
            self._newline(2 if tag in ("p", "h1", "h2", "h3") else 1)
        elif tag in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
            self._newline(1)
        elif tag == "blockquote":
            self.quote = max(0, self.quote - 1)
            self._newline(1)
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
            if self.markup:
                self._close("tt")
            self._newline(1)
        elif tag == "a":
            if self.markup:
                self._close("a")
            elif self.href:
                # show the link target when the text differs from it
                text_tail = "".join(self.parts[-2:]).strip()
                if self.href not in text_tail and not self.href.lower().startswith("mailto:"):
                    self._emit(f" <{self.href}>")
            self.href = None
        elif self.markup:
            mapping = {"b": "b", "strong": "b", "i": "i", "em": "i", "cite": "i", "var": "i", "u": "u", "s": "s",
                       "strike": "s", "del": "s", "code": "tt", "kbd": "tt", "samp": "tt"}
            if tag in mapping:
                self._close(mapping[tag])

    def handle_data(self, data):
        if self.skip:
            return
        if self.pre:
            text = data
        else:
            text = WS_RE.sub(" ", data)
            if self.at_line_start:
                text = text.lstrip(" ")
        if not text:
            return
        if self.markup:
            text = escape(text, quote=False)
        self._emit(text)

    def result(self) -> str:
        while self.inline:
            self.parts.append(f"</{self.inline.pop()}>")
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_text(html: str) -> str:
    conv = _Converter(markup=False)
    try:
        conv.feed(html)
        conv.close()
    except Exception:  # noqa: BLE001
        return re.sub(r"<[^>]+>", "", html)
    return conv.result()


def html_to_markup(html: str) -> str:
    conv = _Converter(markup=True)
    try:
        conv.feed(html)
        conv.close()
    except Exception:  # noqa: BLE001
        return escape(re.sub(r"<[^>]+>", "", html), quote=False)
    return conv.result()


URL_RE = re.compile(r"(https?://[^\s<>\"']+[^\s<>\"'.,;:!?)\]])")


_VERBS = r"(wrote|writes|schrieb|a écrit|escribió|scrisse|schreef|skrev|napisał|написал[аи]?)"
QUOTE_ATTRIBUTION_RE = re.compile(
    r"\b" + _VERBS + r"\b[^:]{0,80}:\s*$"
    r"|^-{2,}\s*(Original|Forwarded) message\s*-{2,}\s*$|^-{5,}\s*$", re.IGNORECASE)
# "On Tue, 1 Sep 2026 at 10:00, Anna Berger" / "<anna@example.net> wrote:" wrapped over two lines
# (the joined lines must carry a date or an address, so a sentence starting with "On" is not one)
WRAPPED_ATTRIBUTION_RE = re.compile(
    r"^(On|Am|Le|El|Il|Op|Den|Dnia|W dniu)\b(?=.*(\d|<[^>]*@[^>]*>)).*\b" + _VERBS + r"\b[^:]{0,80}:\s*$",
    re.IGNORECASE | re.DOTALL)
SIGNATURE_RE = re.compile(r"^-- ?$")


def _quote_bounds(lines: list[str]) -> tuple[int, int, int] | None:
    """(start, end, tail): the trailing quoted block of `lines` plus its attribution.

    `end` is where the quoted lines stop and `tail` where the message ends (a signature
    below the quote, Thunderbird style, folds with it). None when nothing folds."""
    tail = len(lines)
    while tail and not lines[tail - 1].strip():
        tail -= 1
    end = tail
    sig = next((i for i in range(tail - 1, -1, -1) if SIGNATURE_RE.match(lines[i])), None)
    if sig is not None and not any(line.startswith(">") for line in lines[sig:tail]):
        end = sig
        while end and not lines[end - 1].strip():
            end -= 1
    start = end
    while start and (lines[start - 1].startswith(">") or not lines[start - 1].strip()):
        start -= 1
    if not any(line.startswith(">") for line in lines[start:end]):
        return None
    prev = start - 1
    while prev >= 0 and not lines[prev].strip():
        prev -= 1
    if prev >= 0 and QUOTE_ATTRIBUTION_RE.search(lines[prev].strip()):
        start = prev
        above = lines[prev - 1].strip() if prev >= 1 else ""
        if above and WRAPPED_ATTRIBUTION_RE.match(above + " " + lines[prev].strip()):
            start = prev - 1
    while start < end and not lines[start].strip():
        start += 1
    if not any(line.strip() for line in lines[:start]):
        return None
    return start, end, tail


def quote_layout(text: str) -> str:
    """"none" (no quoted history), "trailing" (own words, then the quote) or "inline"
    (answers written between quoted lines, which must all stay visible)."""
    lines = text.splitlines()
    bounds = _quote_bounds(lines)
    first = next((i for i, line in enumerate(lines) if line.startswith(">")), None)
    if first is None:
        return "none"
    if bounds is None:
        return "inline"
    return "inline" if first < bounds[0] else "trailing"


def split_quoted_text(text: str) -> tuple[str, str]:
    """Split plain text into (own words, quoted history) at the trailing quote block (#9).

    The quoted part is the run of ">" lines that ends the message, plus the attribution
    ("On …, X wrote:", possibly wrapped over two lines) before it and a signature after
    it. Messages that are quotes only are left whole; an inline reply keeps its exchange
    and folds only the quote it ends with."""
    lines = text.splitlines()
    bounds = _quote_bounds(lines)
    if bounds is None:
        return text, ""
    start, _end, tail = bounds
    return "\n".join(lines[:start]).rstrip(), "\n".join(lines[start:tail])


def text_to_markup(text: str) -> str:
    """Escape plain text for Pango and make URLs clickable; dim quoted lines."""
    lines = []
    for line in text.splitlines():
        esc = escape(line, quote=False)
        esc = URL_RE.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', esc)
        if line.startswith(">"):
            esc = f'<span foreground="#6f6f6f">{esc}</span>'
        lines.append(esc)
    return "\n".join(lines)
