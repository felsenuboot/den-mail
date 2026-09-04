#!/usr/bin/env bash
# Benchmark den-mail on the desktop session: N runs of the shared scenario, one line of
# JSON per run in bench/results.jsonl (see docs/BENCHMARK.md).
#   DEN_MAIL_TOKEN=… bench/den-mail.sh [runs] [cold|warm]
# A separate profile under bench/profile keeps the real config untouched; "cold" wipes
# its cache before every run, "warm" (default) primes it once and keeps it.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/.." && pwd)"
RUNS=${1:-5}; MODE=${2:-warm}
: "${DEN_MAIL_TOKEN:?export the Fastmail API token as DEN_MAIL_TOKEN}"
PROFILE=$HERE/profile/den-mail
export XDG_CONFIG_HOME=$PROFILE/config XDG_DATA_HOME=$PROFILE/data XDG_CACHE_HOME=$PROFILE/cache
mkdir -p "$XDG_CONFIG_HOME/den-mail"
# the same window as the other clients, and no side effects on the account
cat > "$XDG_CONFIG_HOME/den-mail/config.json" <<JSON
{"window": {"width": ${BENCH_WIDTH:-1600}, "height": ${BENCH_HEIGHT:-1000}}, "mark_read_on_open": false}
JSON
SCENARIO="sleep ${BENCH_SETTLE:-8}; mailbox ${BENCH_FOLDER:-Archive}; sleep 6; search ${BENCH_SEARCH:-invoice}; sleep 6; mailbox Inbox; sleep 4; select ${BENCH_OPEN_INDEX:-0}; sleep 6; quit"
run() {
  local log="$HERE/logs/den-mail-$MODE-$1.log"; mkdir -p "$HERE/logs"
  [ "$MODE" = cold ] && rm -rf "$XDG_DATA_HOME" "$XDG_CACHE_HOME"
  local win; win=$(DEN_MAIL_TIMING=1 DEN_MAIL_AUTOPILOT="$SCENARIO" \
    python3 "$HERE/window-time.py" denmail -- sh -c "cd '$ROOT' && exec python3 -m den_mail >'$log' 2>&1")
  local pid; pid=$(echo "$win" | python3 -c 'import json,sys; print(json.load(sys.stdin)["pid"])')
  # memory while the opened message is on screen: sample until the app exits, keep the peak
  local peak=0 rss
  while kill -0 "$pid" 2>/dev/null; do rss=$("$HERE/tree-rss.sh" "$pid" 2>/dev/null || echo 0); ((rss > peak)) && peak=$rss; sleep 0.5; done
  python3 - "$log" "$win" "$peak" "$MODE" "$1" <<'PY' >> "$HERE/results.jsonl"
import json, re, sys
log, win, peak, mode, run = sys.argv[1:]
row = {"client": "den-mail", "mode": mode, "run": int(run), "rss_peak_mib": int(peak), **json.loads(win)}
row.pop("pid", None); row.pop("address", None)
for m in re.finditer(r"timing: (\S+) at=(\d+)(?: took=(\d+))?", open(log, errors="replace").read()):
    event, at, took = m.groups()
    row[event + "_at_ms"] = int(at)
    if took: row[event + "_ms"] = int(took)
print(json.dumps(row))
PY
  tail -1 "$HERE/results.jsonl"
}
if [ "$MODE" = warm ]; then echo "priming the cache"; run 0 >/dev/null; fi
for i in $(seq 1 "$RUNS"); do run "$i"; done
