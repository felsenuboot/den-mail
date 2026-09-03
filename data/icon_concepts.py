#!/usr/bin/env python3
"""Generate three app-icon concepts (badge, disc, envelope) in Sanzo Wada
colour combinations, following the GNOME HIG app-icon conventions:
128px canvas, 2px grid, content between y=8 and the baseline y=120, flat
colours, a top surface plus a 4px darker front profile, no outer shadows.

Writes SVGs and an index.html preview sheet to data/icon-concepts/.
"""
from __future__ import annotations

import math
from pathlib import Path

OUT = Path(__file__).resolve().parent / "icon-concepts"
PROFILE = 4          # height of the front profile below the top surface

# Every colour below is from Wada's "A Dictionary of Color Combinations"
# (felsenuboot/color-combinator, data/colors.json).  Each combo is a real
# combination id from combinations.json plus a darker Wada blue for the
# front profile and a light Wada colour for the chevrons.
COMBOS = {
    "lyons": dict(  # combination 22: Yellow + Deep Lyons Blue
        top="#1c4286", profile="#12354e", rim="#5a82b3",
        light="#ffffff", light_profile="#97acc8", star="#fff200",
        label="#22 Deep Lyons Blue / Yellow / White"),
    "vandar": dict(  # combination 151: Sulpher Yellow + Yellow Orange + Vandar Poel's Blue
        top="#064f6e", profile="#12354e", rim="#0093a5",
        light="#f5ecc2", light_profile="#a5c8d1", star="#f99d1b",
        label="#151 Vandar Poel's Blue / Yellow Orange / Sulpher Yellow"),
    "antwarp": dict(  # combination 114: Orange Yellow + Antwarp Blue
        top="#007190", profile="#064f6e", rim="#78cdd0",
        light="#ffffff", light_profile="#a7d4e4", star="#fcb315",
        label="#114 Antwarp Blue / Orange Yellow / White"),
}


def fmt(pts):
    return " ".join(f"{x:g},{y:g}" for x, y in pts)


def poly(pts, fill, dy=0, r=0.0, extra=""):
    """Polygon; r>0 rounds the corners by stroking with a round join."""
    p = [(x, y + dy) for x, y in pts]
    s = f'<polygon points="{fmt(p)}" fill="{fill}" {extra}'
    if r:
        s += f' stroke="{fill}" stroke-width="{2*r}" stroke-linejoin="round"'
    return s + "/>"


def chevron(x, y, h, t, dx=0, dy=0):
    """Right-pointing chevron with its notch at (x, y+h/2); arm thickness t."""
    pts = [(0, 0), (t, 0), (t + h / 2, h / 2), (t, h), (0, h), (h / 2, h / 2)]
    return [(x + px + dx, y + py + dy) for px, py in pts]


def star4(cx, cy, R, r):
    pts = []
    for i in range(8):
        a = math.pi / 4 * i - math.pi / 2
        rad = R if i % 2 == 0 else r
        pts.append((round(cx + rad * math.cos(a), 1), round(cy + rad * math.sin(a), 1)))
    return pts


def star5(cx, cy, R, r):
    pts = []
    for i in range(10):
        a = math.pi / 5 * i - math.pi / 2
        rad = R if i % 2 == 0 else r
        pts.append((round(cx + rad * math.cos(a), 1), round(cy + rad * math.sin(a), 1)))
    return pts


def chevrons(c, x0, y0, h, t, gap, n=3, r=0.0, profile=2):
    """n chevrons with a light top surface and a thin darker profile."""
    out = []
    step = t + gap
    for i in range(n):
        pts = chevron(x0 + i * step, y0, h, t)
        if profile:
            out.append(poly(pts, c["light_profile"], dy=profile, r=r))
    for i in range(n):
        pts = chevron(x0 + i * step, y0, h, t)
        out.append(poly(pts, c["light"], r=r))
    return out


def svg(body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" '
            'viewBox="0 0 128 128">\n' + "\n".join(body) + "\n</svg>\n")


