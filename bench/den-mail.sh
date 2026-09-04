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
# the 20 s rest with the message open is the idle window for CPU and memory
SCENARIO="sleep ${BENCH_SETTLE:-8}; mailbox ${BENCH_FOLDER:-Archive}; sleep 6; search ${BENCH_SEARCH:-invoice}; sleep 6; mailbox Inbox; sleep 4; select ${BENCH_OPEN_INDEX:-0}; sleep 4; sleep ${BENCH_IDLE:-20}; quit"
run() {
  local log="$HERE/logs/den-mail-$MODE-$1.log"; mkdir -p "$HERE/logs"
  [ "$MODE" = cold ] && rm -rf "$XDG_DATA_HOME" "$XDG_CACHE_HOME"
  local win; win=$(DEN_MAIL_TIMING=1 DEN_MAIL_AUTOPILOT="$SCENARIO" \
    python3 "$HERE/window-time.py" denmail -- sh -c "cd '$ROOT' && exec python3 -m den_mail >'$log' 2>&1")
  local pid; pid=$(echo "$win" | python3 -c 'import json,sys; print(json.load(sys.stdin)["pid"])')
  # memory and CPU: sample the tree every half second until the app exits (time, rss, pss, ticks)
  local samples="$HERE/logs/den-mail-$MODE-$1.samples"; : > "$samples"
  while kill -0 "$pid" 2>/dev/null; do
    echo "$(date +%s.%N) $("$HERE/tree-rss.sh" "$pid" 2>/dev/null || echo "0 0 0")" >> "$samples"
    sleep 0.5
  done
  python3 - "$log" "$win" "$samples" "$MODE" "$1" "${BENCH_IDLE:-20}" <<'PY' >> "$HERE/results.jsonl"
import json, os, re, sys
log, win, samples, mode, run, idle = sys.argv[1:]
rows = [tuple(float(x) for x in l.split()) for l in open(samples) if len(l.split()) == 4]
rows = [r for r in rows if r[1] > 0]  # samples taken while the process tree was alive
tick = os.sysconf("SC_CLK_TCK")
row = {"client": "den-mail", "mode": mode, "run": int(run), **json.loads(win)}
if rows:
    row["rss_peak_mib"] = int(max(r[1] for r in rows))
    row["pss_end_mib"] = int(rows[-1][2])
    row["cpu_total_s"] = round(rows[-1][3] / tick, 2)
    # the idle window: the last `idle` seconds before the final sample
    end = rows[-1]; start = next((r for r in rows if r[0] >= end[0] - float(idle)), rows[0])
    if end[0] > start[0]:
        row["idle_cpu_pct"] = round((end[3] - start[3]) / tick / (end[0] - start[0]) * 100, 1)
row.pop("pid", None); row.pop("address", None)
for m in re.finditer(r"timing: (\S+) at=(\d+)(?: took=(\d+))?", open(log, errors="replace").read()):
    event, at, took = m.groups()
    if event + "_at_ms" in row: continue  # the first pair of a kind (the switch to Archive, not back)
    row[event + "_at_ms"] = int(at)
    if took: row[event + "_ms"] = int(took)
print(json.dumps(row))
PY
  tail -1 "$HERE/results.jsonl"
}
if [ "$MODE" = warm ]; then echo "priming the cache"; run 0 >/dev/null; fi
for i in $(seq 1 "$RUNS"); do run "$i"; done
