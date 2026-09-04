"""Keyboard shortcuts for the mail windows.

The single-key shortcuts (Gmail style: r, e, s, j, k …) are deliberately not
application accelerators. GtkApplication runs those in the capture phase, before
the focused widget sees the key, so typing "s" into the search box flagged a
conversation instead of inserting the letter, Return opened it and Escape went
to the window (#33). Each window gets a shortcut controller in the bubble phase
instead: a focused text field consumes the keys it wants and only the rest reach
the shortcuts. GTK registers the controller's accelerators with the window's
action muxer, so menus keep showing them next to their items.
"""

from __future__ import annotations

from gi.repository import Gdk, GLib, Gtk

# Action → accelerators. The first one is the one menus show.
ACCELS: dict[str, list[str]] = {
    "win.compose": ["c", "<Control>n"],
    "win.reply": ["r"],
    "win.reply-all": ["a"],
    "win.forward": ["f"],
    "win.archive": ["e"],
    "win.trash": ["numbersign", "Delete"],
    "win.junk": ["exclam"],
    "win.flag": ["s"],
    "win.mark-unread": ["<Shift>u"],
    "win.mark-read": ["<Shift>i"],
    "win.labels": ["l"],
    "win.move": ["v"],
    "win.search": ["slash", "<Control>f"],
    "win.refresh": ["F5", "<Control>r"],
    "win.select-all": ["<Control>a"],
    "win.next": ["j"],
    "win.previous": ["k"],
    "win.open": ["Return", "o"],
    "win.back": ["Escape"],
    "win.preferences": ["<Control>comma"],
    "win.shortcuts": ["<Control>question"],
    "app.quit": ["<Control>q"],
}

# Two-key sequences, Gmail style: "g" then "i" goes to the Inbox.
CHORDS: dict[tuple[str, str], str] = {
    ("g", "i"): "win.goto-inbox",
    ("g", "d"): "win.goto-drafts",
}
CHORD_TIMEOUT_MS = 1500

# The shortcuts dialog: (section, entries); an entry is (title, action or literal
# accelerator, *extra accelerators the widgets provide themselves).
DIALOG: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...] = (
    ("Navigation", (("Next conversation", "win.next", "Down"), ("Previous conversation", "win.previous", "Up"),
                    ("Open conversation", "win.open"), ("Back", "win.back"), ("Search", "win.search"),
                    ("Go to Inbox", "win.goto-inbox"), ("Go to Drafts", "win.goto-drafts"),
                    ("Refresh", "win.refresh"))),
    ("Conversations", (("Archive", "win.archive"), ("Delete", "win.trash"), ("Mark as spam", "win.junk"),
                       ("Flag", "win.flag"), ("Mark unread", "win.mark-unread"), ("Mark read", "win.mark-read"),
                       ("Labels", "win.labels"), ("Move to…", "win.move"), ("Select all", "win.select-all"))),
    ("Compose", (("New message", "win.compose"), ("Reply", "win.reply"), ("Reply all", "win.reply-all"),
                 ("Forward", "win.forward"), ("Send", "<Control>Return"), ("Save draft", "<Control>s"))),
    ("Application", (("Preferences", "win.preferences"), ("Keyboard shortcuts", "win.shortcuts"),
                     ("Quit", "app.quit"))),
)

_MODIFIERS = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK
# Chord keys are matched like the accelerators (Caps Lock and non-Latin layouts included).
_CHORD_TRIGGERS = {key: Gtk.ShortcutTrigger.parse_string(key) for keys in CHORDS for key in keys}


def dialog_accelerator(ref: str, *extra: str) -> str:
    """The accelerator string of a DIALOG entry: its action's keys, a chord, or the literal."""
    if ref in ACCELS:
        keys = list(ACCELS[ref])
    else:
        keys = ["+".join(chord) for chord, action in CHORDS.items() if action == ref] or [ref]
    return " ".join([*keys, *extra])


def application_accels() -> dict[str, list[str]]:
    """The accelerators that belong to the application itself (app.*)."""
    return {action: accels for action, accels in ACCELS.items() if action.startswith("app.")}


class Chords:
    """The two-key sequences as a small state machine, independent of GTK."""

    def __init__(self, timeout_ms: int = CHORD_TIMEOUT_MS):
        self.timeout_ms = timeout_ms
        self._first: str | None = None
        self._at = 0

    def feed(self, key: str, now_ms: int) -> str | None:
        """Feed one unmodified key name; returns the action of a completed chord."""
        if self._first is not None and now_ms - self._at <= self.timeout_ms:
            action = CHORDS.get((self._first, key))
            self._first = None
            if action:
                return action
        self._first = key if any(first == key for first, _second in CHORDS) else None
        self._at = now_ms
        return None


def install(window: Gtk.Window) -> None:
    """Route the win.* shortcuts and chords through `window` in the bubble phase."""
    ctrl = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL,
                                  propagation_phase=Gtk.PropagationPhase.BUBBLE)
    for action, accels in ACCELS.items():
        if not action.startswith("win."):
            continue
        # The muxer remembers the accelerator registered last as the one menus
        # display, so the alternatives go first and the primary one wins.
        for accel in reversed(accels):
            ctrl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(accel),
                                               Gtk.NamedAction.new(action)))
    window.add_controller(ctrl)

    chords = Chords()

    def on_key(ctrl, _keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        event = ctrl.get_current_event()
        if state & _MODIFIERS or event is None or event.is_modifier():
            return False
        key = next((k for k, t in _CHORD_TRIGGERS.items() if t.trigger(event, False) != Gdk.KeyMatch.NONE), "")
        action = chords.feed(key, event.get_time() or GLib.get_monotonic_time() // 1000)
        # Gtk.Widget's activate_action, not Gio.ActionGroup's: the window is both.
        return bool(action) and Gtk.Widget.activate_action(window, action, None)

    keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.BUBBLE)
    keys.connect("key-pressed", on_key)
    window.add_controller(keys)
