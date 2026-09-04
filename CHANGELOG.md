# Changelog

Every release of Den Mail, newest first. Versions follow `MAJOR.MINOR.PATCH`:
a milestone ships as a minor version, fixes in between as patch versions.
Unreleased changes gather at the top until they are tagged.

## [Unreleased]

### Features
- Packaging: a Flatpak manifest and a workflow that attaches a single-file bundle to every release, an AUR PKGBUILD, AppStream metadata, and a per-distribution dependency table in the README (#16)

## [0.2.0] - 2026-09-04

Milestone *Sender cleanup*, plus the views that came just before it.

### Features
- Views in the sidebar, answered from the local cache: Newsletters, Transactions, Security, Updates, Never read and Big attachments; a search inside a view stays local (#19)
- Rules: always label, archive, read or delete mail from a sender, domain, list or category, applied as it arrives; "Always for this sender…" in the context menu, a Rules dialog with hit counts and a link to Fastmail's server-side rules (#22)
- Clean up: every sender ranked by how pointless their mail looks, with bulk Mark read, Archive, Delete and Unsubscribe that reach every message on the server, one Undo for the whole run, and "Always…" rules per sender (#21)
- Screener: optionally hold mail from first-time senders in a Screener view until they are let through or screened out (#24)
- Preferences on three pages (General, Inbox, Account), an Inbox heading in the main menu, a broom in the sidebar, banners in the views, and a tip of the day under the empty conversation pane
- Quick links under the tip card: Clean up, Newsletters, Rules, Search, Shortcuts (#43)

### Fixes
- Loading a mailbox or view clears the remembered selection; with the unread filter on, the previous mailbox's open conversation was carried into the next list
- Engine tests no longer share one config file (GLib caches the config directory per process)
- The screener no longer misses a sender whose mail arrived while a sync was already running (#42)

## [0.1.0] - 2026-09-04

The first working client: JMAP session, push and polling sync into a SQLite
cache, the three-pane window with labels, drag and drop, search operators,
sorting and grouping by sender, safe HTML with remote content held back,
compose with identities and undo send, Masked Email, the newsletter
unsubscribe dialog, the deterministic categoriser, notifications with sender
logos, keyboard shortcuts, and a fake JMAP server the test suite runs against.

[Unreleased]: https://github.com/felsenuboot/den-mail/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/felsenuboot/den-mail/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/felsenuboot/den-mail/releases/tag/v0.1.0
