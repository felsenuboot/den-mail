"""Scripted UI driving for screenshots and smoke tests (no input tool needed).

Set FASTMAIL_GTK_AUTOPILOT to a semicolon-separated script, e.g.
  "sleep 4; select 0; sleep 2; action win.reply; sleep 2; quit"
Commands:
  sleep <seconds>      wait
  select <index>       select the conversation at that index in the list
  mailbox <name>       select a mailbox by name
  search <text>        type into the search box
  action <name>        activate a window action, e.g. win.archive
  compose              open a blank compose window
  masked | identities | preferences   open that dialog
  resize <w> <h>       resize the main window
  quit                 exit the application
"""

from __future__ import annotations

import logging
import os

from gi.repository import GLib

log = logging.getLogger(__name__)


def install(app) -> None:
    script = os.environ.get("FASTMAIL_GTK_AUTOPILOT")
    if not script:
        return
    steps = [s.strip() for s in script.split(";") if s.strip()]
    GLib.timeout_add(500, _run, app, steps)


def _run(app, steps: list[str]) -> bool:
    if not steps:
        return False
    step = steps.pop(0)
    cmd, _, arg = step.partition(" ")
    win = app.window
    delay = 50
    try:
        if cmd == "sleep":
            delay = int(float(arg) * 1000)
        elif cmd == "select" and win:
            win.threadlist.select_position(int(arg))
        elif cmd == "mailbox" and win:
            for mb in win.tree.all():
                if mb.name.lower() == arg.lower():
                    win.sidebar.select_mailbox(mb.id)
                    break
        elif cmd == "search" and win:
            win.threadlist.focus_search()
            win.threadlist.search_entry.set_text(arg)
        elif cmd == "action" and win:
            group, _, name = arg.partition(".")
            action = (win if group == "win" else app).lookup_action(name)
            if action is None:
                log.warning("autopilot: no action %s", arg)
            else:
                action.activate(None)
        elif cmd == "compose" and win:
            win.compose("new")
        elif cmd in ("masked", "identities", "preferences") and win:
            win.lookup_action(cmd).activate(None)
        elif cmd == "resize" and win:
            w, h = arg.split()
            win.set_default_size(int(w), int(h))
        elif cmd == "fullscreen" and win:
            win.fullscreen()
        elif cmd == "maximize" and win:
            win.maximize()
        elif cmd == "quit":
            app.quit()
            return False
        else:
            log.warning("autopilot: unknown step %r", step)
    except Exception:  # noqa: BLE001
        log.exception("autopilot step %r failed", step)
    GLib.timeout_add(delay, _run, app, steps)
    return False
