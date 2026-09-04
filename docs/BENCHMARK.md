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
| `pss_end_mib` | proportional set size of the process tree with the opened message on screen: shared pages counted once, the honest RAM figure | same | same |
| `cpu_total_s` | CPU seconds (user + system) of the process tree for the whole scenario | same | same |
| `idle_cpu_pct` | CPU use over the 20 s rest with the message open: what the client burns doing nothing | same | same |

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
on a 3440x1440 monitor, all clients at 1600x1000, an otherwise idle machine
(load 0.08 at the start, notifications off, idle daemon paused), five runs
each, medians with the best run in brackets. den-mail is a88f9f9 on Python
3.14, GTK 4.22 and WebKitGTK 2.52; the desktop app is Flathub 1.7.0 (Chrome
150); the web client ran in Playwright's Chromium 150 with a logged-in profile.

| ms unless noted | den-mail warm | den-mail cold | Fastmail app | Fastmail web |
| --- | --- | --- | --- | --- |
| launch to a usable inbox | **300** (296) | 2542 (2391) | 1412 (1402) | 868 (853) |
| switch to Archive, 2,901 conversations | 118 (116) | 460 (456) | 209 (197) | **112** (109) |
| search "rechnung" | **177** (168) | 217 (183) | 224 (207) | 199 (169) |
| open a message: subject shown | **17** (17) | 128 (98) | 110 (106) | 75 (50) |
| open a message: body painted | **66** (64) | 177 (169) | 136 (127) | 154 (142) |
| memory, PSS with the message open, MiB | **341** (340) | 381 (376) | 642 (641) | 511 (509) |
| memory, RSS summed over the tree, MiB | 1028 | 1110 | 994 | 1310 |
| CPU seconds for the scenario | **2.3** | 4.0 | 4.6 | 3.0 |
| CPU over 20 s at rest, % | **0.1** | 0.4 | 2.8 | 0.6 |

An earlier round the same morning, while the machine was in use, put den-mail's
folder switch at 606 ms and the first paint at 302 ms; both were den-mail's own
faults and are fixed (#36): the switch mark waited for the server's refresh
behind a list that was already on screen, and every message spawned its own
WebKit web process. Message views now share one process, warmed at start-up.

Reading the numbers:

- `window_ms` (launch to the first window, in `results.jsonl`) is not
  comparable across the three: the web value is Chromium started by Playwright,
  the app value includes `flatpak run`, den-mail's is the interpreter plus GTK.
  The inbox row is the fair start-up figure.
- den-mail warm shows a usable inbox in 0.3 s, before either Fastmail client.
  Cold, with no local data, it needs 2.5 s, almost all of it the first sync;
  cold folder switches are server fetches for every client.
- Search is the server for everyone.
- Opening a message: the subject is up in 17 ms and the body painted in 66 ms,
  a third of the web client's time, now that the web process already exists.
- Memory: PSS counts shared pages once and is the figure to compare; the RSS
  row shows why per-process sums mislead for multi-process apps. den-mail is
  Python plus WebKit's web and network processes; the app is Electron, the
  web client is Chromium with one tab.
- CPU: the scenario costs den-mail 2.3 CPU seconds warm (the cold run's 4 s
  is the first sync), the web client 3.1 and the Electron app 4.6. At rest
  with a message open den-mail uses 0.1 % of a core, the web client 0.6 %,
  the app 2.8 %.
- The "listed" moments of the web client and the app are DOM states a few
  frames before the paint, so their numbers are, if anything, flattering.
