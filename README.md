# Fastmail GTK

A native Fastmail client for GNOME, built on JMAP, GTK4 and libadwaita.

- Three-pane, adaptive layout (sidebar, conversation list, conversation view)
- Labels the way Fastmail does them: a message can live in several mailboxes,
  labels nest, and you can drag conversations onto them
- Cache-first: everything you have looked at is in a local SQLite database, so
  switching mailboxes is instant and the app works offline for reading
- Push updates over Fastmail's EventSource stream, with polling as fallback
- Optimistic actions with undo (archive, delete, spam, flag, read, labels, move)
- HTML mail rendered by WebKitGTK with scripts stripped, remote content blocked
  until you allow it, and inline images served locally
- Compose, reply, reply-all, forward, drafts with autosave, attachments
- Send from any identity, including wildcard `*@yourdomain` identities; star
  your favourites so the From list stays short
- **Links and attachments** open with the desktop's default handlers. Optionally the browser is started
  with its new-window switch, so pages open next to the mail client instead of as a tab somewhere else
- Masked Email management (create, block, restore, delete)
- Search with `from:`, `to:`, `subject:`, `is:unread`, `is:flagged`,
  `has:attachment`, `before:`/`after:` operators, scoped to a mailbox or all mail
- Sorting per mailbox (newest, oldest, sender, subject, size, flagged or unread
  on top), following Fastmail's own per-mailbox sort setting
- Double-click or Enter opens a conversation in its own window
- Sender logos from BIMI records or favicons, coloured labels, light/dark theme
- Desktop notifications for new mail, `mailto:` handler, keyboard shortcuts

## Requirements

Arch Linux package names; other distributions have equivalents.

```
sudo pacman -S --needed python-gobject gtk4 libadwaita libsecret webkitgtk-6.0
```

WebKitGTK is optional. Without it, HTML mail is converted to formatted text.

## Running

```
git clone git@github.com:felsenuboot/fastmail-gtk.git
cd fastmail-gtk
./bin/fastmail-gtk          # run from the checkout
./install.sh                # launcher, desktop entry and icon for your user
```

On first start the app asks for a Fastmail API token. Create one under
*Settings → Privacy & Security → API tokens* with the **Mail**, **Submission**
and **Masked Email** scopes. The token is stored in your keyring via libsecret.

## Keyboard shortcuts

| Keys | Action |
| --- | --- |
| `j` / `k`, arrows | next / previous conversation |
| `Return` / `o` | open conversation (narrow layout) |
| `c`, `Ctrl+N` | new message |
| `r` / `a` / `f` | reply / reply all / forward |
| `e` | archive |
| `#`, `Delete` | delete |
| `!` | mark as spam |
| `s` | flag / unflag |
| `Shift+U` / `Shift+I` | mark unread / read |
| `l` / `v` | labels / move to |
| `/`, `Ctrl+F` | search |
| `F5`, `Ctrl+R` | refresh |
| `Ctrl+Return` | send (compose) |
| `Ctrl+S` | save draft (compose) |
| `Ctrl+?` | shortcuts dialog |

## Theming notes

Wallpaper theming tools (Matugen, pywal, ML4W) write `~/.config/gtk-4.0/colors.css`,
which redefines libadwaita's named colours to a dark palette for every GTK app.
Fastmail GTK keeps that palette while it is dark, and re-asserts libadwaita's
light palette while it is light (Preferences → Appearance), so "Light" actually
looks light. Accent colours always come from your desktop.

Label colours are not available through Fastmail's JMAP API (the web client uses
an internal API), so the app assigns stable colours per label; right-click a
label → Colour to choose your own.

### Links and browser focus

The app hands links to the default browser through the desktop portal and passes along an
xdg-activation token. Whether the browser may then take focus (and pull you to its workspace) is your
compositor's policy: GNOME and KDE allow it by default, Hyprland only with `misc.focus_on_activate`
enabled. "Open links in a new browser window" (Preferences → Reading) sidesteps the question by
starting the browser with its new-window switch (Firefox, Zen, LibreWolf, Chromium, Chrome, Brave,
Vivaldi, Edge, Epiphany, Falkon, qutebrowser); other apps are unaffected.

## Development

Everything network-related is exercised against an in-process fake JMAP server
(`tests/fake_server.py`) that implements the parts of RFC 8620/8621 and the
Masked Email extension the client uses.

```
python -m venv --system-site-packages .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q

# Run the UI against the fake server
python -m tests.fake_server 18081 &
FASTMAIL_GTK_SESSION_URL=http://127.0.0.1:18081/session FASTMAIL_GTK_TOKEN=fake-token \
  XDG_DATA_HOME=/tmp/fm/data XDG_CONFIG_HOME=/tmp/fm/config ./bin/fastmail-gtk
```

`FASTMAIL_GTK_AUTOPILOT="sleep 3; select 0; action win.reply"` drives the UI
from a script (see `fastmail_gtk/autopilot.py`), which is how the screenshots in
development are taken inside a headless `cage` compositor.

Useful environment variables:

| Variable | Purpose |
| --- | --- |
| `FASTMAIL_GTK_TOKEN` | use this token instead of the keyring |
| `FASTMAIL_GTK_SESSION_URL` | JMAP session URL (default `https://api.fastmail.com/jmap/session`) |
| `FASTMAIL_GTK_DEBUG=1` | log every JMAP request |
| `FASTMAIL_GTK_NO_WEBKIT=1` | force the text renderer for HTML mail |

See [ARCHITECTURE.md](ARCHITECTURE.md) for how the pieces fit together, and the
[issue tracker](https://github.com/felsenuboot/fastmail-gtk/issues) for ideas
and open questions (for example, alias creation has no public JMAP API yet).

## Credits

Some symbolic icons are copied from the
[Adwaita icon theme](https://gitlab.gnome.org/GNOME/adwaita-icon-theme)
(CC BY-SA 3.0 / LGPL) so the app renders correctly with any icon theme.

MIT licensed.
