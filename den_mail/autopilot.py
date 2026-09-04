"""Scripted UI driving for screenshots and smoke tests (no input tool needed).

Set DEN_MAIL_AUTOPILOT to a semicolon-separated script, e.g.
  "sleep 4; select 0; sleep 2; action win.reply; sleep 2; quit"
Commands:
  sleep <seconds>      wait
  select <index>       select the conversation at that index in the list
  mailbox <name>       select a mailbox by name
  search <text>        type into the search box
  action <name>        activate a window action, e.g. win.archive
  compose              open a blank compose window
  compose-fill <to> <subject>   address and title the newest compose window
  compose-send         press Send in the newest compose window
  undo-send            press Undo on the newest pending send
  config <key> <json>  set a preference for this run
  masked | identities | preferences   open that dialog
  resize <w> <h>       resize the main window
  unread-filter on|off   toggle the unread filter button
  quotes on|off        reveal or fold the quoted history of every shown message
  expand-all           expand every message card of the shown conversation
  body-size            log the height of every HTML body view
  focus <search|list|sidebar|body>   move keyboard focus, to test key routing
  state                log panes, focus, search text and the selected conversations
  row-pos <mailbox>    log where that sidebar row is, in window coordinates
  trace-keys           log each key press as it passes the focused widget's ancestors
  quit                 exit the application
"""

from __future__ import annotations

import logging
import os

from gi.repository import GLib

log = logging.getLogger(__name__)


def install(app) -> None:
    script = os.environ.get("DEN_MAIL_AUTOPILOT")
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
        elif cmd == "group" and win:
            mode = {"on": "sender", "off": "off"}.get(arg.strip(), arg.strip())
            win.threadlist._group_action.change_state(GLib.Variant("s", mode))
        elif cmd == "select-mode" and win:
            win.threadlist.set_selection_mode(arg.strip() in ("on", "1", "true"))
        elif cmd == "toggle" and win:
            win.threadlist._toggle_position(int(arg))
        elif cmd == "sort-menu" and win:
            win.threadlist.sort_button.popup()
        elif cmd == "fold-all" and win:
            win.threadlist.fold_all(arg.strip() != "off")
        elif cmd == "fold" and win:
            from .models.thread import SenderGroup

            groups = [i for i in win.model.items if isinstance(i, SenderGroup)]
            win.model.toggle_collapsed(groups[int(arg)].key)
        elif cmd == "scope" and win:
            win.threadlist.focus_search()
            win.threadlist.scope.set_selected(1 if arg.strip() == "all" else 0)
        elif cmd == "compose" and win:
            win.compose("new")
        elif cmd == "compose-fill" and win and win.compose_windows:
            to, _, subject = arg.partition(" ")
            cw = win.compose_windows[-1]
            cw.to.set_text(to)
            cw.subject.set_text(subject)
        elif cmd == "compose-send" and win and win.compose_windows:
            win.compose_windows[-1].send()
        elif cmd == "undo-send" and win and win.pending_sends:
            win.pending_sends[-1].undo()
        elif cmd == "config" and win:
            import json

            key, _, value = arg.partition(" ")
            app.config.set(key, json.loads(value))
        elif cmd == "dump-compose" and win and win.compose_windows:
            cw = win.compose_windows[-1]
            log.info("compose From entries: %s (selected: %s)", cw._identity_strings(), cw._identity().display)
        elif cmd == "from-select" and win and win.compose_windows:
            win.compose_windows[-1].from_row.set_selected(int(arg))
        elif cmd == "from-popup" and win and win.compose_windows:
            from gi.repository import Gtk

            from .ui.compose import _find_descendant

            popover = _find_descendant(win.compose_windows[-1].from_row, Gtk.Popover)
            popover.popup()
        elif cmd in ("masked", "identities", "preferences") and win:
            win.lookup_action(cmd).activate(None)
        elif cmd == "resize" and win:
            w, h = arg.split()
            win.set_default_size(int(w), int(h))
        elif cmd == "fullscreen" and win:
            win.fullscreen()
        elif cmd == "theme" and win:
            from .ui.preferences import apply_color_scheme

            app.config.set("color_scheme", arg.strip() or "system")
            apply_color_scheme(app.config)
        elif cmd == "context-menu" and win:
            name, *coords = arg.rsplit(" ", 2) if arg.count(" ") >= 2 else [arg]
            x, y = (int(coords[0]), int(coords[1])) if coords else (100, 200)
            for mb in win.tree.all():
                if mb.name.lower() == name.lower():
                    win.sidebar.show_context_menu(mb, x, y)
                    break
        elif cmd == "thread-menu" and win:
            idx = int(arg)
            win.threadlist.select_position(idx)
            item = win.model.get_item(idx)
            if item is not None and not hasattr(item, "thread_id"):
                item = item.threads[0]   # a sender row: use its first thread
            if item is not None:
                win._on_thread_context_menu(item, 200, 80 + idx * 73)
        elif cmd == "maximize" and win:
            win.maximize()
        elif cmd == "measure" and win:
            _log_min_widths(win, int(arg) if arg.strip() else 250)
        elif cmd == "body-size" and win:
            for card in win.conversation.cards.values():
                web = card.body._web
                if web is not None and web.get_visible():
                    log.info("body-size: %s height=%d", card.email_id, web.get_size_request()[1])
        elif cmd == "expand-all" and win:
            for card in win.conversation.cards.values():
                card.set_expanded(True)
        elif cmd == "quotes" and win:
            shown = arg.strip() in ("on", "1", "true")
            pills = [c.body for c in win.conversation.cards.values() if c.body.quote_button.get_visible()]
            for body in pills:
                body.set_quotes_shown(shown)
            log.info("quotes: %d messages with quoted history, shown=%s", len(pills), shown)
        elif cmd == "unread-filter" and win:
            win.threadlist.unread_button.set_active(arg.strip() in ("on", "1", "true"))
        elif cmd == "focus" and win:
            _focus(win, arg.strip())
        elif cmd == "state" and win:
            _log_state(win)
        elif cmd == "row-pos" and win:
            _log_row_position(win, arg.strip())
        elif cmd == "trace-keys" and win:
            _trace_keys(win)
        elif cmd == "quit":
            app.quit()
            return False
        else:
            log.warning("autopilot: unknown step %r", step)
    except Exception:
        log.exception("autopilot step %r failed", step)
    GLib.timeout_add(delay, _run, app, steps)
    return False


