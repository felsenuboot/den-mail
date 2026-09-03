#!/usr/bin/env python3
"""Generate the app icon: a minimal envelope in Fastmail's brand blues.

Follows the GNOME HIG app-icon conventions: 128 px canvas on a 2 px grid,
content between y=24 and the baseline y=120, flat colours without
gradients, a top surface plus a 4 px darker front profile, no shadow
outside the silhouette.

The envelope is four flat triangles that meet at exactly one centre point
(lit flap, two side panels, darker bottom fold) with a small yellow dot on
the right panel, placed clear of every seam.  The colours are the
--brand-color-* ramp from fastmail.com's stylesheet.

The symbolic variant is a 2 px-stroke envelope outline on the 16 px grid.
"""

FLAP = "#3385c6"     # blue-80
SIDE = "#0067b9"     # blue-100, the brand blue
FOLD = "#245d8a"     # blue-130
PROFILE_C = "#194262"  # blue-150
DOT = "#ffc107"      # brand yellow

PROFILE = 4          # front profile height
X0, Y0, X1, Y1 = 8, 24, 120, 116   # top surface bounds
RX = 8               # corner radius
CX, CY = 64, (Y0 + Y1) // 2       # the point all four triangles share
DOT_AT, DOT_R = (106, 70), 8


def poly(pts, fill):
    return f'<polygon points="{" ".join(f"{x},{y}" for x, y in pts)}" fill="{fill}"/>'


rect = f'x="{X0}" y="{Y0}" width="{X1 - X0}" height="{Y1 - Y0}" rx="{RX}"'
svg = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">',
    f'  <defs><clipPath id="env"><rect {rect}/></clipPath></defs>',
    f'  <rect x="{X0}" y="{Y0 + PROFILE}" width="{X1 - X0}" height="{Y1 - Y0}" rx="{RX}" fill="{PROFILE_C}"/>',
    '  <g clip-path="url(#env)">',
    "    " + poly([(X0, Y0), (CX, CY), (X0, Y1)], SIDE),
    "    " + poly([(X1, Y0), (CX, CY), (X1, Y1)], SIDE),
    "    " + poly([(X0, Y1), (CX, CY), (X1, Y1)], FOLD),
    "    " + poly([(X0, Y0), (X1, Y0), (CX, CY)], FLAP),
    "  </g>",
    f'  <circle cx="{DOT_AT[0]}" cy="{DOT_AT[1]}" r="{DOT_R}" fill="{DOT}"/>',
    "</svg>",
    "",
]
open("data/io.github.felsenuboot.FastmailGtk.svg", "w").write("\n".join(svg))

sym = '''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
  <rect x="2" y="3" width="12" height="10" rx="1.5" fill="none" stroke="#2e3436" stroke-width="2"/>
  <path d="M3 4.5l5 4.5 5-4.5" fill="none" stroke="#2e3436" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
'''
open("data/io.github.felsenuboot.FastmailGtk-symbolic.svg", "w").write(sym)
print("wrote app icon and symbolic")
