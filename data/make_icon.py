"""Geometric app icon: a right-pointing triangle followed by two chevrons that follow its contour.

128x128 GNOME grid. The chevrons are exact parallel offsets of the triangle's two slanted edges,
so the gaps are uniform along their whole length; corners are rounded by stroking an inset polygon.
"""
import math, sys

BLUE = "#006eb8"        # Sanzo Wada "Blue" (A Dictionary of Color Combinations)
CLOUD = "#0066ad"       # a touch darker than the blue, for the faint cloud on the triangle
R = 3.0                 # corner radius
Y0, Y1 = 26.0, 102.0    # vertical extent of all shapes
X0 = 13.0               # flat left edge of the triangle
W = 42.0                # triangle width (apex at X0 + W)
GAP, T = 6.5, 15.0      # gap between shapes and chevron thickness (perpendicular distances)

cy = (Y0 + Y1) / 2
h = (Y1 - Y0) / 2                        # half height
L = math.hypot(W, h)
ux, uy = W / L, h / L                    # direction of the upper edge (top-left -> apex)
k = 1 / (h / L)                          # horizontal shift per unit of perpendicular offset (1/sin)


def x_on_offset_line(d, y):
    """x of the slanted edge offset outward by d, at height y (the lower edge mirrors the upper)."""
    if y > cy:
        y = Y0 + Y1 - y
    # upper edge: (X0, Y0) + s*u, outward normal n = (uy, -ux)
    px, py = X0 + d * uy, Y0 - d * ux
    s = (y - py) / uy
    return px + s * ux


def chevron(d_in, d_out, r):
    """Polygon (inset by r for the rounding stroke) of the band between offsets d_in and d_out."""
    di, do = d_in + r, d_out - r
    yt, yb = Y0 + r, Y1 - r
    pts = [(x_on_offset_line(di, yt), yt), (X0 + W + k * di, cy), (x_on_offset_line(di, yb), yb),
           (x_on_offset_line(do, yb), yb), (X0 + W + k * do, cy), (x_on_offset_line(do, yt), yt)]
    return pts


def triangle(r):
    # inset triangle: left edge x = X0 + r, slanted edges offset inward by r
    xl = X0 + r
    yt = Y0 + (-(-r) * ux) + uy * ((xl - (X0 + r * uy * -1)) / ux) if False else None
    # compute the top-left corner as the intersection of x = xl with the upper edge offset by -r
    d = -r
    px, py = X0 + d * uy, Y0 - d * ux
    s = (xl - px) / ux
    yt = py + s * uy
    apex = X0 + W + k * d
    return [(xl, yt), (apex, cy), (xl, Y1 - (yt - Y0))]


def poly(pts, fill, extra=""):
    p = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
    return f'<polygon points="{p}" fill="{fill}" stroke="{fill}" stroke-width="{2*R}" stroke-linejoin="round"{extra}/>'


tri = triangle(R)
c1 = chevron(GAP, GAP + T, R)
c2 = chevron(2 * GAP + T, 2 * (GAP + T), R)
right = X0 + W + k * 2 * (GAP + T)
shift = (128 - (right + X0)) / 2  # centre horizontally
def sh(pts): return [(x + shift, y) for x, y in pts]
tri, c1, c2 = sh(tri), sh(c1), sh(c2)

# faint cloud on the triangle: a soft blob, clipped to the (unrounded-stroke) triangle
tx = X0 + shift
cloud = (f'<g fill="{CLOUD}"><circle cx="{tx+14:.1f}" cy="{cy+6:.1f}" r="9"/>'
         f'<circle cx="{tx+24:.1f}" cy="{cy+1:.1f}" r="11"/><circle cx="{tx+34:.1f}" cy="{cy+7:.1f}" r="8"/>'
         f'<rect x="{tx+8:.1f}" y="{cy+4:.1f}" width="32" height="11" rx="5"/></g>')
tri_clip = " ".join(f"{x:.2f},{y:.2f}" for x, y in tri)

svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <defs>
    <clipPath id="tri"><polygon points="{tri_clip}" stroke="{BLUE}" stroke-width="{2*R}" stroke-linejoin="round"/></clipPath>
  </defs>
  {poly(tri, BLUE)}
  <g clip-path="url(#tri)">{cloud}</g>
  {poly(c1, BLUE)}
  {poly(c2, BLUE)}
</svg>
'''
open("data/io.github.felsenuboot.FastmailGtk.svg", "w").write(svg)

# symbolic (16x16): same construction, scaled, no cloud, currentColor via the standard #2e3436 fill
def scale(pts, f): return [(x * f, y * f) for x, y in pts]
f = 16 / 128
def spoly(pts):
    p = " ".join(f"{x:.3f},{y:.3f}" for x, y in pts)
    return f'<polygon points="{p}" fill="#2e3436" stroke="#2e3436" stroke-width="{2*R*f:.3f}" stroke-linejoin="round"/>'
sym = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
  {spoly(scale(tri, f))}
  {spoly(scale(c1, f))}
  {spoly(scale(c2, f))}
</svg>
'''
open("data/io.github.felsenuboot.FastmailGtk-symbolic.svg", "w").write(sym)
print("bbox x: %.1f .. %.1f, y: %.0f .. %.0f" % (X0 + shift, right + shift, Y0, Y1))
