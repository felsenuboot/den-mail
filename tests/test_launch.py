"""Pure helpers behind the "open links and attachments" preference."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from fastmail_gtk.launch import new_window_argv  # noqa: E402

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


def test_commandline_survives_glib_field_code_expansion(tmp_path):
    """GLib treats the command line as an Exec entry; '%20' must reach the browser intact."""
    import time

    from gi.repository import Gio

    from fastmail_gtk.launch import commandline_for

    out = tmp_path / "argv.txt"
    stub = tmp_path / "stub.sh"
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "' + str(out) + '"\n')
    stub.chmod(0o755)
    argv = [str(stub), "--class=%", "https://example.com/a%20b?x=%3D1&y=100%25", "file:///tmp/My%20File.pdf",
            "plain arg"]
    info = Gio.AppInfo.create_from_commandline(commandline_for(argv), None, Gio.AppInfoCreateFlags.NONE)
    assert info.launch([], None)
    deadline = time.monotonic() + 5
    while not out.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.1)
    assert out.read_text().splitlines() == argv[1:]
