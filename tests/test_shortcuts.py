"""Shortcut tables and the chord state machine."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk

from den_mail.shortcuts import ACCELS, CHORDS, DIALOG, Chords, application_accels, dialog_accelerator


def test_every_accelerator_parses_and_names_a_window_or_app_action():
    for action, accels in ACCELS.items():
        assert action.startswith(("win.", "app.")), action
        assert accels, action
        for accel in accels:
            assert Gtk.ShortcutTrigger.parse_string(accel) is not None, (action, accel)
    assert application_accels() == {"app.quit": ["<Control>q"]}


def test_chords_complete_within_the_timeout_only():
    chords = Chords(timeout_ms=1000)
    assert chords.feed("g", 0) is None
    assert chords.feed("i", 500) == "win.goto-inbox"
    assert chords.feed("i", 600) is None  # the chord is consumed
    assert chords.feed("g", 1000) is None
    assert chords.feed("d", 2500) is None  # too late
    assert chords.feed("g", 3000) is None
    assert chords.feed("x", 3100) is None  # not a chord; also does not start one
    assert chords.feed("d", 3200) is None
    assert chords.feed("g", 4000) is None
    assert chords.feed("g", 4100) is None  # a repeated prefix restarts the chord
    assert chords.feed("d", 4200) == "win.goto-drafts"
    assert all(action.startswith("win.") for action in CHORDS.values())


def test_dialog_lists_every_binding_once():
    refs = [entry[1] for _section, entries in DIALOG for entry in entries]
    for action in (*ACCELS, *CHORDS.values()):
        assert refs.count(action) == 1, action
    assert dialog_accelerator("win.compose") == "c <Control>n"
    assert dialog_accelerator("win.goto-inbox") == "g+i"
    assert dialog_accelerator("win.next", "Down") == "j Down"
    assert dialog_accelerator("<Control>Return") == "<Control>Return"  # compose window, literal
    for _section, entries in DIALOG:
        for _title, ref, *extra in entries:
            for accel in dialog_accelerator(ref, *extra).split():
                for key in accel.split("+"):  # "g+i" is a key sequence in Adw.ShortcutsItem syntax
                    assert Gtk.ShortcutTrigger.parse_string(key) is not None, accel
