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

## Views

The sidebar's Views section (`den_mail/views.py`, #19) lists local queries:
the category views read the `classification` table, "Never read" groups the
`emails` table by sender, "Big attachments" filters on size. Each is one SQL
statement over the cache (`ROW_NUMBER` per thread stands in for JMAP's
`collapseThreads`), with the mailbox list's sort choices as `ORDER BY` and the
search box grammar as extra `WHERE` clauses. The `emails` table carries
`size`, `from_email`, `from_sort`, `seen` and `flagged` columns for this,
filled from the stored JSON once when an older cache is opened. A view is a
`MailboxObject` with `is_view` set and no rights, so the sidebar, the sort
overrides and the actions treat it like a mailbox, except that nothing is
moved into or out of it. The window keeps the view's ordered ids, hands the
list one page at a time, and re-runs the query, debounced, whenever the cache
changes; the counts on the badges are recomputed the same way.

## Sender statistics

`den_mail/senders.py` (#21) groups the cached mail by sender in one SQL
statement: counts, unread, first and last date, size, a List-Unsubscribe
flag (`has_unsubscribe`, a cache column), whether the user wrote to the
address (`correspondents`) and how much of their mail the user trashed or
destroyed unread (`sender_deletions`, which the engine increments as it
performs those actions). The score that orders the cleanup dialog is a
property of the dataclass. Bulk actions go through `SyncEngine.act_on_sender`,
which queries the server for every message from the address outside Trash
and Spam, caches the ones it never listed, and performs one action, so the
dialog's undo covers them all.

## Screener

With the screener on (#24), the engine checks each created message in the
Inbox before caching the batch: a sender the cache has never seen (the
`addresses`, `correspondents` and `screener` tables) is stored as pending in
`screener`, the message raises no notification, and the Screener view lists
the pending senders' mail. `ThreadListModel.set_screened` hides their
threads from the Inbox. Letting a sender through stores `allow`; screening
them out stores `block`, adds an archive rule for the address and runs it
over their mail now through `act_on_sender`.

## Rules

Client-side rules (`den_mail/rules.py`, #22) are stored in the config file
and run inside the sync engine: after `Email/changes` has cached a batch of
created messages, the ones in the Inbox are matched against every rule, the
rules that fired are combined into one `EmailAction` per set of rules (Trash
wins, archive removes the Inbox, labels add up) and performed through the
same path as a user action, before the batch is announced as new mail. The
engine reports which rules fired and the window counts the hits.

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
