#!/usr/bin/env bash
# Memory of a process and all its descendants (WebKit and Electron helpers included):
# prints "<rss> <pss>" in MiB. RSS counts shared pages once per process, so it overstates
# multi-process apps; PSS splits shared pages between their users and is the honest one.
# usage: tree-rss.sh <pid>
set -euo pipefail
root=$1
pids=("$root")
queue=("$root")
while ((${#queue[@]})); do
  p=${queue[0]}; queue=("${queue[@]:1}")
  for c in $(pgrep -P "$p" || true); do pids+=("$c"); queue+=("$c"); done
done
rss=0; pss=0
for p in "${pids[@]}"; do
  r=$(awk '/^VmRSS:/ {print $2}' "/proc/$p/status" 2>/dev/null || echo 0)
  s=$(awk '/^Pss:/ {print $2}' "/proc/$p/smaps_rollup" 2>/dev/null || echo 0)
  rss=$((rss + ${r:-0})); pss=$((pss + ${s:-0}))
done
echo "$((rss / 1024)) $((pss / 1024))"
