#!/usr/bin/env bash
# Runs the app headlessly against the fake server, once per autopilot script,
# and fails on any traceback or GTK critical in the logs; the screenshots land
# in $OUT (default: a temporary directory) for a look. The same mechanism as
# data/screenshots/make.sh, which needs cage and grim; here also dbus-run-session
# when no session bus is around (CI). Usage: tools/ui_smoke.sh [out-dir]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$(mktemp -d)}"
mkdir -p "$OUT"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
PORT="${DEN_MAIL_SMOKE_PORT:-18099}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$WORK/runtime}"; mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
export DEN_MAIL_SESSION_URL="http://127.0.0.1:$PORT/session" DEN_MAIL_TOKEN=fake-token DEN_MAIL_NO_WEBKIT="${DEN_MAIL_NO_WEBKIT:-1}"
export XDG_DATA_HOME="$WORK/data" XDG_CONFIG_HOME="$WORK/config" XDG_CACHE_HOME="$WORK/cache" ROOT WORK
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1

(cd "$ROOT" && exec python3 -m tests.fake_server "$PORT") >"$WORK/fake.log" 2>&1 &
FAKE=$!
trap 'kill $FAKE 2>/dev/null; rm -rf "$WORK"' EXIT
sleep 2

failed=0
shot() {  # name, autopilot script, seconds before the capture
  local name=$1 script=$2 wait=$3
  DEN_MAIL_AUTOPILOT="$script" timeout 90 cage -- sh -c \
    "(cd \"\$ROOT\" && exec python3 -m den_mail) >\"\$WORK/$name.log\" 2>&1 & sleep $wait; grim \"$OUT/$name.png\"; kill %1; wait" \
    >"$WORK/$name.cage.log" 2>&1 || true
  if grep -q -E 'Traceback|CRITICAL|autopilot: (unknown|no )' "$WORK/$name.log"; then
    echo "FAIL $name"; grep -E -A12 'Traceback|CRITICAL|autopilot: (unknown|no )' "$WORK/$name.log" | head -40; failed=1
  elif [ ! -s "$OUT/$name.png" ]; then
    echo "FAIL $name: no screenshot"; tail -5 "$WORK/$name.log" "$WORK/$name.cage.log"; failed=1
  else
    echo "ok   $name"
  fi
}

shot inbox "sleep 3; select 1" 8
shot group-sender "sleep 3; group on; sleep 2; fold 1; sleep 1; select 2" 9
shot selection "sleep 3; select-mode on; sleep 1; toggle 0; toggle 2; action win.archive" 9
shot search "sleep 3; search has:attachment" 8
shot categories "sleep 3; category-filter newsletters; sleep 2; category-filter off" 8
shot views "sleep 3; view newsletters; sleep 1; select 0; sleep 1; view never-read; sleep 1; view big-attachments" 9
shot compose "sleep 3; compose; sleep 2; from-popup" 8
shot dialogs "sleep 3; identities; sleep 1; masked; sleep 1; action win.newsletters; sleep 1; rules; sleep 1; cleanup" 10
shot cleanup-run "sleep 4; cleanup; sleep 2; cleanup-all mark_read; sleep 3; cleanup-all archive" 12
shot preferences "sleep 3; preferences inbox" 7
shot assistant "sleep 3; config assistant_enabled true; preferences assistant" 7
shot summary "sleep 3; config assistant_enabled true; select 1; sleep 2; action win.summarise" 9
shot beside "sleep 3; config beside_min_width 1000; select 1; sleep 1; action win.open-beside; sleep 1; select 2" 10
shot sender-rule "sleep 3; sender-rule digest@lists.example.com" 7
shot narrow "sleep 3; resize 700 600; sleep 2; select 1" 8

echo "screenshots in $OUT"
exit $failed
