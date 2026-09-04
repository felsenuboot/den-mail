#!/usr/bin/env python3
"""Summarise bench/results.jsonl as a Markdown table: median and best per client and metric."""
import json
import statistics
import sys
from pathlib import Path

rows = [json.loads(line) for line in Path(sys.argv[1] if len(sys.argv) > 1 else "bench/results.jsonl").read_text().splitlines() if line.strip()]
metrics = ["window_ms", "inbox-listed_at_ms", "switch-listed_ms", "search-listed_ms", "open-rendered_ms", "open-painted_ms", "rss_peak_mib"]
clients = sorted({(r["client"], r.get("mode", "")) for r in rows})
print("| metric | " + " | ".join(f"{c}{' ' + m if m else ''}" for c, m in clients) + " |")
print("| --- | " + " | ".join("---" for _ in clients) + " |")
for metric in metrics:
    cells = []
    for c, m in clients:
        vals = [r[metric] for r in rows if r["client"] == c and r.get("mode", "") == m and metric in r and r.get("run", 1) > 0]
        cells.append(f"{statistics.median(vals):.0f} (best {min(vals):.0f}, n={len(vals)})" if vals else "-")
    print(f"| {metric} | " + " | ".join(cells) + " |")
