"""List-Unsubscribe support: RFC 2369 header parsing and RFC 8058 one-click requests."""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .jmap.client import USER_AGENT

ANGLE_RE = re.compile(r"<([^<>]+)>")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class UnsubscribeError(Exception):
    pass


@dataclass
class UnsubscribePlan:
    kind: str  # "one-click" (POST to the URL) | "browser" (open the URL) | "mailto" (send a message)
    url: str | None = None
    to: str | None = None
    subject: str = ""
    body: str = ""

    @property
    def target(self) -> str:
        """Human-readable destination: the host of the URL or the mailto address."""
        if self.url:
            return urllib.parse.urlsplit(self.url).hostname or self.url
        return self.to or ""


def parse_list_unsubscribe(header: str | None, post_header: str | None) -> UnsubscribePlan | None:
    """Pick the best unsubscribe method from the List-Unsubscribe(-Post) headers.

    Preference: one-click POST (needs https and the -Post header), then an https/http
    page for the browser, then a mailto: message. Plain http one-click is only
    accepted for loopback hosts (tests)."""
    urls = [u.strip() for u in ANGLE_RE.findall(header or "")]
    if not urls and header and "://" in header:  # tolerate a bare URL without angle brackets
        urls = [header.strip()]
    https = [u for u in urls if u.lower().startswith("https://")]
    http = [u for u in urls if u.lower().startswith("http://")]
    mailtos = [u for u in urls if u.lower().startswith("mailto:")]
    one_click = "list-unsubscribe=one-click" in (post_header or "").lower().replace(" ", "")
    if one_click:
        loopback_http = [u for u in http if urllib.parse.urlsplit(u).hostname in LOOPBACK]
        for u in https + loopback_http:
            return UnsubscribePlan("one-click", url=u)
    if https or http:
        return UnsubscribePlan("browser", url=(https or http)[0])
    if mailtos:
        parts = urllib.parse.urlsplit(mailtos[0])
        to = urllib.parse.unquote(parts.path).split(",")[0].strip()
        query = urllib.parse.parse_qs(parts.query)
        if to:
            return UnsubscribePlan("mailto", to=to, subject=(query.get("subject") or ["unsubscribe"])[0],
                                   body=(query.get("body") or [""])[0])
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        return None


def one_click_request(url: str, timeout: float = 15.0) -> None:
    """Perform the RFC 8058 POST. Raises UnsubscribeError on any non-2xx outcome (redirects included)."""
    req = urllib.request.Request(url, data=b"List-Unsubscribe=One-Click", method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        raise UnsubscribeError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise UnsubscribeError(str(getattr(e, "reason", None) or e)) from e
    if not 200 <= status < 300:
        raise UnsubscribeError(f"HTTP {status}")
