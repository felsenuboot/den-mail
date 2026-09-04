#!/usr/bin/env python3
"""Benchmark the Fastmail web client and the Fastmail desktop app through the Chrome
DevTools Protocol, with the same scenario as bench/den-mail.sh (docs/BENCHMARK.md).

  bench/web.py web [runs]        Playwright's own Chromium with the persistent profile
                                 bench/profile/chromium (log in there once: bench/web.py login)
  bench/web.py app [runs]        the Flathub app, started with --remote-debugging-port
  bench/web.py login             open the profile for the one-time login
  bench/web.py probe [web|app]   print the page's accessibility tree, to adjust the locators

Needs `pip install playwright` and `playwright install chromium` in a venv (the CDP
connection to the desktop app needs no browser download). Each run appends a JSON
line to bench/results.jsonl. The "listed" moments are DOM states (the URL names the
folder and the first conversation row has changed), a little earlier than the paint,
which is why the frame-counted cross-check in the document exists.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE = HERE / "profile" / "chromium"
RESULTS = HERE / "results.jsonl"
FOLDER = os.environ.get("BENCH_FOLDER", "Archive")
SEARCH = os.environ.get("BENCH_SEARCH", "invoice")
OPEN_INDEX = int(os.environ.get("BENCH_OPEN_INDEX", "0"))
WIDTH, HEIGHT = int(os.environ.get("BENCH_WIDTH", "1600")), int(os.environ.get("BENCH_HEIGHT", "1000"))
CDP_PORT = int(os.environ.get("BENCH_CDP_PORT", "9222"))
APP_CMD = os.environ.get("BENCH_APP_CMD", f"flatpak run com.fastmail.Fastmail --remote-debugging-port={CDP_PORT}")

# Fastmail's web UI (probed 2026-09-04): the conversation list is a grid of rows whose
# cells are sender, subject, preview and date; folders are links whose href carries the
# folder path; the search field is the textbox "Search mail"; the reading pane's level-1
# heading is the subject. Re-probe with `bench/web.py probe` when one stops matching.
ROW = "[role=grid] [role=row]"


def now_ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)


def step(what: str) -> None:
    if os.environ.get("BENCH_VERBOSE"):
        print(f"  … {what}", flush=True)


def first_row(page) -> str:
    rows = page.locator(ROW)
    return rows.first.inner_text() if rows.count() else ""


def wait_rows(page, before: str, timeout: float = 60) -> None:
    """A conversation row exists and the first one is not the one shown before."""
    page.wait_for_function(
        "([sel, before]) => { const r = document.querySelector(sel); return !!r && r.innerText.trim() !== before.trim(); }",
        arg=[ROW, before], timeout=timeout * 1000)


def folder_link(page, name: str):
    return page.locator(f'a[href*="/mail/{name}/"]').first


def scenario(page, t0: float, row: dict) -> None:
    step("waiting for the inbox url")
    page.wait_for_url("**/mail/Inbox/**", timeout=120000)
    step("waiting for inbox rows")
    wait_rows(page, "", timeout=120)
    row["inbox-listed_at_ms"] = now_ms(t0)
    shown = first_row(page)
    t = time.perf_counter()
    step(f"clicking {FOLDER}")
    folder_link(page, FOLDER).click()
    page.wait_for_url(f"**/mail/{FOLDER}/**")
    wait_rows(page, shown)
    row["switch-listed_ms"] = now_ms(t)
    shown = first_row(page)
    t = time.perf_counter()
    step("searching")
    box = page.get_by_role("textbox", name="Search mail")
    box.click()
    box.fill(SEARCH)
    box.press("Enter")
    page.wait_for_url("**/mail/search:**")
    wait_rows(page, shown)
    row["search-listed_ms"] = now_ms(t)
    shown = first_row(page)
    step("back to the inbox")
    folder_link(page, "Inbox").click()
    page.wait_for_url("**/mail/Inbox/**")
    wait_rows(page, shown)
    target = page.locator(ROW).nth(OPEN_INDEX)
    cells = target.locator("[role=gridcell]").all_inner_texts()
    subject = (cells[2] if len(cells) > 2 else cells[-1]).strip()[:40]
    preview = (cells[3] if len(cells) > 3 else "").strip()[:30]
    t = time.perf_counter()
    step("opening the conversation")
    target.click()
    # rendered: the reading pane's heading is the subject; painted: the body text is on the
    # page (inline or in an iframe) and a frame has been drawn since
    page.wait_for_function("s => [...document.querySelectorAll('h1')].some(h => h.textContent.trim().startsWith(s))",
                           arg=subject, timeout=60000)
    row["open-rendered_ms"] = now_ms(t)
    page.wait_for_function(
        """p => { if (!p) return true;
                 const inDoc = d => d && d.body && d.body.innerText.includes(p);
                 const frames = [...document.querySelectorAll('iframe')].some(f => { try { return inDoc(f.contentDocument); } catch (e) { return false; } });
                 return frames || document.body.innerText.split(p).length > 2; }""",
        arg=preview, timeout=60000)
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    row["open-painted_ms"] = now_ms(t)


def tree_memory(pid: int) -> dict:
    """RSS and PSS of the process tree in MiB, with the opened message on screen."""
    out = subprocess.run([str(HERE / "tree-rss.sh"), str(pid)], capture_output=True, text=True).stdout.split()
    rss, pss = (int(out[0]), int(out[1])) if len(out) == 2 else (0, 0)
    return {"rss_peak_mib": rss, "pss_end_mib": pss}


def chromium_root_pid() -> int:
    """The Chromium of the bench profile: the process with the profile path whose parent has not."""
    out = subprocess.run(["pgrep", "-f", str(PROFILE)], capture_output=True, text=True).stdout.split()
    pids = {int(x) for x in out}
    for pid in sorted(pids):
        ppid = int(open(f"/proc/{pid}/stat").read().rsplit(") ", 1)[1].split()[1])
        if ppid not in pids:
            return pid
    return min(pids) if pids else 0


def record(row: dict) -> None:
    with RESULTS.open("a") as f:
        f.write(json.dumps(row) + "\n")
    print(row, flush=True)


def run_web(runs: int) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        for i in range(1, runs + 1):
            t0 = time.perf_counter()
            ctx = p.chromium.launch_persistent_context(str(PROFILE), headless=False, viewport=None,
                                                       args=[f"--window-size={WIDTH},{HEIGHT}"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://app.fastmail.com/mail/Inbox/", wait_until="commit")
            row = {"client": "web", "run": i, "window_ms": now_ms(t0)}
            scenario(page, t0, row)
            row.update(tree_memory(chromium_root_pid()))
            ctx.close()
            record(row)
            time.sleep(2)


def run_app(runs: int) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        for i in range(1, runs + 1):
            t0 = time.perf_counter()
            win = subprocess.run([sys.executable, str(HERE / "window-time.py"), "fastmail", "--", "sh", "-c", APP_CMD],
                                 capture_output=True, text=True).stdout.strip().splitlines()[-1]
            info = json.loads(win)
            browser = None
            for _ in range(300):  # the debugging port comes up shortly after the window (60 s at most)
                try:
                    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
                    break
                except Exception:  # noqa: BLE001 - not listening yet
                    time.sleep(0.2)
            if browser is None:
                raise SystemExit("could not connect to the desktop app; did it start with --remote-debugging-port?")
            page = next(pg for ctx in browser.contexts for pg in ctx.pages if "fastmail" in pg.url)
            row = {"client": "app", "run": i, "window_ms": info["window_ms"]}
            scenario(page, t0, row)
            row.update(tree_memory(info["pid"]))
            browser.close()
            subprocess.run(["flatpak", "kill", "com.fastmail.Fastmail"], check=False)
            time.sleep(4)
            record(row)


def login() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE), headless=False, viewport=None)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://app.fastmail.com/")
        print("log in (with 2FA) in that window; it closes by itself once the inbox shows", flush=True)
        page.wait_for_url("**/mail/**", timeout=15 * 60 * 1000)
        page.wait_for_timeout(5000)  # let the session settle before the profile is saved
        ctx.close()


def probe(target: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        if target == "app":
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            page = browser.contexts[0].pages[0]
        else:
            ctx = p.chromium.launch_persistent_context(str(PROFILE), headless=True, viewport={"width": WIDTH, "height": HEIGHT})
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://app.fastmail.com/mail/Inbox/")
            page.wait_for_timeout(10000)
        print(page.locator("body").aria_snapshot())


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "help"
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    {"web": lambda: run_web(runs), "app": lambda: run_app(runs), "login": login,
     "probe": lambda: probe(sys.argv[2] if len(sys.argv) > 2 else "web")}.get(what, lambda: print(__doc__))()
