"""App icon per the GNOME HIG app-icon guidelines, generated so the geometry stays exact.

* 128x128 canvas, 2 px grid, 8 px margins: top surface from y=8, bottom of the front profile on
  the y=120 baseline like the standard GNOME icons.
* "Not flat": each shape is a top surface (GNOME Blue 3) with a 4 px front profile below it
  (Blue 5), light straight from above, flat colours, no shadows outside the silhouette.
* Metaphor: a right-pointing triangle followed by two chevrons that are exact parallel offsets of
  the triangle's slanted edges (uniform gaps), i.e. a message being passed on.
* The symbolic variant uses the same metaphor on the 16 px grid with 2 px strokes.
"""
import math

# GNOME palette (developer.gnome.org/hig/reference/palette.html)
TOP = "#3584e4"      # Blue 3: top surface
FRONT = "#1a5fb4"    # Blue 5: front profile
CLOUD = "#1c71d8"    # Blue 4: faint cloud on the triangle

PROFILE = 4          # front profile height (2 detail units)
R = 4.0              # corner radius
Y0, Y1 = 8.0, 116.0  # top surface; profile reaches the 120 baseline
X0 = 11.0            # flat left edge of the triangle
W = 44.0             # triangle width (apex at X0 + W)
GAP, T = 8.0, 16.0   # gap between shapes and chevron thickness (perpendicular distances)

cy = (Y0 + Y1) / 2
half = (Y1 - Y0) / 2
L = math.hypot(W, half)
ux, uy = W / L, half / L        # direction of the upper edge (top-left -> apex)
k = L / half                    # horizontal shift per unit of perpendicular offset (1/sin)


def x_on_offset_line(d, y):
    """x of the slanted edge offset outward by d, at height y (the lower edge mirrors the upper)."""
    if y > cy:
        y = Y0 + Y1 - y
    px, py = X0 + d * uy, Y0 - d * ux  # upper edge (X0, Y0) + s*u, outward normal (uy, -ux)
    return px + (y - py) / uy * ux


def chevron(d_in, d_out, r):
    """Band between offsets d_in and d_out, inset by r (the rounding stroke adds it back)."""
    di, do = d_in + r, d_out - r
    yt, yb = Y0 + r, Y1 - r
    return [(x_on_offset_line(di, yt), yt), (X0 + W + k * di, cy), (x_on_offset_line(di, yb), yb),
            (x_on_offset_line(do, yb), yb), (X0 + W + k * do, cy), (x_on_offset_line(do, yt), yt)]


def triangle(r):
    xl = X0 + r
    px, py = X0 - r * uy, Y0 + r * ux  # upper edge offset inward by r
    yt = py + (xl - px) / ux * uy
    return [(xl, yt), (X0 + W - k * r, cy), (xl, Y1 - (yt - Y0))]


def poly(pts, fill, dy=0.0):
    p = " ".join(f"{x:.2f},{y + dy:.2f}" for x, y in pts)
    return f'<polygon points="{p}" fill="{fill}" stroke="{fill}" stroke-width="{2 * R}" stroke-linejoin="round"/>'


shapes = [triangle(R), chevron(GAP, GAP + T, R), chevron(2 * GAP + T, 2 * (GAP + T), R)]
right = X0 + W + k * 2 * (GAP + T)
shift = round((128 - (right + X0)) / 2)
shapes = [[(x + shift, y) for x, y in s] for s in shapes]
tri = shapes[0]
tx = X0 + shift
cloud = (f'<g fill="{CLOUD}"><circle cx="{tx + 15:.1f}" cy="{cy + 8:.1f}" r="9"/>'
         f'<circle cx="{tx + 25:.1f}" cy="{cy + 2:.1f}" r="11"/><circle cx="{tx + 35:.1f}" cy="{cy + 9:.1f}" r="7"/>'
         f'<rect x="{tx + 8:.1f}" y="{cy + 6:.1f}" width="34" height="11" rx="5"/></g>')
tri_pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in tri)

svg = ['<?xml version="1.0" encoding="UTF-8"?>',
       '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">',
       f'  <defs><clipPath id="tri"><polygon points="{tri_pts}" stroke="{TOP}" stroke-width="{2 * R}" '
       'stroke-linejoin="round"/></clipPath></defs>']
svg += ["  " + poly(s, FRONT, PROFILE) for s in shapes]          # front profiles first
svg += ["  " + poly(s, TOP) for s in shapes]                     # top surfaces
svg += [f'  <g clip-path="url(#tri)">{cloud}</g>', "</svg>", ""]
open("data/io.github.felsenuboot.FastmailGtk.svg", "w").write("\n".join(svg))

sym = '''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
  <path d="M1 3.5v9a1 1 0 0 0 1.6.8l5-4.5a1 1 0 0 0 0-1.6l-5-4.5A1 1 0 0 0 1 3.5z" fill="#2e3436"/>
  <path d="M9 4l4 4-4 4" fill="none" stroke="#2e3436" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M12 4l3 4-3 4" fill="none" stroke="#2e3436" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity="0.6"/>
</svg>
'''
open("data/io.github.felsenuboot.FastmailGtk-symbolic.svg", "w").write(sym)
print("extent x %.0f..%.0f, top %.0f, baseline %.0f" % (X0 + shift, right + shift, Y0, Y1 + PROFILE))
