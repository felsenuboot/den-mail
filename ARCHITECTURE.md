# Architecture

A mail client is a cache plus a sync engine with a UI on top. The code is
organised in layers that only talk downwards:

```
ui/           GTK widgets, windows, dialogs        (main thread)
models/       GObject list models the UI binds to  (main thread)
store/        SQLite cache + sync engine + actions (worker thread writes)
jmap/         JMAP transport, push (EventSource)   (network threads)
html/         sanitiser, HTML→text, compose helpers (pure functions)
```

## Data flow

1. **Session.** `jmap.client.JMAPClient` fetches the session resource once and
   caches it in the database, so later starts render from cache before the
   network is touched.
2. **Full sync** (first run): `Mailbox/get`, `Identity/get`, `MaskedEmail/get`,
   plus the current `Email` and `Thread` states. No email is downloaded
   speculatively.
3. **Queries.** Opening a mailbox runs `Email/query` (collapsed by thread) chained
   with `Email/get → Thread/get → Email/get` so that every message of every
   visible thread is cached with its list properties in one round trip. The
   query result (ordered ids, `queryState`, `canCalculateChanges`) is stored in
   `query_cache`.
4. **Incremental sync.** On a push event or timer, `Mailbox/changes`,
   `Email/changes`, `Thread/changes` update the cache. Visible queries are
   brought up to date with `Email/queryChanges` when the server allows it,
   otherwise re-run. `cannotCalculateChanges` on `Email/changes` resets the cache.
5. **Bodies** are fetched on demand (`Email/get` with body values, headers such
   as `Delivered-To`) and cached alongside the list properties.
6. **Blobs** (attachments, inline images) are downloaded through a small thread
   pool into the cache directory.

## Threads

- The **GTK main thread** owns every widget and model. It only reads from the
  database (WAL mode, one connection per thread).
- The **sync worker** is a single thread with a priority queue: user actions
  first, then loads (queries, bodies), then background sync. Serialising writes
  through one thread keeps JMAP state strings consistent.
- The **push listener** reads Fastmail's EventSource stream and asks the worker
  to sync when a state changes. Falls back to polling when disconnected.
- The engine emits GObject signals (`mailboxes-changed`, `emails-changed`,
  `query-updated`, `body-ready`, `new-mail`, …) which are always dispatched on
  the main thread through `GLib.idle_add`.

## Actions and undo

`store/actions.py` describes a change as keyword and mailbox deltas.
`SyncEngine.perform` applies it to the cache immediately (and adjusts mailbox
counters), emits `emails-changed` so the UI updates, sends one `Email/set`, and
rolls the local change back if the server rejects it. The original values are
returned as an `UndoRecord`, which the window shows as an "Undo" toast.

Sending is one request: `Email/set create` (draft) + `EmailSubmission/set` with
`onSuccessUpdateEmail` moving the message from Drafts to Sent, plus the
destruction of any previous draft.

## Labels

Fastmail labels are JMAP mailboxes; a message lists all of them in
`mailboxIds`. The sidebar shows system mailboxes (by role) followed by the
label tree (`Gtk.TreeListModel` over `Gio.ListStore`s that preserve object
identity across updates). Thread rows aggregate the cached messages of the
thread *within the current mailbox*: unread if any is unread, flagged if any is
flagged, and label chips are the union of the messages' other labels.

## Identities and aliases

`Identity/get` lists every address the account may send from, including
aliases and wildcard identities. Replies pick the identity matching the
`Delivered-To` header or a recipient address. Masked Email uses Fastmail's
`https://www.fastmail.com/dev/maskedemail` capability. Creating regular aliases
has no public API; see issue #1.

## HTML mail

`html/sanitize.py` strips scripts, frames, forms and event handlers, rewrites
`cid:` references to `fmcid://<email>/<cid>` and replaces remote resources by a
placeholder unless allowed. `ui/message_body.py` loads the result into a
WebKitGTK view that has JavaScript markup disabled, an ephemeral network
session, and a registered `fmcid` scheme handler that serves inline parts from
the blob cache. Without WebKit, `html/totext.py` converts HTML to Pango markup.

## Testing

`tests/fake_server.py` is an in-process JMAP server with a fixture account
(nested labels, threads, HTML with tracking pixels, inline images, attachments,
drafts, masked emails, wildcard identities). It implements method chaining and
result references, `/changes` and `/queryChanges` with real state tracking, and
the EventSource stream, so `tests/test_engine.py` runs the actual client code
end to end. `den_mail/autopilot.py` scripts the UI for screenshots.