def _log_min_widths(win, threshold: int) -> None:
    """Log the minimum width of each pane and of every descendant wider than `threshold`.

    Used to find which widget keeps a pane from shrinking below a breakpoint."""
    from gi.repository import Gtk

    def min_w(widget) -> int:
        return widget.measure(Gtk.Orientation.HORIZONTAL, -1)[0]

    log.info("measure: window %dx%d (scale %d, collapsed main=%s inner=%s); min widths: window %d, main %d, "
             "inner %d, sidebar %d, threadlist %d, conversation %d",
             win.get_width(), win.get_height(), win.get_scale_factor(), win.main.get_collapsed(),
             win.inner.get_collapsed(), min_w(win), min_w(win.main), min_w(win.inner), min_w(win.sidebar),
             min_w(win.threadlist), min_w(win.conversation))

    def walk(widget, depth: int) -> None:
        w = min_w(widget)
        if w >= threshold:
            name = type(widget).__name__
            css = " ".join(c for c in widget.get_css_classes() if c) or "-"
            log.info("measure: %s%s min=%d css=%s", "  " * depth, name, w, css)
        child = widget.get_first_child()
        while child is not None:
            walk(child, depth + 1)
            child = child.get_next_sibling()

    for pane in (win.sidebar, win.threadlist, win.conversation):
        walk(pane, 0)


