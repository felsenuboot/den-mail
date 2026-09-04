"""The stylesheet parses to the end: an unclosed block once dropped every rule after it (#105)."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

CSS = Path(__file__).resolve().parent.parent / "den_mail" / "style.css"


def test_stylesheet_has_no_parsing_errors():
    errors = []
    provider = Gtk.CssProvider()
    provider.connect("parsing-error", lambda _p, section, err: errors.append(f"{section.to_string()}: {err.message}"))
    provider.load_from_path(str(CSS))
    assert errors == []


def test_braces_balance():
    text = CSS.read_text()
    assert text.count("{") == text.count("}")
