#!/usr/bin/env python3
"""Start a command and report how long its first window takes to appear on Hyprland.

usage: window-time.py <class-substring> -- <command...>
Polls `hyprctl clients -j` every 20 ms; prints the milliseconds from launch to the first
client whose class or initialClass contains the substring, then leaves the app running
and prints its PID. Works for any client, which makes the start-up number comparable
across den-mail, the Fastmail desktop app and a browser."""
import json
import subprocess
import sys
import time


def clients() -> list[dict]:
    out = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True).stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def main() -> int:
    if "--" not in sys.argv:
        print(__doc__)
        return 2
    i = sys.argv.index("--")
    needle, cmd = sys.argv[1].lower(), sys.argv[i + 1:]
    before = {c["address"] for c in clients()}
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # no pipe for the app to hold open
    deadline = t0 + 120
    while time.perf_counter() < deadline:
        for c in clients():
            if c["address"] in before:
                continue
            if needle in (c.get("class") or "").lower() or needle in (c.get("initialClass") or "").lower():
                ms = (time.perf_counter() - t0) * 1000
                print(json.dumps({"window_ms": round(ms), "pid": proc.pid, "class": c.get("class"),
                                  "address": c["address"]}))
                return 0
        time.sleep(0.02)
    print(json.dumps({"error": "no window", "pid": proc.pid}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
