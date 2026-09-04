#!/usr/bin/env python3
"""Draw the benchmark overview: six small bar charts, one per metric, three clients each,
as docs/benchmark/<date>-{light,dark}.svg, from result files (bench/results*.jsonl).

  bench/charts.py [results.jsonl ...]

Medians per client and metric over every row that has the metric; den-mail's warm rows
only. The charts are plain SVG (no scripts, GitHub renders them as images), a fixed
client order and colour per client, thin bars with a rounded data end, every value
labelled in text colour, and a dark variant stepped for the dark surface."""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "docs" / "benchmark"

CLIENTS = [("den-mail", "den-mail"), ("app", "Fastmail app"), ("web", "Fastmail web")]
METRICS = [
    ("inbox-listed_at_ms", "Launch to a usable inbox", "ms"),
    ("switch-listed_ms", "Switch to a folder of 2,900 conversations", "ms"),
    ("search-listed_ms", "Search", "ms"),
    ("open-painted_ms", "Open a message, first paint", "ms"),
    ("pss_end_mib", "Memory with a message open (PSS)", "MiB"),
    ("idle_cpu_pct", "CPU at rest with a message open", "% of a core"),
]
# libadwaita's own colours: the window and card surfaces and text of its light and dark
# styles, and GNOME's palette for the clients (Blue 4/3, Orange 4, Green 5; the darker
# orange and green also on the dark surface, where Orange 3 and Green 4 are too light).
THEMES = {
    "light": {"window": "#fafafb", "card": "#ffffff", "edge": "rgba(0,0,6,0.07)", "text": "rgba(0,0,0,0.8)",
              "muted": "rgba(0,0,0,0.55)", "series": ["#1c71d8", "#e66100", "#26a269"]},
    "dark": {"window": "#222226", "card": "#303034", "edge": "rgba(0,0,6,0.36)", "text": "#ffffff",
             "muted": "rgba(255,255,255,0.55)", "series": ["#3584e4", "#e66100", "#26a269"]},
}
PANEL_H = 150
FONT = "Cantarell, Inter, -apple-system, 'Segoe UI', system-ui, sans-serif"


def medians(files: list[Path]) -> dict[str, dict[str, float]]:
    rows = [json.loads(line) for f in files for line in f.read_text().splitlines() if line.strip()]
    out: dict[str, dict[str, float]] = {}
    for key, _title, _unit in METRICS:
        for client, _label in CLIENTS:
            vals = [r[key] for r in rows if r["client"] == client and key in r and r.get("run", 1) > 0
                    and (client != "den-mail" or r.get("mode") == "warm")]
            if vals:
                out.setdefault(client, {})[key] = statistics.median(vals)
    return out


def fmt(value: float, unit: str) -> str:
    if unit == "% of a core":
        return f"{value:.1f} %"
    return f"{value:,.0f} {unit}"


def panel(x: float, y: float, w: float, title: str, unit: str, values: dict[str, float], t: dict) -> str:
    """One metric: title, three labelled bars from a shared left baseline, lower is better."""
    label_w, value_w, bar_h, row_h = 108, 78, 18, 30
    top = 40
    parts = [f'<rect x="{x}" y="{y}" width="{w}" height="{PANEL_H}" rx="12" fill="{t["card"]}" stroke="{t["edge"]}"/>',
             f'<text x="{x + 16}" y="{y + 26}" font-size="14" font-weight="700" fill="{t["text"]}">{title}</text>']
    biggest = max(values.values()) if values else 1
    x += 16
    w -= 32
    plot_w = w - label_w - value_w
    baseline_x = x + label_w
    for i, (client, label) in enumerate(CLIENTS):
        cy = y + top + i * row_h
        colour = t["series"][i]
        parts.append(f'<text x="{x + label_w - 10}" y="{cy + bar_h - 5}" text-anchor="end" font-size="13" '
                     f'fill="{t["text"]}">{label}</text>')
        if client not in values:
            parts.append(f'<text x="{baseline_x + 8}" y="{cy + bar_h - 5}" font-size="13" fill="{t["muted"]}">no data</text>')
            continue
        v = values[client]
        length = max(6, plot_w * v / biggest)
        # 4px rounded data end, square at the baseline: a rounded bar with its left end squared off
        parts.append(f'<rect x="{baseline_x}" y="{cy}" width="{length:.1f}" height="{bar_h}" rx="4" fill="{colour}"/>')
        parts.append(f'<rect x="{baseline_x}" y="{cy}" width="4" height="{bar_h}" fill="{colour}"/>')
        parts.append(f'<text x="{baseline_x + length + 8:.1f}" y="{cy + bar_h - 5}" font-size="13" '
                     f'fill="{t["text"]}">{fmt(v, unit)}</text>')
    return "\n".join(parts)


def overview(data: dict[str, dict[str, float]], theme: str) -> str:
    t = THEMES[theme]
    cols, panel_w, panel_h, margin, gap = 2, 440, PANEL_H, 24, 16
    header = 64
    rows = (len(METRICS) + cols - 1) // cols
    width = margin * 2 + cols * panel_w + (cols - 1) * gap
    height = header + rows * panel_h + (rows - 1) * gap + margin
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
             f'font-family="{FONT}" role="img" aria-label="den-mail against Fastmail\'s desktop app and web client">',
             f'<rect width="{width}" height="{height}" fill="{t["window"]}"/>',
             f'<text x="{margin}" y="{margin + 4}" font-size="17" font-weight="700" fill="{t["text"]}">'
             f'den-mail against Fastmail\'s own clients</text>',
             f'<text x="{margin}" y="{margin + 24}" font-size="13" fill="{t["muted"]}">'
             f'Same account, machine and network, an idle desktop, medians of eight runs. Lower is better everywhere.</text>']
    # legend: swatch and name per client, in the fixed order
    lx = width - margin
    for i, (_client, label) in reversed(list(enumerate(CLIENTS))):
        lx -= 8 + len(label) * 7.2
        parts.append(f'<rect x="{lx - 18:.0f}" y="{margin - 6}" width="12" height="12" rx="6" fill="{t["series"][i]}"/>')
        parts.append(f'<text x="{lx:.0f}" y="{margin + 4}" font-size="13" fill="{t["muted"]}">{label}</text>')
        lx -= 30
    for n, (key, title, unit) in enumerate(METRICS):
        px = margin + (n % cols) * (panel_w + gap)
        py = header + (n // cols) * (panel_h + gap)
        values = {c: data[c][key] for c, _l in CLIENTS if c in data and key in data[c]}
        parts.append(panel(px, py, panel_w, title, unit, values, t))
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    files = [Path(a) for a in sys.argv[1:]] or [HERE / "results.jsonl"]
    data = medians(files)
    OUT.mkdir(parents=True, exist_ok=True)
    # dated names: GitHub's image proxy caches by URL, so a new round gets a new file
    stamp = next((m.group(1) for f in files if (m := re.search(r"(\d{4}-\d{2}-\d{2})", f.name))), "latest")
    for theme in THEMES:
        (OUT / f"{stamp}-{theme}.svg").write_text(overview(data, theme))
        print("wrote", OUT / f"{stamp}-{theme}.svg")
    for client, values in data.items():
        print(client, {k: round(v, 1) for k, v in values.items()})


if __name__ == "__main__":
    main()
