#!/usr/bin/env python3
"""Benchmark the Fastmail web client and the Fastmail desktop app through the Chrome
DevTools Protocol, with the same scenario as bench/den-mail.sh (docs/BENCHMARK.md).

  bench/web.py web [runs]        Playwright's own Chromium with the persistent profile
                                 bench/profile/chromium (log in there once: bench/web.py login)
  bench/web.py app [runs]        the Flathub app, started with --remote-debugging-port
  bench/web.py login             open the profile for the one-time login
  bench/web.py probe             print the page's accessibility tree, to adjust the locators

Needs `pip install playwright` and `playwright install chromium` in a venv (the CDP
connection to the desktop app needs no browser download). Each run appends a JSON
line to bench/results.jsonl. The "listed" moments are DOM states: the folder's name
shows in the list header and the first conversation row is present. That is a little
earlier than the paint, which is why the frame-counted cross-check exists.
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

# Locators for Fastmail's web UI. They are role/text based so they survive class-name
# churn; run `bench/web.py probe` on the live page and adjust when one stops matching.
LIST_ROW = "[role=listbox] [role=option], [role=list] [role=listitem]"  # a conversation row
LIST_TITLE = "h1, h2, [role=heading]"                                    # the folder name above the list


def now_ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)


def wait_list(page, name: str, timeout: float = 60) -> None:
    """The list header says `name` and at least one conversation row exists."""
    page.wait_for_function(
        """([title, row, name]) => [...document.querySelectorAll(title)].some(h => h.textContent.trim().startsWith(name))
                                   && document.querySelector(row) !== null""",
        arg=[LIST_TITLE, LIST_ROW, name], timeout=timeout * 1000)


def scenario(page, t0: float, row: dict) -> None:
    wait_list(page, "Inbox")
    row["inbox-listed_at_ms"] = now_ms(t0)
    t = time.perf_counter()
    page.get_by_role("link", name=FOLDER, exact=True).first.click()
    wait_list(page, FOLDER)
    row["switch-listed_ms"] = now_ms(t)
    t = time.perf_counter()
    box = page.get_by_role("searchbox").first
    box.click()
    box.fill(SEARCH)
    box.press("Enter")
    page.wait_for_function("row => document.querySelector(row) !== null", arg=LIST_ROW, timeout=60000)
    page.wait_for_load_state("networkidle")
    row["search-listed_ms"] = now_ms(t)
    page.get_by_role("link", name="Inbox", exact=True).first.click()
    wait_list(page, "Inbox")
    t = time.perf_counter()
    page.locator(LIST_ROW).nth(OPEN_INDEX).click()
    # the message body: Fastmail renders it in an iframe; wait for its document to be complete
    page.wait_for_function("() => [...document.querySelectorAll('iframe')].some(f => f.contentDocument && f.contentDocument.readyState === 'complete' && f.contentDocument.body && f.contentDocument.body.innerText.trim().length > 0)", timeout=60000)
    row["open-painted_ms"] = now_ms(t)


def tree_rss_mib(pid: int) -> int:
    out = subprocess.run([str(HERE / "tree-rss.sh"), str(pid)], capture_output=True, text=True).stdout.strip()
    return int(out or 0)


def run_web(runs: int) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        for i in range(1, runs + 1):
            t0 = time.perf_counter()
            ctx = p.chromium.launch_persistent_context(str(PROFILE), headless=False, viewport=None,
                                                       args=[f"--window-size={WIDTH},{HEIGHT}"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://app.fastmail.com/mail/Inbox/")
            row = {"client": "web", "run": i, "window_ms": now_ms(t0)}
            scenario(page, t0, row)
            row["rss_peak_mib"] = tree_rss_mib(ctx.browser.process.pid) if ctx.browser else 0
            ctx.close()
            RESULTS.open("a").write(json.dumps(row) + "\n")
            print(row)


def run_app(runs: int) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        for i in range(1, runs + 1):
            t0 = time.perf_counter()
            win = subprocess.run([sys.executable, str(HERE / "window-time.py"), "fastmail", "--", "sh", "-c", APP_CMD],
                                 capture_output=True, text=True).stdout.strip().splitlines()[-1]
            info = json.loads(win)
            browser = None
            for _ in range(100):  # the debugging port comes up shortly after the window
                try:
                    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
                    break
                except Exception:  # noqa: BLE001 - not listening yet
                    time.sleep(0.2)
            if browser is None:
                raise SystemExit("could not connect to the desktop app; did it start with --remote-debugging-port?")
            page = browser.contexts[0].pages[0]
            row = {"client": "app", "run": i, "window_ms": info["window_ms"]}
            scenario(page, t0, row)
            row["rss_peak_mib"] = tree_rss_mib(info["pid"])
            page.evaluate("window.close()")
            subprocess.run(["pkill", "-P", str(info["pid"])], check=False)
            os.kill(info["pid"], 15)
            time.sleep(3)
            RESULTS.open("a").write(json.dumps(row) + "\n")
            print(row)


def login() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE), headless=False, viewport=None)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://app.fastmail.com/")
        input("log in (with 2FA) in that window, wait for the inbox, then press Enter here: ")
        ctx.close()


def probe(target: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        if target == "app":
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            page = browser.contexts[0].pages[0]
        else:
            ctx = p.chromium.launch_persistent_context(str(PROFILE), headless=False, viewport=None)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://app.fastmail.com/mail/Inbox/")
            page.wait_for_timeout(8000)
        print(page.locator("body").aria_snapshot())


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "help"
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    {"web": lambda: run_web(runs), "app": lambda: run_app(runs), "login": login,
     "probe": lambda: probe(sys.argv[2] if len(sys.argv) > 2 else "web")}.get(what, lambda: print(__doc__))()
