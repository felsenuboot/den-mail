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

Against the real account, use a separate API token for development: Fastmail
keeps one push stream per token and closes the older one whenever a new one
opens, so a script or second instance on the desktop's token leaves both
reconnecting instead of receiving push.

## Scripted UI and screenshots

`DEN_MAIL_AUTOPILOT="sleep 3; select 0; action win.reply"` drives the UI
from a script (see `den_mail/autopilot.py` for the commands: `select`,
`mailbox`, `view <name>`, `sender-rule <address>`, `cleanup-all <kind>`, `screen allow|block <address>`, `preferences <page>`, `search`, `action`, `compose`, `from-popup`, `theme`, `context-menu`,
`thread-menu`, `group off|sender|domain`, `fold N`, `fold-all on|off`, `select-mode on|off`, `toggle N`, `scope all|mailbox`, `resize`, `unread-filter on|off`, `category-filter <category>|off`, `syncing on|off`, `quotes on|off`, `expand-all`, `compose-fill <to> <subject>`, `compose-send`, `undo-send`, `config <key> <json>`, `focus search|list|sidebar|body`, `state`, `row-pos <mailbox>`, `trace-keys`, `quit`, …). The screenshots in `data/screenshots/` are
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

## Categoriser

`den_mail/classify/rules.py` sorts every cached message into a category from its
list headers, sender and wording (#18). The rules are generic (English, German
and a few Japanese and Chinese tokens); they were checked against a real
account with `tools/categoriser_report.py`, which runs them over an account's
SQLite cache and prints counts, the rule that fired for each category, and
random samples. After changing a rule, or on a new account, run it and read the
samples of the categories you doubt; nothing leaves the machine. What the
rules cannot tell apart (notifications from friendly mailboxes with list
headers, wording in other languages, a person's mail that mentions an invoice)
is listed in #40, the brief for the learning layers.

`den_mail/classify/bayes.py` (#23) is that layer: a naive Bayes model over
tokens (sender address, local part and domain, subject and preview words, the
list headers present, and the user's behaviour towards the sender), trained
from the user's corrections (weight 4) and the rules' verdicts at or above
0.8 (the newest 5000). It is stored in the cache (`bayes_docs`,
`bayes_tokens`), rebuilt by the engine after a correction, and consulted in
`Database._classify` only where the rules' confidence is below 0.8; it
speaks once it has 20 documents and 3 corrections and is at least 85 % sure.
The `classification` row keeps `source` (rules, bayes, user) and `reason`,
which the message details show.

## Process: branches, pull requests, releases

The rules every session follows are in `CLAUDE.md` at the top of the repo;
in short: an issue for everything, a branch per issue (`<issue>-<slug>`), a
pull request with `Closes #<issue>` merged by squash so master has one commit
per issue, a changelog fragment in the same PR (one file per change in
`changelog.d/`, see its README; `tools/changelog.py preview` shows the
section they make), and a milestone per release whose title carries the
version. A release is a `release/X.Y.Z` branch that bumps `VERSION` and runs
`tools/changelog.py release X.Y.Z` to turn the fragments into a section,
then a `vX.Y.Z` tag on the merge commit; the Release workflow
(`.github/workflows/release.yml`) turns the tag into a GitHub release with
that section as notes and refuses a tag that does not match `VERSION`.
`.github/release.yml` groups pull requests by label for GitHub's own
release-notes generator. Master is protected: pull requests and green CI are
required, force pushes are blocked, the repository admin can bypass.
## Packaging

`packaging/flatpak/` holds the Flatpak manifest (GNOME runtime; the app is
the only module, installed with pip into /app). The Flatpak workflow builds
it on pull requests that touch the packaging and on release tags, where it
attaches the single-file bundle to the GitHub release. `packaging/aur/` holds
the PKGBUILD for a tagged release; after a release, bump `pkgver`, run
`updpkgsums` and `makepkg --printsrcinfo > .SRCINFO`, and push both to the
AUR. `data/io.github.felsenuboot.DenMail.metainfo.xml` is the AppStream
metadata both use; add a `<release>` there with every version.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `DEN_MAIL_TOKEN` | use this token instead of the keyring |
| `DEN_MAIL_SESSION_URL` | JMAP session URL (default `https://api.fastmail.com/jmap/session`) |
| `DEN_MAIL_DEBUG=1` | log every JMAP request (method names and sizes, never bodies) |
| `DEN_MAIL_NO_WEBKIT=1` | force the text renderer for HTML mail |
| `DEN_MAIL_AUTOPILOT` | script to run after start-up (see above) |
| `DEN_MAIL_ASSISTANT_KEY` | the assistant's API key instead of the keyring |
| `DEN_MAIL_TIMING=1` | log `timing:` marks for start-up, folder switch, search and opening (see [BENCHMARK.md](BENCHMARK.md)) |

## Adding an LLM provider

One file in `den_mail/llm/` with a class that has `name`, `__init__(url,
model, key)`, `complete(system, user, json_schema=None) -> str` and
`check() -> str`, plus a module-level `SPEC = Spec(key, title, default_url,
default_model, needs_key, factory)`. Use `llm.http.request_json` for the
requests so errors come out as `LLMError` with a message fit for the user.
Add the module to the `PROVIDERS` tuple in `den_mail/llm/__init__.py`;
Preferences, the keyring entry and the budget need nothing else. Test it the
way `tests/test_llm.py` does, against the canned local HTTP server.

## Where things live

See [ARCHITECTURE.md](../ARCHITECTURE.md) for the layering (JMAP transport,
SQLite cache and sync worker, models, UI). Ideas and open questions are on the
[issue tracker](https://github.com/felsenuboot/den-mail/issues); the inbox
cleanup roadmap is #15.

## UI smoke test

`tools/ui_smoke.sh [out-dir]` runs the app headlessly once per autopilot
script (the inbox, grouping, selection, search, views, compose, every dialog,
the cleanup bulk actions, a narrow window) against the fake server, fails on
a traceback or GTK critical in the logs, and leaves the screenshots in the
directory. CI runs it in an Arch container (`ui-smoke` in `ci.yml`) and keeps
the screenshots as an artifact; it is not a required check.

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
