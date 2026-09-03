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
`thread-menu`, `group on|off`, `scope all|mailbox`, `resize`, `quit`, …). The screenshots in `data/screenshots/` are
taken that way inside a headless `cage` compositor:

```
WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 \
  cage -- sh -c './bin/den-mail & sleep 6; grim shot.png; kill %1'
```

`wtype` works inside the cage session for key presses.

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
