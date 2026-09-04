#!/usr/bin/env python3
"""Feature badges for the README: a symbolic icon in white on a round plate in a GNOME palette
colour, the way GNOME Settings and Tour introduce things. Sources are the app's own icons
and Adwaita's (LGPL / CC BY-SA 3.0); run on a machine with the Adwaita icon theme installed."""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_ICONS = HERE.parent.parent / "den_mail" / "icons" / "hicolor" / "scalable" / "actions"
ADWAITA = Path("/usr/share/icons/Adwaita/symbolic")

# name, source icon, plate colour (GNOME palette: https://developer.gnome.org/hig/reference/palette.html)
BADGES = [
    ("labels", APP_ICONS / "fm-tag-symbolic.svg", "#3584e4"),                         # Blue 3
    ("offline", ADWAITA / "devices/drive-harddisk-symbolic.svg", "#26a269"),           # Green 5
    ("undo", ADWAITA / "actions/edit-undo-symbolic.svg", "#e66100"),                   # Orange 4
    ("safe-html", ADWAITA / "status/security-high-symbolic.svg", "#c01c28"),           # Red 4
    ("identities", ADWAITA / "status/avatar-default-symbolic.svg", "#9141ac"),         # Purple 3
    ("bulk", ADWAITA / "actions/edit-select-all-symbolic.svg", "#1a5fb4"),             # Blue 5
    ("unsubscribe", APP_ICONS / "fm-blocked-symbolic.svg", "#c64600"),                 # Orange 5
    ("search", ADWAITA / "actions/system-search-symbolic.svg", "#5e5c64"),             # Dark 3
    ("desktop", ADWAITA / "legacy/preferences-system-notifications-symbolic.svg", "#1c71d8"),  # Blue 4
]


def inner(svg: str) -> str:
    """The drawing inside a 16 px symbolic SVG, recoloured white."""
    body = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1)
    body = re.sub(r'fill="#[0-9a-fA-F]{3,6}"', 'fill="#ffffff"', body)
    body = re.sub(r"fill:#[0-9a-fA-F]{3,6}", "fill:#ffffff", body)
    return body.strip()


def badge(source: Path, colour: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">'
            f'<circle cx="24" cy="24" r="24" fill="{colour}"/>'
            f'<svg x="12" y="12" width="24" height="24" viewBox="0 0 16 16" fill="#ffffff">{inner(source.read_text())}</svg>'
            f"</svg>\n")


def main() -> None:
    for name, source, colour in BADGES:
        (HERE / f"{name}.svg").write_text(badge(source, colour))
        print("wrote", name)


if __name__ == "__main__":
    main()
