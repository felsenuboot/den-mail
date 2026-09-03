"""Turn a JMAP Email's body part lists into one thing to render.

`htmlBody` and `textBody` are *sequences* of parts (RFC 8621 §4.1.4): a
message from Apple Mail with a photo in the middle arrives as
text/plain, image/jpeg, text/plain and no text/html at all.  Showing only
the first part loses the picture and often shows an empty line."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from .compose import text_to_html

INLINE_CID_PREFIX = "part:"   # synthetic Content-ID for body parts that have none


@dataclass
class BodyContent:
    html: str | None      # what to render in the HTML view, if anything
    text: str | None      # plain text alternative (all text parts joined)
    truncated: bool
    has_html: bool        # a real text/html part exists (dark-mode adaptation applies)


def _cid_for(part: dict) -> str:
    cid = (part.get("cid") or "").strip("<>")
    return cid or f"{INLINE_CID_PREFIX}{part.get('partId')}"


def _image_tag(part: dict) -> str:
    return (f'<img src="cid:{escape(_cid_for(part), quote=True)}" '
            f'alt="{escape(part.get("name") or "", quote=True)}" style="max-width:100%;height:auto">')


def _render_sequence(parts: list[dict], values: dict) -> tuple[str, bool]:
    """HTML for a body part sequence: html as is, text converted, images inline."""
    out: list[str] = []
    truncated = False
    for part in parts:
        ptype = (part.get("type") or "").lower()
        v = values.get(part.get("partId"))
        if ptype.startswith("image/"):
            out.append(_image_tag(part))
        elif v is None or v.get("value") is None:
            continue
        elif ptype == "text/html":
            out.append(v["value"])
            truncated = truncated or bool(v.get("isTruncated"))
        elif ptype.startswith("text/"):
            if v["value"].strip():
                out.append(text_to_html(v["value"]))
            truncated = truncated or bool(v.get("isTruncated"))
    return "\n".join(out), truncated


def assemble_body(full: dict) -> BodyContent:
    values = full.get("bodyValues") or {}
    html_parts = full.get("htmlBody") or []
    text_parts = full.get("textBody") or []

    texts = []
    text_truncated = False
    for part in text_parts:
        v = values.get(part.get("partId"))
        if v and v.get("value") is not None and (part.get("type") or "").startswith("text/"):
            texts.append(v["value"])
            text_truncated = text_truncated or bool(v.get("isTruncated"))
    text = "\n".join(t for t in texts if t.strip()) or None

    has_html = any((p.get("type") or "").lower() == "text/html" for p in html_parts)
    if has_html:
        html, truncated = _render_sequence(html_parts, values)
        if html.strip():
            return BodyContent(html, text, truncated or text_truncated, True)
    # no usable HTML: text parts, with any inline pictures between them
    if any((p.get("type") or "").lower().startswith("image/") for p in text_parts):
        html, truncated = _render_sequence(text_parts, values)
        return BodyContent(html, text, truncated or text_truncated, False)
    return BodyContent(None, text, text_truncated, False)


def find_inline_part(body: dict, cid: str) -> dict | None:
    """The body part for a Content-ID, including synthetic part:<id> ones."""
    seen: set[str] = set()
    for key in ("attachments", "htmlBody", "textBody"):
        for part in body.get(key) or []:
            pid = part.get("partId")
            if pid in seen:
                continue
            seen.add(pid)
            if _cid_for(part) == cid and part.get("blobId"):
                return part
    return None
