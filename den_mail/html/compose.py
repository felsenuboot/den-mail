"""Helpers for outgoing mail: text→HTML, quoting, reply/forward headers."""

from __future__ import annotations

import re
from datetime import datetime
from html import escape

from ..jmap.types import address_display, address_full
from .totext import html_to_text

URL_RE = re.compile(r"(https?://[^\s<>\"']+[^\s<>\"'.,;:!?)\]])")


def text_to_html(text: str) -> str:
    """Convert plain text (with '>' quotes) to simple, readable HTML."""
    out: list[str] = []
    depth = 0

    def set_depth(new: int) -> None:
        nonlocal depth
        while depth < new:
            out.append("<blockquote>")
            depth += 1
        while depth > new:
            out.append("</blockquote>")
            depth -= 1

    for line in text.split("\n"):
        stripped = line
        level = 0
        while stripped.startswith(">"):
            level += 1
            stripped = stripped[1:]
            stripped = stripped.removeprefix(" ")
        set_depth(level)
        esc = escape(stripped, quote=False)
        esc = URL_RE.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', esc)
        out.append(f"<div>{esc or '<br>'}</div>")
    set_depth(0)
    return "".join(out)


def quote_text(text: str) -> str:
    """Prefix every line with "> "; blank runs collapse to one line, trailing blanks go."""
    lines = []
    for raw in text.strip("\n").split("\n"):
        line = raw.rstrip()
        if line or (lines and lines[-1]):
            lines.append(line)
    return "\n".join(f"> {line}" if line else ">" for line in lines)


URL_LINE_RE = re.compile(r"^\s*<?https?://\S+>?\s*$")


def _text_part_is_poor(text: str) -> bool:
    """A text part that is mostly blank lines and bare URLs: the text version many
    mailing tools generate, which reads as nothing when quoted."""
    lines = text.split("\n")
    blank = sum(1 for line in lines if not line.strip())
    urls = sum(1 for line in lines if URL_LINE_RE.match(line))
    prose = len(lines) - blank - urls
    return prose == 0 or (urls and urls >= prose) or blank > 2 * (prose + urls)


def _part_value(email: dict, kind: str) -> str | None:
    values = email.get("bodyValues") or {}
    for part in email.get(kind) or []:
        val = values.get(part.get("partId"))
        if val and val.get("value") is not None:
            return val["value"]
    return None


def email_body_text(email: dict, quoting: bool = False) -> str:
    """Best-effort plain text of a full Email object (textBody preferred).

    When quoting (reply, forward) a text part that is only blank lines and tracking
    links loses to the HTML part, rendered without link targets."""
    text = _part_value(email, "textBody")
    html = _part_value(email, "htmlBody")
    if quoting and html is not None and (text is None or _text_part_is_poor(text)):
        # table cells come out tab-separated; two spaces read better in a quote
        return re.sub(r"[ \t]*\t[ \t]*", "  ", html_to_text(html, link_targets=False))
    if text is not None:
        return text
    if html is not None:
        return html_to_text(html)
    return email.get("preview") or ""


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso).astimezone()
        return dt.strftime("%a, %d %b %Y at %H:%M")
    except ValueError:
        return iso


def reply_body(email: dict, signature: str = "") -> str:
    sender = (email.get("from") or [{}])[0]
    header = f"On {_fmt_date(email.get('sentAt') or email.get('receivedAt'))}, {address_display(sender)} wrote:"
    body = quote_text(email_body_text(email, quoting=True))
    sig = f"\n\n-- \n{signature}" if signature else ""
    return f"\n{sig}\n\n{header}\n{body}\n"


def forward_body(email: dict, signature: str = "") -> str:
    lines = ["", "", "---------- Forwarded message ----------"]
    lines.append(f"From: {', '.join(address_full(a) for a in email.get('from') or [])}")
    lines.append(f"Date: {_fmt_date(email.get('sentAt') or email.get('receivedAt'))}")
    lines.append(f"Subject: {email.get('subject') or ''}")
    if email.get("to"):
        lines.append(f"To: {', '.join(address_full(a) for a in email['to'])}")
    if email.get("cc"):
        lines.append(f"Cc: {', '.join(address_full(a) for a in email['cc'])}")
    lines.append("")
    lines.append(email_body_text(email, quoting=True))
    sig = f"\n\n-- \n{signature}" if signature else ""
    return sig + "\n".join(lines) + "\n"


def reply_subject(subject: str | None) -> str:
    s = (subject or "").strip()
    return s if re.match(r"^(re|aw|sv|antw)\s*:", s, re.IGNORECASE) else f"Re: {s}"


def forward_subject(subject: str | None) -> str:
    s = (subject or "").strip()
    return s if re.match(r"^(fwd?|wg)\s*:", s, re.IGNORECASE) else f"Fwd: {s}"


def parse_address_list(text: str) -> list[dict]:
    """Parse 'Name <a@b>, c@d' into JMAP EmailAddress objects."""
    import email.utils

    result = []
    for name, addr in email.utils.getaddresses([text.replace(";", ",")]):
        addr = addr.strip()
        if not addr:
            continue
        result.append({"name": name.strip() or None, "email": addr})
    return result


def format_address_list(addrs: list[dict] | None) -> str:
    return ", ".join(address_full(a) for a in addrs or [])