# ---------------------------------------------------------------- concepts
def badge(c):
    """Rounded square, four-point star above three bold chevrons."""
    x, y, w, h, rr = 12, 8, 104, 108, 18
    body = [
        f'<rect x="{x}" y="{y + PROFILE}" width="{w}" height="{h}" rx="{rr}" fill="{c["profile"]}"/>',
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rr}" fill="{c["top"]}"/>',
        poly(star4(64, 36, 16, 5), c["star"]),
    ]
    body += chevrons(c, 20, 58, 44, 12, 12, r=1.0)
    return svg(body)


def disc(c):
    """Circle with a lighter rim, rounded chevrons and a four-point star."""
    cx, cy, R = 64, 62, 54
    body = [
        f'<circle cx="{cx}" cy="{cy + PROFILE}" r="{R}" fill="{c["profile"]}"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="{c["rim"]}"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{R - 6}" fill="{c["top"]}"/>',
        poly(star4(64, 36, 14, 4.5), c["star"]),
    ]
    body += chevrons(c, 32, 56, 36, 10, 8, r=2.0)
    return svg(body)


def envelope(c):
    """Landscape envelope: chevrons on the flap, star in a ring at the tip."""
    x0, y0, x1, y1 = 8, 24, 120, 116   # top surface bounds
    rr = 8
    tip_y = 78                          # where the flap meets
    body = [
        # front profile
        f'<rect x="{x0}" y="{y0 + PROFILE}" width="{x1 - x0}" height="{y1 - y0}" rx="{rr}" fill="{c["profile"]}"/>',
        # envelope body
        f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="{rr}" fill="{c["top"]}"/>',
        # lower V fold, a shade darker than the body
        f'<clipPath id="env"><rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="{rr}"/></clipPath>',
        poly([(x0, y1), (64, tip_y - 4), (x1, y1)], c["profile"], extra='clip-path="url(#env)"'),
        # flap, lighter than the body (lit from above)
        poly([(x0, y0), (x1, y0), (64, tip_y)], c["rim"], extra='clip-path="url(#env)"'),
        # ring + star at the flap tip
        f'<circle cx="64" cy="{tip_y}" r="18" fill="{c["profile"]}"/>',
        f'<circle cx="64" cy="{tip_y - 2}" r="18" fill="{c["light"]}"/>',
        f'<circle cx="64" cy="{tip_y - 2}" r="14" fill="{c["top"]}"/>',
        poly(star5(64, tip_y - 1, 10, 4.2), c["star"]),
    ]
    body += chevrons(c, 34, 32, 20, 8, 8, r=0.5, profile=2)
    return svg(body)


CONCEPTS = {"badge": badge, "disc": disc, "envelope": envelope}


def main():
    OUT.mkdir(exist_ok=True)
    rows = []
    for cname, fn in CONCEPTS.items():
        cells = []
        for kname, combo in COMBOS.items():
            name = f"{cname}-{kname}.svg"
            (OUT / name).write_text(fn(combo))
            cells.append(
                f'<figure><img src="{name}" width="128"><img src="{name}" width="64">'
                f'<img src="{name}" width="48"><img src="{name}" width="32">'
                f'<figcaption>{name}<br><small>{combo["label"]}</small></figcaption></figure>')
        rows.append(f"<section><h2>{cname}</h2><div class=row>{''.join(cells)}</div></section>")
    html = """<!doctype html><meta charset=utf-8><title>Icon concepts</title>
<style>
body{margin:0;font:14px system-ui}
.strip{padding:24px 32px}.light{background:#f6f5f4;color:#241f31}.dark{background:#241f31;color:#f6f5f4}
.row{display:flex;gap:40px;flex-wrap:wrap}figure{margin:0;display:flex;align-items:flex-end;gap:12px}
figcaption{margin-left:12px;font-size:12px}h2{margin:0 0 12px;text-transform:capitalize}
section{margin-bottom:32px}
</style>
""" + f'<div class="strip light">{"".join(rows)}</div><div class="strip dark">{"".join(rows)}</div>\n'
    (OUT / "index.html").write_text(html)
    print(f"wrote {len(CONCEPTS) * len(COMBOS)} SVGs + index.html to {OUT}")


if __name__ == "__main__":
    main()