def _find(widget, match):
    """Depth-first search for the first descendant that `match` accepts."""
    child = widget.get_first_child()
    while child is not None:
        if match(child):
            return child
        found = _find(child, match)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _focus(win, what: str) -> None:
    from gi.repository import Gtk

    if what == "search":
        win.threadlist.focus_search()
    elif what == "list":
        win.threadlist.listview.grab_focus()
    elif what == "sidebar":
        win.sidebar.listview.grab_focus()
    elif what == "body":  # a message body: the WebKit view of an HTML mail, else a text body's label
        body = _find(win.conversation, lambda w: "WebView" in type(w).__name__) or _find(
            win.conversation, lambda w: isinstance(w, Gtk.Label) and w.get_selectable() and w.get_wrap())
        if body is not None:
            body.grab_focus()
        log.info("focus body: %s", type(body).__name__ if body is not None else None)
    else:
        log.warning("autopilot: unknown focus target %r", what)


def _log_state(win) -> None:
    """Log what a key or pointer test needs to check afterwards."""
    focus = win.get_focus()
    chain = []
    while focus is not None and len(chain) < 8:
        chain.append(type(focus).__name__)
        focus = focus.get_parent()
    focus = " < ".join(chain) if chain else None
    sidebar = win.sidebar.selected
    log.info("state: mailbox=%s sidebar=%s main.show_content=%s inner.show_content=%s search=%r search_mode=%s "
             "unread_only=%s items=%d focus=%s selected=%s compose=%d threads=%d pending=%s",
             win.current_mailbox.name if win.current_mailbox else None, sidebar.name if sidebar else None,
             win.main.get_show_content(), win.inner.get_show_content(), win.threadlist.search_entry.get_text(),
             win.threadlist.search_bar.get_search_mode(), win.threadlist.unread_only, win.model.get_n_items(), focus,
             [(t.subject, "flagged" if t.flagged else "-", "unread" if t.unread else "-") for t in win.selected],
             len(win.compose_windows), len(win.thread_windows),
             [(p.remaining, p.toast.get_title() if p.toast else None) for p in win.pending_sends])


def _log_row_position(win, name: str) -> None:
    from .ui.sidebar import MailboxRow

    row = _find(win.sidebar.listview, lambda w: isinstance(w, MailboxRow) and w.obj is not None
                and w.obj.name.lower() == name.lower())
    if row is None:
        log.warning("row-pos: no row %r", name)
        return
    _ok, bounds = row.compute_bounds(win)
    log.info("row-pos: %s x=%d y=%d w=%d h=%d", name, bounds.get_x(), bounds.get_y(), bounds.get_width(),
             bounds.get_height())
    expander = row.get_parent()  # the Gtk.TreeExpander: its arrow sits left of the row content
    _ok, bounds = expander.compute_bounds(win)
    log.info("row-pos: %s-expander x=%d y=%d w=%d h=%d", name, bounds.get_x(), bounds.get_y(),
             bounds.get_width(), bounds.get_height())


_trace_controllers: list = []


def _trace_keys(win) -> None:
    """Log every key press as it bubbles through the focused widget's ancestors, to find what eats it."""
    from gi.repository import Gdk, Gtk

    for widget, ctrl in _trace_controllers:  # a second call watches the new focus chain only
        widget.remove_controller(ctrl)
    _trace_controllers.clear()
    focus = win.get_focus()
    widgets = []
    while focus is not None:
        widgets.append(focus)
        focus = focus.get_parent()
    for widget in widgets:
        for phase in (Gtk.PropagationPhase.CAPTURE, Gtk.PropagationPhase.BUBBLE):
            ctrl = Gtk.EventControllerKey(propagation_phase=phase)
            ctrl.connect("key-pressed", lambda _c, keyval, _code, _state, w=widget, ph=phase: (
                log.info("trace-keys: %s %s at %s", Gdk.keyval_name(keyval), ph.value_nick, type(w).__name__), False)[1])
            widget.add_controller(ctrl)
            _trace_controllers.append((widget, ctrl))
    log.info("trace-keys: watching %d widgets from %s up", len(widgets), type(widgets[0]).__name__ if widgets else None)
