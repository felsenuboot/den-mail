# How to bring Den Mail to macOS

Asked on 2026-09-14: the best way to use Den Mail on a MacBook, ideally as a
native SwiftUI app with Liquid Glass and Apple's design paradigms. This is
the assessment and the plan, so the question does not have to be reopened.

## What the app is made of

| Area | Lines | Ties to GTK |
| --- | --- | --- |
| UI (`den_mail/ui`) | 7 970 | Everything: widgets, WebKitGTK, dialogs |
| Engine: store, JMAP, classify, HTML, rules, views, LLM | 5 300 | None. Pure Python over `urllib`, `sqlite3`, `html.parser` |
| Sync engine (`store/sync.py`) | 1 630 | Only its `GObject` signals and `GLib.idle_add` for the hop to the main thread |
| Platform glue: secrets, lock, avatars, launch, shortcuts, config | 1 300 | libsecret, polkit, `Gio.Resolver`, `Gdk`, `GLib` paths |
| Tests | 4 670 | Fake JMAP server (RFC 8620/8621, Masked Email, SSE push, RFC 8058 unsubscribe), engine, offline, classifier, HTML |

The engine is a clean layer already. The fake server is a plain stdlib HTTP
server (`python -m tests.fake_server <port>`) that any client, in any
language, can be pointed at; one import ties it to the package (#165).

## The options

1. **Run the GTK app on macOS** (Homebrew GTK 4, libadwaita, PyGObject). Does
   not get there: WebKitGTK has no macOS build, so HTML mail would be
   text-only; libsecret needs a Secret Service; libadwaita windows look and
   behave like GNOME on a Mac. No Liquid Glass, no menu bar, no Keychain.
   Ruled out.
2. **SwiftUI shell over the Python engine** (a bundled Python runtime, a local
   socket or an in-process bridge). Keeps 5 300 lines of engine and throws
   away the Apple-native feel where it matters most: the engine's threads and
   signals do not compose with Swift Concurrency, the app ships a 40 MB
   interpreter, notarising a Python framework is its own project, and every
   engine change has to be mirrored in a bridge protocol. Ruled out.
3. **A shared Rust core with two native UIs** (PyO3 for GTK, UniFFI for
   Swift). Only pays off if the Linux app adopts the Rust engine too, which
   `docs/RUST.md` argued against. Two engines would then coexist for a year.
   Not now; revisit if a third platform ever appears.
4. **A native Swift port: a SwiftPM core package plus a SwiftUI app.**
   Recommended, below.

## Recommendation: a sister project in Swift

A second repository, `den-mail-mac`, with two targets:

- **`DenMailCore`**, a SwiftPM package using only Foundation and SQLite. It
  builds and its tests run **on Linux** (Swift 6.3 via `swiftly`), so most of
  the port can be written and tested on the Arch machine, with the fake JMAP
  server from this repository as the conformance suite. It holds what the
  Python engine holds today: JMAP client and types, push, the SQLite cache
  with the same schema, the sync engine as an `actor` that publishes an
  `AsyncStream` of events instead of GObject signals, actions and undo
  records, the outbox and replay, the categoriser and the Bayes and label
  models, the HTML sanitiser, dark-mode rewrite and text conversion, rules,
  sender statistics, views, newsletters, unsubscribe, send-later presets,
  summaries and the LLM providers.
- **`DenMail.app`**, an Xcode target, macOS 26 as the minimum, SwiftUI
  throughout, AppKit only where SwiftUI has no answer (the WebKit view, key
  monitoring for single-key shortcuts).

The Python app stays the reference implementation. Where the Swift code and
the Python code disagree, the tests against the fake server decide.

### Mapping

| Den Mail today | On the Mac |
| --- | --- |
| `GObject` signals, `GLib.idle_add` | `actor SyncEngine`, `AsyncStream<Event>`, `@MainActor` view models with `@Observable` |
| Worker thread + blob pool, `PriorityQueue` | Structured concurrency: one serial actor for state, a `TaskGroup` for blobs |
| `urllib` | `URLSession` async/await, `Codable` JMAP types |
| SSE push thread with socket shutdown on stop | `URLSessionDataDelegate` feeding an `AsyncStream` (works on Linux too; `bytes(for:)` does not), `Task.cancel` to stop |
| `sqlite3`, WAL, one connection per thread | GRDB (official on Apple platforms, community-supported on Linux) with the same tables, so a cache is comparable across both apps |
| `html.parser` sanitiser | SwiftSoup for the sanitiser, plus a `WKContentRuleList` that blocks remote loads as a second line |
| WebKitGTK, `fmcid://` scheme, JS off, policy handler | `WKWebView`, `WKURLSchemeHandler` for `fmcid`, JavaScript off in the configuration, `WKNavigationDelegate` for links and pop-ups |
| libsecret `login` collection | Keychain (`SecItem`, generic password, this app's access group) |
| Lock: polkit, own keyring, passphrase | `LocalAuthentication` (Touch ID or password), idle from `CGEventSource` seconds since last event |
| `Gio.Notification` | `UserNotifications`, click routes to the thread |
| `HANDLES_OPEN` for `mailto:` | `CFBundleURLTypes` declares `mailto`; `onOpenURL`; the user picks the default client in Mail's settings, as macOS wants it |
| `hold()` to run in the background | Default Mac behaviour: closing the window keeps the app running |
| `Gio.Resolver` TXT lookup (BIMI) | `dnssd` `DNSServiceQueryRecord` |
| `Gtk.FileDialog`, `Gdk.Clipboard`, drag and drop | `NSOpenPanel`/`NSSavePanel` via `.fileImporter`, `NSPasteboard`, `.draggable`/`.dropDestination` with `Transferable` |
| JSON config file in `XDG_CONFIG_HOME` | `UserDefaults` via `@AppStorage`; large state stays in SQLite |
| `style.css`, theming | Asset catalog colours, system materials, no custom CSS |

### Apple paradigms: what changes on purpose

- **Window.** `NavigationSplitView` with three columns: sidebar (mailboxes,
  labels, views), conversation list, reading pane. A glass toolbar with
  `.searchable` in it; the list header's unread and category filters become
  toolbar menus. The second reading pane on ultrawide windows (`beside.py`)
  has no Mac equivalent; a conversation opens in its own window instead.
- **Menu bar and shortcuts.** Every action lives in the menu bar with a
  `⌘` shortcut through `.commands`; the Gmail-style single-key shortcuts stay,
  through a local `NSEvent` monitor, because they are part of the app.
- **Preferences** become a `Settings` scene (`⌘,`) with tabs: General, Inbox,
  Assistant, Account. Same rows, Apple layout.
- **Undo.** Every action registers with the window's `UndoManager`, so `⌘Z`
  and Edit ▸ Undo work as on any Mac; the toast stays only for Undo send,
  where the countdown is the point.
- **Categories.** Apple Mail on macOS 26 sorts into Primary, Transactions,
  Updates and Promotions with the same words; Den Mail's chips and filters
  map onto that vocabulary, plus Security, Newsletters and Lists.
- **Compose** is a `WindowGroup` keyed by draft id: one window per draft,
  restored by the system.
- **Lock** stays optional, unlocked with Touch ID; FileVault and the screen
  lock cover the main case, so it defaults to off.
- **Sidebar and lists** use `List` with the sidebar style, section headers
  and disclosure groups; the grouped-by-sender fold becomes a `DisclosureGroup`
  per sender.

### Where the work is

| Part | Python today | Swift, rough | Notes |
| --- | --- | --- | --- |
| JMAP client, types, push | 640 | 900 | SSE by delegate; `Codable` models |
| Store, sync, actions, outbox, undo | 2 810 | 3 500 | The careful part; port `test_engine.py` and `test_offline.py` first |
| Classifier, rules, senders, views, newsletters, unsubscribe, schedule | 1 700 | 1 800 | Mechanical; views are SQL |
| HTML sanitiser, dark mode, text, schema.org | 1 360 | 1 400 | SwiftSoup; port `test_html.py` |
| LLM providers, summaries | 700 | 700 | `URLSession`, same JSON |
| UI | 7 970 | 8 000 to 10 000 | SwiftUI is denser for lists and forms, not for the message body and drag and drop |

About the same size as the app today. The Linux app was written in two
weeks of issue-driven work with Claude Code; the same working style gives a
usable Mac app (read, act, compose, notifications, Keychain) in the first
weeks, with the cleanup, screener, rules, masked email, lock and summaries
following as milestones against the same issue conventions.

## Risks

- **Sync engine edge cases.** The tests encode them; port the tests before
  the code and run the Swift core against `tests.fake_server` on every check.
- **Sanitiser parity.** Two parsers will not agree on every broken HTML mail;
  the `WKContentRuleList` and disabled JavaScript make disagreement safe, not
  merely rare.
- **Push on Linux** during development: no `bytes(for:)` in
  FoundationNetworking, hence the delegate-based stream from day one.
- **GRDB on Linux** is community-supported. If it breaks, the tables are
  plain SQL and a thin `sqlite3` wrapper is a day's work.
- **Two apps to keep in step.** The categoriser rules, views and sanitiser
  will drift unless each change is filed as an issue in both repositories.
  Sharing the SQLite schema and the fake server keeps the drift visible.

## Tooling, distribution, cost

- **Building.** Xcode 26 on the MacBook, `xcodebuild` from the command line,
  so Claude Code drives builds and tests there as it drives `pytest` here.
  An autopilot environment variable like the Linux one, plus `screencapture`,
  gives the same scripted screenshots.
- **Signing.** A local build runs on the machine that built it without an
  Apple Developer account. Sharing the app with anyone else needs Developer
  ID signing and notarisation, 99 USD a year.
- **CI.** None. The repository is private, macOS runners bill at ten times
  the Linux rate, and the core tests run on Linux locally anyway.
- **Multi-account** (#3) is cheaper to design in from the start in the Swift
  core than to retrofit; give the schema an account column on day one even if
  the UI shows one account.

## First steps

1. #165: make the fake server importable without `den_mail`, document
   `python -m tests.fake_server <port>` as the conformance interface.
2. A new private repository `felsenuboot/den-mail-mac` under `~/ghq`,
   `swiftly` with Swift 6.3 on the Arch machine, a `DenMailCore` package with
   the JMAP client and a test that fetches the session and lists mailboxes
   from the fake server.
3. Port the store and sync engine with `test_engine.py` and
   `test_offline.py` translated to Swift Testing.
4. On the MacBook: the Xcode target, login to Keychain, the three-column
   window, the WebKit body. First usable build.
5. Then feature milestones in the order the Linux app grew: actions and undo,
   compose and send-later, notifications, categories and views, cleanup and
   rules, screener, masked email, lock, summaries.

## Keeping the two apps in step

Two native codebases means every feature is written twice; that is the price
of the plan. It stays affordable in three ways, and there is one escape
hatch.

- **Mirror issues, not memory.** A feature or fix that lands in den-mail gets
  an issue in den-mail-mac labelled `port`, linking the Linux pull request.
  That PR is the specification: design, wording, edge cases and tests are
  already decided, so the Mac side translates rather than designs. Den-mail
  is public, so a small workflow that opens the mirror issue when a PR with
  the `feature` or `bug` label merges costs nothing. A parity table in the
  Mac repository's README shows what is missing. Features can start on the
  Mac and flow the other way too.
- **Engine behaviour as shared data.** The UI is two implementations by
  nature. The engine need not be, and it is where drift would go unnoticed:
  categoriser rules, views, the sanitiser's allow-list, unsubscribe parsing.
  Shared test fixtures (categoriser cases, sanitiser input and expected
  output, unsubscribe headers) live in den-mail as JSON and both suites load
  them, so a rule added on Linux fails the Mac tests until it is ported. Where
  behaviour is declarative, share the data itself: the category rules as a
  rule file both apps read, the views as one SQL file over the shared schema.
  The fake JMAP server covers sync the same way.
- **Port finished designs.** What keeps the UI port cheap is that it is always
  a translation of a merged PR, never a design from scratch.

The escape hatch is the shared Rust core from the options above. If engine
drift still hurts after a few months, extract the engine into Rust once, used
by the GTK app through PyO3 and by the Mac app through UniFFI. Do it then and
not first: by then the engine's behaviour is stable and pinned by tests on
both sides, so the rewrite is mechanical.

## Handoff for a session on the Mac

Paste this into Claude Code in a terminal on the MacBook:

> Read `docs/MACOS.md` and `CLAUDE.md` in `~/ghq/github.com/felsenuboot/den-mail`
> (clone it with `ghq get felsenuboot/den-mail` if it is not there). We are
> starting the macOS port it describes. Create the private repository
> `felsenuboot/den-mail-mac` under `~/ghq` with the same conventions as
> den-mail (issues, milestones, branches per issue, squash merges, changelog
> fragments, a CLAUDE.md that says so, no CI). Check that Xcode 26 and Swift
> 6.3 are installed, and `swiftly` if the core is also to be built on Linux.
> Then do the first steps from the document in order: the `DenMailCore`
> package with a JMAP client and a test that fetches the session and lists
> mailboxes from den-mail's fake server (`python -m tests.fake_server 18081`
> in the den-mail checkout; #165 there makes it run without the package),
> then the store and sync engine with `test_engine.py` and `test_offline.py`
> translated to Swift Testing, then the Xcode app target with login to the
> Keychain, the three-column window and the WebKit body. Use silent
> subagents for reading and research; keep the main model for decisions.

Before the first UI work, put the shared fixtures in place (the section
above), so the categoriser and sanitiser ports are tested against the same
cases as the Python code from the start.
