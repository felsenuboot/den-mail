# Changelog

Every release of Den Mail, newest first. Versions follow `MAJOR.MINOR.PATCH`:
a milestone ships as a minor version, fixes in between as patch versions.
Unreleased changes gather at the top until they are tagged.

## [Unreleased]

### Features
- An assistant layer behind the coming summaries: an Assistant page in Preferences chooses Ollama on this machine, any OpenAI-compatible API or Anthropic, with the server, the model, a key kept in the keyring, a requests-per-day limit, a Test button and a note on whether mail text leaves the machine; off by default (#69)

### Fixes
- The five CodeQL alerts open on master are gone: an attribution regex restructured, an unused result, a wrapper lambda, an unused import and a procedure's return value (#72)

## [0.4.0] - 2026-09-04

Milestone *Submission queue and engine lifetime* (send later, offline outbox), plus the lock screen and schema.org data in mail.

### Features
- schema.org data in HTML mail (orders, parcels, invoices, flight, train, hotel and event reservations) makes the message Transactions for sure and shows a summary line above the body with a copy button for the tracking or reservation number (#20)
- A lock screen: on demand, after some idle time or when the session locks; unlocking through the system's authentication prompt where the polkit policy is installed, else a passphrase (#28)
- Offline outbox: archive, label, delete and send while the connection is down; the changes stay applied locally, queue in the cache and go out with the next sync, and the sidebar counts what is waiting (#8)
- Send later: a clock next to Send schedules the message for a preset or any time; it waits in Scheduled and can be cancelled from the conversation (#6)

### Fixes
- The lock's menu entry and shortcut only work while the lock is enabled in Preferences (off by default), enabling it asks for a passphrase or PIN first where the system prompt is unavailable, so Unlock always asks for something, and the local secret can be a PIN (#65)
- Typing in a compose window or reading in a thread window counts as activity for the idle lock (#55)

## [0.3.0] - 2026-09-04

Milestones *Release engineering*, *Learning layer* and *JMAP Contacts*.

### Features
- The Fastmail address book, with a token that has the Contacts scope: contacts complete recipients first, and a contact's photo replaces the sender domain's logo (#4, #14)
- CI runs the UI headlessly once per autopilot script and keeps the screenshots as an artifact (#11)
- "Categorise as…" in the context menu keeps your word over the rules; a learned layer trained from those corrections decides where the rules are unsure, sees what you do with a sender's mail, and the message details say why a message got its category (#23, #40)
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

[Unreleased]: https://github.com/felsenuboot/den-mail/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/felsenuboot/den-mail/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/felsenuboot/den-mail/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/felsenuboot/den-mail/releases/tag/v0.1.0
