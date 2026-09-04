# Changelog

Every release of Den Mail, newest first. Versions follow `MAJOR.MINOR.PATCH`:
a milestone ships as a minor version, fixes in between as patch versions.
Unreleased changes gather at the top until they are tagged.

## [Unreleased]

Changes waiting for the next release are one file each in `changelog.d/`;
`python tools/changelog.py preview` shows the section they will make.

## [0.6.2] - 2026-09-05

The first evening of polish: colours back, cards you can see in the dark, GNOME-style cards and buttons, the version in the app, accessible names, one-line texts, an Advanced row in Preferences, and a tour that covers everything.

### Features
- The version is visible in the app: About, the Account page of Preferences, and the tooltip of the app name in the sidebar show it, with the git commit as the build when the app runs from a checkout (#112)

### Fixes
- Compose: opening a window no longer logs a GTK warning about the From address being parsed as markup (#129)
- Every error toast says "Could not …" followed by the reason, instead of a mix of "… failed" and "Could not …" (#125)
- From a pass over every screen in both themes: category chips in Clean up are never cut short, the Identities, Rules and Inbox preference texts are one line each, and Suggest labels sits in its own Learning group (#124)
- Accessibility: every icon-only button has a name a screen reader can say (its tooltip), and the few buttons that had no tooltip got one (#123)
- Dark mode: the tip, summary and message cards have a lighter background and a visible edge, so they stand out from the pane the way they do in light mode (#122)
- The summary above a conversation is a card with a "Summary" caption and its controls in the corner, like the tip card, instead of a tinted bar with an accent stripe (#118)
- The tip on the empty conversation pane is a proper card with a border, its action is a real button, and the quick links under it are labelled buttons that wrap on a narrow pane, following the GNOME interface guidelines (#106)
- Labels and categories are coloured again: an unclosed block in the stylesheet had dropped every rule after it. A test now parses the stylesheet, and "Coloured labels and categories" in Preferences turns the colours off for plain chips (#105)

### Changes
- docs/RUST.md weighs a Rust port: where the time goes, what a rewrite would cost, and the recommendation not to port (#132)
- Summarise is also in the conversation's More menu, with its shortcut, so the sparkle in the header is one of three ways rather than the only one (#116)
- Preferences: the rows one rarely needs (the fallback poll interval, the assistant's requests per day) fold behind an Advanced row, so each page shows what matters first (#107)
- The tour has a path through the first ten minutes, sections for the assistant and summaries, label suggestions, two conversations side by side and background running, the three unlock methods, fresh screenshots, and an aligned dictionary entry at the top (#103)

## [0.6.1] - 2026-09-05

Fixes for what the first day of use turned up: notifications, PINs, the keyring choice, thinking models, and shorter preference texts.

### Fixes
- Assistant: a thinking model (Qwen3 on llama.cpp or Ollama, DeepSeek-R1) no longer leaves a summary hanging and empty; local servers are asked to skip the thinking, answers are capped, the wait can be up to five minutes, and an answer that is reasoning only is reported as such (#98)
- Preferences: every group has a one-line description again; the Lock page explains the chosen unlock method under the choice, the Assistant page keeps the defaults as tooltips, and nothing refers to the README (#96)
- Lock: a PIN can be set and used again (the dialog and Unlock failed silently for PINs), the dialog says what is wrong instead of reopening, switching between passphrase and PIN clears the old secret, and cancelling the keyring's password prompt puts the unlock method back to what it was (#95)
- Clicking a new-mail notification brings the window up and opens that message's conversation, in the list when it is there and in a thread window otherwise; it used to only present the window (#93)

## [0.6.0] - 2026-09-04

Everything the Standalone milestone held after 0.5.0: offline drafts, the keyring unlock, logo sources, label suggestions, background running, two conversations side by side, closable hints, rules explained, and changelog fragments for the process.

### Features
- Lock: a third way to unlock, through the keyring. Choosing it creates a keyring collection of the app's own ("Den Mail"), which the keyring daemon locks with the app and unlocks with its own prompt; the login keyring and other apps are never touched, and it works inside a Flatpak. "Unlock with" in Preferences now lists every method available on the machine (#66)
- Sender logos: a site without a favicon at the usual paths is asked once for the icon its home page links to; and "Sender logos" in Preferences chooses where logos come from: each sender's site, DuckDuckGo's icon service so sender sites see nothing, BIMI only with no web contact, or off (#63)
- Offline drafts: saving a draft while the server is unreachable keeps it locally, lists it in Drafts, and creates it on the server with the next sync; later saves and Send while still offline update that one queued draft instead of chaining, and an open compose window learns the server's id when it arrives (#61)
- Label suggestions: the app learns from the mail you have labelled and, when it is sure, offers "Work?" as a chip on a conversation that lacks the label; one click applies it. Off in Preferences → Inbox if unwanted; folders are never suggested (#60)
- Open beside: on a wide window (an ultrawide monitor), "Open beside" in a conversation's context menu (or B) pins it in a second column next to the reading pane, with its own Reply, Archive, labels and summary, while the list keeps driving the first; on a narrower window it opens a thread window as before (#35)
- Run in the background: with "Keep running when the window is closed" on (Preferences → Account), closing the window keeps the sync going and new-mail notifications coming; a click on one, or starting the app again, brings the window back, and Quit in the main menu (Ctrl+Q) ends it (#2)

### Fixes
- The hints above the list (Clean up in the Inbox and the category views, the Screener's) have a close button, and once closed, or once Clean up has been opened, the cleanup hints stay away (#84)
- The Search quick link's tooltip is a sentence about what the search does instead of a bare list of operators (#80)

### Changes
- Rules: the Rules dialog, the "Always for this sender" prompt and the Inbox preferences say plainly that these rules are Den Mail's own, run on this computer while the app is open, and are not the rules in Fastmail's settings, with a link to those for rules that should run on the server (#85)
- Changelog lines live one per file in changelog.d/ until a release assembles them, so pull requests no longer conflict on CHANGELOG.md (#57)

## [0.5.0] - 2026-09-04

Milestone *Assistant*: a language-model layer with three providers, and summaries on it.

### Features
- Summarise a conversation: with the assistant on, a sparkle in the conversation header (and Ctrl+Shift+S) sums the thread up in a few lines above the messages, quoted history left out, cached per thread; Clean up shows a one-line description of a sender's newest message on the expanded row (#68)
- An assistant layer behind the coming summaries: an Assistant page in Preferences chooses Ollama on this machine, any OpenAI-compatible API or Anthropic, with the server, the model, a key kept in the keyring, a requests-per-day limit, a Test button and a note on whether mail text leaves the machine; off by default (#69)

### Fixes
- The two CodeQL notes on the provider protocol's method bodies are settled (#72)
- With the screener on, the Inbox badge no longer counts the unread mail it holds back; the Screener view's badge does (#62)
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
