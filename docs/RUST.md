# Would a Rust port pay off?

Asked on 2026-09-05, with the thought that Rust "may be quicker". This is the
assessment, so the question does not have to be reopened from scratch.

## What the app is today

| Area | Lines | Notes |
| --- | --- | --- |
| UI (`den_mail/ui`) | 7 970 | GTK4 and libadwaita widgets, WebKitGTK for HTML mail |
| Store (`den_mail/store`) | 2 810 | SQLite cache, sync worker, actions and undo, outbox |
| Top level (`den_mail/*.py`) | 2 975 | config, avatars, rules, senders, views, lock, summaries, tips |
| HTML (`den_mail/html`) | 1 360 | sanitiser, text conversion, schema.org |
| Classify (`den_mail/classify`) | 680 | rules, naive Bayes, label models |
| JMAP (`den_mail/jmap`) | 640 | client, types, push |
| LLM (`den_mail/llm`) | 490 | providers |
| Tests | 4 670 | fake JMAP server, engine tests, UI smoke |

Runtime dependencies: PyGObject and the GTK stack. Start to a listed inbox is
about 250 ms; resident memory about 320 MB without WebKit, of which an empty
libadwaita window is 200 MB.

## Where the time goes

Nothing the user waits for is CPU-bound Python. The waits are the network
(JMAP round trips, push), WebKit rendering an HTML mail, and SQLite, which
runs as C in both languages. The list view, the sidebar and the dialogs are
GTK widgets that Python only configures; scrolling, layout and drawing happen
in C either way. The Bayes and label models tokenise a few thousand messages
at retrain time on the worker thread, once every few hours, in a fraction of
a second.

A Rust port would therefore make the app start faster (the interpreter and
the PyGObject bindings cost roughly 100 ms and 100 MB) and nothing else the
user can feel. The memory that remains is GTK, libadwaita and WebKit.

## What a port would cost

- Every line of UI is rewritten: 8 000 lines of widget code become perhaps
  10 000 lines of gtk4-rs and libadwaita-rs, with the borrow checker in the
  way of the callback-heavy style GTK wants (`Rc<RefCell<…>>` or
  `glib::clone!` around every handler). This is the part that took most of the
  development time in Python; it takes longer in Rust, not less.
- The engine (sync, cache, actions, undo, outbox, offline drafts) is 2 800
  lines of careful state handling with a test suite against a fake server.
  Rewriting it means re-finding every edge case that the tests encode.
- WebKitGTK from Rust works (the `webkit6` crate) but is less travelled than
  PyGObject's binding; the sanitiser and the `fmcid` scheme handler would need
  re-validation.
- Packaging changes from "python and the GTK stack" to a compiled binary per
  architecture; the Flatpak manifest, the AUR package and CI all change.
- Development speed drops for a long while: what is a five-minute change in
  Python (a new preference row, a new provider, a wording fix) is a compile
  cycle and a type puzzle in Rust. Tonight's fifty pull requests would not
  have happened in Rust.

Estimate: two to three months of full-time work to reach parity, with a
freeze on features meanwhile, for a start-up gain of about 100 ms and a
memory gain of about 100 MB.

## What "quicker" could mean instead

If start-up or memory ever matter, the cheaper moves are:

- Import less at start: WebKit and the HTML renderer are already lazy; the
  remaining big import is PyGObject itself.
- Keep the app running in the background (#2, shipped): a second start is a
  window present, not a start.
- Let the engine do less at bootstrap: measure a cold cache with
  `DEN_MAIL_TIMING=1` and trim the backfill jobs that follow it.

If a single hot spot ever appears in a profile (it has not), that one module
can move to a Rust extension through PyO3 without touching the UI.

## Recommendation

No port. The app is fast where the user notices, the weight is in libraries
a port would keep, and the rewrite would cost months and stop the polish
work that is going on now. Revisit only if a profile shows Python itself in
the way, and then port that module, not the app.
