#!/usr/bin/env bash
# Installs the launcher, desktop entry and icons for the current user, so the app
# shows up in application launchers and can be set as the mailto: handler.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP=io.github.felsenuboot.FastmailGtk
BIN=~/.local/bin
APPS=~/.local/share/applications
ICONS=~/.local/share/icons/hicolor
mkdir -p "$BIN" "$APPS" "$ICONS/scalable/apps" "$ICONS/symbolic/apps"
ln -sf "$HERE/bin/fastmail-gtk" "$BIN/fastmail-gtk"
# Absolute Exec path: launchers do not necessarily have ~/.local/bin in PATH.
sed "s|^Exec=.*|Exec=$BIN/fastmail-gtk %u|" "$HERE/data/$APP.desktop" > "$APPS/$APP.desktop"
chmod 644 "$APPS/$APP.desktop"
install -m644 "$HERE/data/$APP.svg" "$ICONS/scalable/apps/$APP.svg"
install -m644 "$HERE/data/$APP-symbolic.svg" "$ICONS/symbolic/apps/$APP-symbolic.svg"
# Fixed-size PNGs for docks/taskbars that do not rasterise SVG themselves.
if command -v magick >/dev/null 2>&1; then
  for s in 16 22 24 32 48 64 96 128 256 512; do
    mkdir -p "$ICONS/${s}x${s}/apps"
    magick -background none "$HERE/data/$APP.svg" -resize "${s}x${s}" "$ICONS/${s}x${s}/apps/$APP.png"
  done
fi
cp "$HERE/data/$APP.svg" "$HERE/fastmail_gtk/icons/hicolor/scalable/apps/$APP.svg"
gtk4-update-icon-cache -q -t -f "$ICONS" 2>/dev/null || gtk-update-icon-cache -q -t -f "$ICONS" 2>/dev/null || true
update-desktop-database -q "$APPS" 2>/dev/null || true
echo "Installed. Run 'fastmail-gtk' or launch Fastmail GTK from your app launcher."
echo "To make it the default mail client: xdg-settings set default-url-scheme-handler mailto $APP.desktop"
