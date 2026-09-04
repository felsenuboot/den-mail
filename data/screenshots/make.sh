#!/usr/bin/env bash
# Regenerates the tour screenshots from the fake account: one autopilot script per
# picture, taken with grim inside a headless cage session (see docs/DEVELOPMENT.md).
# Needs cage, grim, magick and a fake server: python -m tests.fake_server 18081
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
export DEN_MAIL_SESSION_URL=http://127.0.0.1:18081/session DEN_MAIL_TOKEN=fake-token
export XDG_DATA_HOME=$WORK/data XDG_CONFIG_HOME=$WORK/config XDG_CACHE_HOME=$WORK/cache ROOT WORK

shot() {  # name, autopilot script, seconds before the capture
  DEN_MAIL_AUTOPILOT="$2" WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 \
    timeout 90 cage -- sh -c "(cd \"\$ROOT\" && exec python3 -m den_mail) >\"\$WORK/$1.log\" 2>&1 & sleep $3; grim \"\$WORK/$1.png\"; kill %1" \
    >/dev/null 2>&1 || true
  cp "$WORK/$1.png" "$HERE/$1.png" && echo "made $1"
}
frames() {  # name, autopilot script, number of frames, seconds between frames (for a GIF)
  DEN_MAIL_AUTOPILOT="$2" WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 \
    timeout 120 cage -- sh -c "(cd \"\$ROOT\" && exec python3 -m den_mail) >\"\$WORK/$1.log\" 2>&1 & sleep 3; for i in \$(seq -w 1 $3); do grim \"\$WORK/$1-\$i.png\"; sleep $4; done; kill %1" \
    >/dev/null 2>&1 || true
  magick -delay $(( ${4%.*} * 100 )) -loop 0 "$WORK/$1"-*.png -layers optimize "$HERE/$1.gif" && echo "made $1.gif"
}

shot inbox-dark "sleep 3; select 1" 8
shot inbox-light "sleep 3; theme light; sleep 1; select 1" 9
shot group-sender "sleep 3; group on; sleep 2; fold 1; fold 3; sleep 1; select 2" 10
shot selection "sleep 3; select-mode on; sleep 1; toggle 0; toggle 2; toggle 4" 9
shot labels "sleep 3; select 1; sleep 2; action win.labels" 9
shot context-menu "sleep 3; select 1; sleep 2; thread-menu 1" 9
shot search "sleep 3; search has:attachment" 9
shot compose "sleep 3; compose; sleep 2; from-popup" 9
shot identities "sleep 3; identities" 8
shot masked "sleep 3; masked" 8
shot newsletters "sleep 3; action win.newsletters" 8
shot preferences "sleep 3; preferences" 8
# the theme split: light above the diagonal, dark below it
magick "$HERE/inbox-light.png" \( "$HERE/inbox-dark.png" \( -size 1280x720 xc:black -fill white -draw "polygon 1280,0 1280,720 0,720" \) -alpha off -compose copy_opacity -composite \) -compose over -composite "$HERE/theme-split.png" && echo "made theme-split"
rm -f "$HERE/inbox-light.png"
# the workflow: group by sender, fold everything, tick a sender, archive it
frames group-archive "sleep 4; group on; sleep 2; fold-all on; sleep 2; select-mode on; sleep 2; toggle 2; sleep 2; action win.archive; sleep 3" 8 1.5
