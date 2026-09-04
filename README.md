# <img src="data/io.github.felsenuboot.DenMail.svg" width="40" alt=""> Den Mail

[![CI](https://github.com/felsenuboot/den-mail/actions/workflows/ci.yml/badge.svg)](https://github.com/felsenuboot/den-mail/actions/workflows/ci.yml)
[![CodeQL](https://github.com/felsenuboot/den-mail/actions/workflows/codeql.yml/badge.svg)](https://github.com/felsenuboot/den-mail/actions/workflows/codeql.yml)

A native Fastmail client for the GNOME desktop: GTK4, libadwaita and JMAP.
Three panes, labels that nest, push updates, offline reading, and every alias
you own in the From field.

![The inbox with a newsletter open, dark theme](data/screenshots/inbox-dark.png)

> [!NOTE]
> **Status and disclaimer.** This is a personal project, written largely with
> Claude Code and reviewed by a human, but not audited. It works on my machine
> (Arch, Hyprland, Premium Fastmail Account). Use at your own risk; there is no warranty.
> Issues and pull requests are welcome. Use it at your
> own risk; there is no warranty of any kind. Issues and pull requests are
> welcome. This project is not affiliated with or endorsed by Fastmail.


## Features

- **Labels, the Fastmail way.** Messages carry several labels, labels nest, and
  you drag conversations onto them. Removing the Inbox chip archives.
- **Fast and offline.** A local SQLite cache makes folder switches instant and
  lets you read without a connection. Changes arrive over Fastmail's push stream.
- **Undo for everything.** Archive, delete, spam, flag, read, label and move
  happen immediately and can be undone from the toast. A sent message waits a
  few seconds (your choice, in Preferences) before it really goes out.
- **Safe HTML.** Scripts stripped, remote content blocked until you allow it,
  dark mode that adapts light-coloured mail (with a per-message switch back).
- **All your identities.** Send from any alias or wildcard address, star the
  ones you use, manage Masked Email addresses.
- **Bulk actions.** Ctrl/Shift-click or the Select button with checkboxes;
  group by sender (any sort order), fold groups, act on a whole sender at once.
- **One-click unsubscribe**, an unread filter, quoted history folded behind
  a `···` pill, search operators
  (`from:` `to:` `subject:` `is:unread` `has:attachment` `before:` `after:`),
  notifications with the sender's logo, `mailto:` handling, keyboard shortcuts.

## How fast is it

Measured against Fastmail's own clients on the same account, machine and
network, five runs each on an idle desktop (medians, milliseconds;
[method and full results](docs/BENCHMARK.md)):

| | den-mail | Fastmail desktop app | Fastmail web |
| --- | --- | --- | --- |
| launch to a usable inbox | **300** | 1412 | 868 |
| switch to a folder of 2,900 conversations | 118 | 209 | **112** |
| search | **177** | 224 | 199 |
| open a message, body painted | **66** | 136 | 154 |
| memory with a message open (PSS, MiB) | **341** | 642 | 511 |

The local cache is what makes the difference: folders and the inbox come from
SQLite and the server's answer only refreshes them. Without a cache (first
start) den-mail needs 2.5 s to the inbox, like any client syncing from scratch.

| Light and dark theme | Group by sender |
| --- | --- |
| ![Half light, half dark](data/screenshots/theme-split.png) | ![The list grouped by sender](data/screenshots/group-sender.png) |

More screenshots, with search, compose, the dialogs and a clip of the
group-and-archive workflow, are in the [tour](docs/TOUR.md).

## Install

Python 3.12+, PyGObject, GTK 4, libadwaita 1.5+, libsecret and, for HTML mail,
WebKitGTK 6.0. Optional: setproctitle, so the process shows up as `den-mail`
rather than `python3` in top and friends.

```
# Arch
sudo pacman -S --needed python-gobject gtk4 libadwaita libsecret webkitgtk-6.0 python-setproctitle
# Fedora: python3-gobject gtk4 libadwaita libsecret webkitgtk6.0 python3-setproctitle
# Debian/Ubuntu: python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-secret-1 gir1.2-webkit-6.0 python3-setproctitle

git clone https://github.com/felsenuboot/den-mail.git
cd den-mail
./install.sh
```

That adds a launcher, desktop entry and icons to your user profile. You can
also run `./bin/den-mail` straight from the checkout.

On first start the app asks for a Fastmail API token. Create one under
*Settings → Privacy & Security → API tokens* with the **Mail**, **Submission**
and **Masked Email** scopes. It is stored in your keyring and sent only to the
JMAP session URL.

<details>
<summary>Keyboard shortcuts</summary>

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
| `g` then `i` / `d` | go to Inbox / Drafts |
| `F5`, `Ctrl+R` | refresh |
| `Ctrl+Return` / `Ctrl+S` | send / save draft (compose) |
| `Escape` | back (narrow layout) |
| `Ctrl+A` | select all |
| `Ctrl+,` / `Ctrl+?` / `Ctrl+Q` | preferences / shortcuts dialog / quit |

</details>

<details>
<summary>Troubleshooting</summary>

- **The light theme looks half dark.** Wallpaper theming tools (Matugen, pywal,
  ML4W) write `~/.config/gtk-4.0/colors.css`, which repaints every GTK app. The
  app re-asserts libadwaita's light palette, so Light means light.
- **Label colours differ from the web app.** Fastmail's public JMAP API does not
  expose them, so the app assigns its own. Right-click a label → Colour.
- **Clicking a link does not bring the browser forward.** That is the
  compositor's call (xdg-activation). On Hyprland enable
  `misc.focus_on_activate`, or turn on "Open links in a new browser window" in
  Preferences → Reading.
- **HTML mail shows up blank.** WebKit's DMA-BUF renderer is already disabled,
  which fixed this on an NVIDIA/Wayland setup. If it still happens, open an
  issue with your GPU and driver.
- **Aliases cannot be created in the app.** Fastmail's public API does not
  allow it (only Masked Email), so the app links to the web settings.

</details>

## Privacy

The API token lives in the system keyring (libsecret). HTML mail is sanitised
before it reaches WebKitGTK, remote content stays blocked until you allow it or
trust the sender, and attachments download on request only. Sender logos are
fetched from the sender's domain (BIMI record or favicon), which reveals nothing
about a specific message; turn them off in Preferences → Appearance.

## Contributing

Bug reports, ideas and pull requests are welcome on the
[issue tracker](https://github.com/felsenuboot/den-mail/issues). The inbox
cleanup roadmap is tracked in #15. Developer notes are in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md), the design in
[ARCHITECTURE.md](ARCHITECTURE.md).

## About the name

**伝** (*den*) is the Japanese character for conveying, transmitting, passing
something on, which is what mail does. The two-syllable name with a mail suffix
follows the example of [Zen Browser](https://zen-browser.app/). The project
started as *fastmail-gtk*; settings and the keyring token from that name migrate
automatically.

## Credits and licence

Some symbolic icons come from the
[Adwaita icon theme](https://gitlab.gnome.org/GNOME/adwaita-icon-theme)
(CC BY-SA 3.0 / LGPL). The app icon uses Fastmail's brand blue; the 伝
calligraphy in the tour is set in [Yuji Syuku](https://github.com/Kinutafontfactory/Yuji) (OFL).

MIT licence, see [LICENSE](LICENSE).
