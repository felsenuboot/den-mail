# Development notes

Everything network-related is exercised against an in-process fake JMAP server
(`tests/fake_server.py`) that implements the parts of RFC 8620/8621 and the
Masked Email extension the client uses, plus an RFC 8058 unsubscribe endpoint.

```
python -m venv --system-site-packages .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q
```

## Running the UI against the fake server

```
python -m tests.fake_server 18081 &
DEN_MAIL_SESSION_URL=http://127.0.0.1:18081/session DEN_MAIL_TOKEN=fake-token \
  XDG_DATA_HOME=/tmp/fm/data XDG_CONFIG_HOME=/tmp/fm/config XDG_CACHE_HOME=/tmp/fm/cache \
  ./bin/den-mail
```

With `DEN_MAIL_SESSION_URL` set the app registers a non-unique GApplication,
so it does not join a running desktop instance.

## Scripted UI and screenshots

`DEN_MAIL_AUTOPILOT="sleep 3; select 0; action win.reply"` drives the UI
from a script (see `den_mail/autopilot.py` for the commands: `select`,
`mailbox`, `search`, `action`, `compose`, `from-popup`, `theme`, `context-menu`,
`thread-menu`, `group off|sender|domain`, `fold N`, `fold-all on|off`, `select-mode on|off`, `toggle N`, `scope all|mailbox`, `resize`, `unread-filter on|off`, `quotes on|off`, `expand-all`, `compose-fill <to> <subject>`, `compose-send`, `undo-send`, `config <key> <json>`, `focus search|list|sidebar|body`, `state`, `row-pos <mailbox>`, `trace-keys`, `quit`, …). The screenshots in `data/screenshots/` are
taken that way inside a headless `cage` compositor:

```
WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 \
  cage -- sh -c './bin/den-mail & sleep 6; grim shot.png; kill %1'
```

`wtype` works inside the cage session for key presses, except that the first
key of a session is dropped, so send a bare `wtype -k Shift_L` first. Combined
with the autopilot's `focus`, `state` and `trace-keys` steps that checks where
keys go (the shortcut letters must reach a focused search box, and the
shortcuts otherwise; `trace-keys` shows the widget that swallows one). For the
pointer (hover, clicks) run the app on the rootful Xwayland described below
and drive it through XTest, for example with a few lines of ctypes against
`libXtst`; `row-pos <mailbox>` logs where a sidebar row is.

Cage fullscreens every window to its 1280x720 headless output, so to test a
particular window size (breakpoints, narrow layouts) run a rootful Xwayland
inside cage and let the app pick its own size on X11, where nothing maximises it:

```
WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 cage -- sh -c '
  Xwayland :97 -geometry 1020x700 & sleep 2
  GDK_BACKEND=x11 DISPLAY=:97 DEN_MAIL_AUTOPILOT="sleep 1; resize 1020 700; sleep 3; select 1; sleep 3; measure" ./bin/den-mail &
  sleep 9; grim shot.png; kill %2 %1'
```

For an ultrawide window, give cage two headless outputs and let the app ask for
the whole span: `WLR_HEADLESS_OUTPUTS=2` makes a 2560x720 screen, a seeded
`config.json` with `{"window": {"width": 2560, "height": 720}}` in the fresh
`XDG_CONFIG_HOME` sizes the window, and `grim` without `-o` captures both
outputs in one image. Cage fullscreens a rootful Xwayland to a single output,
so that route cannot go wider than 1280.

The autopilot `measure [threshold]` step logs the window's real size, which split
views are collapsed, the minimum width of each pane and every descendant wider
than the threshold, which is how to find the widget that keeps a pane from
shrinking below a breakpoint.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `DEN_MAIL_TOKEN` | use this token instead of the keyring |
| `DEN_MAIL_SESSION_URL` | JMAP session URL (default `https://api.fastmail.com/jmap/session`) |
| `DEN_MAIL_DEBUG=1` | log every JMAP request (method names and sizes, never bodies) |
| `DEN_MAIL_NO_WEBKIT=1` | force the text renderer for HTML mail |
| `DEN_MAIL_AUTOPILOT` | script to run after start-up (see above) |

## Where things live

See [ARCHITECTURE.md](../ARCHITECTURE.md) for the layering (JMAP transport,
SQLite cache and sync worker, models, UI). Ideas and open questions are on the
[issue tracker](https://github.com/felsenuboot/den-mail/issues); the inbox
cleanup roadmap is #15.

## Checks

CI (`.github/workflows/ci.yml`) runs Ruff, ShellCheck, Bandit and the test
suite; CodeQL runs separately. The same commands locally:

```
pipx run --spec ruff==0.16.5 ruff check .
pipx run --spec shellcheck-py==0.11.0.1 shellcheck install.sh bin/den-mail
pipx run --spec "bandit[toml]==1.9.2" bandit -q -c pyproject.toml -r den_mail data
.venv/bin/python -m pytest -q
```

Bandit findings that are false positives get a `# nosec Bxxx` on the line and
a comment above saying why; Ruff's rule set is pinned in `pyproject.toml`.

## Screenshots

The images in `data/screenshots/` and `docs/TOUR.md` are made from the fake
account in a headless cage session (see above): one autopilot script per
picture, `grim` inside the session, and `magick` to assemble a GIF from a handful
of frames (only for a workflow that a still cannot show) or to split a light
and a dark capture diagonally. `data/screenshots/make.sh` does all of it (start
`python -m tests.fake_server 18081` first); run it after UI changes so the tour
stays honest. Stop a stuck session by the PID of the process you started, never
by name: `pkill Xwayland` also hits the desktop's own Xwayland.
