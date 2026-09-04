# Benchmark: den-mail against Fastmail's web client and desktop app

Three clients on the same account, the same machine, the same network and the
same window size, running the same scenario several times. Everything runs on
the real desktop session, not the headless harness, because the headless
compositor renders in software and would penalise nothing but den-mail.

## What is measured

| Metric | Meaning | den-mail | web / app |
| --- | --- | --- | --- |
| `window_ms` | launch to the first window on screen | `bench/window-time.py` polls Hyprland | same script |
| `inbox-listed_at_ms` | launch to a usable inbox list | `timing: inbox-listed` from `DEN_MAIL_TIMING=1` | the list header says Inbox and a row exists |
| `switch-listed_ms` | click on the Archive folder to its list | `switch-start` → `switch-listed` | same DOM condition for Archive |
| `search-listed_ms` | search text to results | `search-start` → `search-listed` | first row after Enter, network idle |
| `open-rendered_ms` | select a conversation to body handed to the renderer | `open-start` → `open-rendered` | – |
| `open-painted_ms` | … to the body being loaded | `open-painted` (WebKit load finished) | the message iframe is complete and has text |
| `rss_peak_mib` | resident memory of the process tree, peak over the run | WebKit helpers included | Electron / Chromium helpers included |

The "listed" moments for the web client and the app are DOM states, which land a
few frames before the paint. For a check that treats all three alike, record the
screen (`wf-recorder`, 75 fps on this monitor) during one run each and count the
frames between the click and the changed list; if that agrees with the numbers
within a couple of frames, the DOM conditions are fair.

The scenario, shared by the three drivers (the `BENCH_*` variables change it):

1. start, wait for the Inbox
2. switch to `BENCH_FOLDER` (default Archive)
3. search for `BENCH_SEARCH` (default invoice)
4. back to the Inbox, open conversation `BENCH_OPEN_INDEX` (default the first)
5. quit

den-mail runs the scenario in a separate profile with `mark_read_on_open` off;
the web client and the app do mark the opened conversation read, so choose an
index that is already read, or restore it afterwards.

## What we need first

- [ ] A Fastmail API token with the Mail scope, exported as `DEN_MAIL_TOKEN`
      for the den-mail runs (the app's own keyring token also works:
      `secret-tool lookup app den-mail account <account>`).
- [ ] The desktop app: `flatpak install flathub com.fastmail.Fastmail`, started
      once by hand and logged in. The bench starts it with
      `--remote-debugging-port=9222`, which Electron accepts.
- [ ] Playwright for the web client and for driving the app:
      `python -m venv bench/venv && bench/venv/bin/pip install playwright &&
      bench/venv/bin/playwright install chromium` (about 150 MB), then
      `bench/venv/bin/python bench/web.py login` for the one-time login with
      2FA into `bench/profile/chromium`. Firefox-based browsers such as Zen
      cannot be driven over CDP, hence Playwright's own Chromium.
- [ ] One probe of the live UI: `bench/web.py probe` prints the accessibility
      tree, from which the locators in `bench/web.py` (folder link, search box,
      list rows, list heading) are confirmed or adjusted. Fastmail's markup is
      not documented, so this step is expected.
- [ ] The scenario's constants: which folder, which search word, which
      conversation to open (an already read one with an HTML body).
- [ ] Twenty minutes of a quiet machine: no other heavy apps, no typing or
      mouse while a run is on, all three clients at 1600x1000 on the main
      monitor. New mail arriving during a run shifts the list; note it.
- [ ] Optional: `wf-recorder` for the frame-counted cross-check.

## Running

```
export DEN_MAIL_TOKEN=…
bench/den-mail.sh 5 warm          # primes the cache once, then 5 runs
bench/den-mail.sh 5 cold          # wipes the cache before every run (network bound)
bench/venv/bin/python bench/web.py web 5
bench/venv/bin/python bench/web.py app 5
bench/report.py                   # medians and best values as a Markdown table
```

Every run appends one JSON line to `bench/results.jsonl` (ignored by git);
`bench/logs/` keeps den-mail's logs. Warm and cold are separate rows for
den-mail; the web client and the app keep their own caches between runs, which
is their warm state, so compare them with den-mail's warm numbers.

## Fairness notes

- Same account state for all three: run them back to back, in the same hour.
- Cold starts are network bound for every client; warm starts show the client.
- The web client and the app share one code base (the app is Electron around
  the web client), so their numbers should be close; a large gap points at the
  measurement rather than the clients.
- den-mail's `open-rendered` has no counterpart: the web client only exposes
  the moment its message iframe is complete, so compare `open-painted`.
- Memory is the whole process tree. For den-mail that is Python plus WebKit's
  web and network processes; for the others it is Electron's or Chromium's
  helpers, which is what the user pays for as well.

## Results, 2026-09-04

Account ich@felixschramm.eu (Inbox 39 conversations, Archive 2,901), Hyprland
on a 3440x1440 monitor, all clients at 1600x1000, five runs each, medians with
the best run in brackets. den-mail is commit b0ed1c6 on Python 3.14, GTK 4.22,
WebKitGTK 2.52; the desktop app is Flathub 1.7.0 (Chrome 150); the web client
ran in Playwright's Chromium 150.

| metric | app | den-mail cold | den-mail warm | web |
| --- | --- | --- | --- | --- |
| window_ms | 1252 (best 1248, n=5) | 719 (best 693, n=5) | 743 (best 720, n=5) | 320 (best 312, n=5) |
| inbox-listed_at_ms | 1434 (best 1424, n=5) | 2461 (best 2379, n=5) | 578 (best 566, n=5) | 877 (best 860, n=5) |
| switch-listed_ms | 207 (best 202, n=5) | 448 (best 442, n=5) | 606 (best 597, n=5) | 128 (best 118, n=5) |
| search-listed_ms | 226 (best 189, n=5) | 216 (best 201, n=5) | 198 (best 175, n=5) | 189 (best 169, n=5) |
| open-rendered_ms | 167 (best 118, n=5) | 212 (best 197, n=5) | 86 (best 84, n=5) | 84 (best 79, n=5) |
| open-painted_ms | 208 (best 155, n=5) | 420 (best 404, n=5) | 302 (best 296, n=5) | 159 (best 146, n=5) |
| rss_peak_mib | 1036 (best 995, n=5) | 1122 (best 1120, n=5) | 1044 (best 1043, n=5) | 1377 (best 1329, n=5) |

Reading the numbers:

- `window_ms` is not comparable across the three: the web value is Chromium
  started by Playwright, the app value includes `flatpak run`, den-mail's is
  the interpreter plus GTK. `inbox-listed_at_ms` is the fair start-up figure.
- den-mail warm shows a usable inbox in 0.58 s, before either Fastmail client.
  Cold (no local cache) it needs 2.5 s, most of it the first sync.
- The folder switch is den-mail's weak spot: 0.6 s warm for the 2,901-item
  Archive against 0.13 s (web) and 0.21 s (app). The list is served from the
  local cache, so this is the thread model being rebuilt on the main thread
  (`set_email_ids`, one summary query per conversation), not the network.
- Search is a wash: every client waits for the server.
- Opening a message: den-mail hands the body to WebKit as fast as the web
  client shows the subject (86 ms), but WebKit's own load to the first paint
  takes 0.3 s against 0.16 s for the web client, where the body arrives in
  the already running renderer.
- Memory is the sum of resident sizes over the process tree, which counts
  shared pages once per process; it flatters nobody in particular but is
  not a precise figure. All three sit around 1 GiB; den-mail's is Python
  plus WebKit's web and network processes.
