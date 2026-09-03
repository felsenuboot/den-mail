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
    """Every method the sender offers. The primary one (`kind`) is tried first: the RFC 8058
    one-click POST, then the mailto: message, then opening the page in the browser (a plain GET
    on a one-click endpoint often just shows an error page)."""

    one_click: str | None = None  # https URL that accepts the RFC 8058 POST
    mailto: str | None = None  # address for the mailto: method
    subject: str = ""
    body: str = ""
    page: str | None = None  # https/http page for the browser

    @property
    def kind(self) -> str:
        return "one-click" if self.one_click else "mailto" if self.mailto else "browser"

    @property
    def url(self) -> str | None:
        return self.one_click if self.kind == "one-click" else self.page

    @property
    def to(self) -> str | None:
        return self.mailto

    @property
    def target(self) -> str:
        """Human-readable destination of the primary method: host of the URL or the mailto address."""
        if self.kind == "mailto":
            return self.mailto or ""
        return urllib.parse.urlsplit(self.url or "").hostname or (self.url or "")

    def fallback(self) -> UnsubscribePlan | None:
        """The plan without its primary method, for retrying after a failure."""
        if self.kind == "one-click":
            rest = UnsubscribePlan(mailto=self.mailto, subject=self.subject, body=self.body, page=self.page)
        elif self.kind == "mailto":
            rest = UnsubscribePlan(page=self.page)
        else:
            return None
        return rest if (rest.mailto or rest.page) else None


def parse_list_unsubscribe(header: str | None, post_header: str | None) -> UnsubscribePlan | None:
    """Collect the unsubscribe methods from the List-Unsubscribe(-Post) headers.

    One-click needs https plus the -Post header; plain http one-click is only accepted for
    loopback hosts (tests)."""
    urls = [u.strip() for u in ANGLE_RE.findall(header or "")]
    if not urls and header and "://" in header:  # tolerate a bare URL without angle brackets
        urls = [header.strip()]
    https = [u for u in urls if u.lower().startswith("https://")]
    http = [u for u in urls if u.lower().startswith("http://")]
    mailtos = [u for u in urls if u.lower().startswith("mailto:")]
    plan = UnsubscribePlan()
    if "list-unsubscribe=one-click" in (post_header or "").lower().replace(" ", ""):
        loopback_http = [u for u in http if urllib.parse.urlsplit(u).hostname in LOOPBACK]
        plan.one_click = next(iter(https + loopback_http), None)
    plan.page = next(iter(https + http), None)
    if mailtos:
        parts = urllib.parse.urlsplit(mailtos[0])
        to = urllib.parse.unquote(parts.path).split(",")[0].strip()
        query = urllib.parse.parse_qs(parts.query)
        if to:
            plan.mailto = to
            plan.subject = (query.get("subject") or ["unsubscribe"])[0]
            plan.body = (query.get("body") or [""])[0]
    return plan if (plan.one_click or plan.mailto or plan.page) else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
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
