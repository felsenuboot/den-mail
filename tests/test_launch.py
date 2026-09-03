"""Pure helpers behind the "open links and attachments" preference."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from fastmail_gtk.launch import new_window_argv, pick_window, window_classes  # noqa: E402

URL = "https://example.com/x?a=1&b=2"


def test_new_window_argv_for_known_browsers():
    assert new_window_argv("/opt/zen-browser-bin/zen-bin %u", URL) == ["/opt/zen-browser-bin/zen-bin", "--new-window", URL]
    assert new_window_argv("/usr/bin/chromium %U", URL) == ["/usr/bin/chromium", "--new-window", URL]
    assert new_window_argv("qutebrowser %u", URL) == ["qutebrowser", "--target", "window", URL]
    # wrappers: the browser is not the first token
    assert new_window_argv("env MOZ_ENABLE_WAYLAND=1 firefox %u", URL) == [
        "env", "MOZ_ENABLE_WAYLAND=1", "firefox", "--new-window", URL]
    # flatpak file forwarding: the switch must stay outside the @@u … @@ span
    fp = "flatpak run --command=firefox --file-forwarding org.mozilla.firefox @@u %u @@"
    assert new_window_argv(fp, URL) == ["flatpak", "run", "--command=firefox", "--file-forwarding",
                                        "org.mozilla.firefox", "--new-window", "@@u", URL, "@@"]
    # no placeholder at all: append
    assert new_window_argv("firefox", URL) == ["firefox", "--new-window", URL]
    # other field codes are dropped, %% unescaped
    assert new_window_argv("firefox --class=%% %i %u", URL) == ["firefox", "--class=%", "--new-window", URL]


def test_new_window_argv_unknown_apps_and_garbage():
    assert new_window_argv("pinta %F", URL) is None
    assert new_window_argv("", URL) is None
    assert new_window_argv("firefox 'unterminated %u", URL) is None


def test_window_classes_from_desktop_entry():
    assert window_classes("zen.desktop", None, "/opt/zen-browser-bin/zen-bin") == ["zen", "zen-bin"]
    assert window_classes("org.mozilla.firefox.desktop", "Firefox", "firefox") == ["org.mozilla.firefox", "firefox"]
    assert window_classes(None, None, None) == []


def test_pick_window_prefers_most_recently_focused_match():
    clients = [
        {"address": "0x1", "class": "kitty", "initialClass": "kitty", "focusHistoryID": 0, "mapped": True},
        {"address": "0x2", "class": "zen", "initialClass": "zen", "focusHistoryID": 4, "mapped": True},
        {"address": "0x3", "class": "zen", "initialClass": "zen", "focusHistoryID": 2, "mapped": True},
        {"address": "0x4", "class": "zen", "initialClass": "zen", "focusHistoryID": 1, "mapped": False},
        {"address": "0x5", "class": "", "initialClass": "Zen", "focusHistoryID": 9, "mapped": True},
    ]
    assert pick_window(clients, ["zen", "zen-bin"]) == "0x3"
    assert pick_window(clients, ["firefox"]) is None
    assert pick_window([], ["zen"]) is None
    assert pick_window([clients[4]], ["zen"]) == "0x5"  # initialClass counts too, case-insensitively
