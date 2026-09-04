"""Message body rendering: WebKitGTK when available, Pango markup otherwise.

Security model for HTML mail:
  * the document is sanitised (scripts, frames, forms, event handlers removed)
  * WebKit runs with JavaScript markup disabled and an ephemeral network session
  * remote resources are replaced by a placeholder until the user allows them
  * inline images are served through a private `fmcid://<email>/<cid>` scheme
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from urllib.parse import unquote

import gi
from gi.repository import Gdk, Gio, GLib, Gtk

from .. import timing
from ..html.sanitize import sanitize_html
from ..html.totext import html_to_markup, quote_layout, split_quoted_text, text_to_markup
from .widgets import open_uri

log = logging.getLogger(__name__)

HAVE_WEBKIT = False
if not os.environ.get("DEN_MAIL_NO_WEBKIT"):
    # WebKitGTK's DMA-BUF renderer intermittently paints nothing on NVIDIA/Wayland
    # setups; mail bodies are static, so the plain renderer costs us nothing.
    os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
    try:
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit

        HAVE_WEBKIT = True
    except (ValueError, ImportError):
        log.info("WebKitGTK 6.0 not available; using text fallback for HTML mail")

CID_SCHEME = "fmcid"

# (email_id, cid) -> callback(bytes|None, mime|None); set by the window
CidResolver = Callable[[str, str, Callable[[bytes | None, str | None], None]], None]

_context: WebKit.WebContext | None = None
_session = None
_resolver: CidResolver | None = None


def set_cid_resolver(resolver: CidResolver) -> None:
    global _resolver
    _resolver = resolver


def _get_context():
    global _context, _session
    if _context is None:
        _context = WebKit.WebContext()
        _context.set_cache_model(WebKit.CacheModel.DOCUMENT_VIEWER)
        _context.register_uri_scheme(CID_SCHEME, _on_cid_request)
        _session = WebKit.NetworkSession.new_ephemeral()
    return _context


def _on_cid_request(request) -> None:
    uri = request.get_uri()  # fmcid://<email_id>/<cid>
    rest = uri.split("://", 1)[1] if "://" in uri else uri.split(":", 1)[1]
    email_id, _, cid = rest.partition("/")
    cid = unquote(cid)
    if _resolver is None:
        request.finish_error(GLib.Error("no resolver"))
        return

    def done(data: bytes | None, mime: str | None) -> None:
        if data is None:
            request.finish_error(GLib.Error(f"inline part {cid} not found"))
            return
        stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
        request.finish(stream, len(data), mime or "application/octet-stream")

    _resolver(email_id, cid, done)


if HAVE_WEBKIT:

    _primary_view = None

    def _related_view():
        """A never-shown view every message view is related to, so they share its web
        process instead of each spawning one (that spawn was most of the time from
        selecting a message to its first paint)."""
        global _primary_view
        if _primary_view is None:
            _primary_view = WebKit.WebView(web_context=_get_context(), network_session=_session)
        return _primary_view

    class _SizedWebView(WebKit.WebView):
        """A WebView that grows to its content height (no inner scrolling)."""

        def __init__(self):
            super().__init__(web_context=_get_context(), network_session=_session, related_view=_related_view())
            settings = self.get_settings()
            settings.set_enable_javascript(True)  # needed for evaluate_javascript()
            settings.set_enable_javascript_markup(False)  # ...but pages may not run their own
            settings.set_enable_html5_local_storage(False)
            settings.set_enable_html5_database(False)
            settings.set_enable_page_cache(False)
            settings.set_enable_webgl(False)
            settings.set_enable_developer_extras(False)
            settings.set_enable_media(False)
            settings.set_enable_media_stream(False)
            settings.set_enable_webaudio(False)
            settings.set_allow_modal_dialogs(False)
            settings.set_enable_smooth_scrolling(True)
            settings.set_default_font_size(15)
            self.set_vexpand(False)
            self.set_hexpand(True)
            self._last_width = 0
            self._pending = 0
            self.show_quotes = False  # re-applied once a load finishes, so a toggle during a load is not lost
            self.connect("load-changed", self._on_load_changed)
            self.connect("decide-policy", self._on_decide_policy)
            self.connect("create", self._on_create)  # never open WebKit child windows
            self.connect("context-menu", lambda *_: True)
            self.connect("notify::estimated-load-progress", lambda *_: self._schedule_measure())

        def do_size_allocate(self, width, height, baseline):
            WebKit.WebView.do_size_allocate(self, width, height, baseline)
            if width != self._last_width:
                self._last_width = width
                self._schedule_measure()

        def _on_load_changed(self, _view, event):
            if event == WebKit.LoadEvent.FINISHED:
                timing.mark("open-painted")
                self.apply_quotes()
                self._schedule_measure()
                GLib.timeout_add(400, lambda: (self._measure(), False)[1])
                GLib.timeout_add(1500, lambda: (self._measure(), False)[1])

        def _schedule_measure(self):
            if self._pending:
                return
            self._pending = GLib.timeout_add(60, self._measure_cb)

        def _measure_cb(self):
            self._pending = 0
            self._measure()
            return False

        def apply_quotes(self):
            """Show or hide the quoted history (the den-quote elements) in the loaded page."""
            self.evaluate_javascript(
                f"document.body && document.body.classList.toggle('den-show-quotes', {'true' if self.show_quotes else 'false'})",
                -1, None, None, None, None, None)
            self._schedule_measure()

        def _measure(self):
            if not self.get_mapped():
                return
            # The root's scrollHeight never drops below the viewport, which is this widget's
            # last size, so it could only grow; offsetHeight is the laid-out content.
            self.evaluate_javascript(
                "Math.max(document.documentElement.offsetHeight, document.body ? document.body.scrollHeight : 0)",
                -1, None, None, None, self._on_measured, None,
            )

        def _on_measured(self, view, result, _data):
            try:
                value = view.evaluate_javascript_finish(result)
                height = int(value.to_double())
            except (GLib.Error, AttributeError):
                return
            if height > 0 and abs(height - self.get_size_request()[1]) > 2:
                self.set_size_request(-1, min(height + 4, 20000))

        def _on_create(self, _view, action):
            uri = action.get_request().get_uri()
            if uri and not uri.startswith((CID_SCHEME, "about:")):
                open_uri(uri, self.get_root() if isinstance(self.get_root(), Gtk.Window) else None)

        def _on_decide_policy(self, _view, decision, decision_type):
            # Links (including target=_blank ones, which arrive as NEW_WINDOW_ACTION) open in the
            # system browser; only the initial load_html navigation is allowed inside the view.
            if decision_type in (WebKit.PolicyDecisionType.NAVIGATION_ACTION,
                                 WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
                action = decision.get_navigation_action()
                is_link = (action.get_navigation_type() == WebKit.NavigationType.LINK_CLICKED
                           or action.is_user_gesture()
                           or decision_type == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION)
                if is_link:
                    uri = action.get_request().get_uri()
                    decision.ignore()
                    if uri and not uri.startswith((CID_SCHEME, "about:")):
                        open_uri(uri, self.get_root() if isinstance(self.get_root(), Gtk.Window) else None)
                    return True
                return False
            if decision_type == WebKit.PolicyDecisionType.RESPONSE and not decision.is_mime_type_supported():
                decision.ignore()
                return True
            return False


class MessageBody(Gtk.Box):
    """Shows one message body (HTML via WebKit or text via a Label)."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("message-body")
        self._web = None
        self._label = None
        self.has_remote = False
        self._html: str | None = None
        self._email_id: str | None = None
        self._text: str | None = None      # the text body without its quoted history
        self._full_text: str | None = None  # the text body as received
        self._text_alternative: str | None = None  # the text/plain part beside an HTML body
        self._remote_in_quotes = False
        self.quotes_shown = False
        self.on_quotes_toggled: Callable[[], None] = lambda: None
        # Quoted history collapses behind this pill (#9); it sits below the content.
        self.quote_button = Gtk.Button(label="···", tooltip_text="Show quoted text", halign=Gtk.Align.START,
                                       visible=False)
        self.quote_button.add_css_class("quote-toggle")
        self.quote_button.connect("clicked", lambda *_: self.set_quotes_shown(not self.quotes_shown))
        self.append(self.quote_button)

    def _ensure_label(self) -> Gtk.Label:
        if self._label is None:
            self._label = Gtk.Label(xalign=0, yalign=0, wrap=True, wrap_mode=2, selectable=True, use_markup=True,
                                    hexpand=True)
            self._label.add_css_class("message-text")
            self._label.set_margin_top(8)
            self._label.set_margin_bottom(8)
            self._label.set_margin_start(4)
            self._label.set_margin_end(4)
            self._label.connect("activate-link", self._on_link)
            self.insert_child_after(self._label, None)
        self._label.set_visible(True)
        if self._web is not None:
            self._web.set_visible(False)
        return self._label

    def _ensure_web(self):
        if self._web is None:
            self._web = _SizedWebView()
            self._web.add_css_class("html-body")
            self.insert_child_after(self._web, None)
        self._web.set_visible(True)
        if self._label is not None:
            self._label.set_visible(False)
        return self._web

    def _on_link(self, _label, uri: str) -> bool:
        open_uri(uri, self.get_root() if isinstance(self.get_root(), Gtk.Window) else None)
        return True

    # ------------------------------------------------------------- public

    def show_text(self, text: str) -> None:
        self._html = None
        self._full_text = text
        self._text, quoted = split_quoted_text(text)
        label = self._ensure_label()
        label.set_markup(text_to_markup(text if self.quotes_shown else self._text))
        self.has_remote = False
        self._sync_quote_button(bool(quoted))
        timing.mark("open-rendered")

    def show_html(self, html: str, email_id: str, allow_remote: bool, dark: bool = False,
                  text: str | None = None) -> None:
        """`text` is the message's text/plain alternative: when it shows an inline reply
        (answers between quoted lines) the HTML quote containers hold the sender's own
        words too, so nothing is folded."""
        self._html = html
        self._text = None
        self._text_alternative = text
        self._email_id = email_id
        self._allow_remote = allow_remote
        self._dark = dark
        if HAVE_WEBKIT:
            fold = text is None or quote_layout(text) != "inline"
            result = sanitize_html(html, allow_remote=allow_remote, cid_scheme=f"{CID_SCHEME}://{email_id}/",
                                   dark=dark, show_quotes=self.quotes_shown, fold_quotes=fold)
            self._remote_in_quotes = result.has_remote_in_quotes
            self.has_remote = result.has_remote_content or (self.quotes_shown and result.has_remote_in_quotes)
            self._sync_quote_button(result.has_quotes)
            web = self._ensure_web()
            web.show_quotes = self.quotes_shown
            rgba = Gdk.RGBA()
            rgba.parse("#1e1e1e" if dark else "#ffffff")
            web.set_background_color(rgba)
            if dark:
                web.add_css_class("dark")
            else:
                web.remove_css_class("dark")
            web.load_html(result.html, f"{CID_SCHEME}://{email_id}/")
            timing.mark("open-rendered")  # handed to WebKit; open-painted follows once it has loaded
        else:
            label = self._ensure_label()
            label.set_markup(html_to_markup(html))
            self.has_remote = "http" in html and ("<img" in html.lower())
            self._sync_quote_button(False)

    def _sync_quote_button(self, has_quotes: bool) -> None:
        self.quote_button.set_visible(has_quotes)
        self.quote_button.set_tooltip_text("Hide quoted text" if self.quotes_shown else "Show quoted text")
        if self.quotes_shown:
            self.quote_button.add_css_class("expanded")
        else:
            self.quote_button.remove_css_class("expanded")

    def set_quotes_shown(self, shown: bool) -> None:
        """Reveal or collapse the quoted history of the current body."""
        self.quotes_shown = shown
        if self._text is not None and self._label is not None:
            self._label.set_markup(text_to_markup(self._full_text if shown else self._text))
        elif self._web is not None and self._web.get_visible():
            self._web.show_quotes = shown
            self._web.apply_quotes()
            self.has_remote = self.has_remote or (shown and self._remote_in_quotes)
        self._sync_quote_button(self.quote_button.get_visible())
        self.on_quotes_toggled()

    def allow_remote(self) -> None:
        if self._html is not None and self._email_id is not None:
            self.show_html(self._html, self._email_id, allow_remote=True, dark=getattr(self, "_dark", False),
                           text=self._text_alternative)

    def set_dark(self, dark: bool) -> None:
        """Re-render the current HTML for a theme change."""
        if self._html is not None and self._email_id is not None and getattr(self, "_dark", None) != dark:
            self.show_html(self._html, self._email_id, allow_remote=getattr(self, "_allow_remote", False), dark=dark,
                           text=self._text_alternative)
