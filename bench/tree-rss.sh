#!/usr/bin/env bash
# Resident memory of a process and all its descendants, in MiB (WebKit and Electron
# helpers included). usage: tree-rss.sh <pid>
set -euo pipefail
root=$1
pids=("$root")
queue=("$root")
while ((${#queue[@]})); do
  p=${queue[0]}; queue=("${queue[@]:1}")
  for c in $(pgrep -P "$p" || true); do pids+=("$c"); queue+=("$c"); done
done
total=0
for p in "${pids[@]}"; do
  rss=$(awk '/^VmRSS:/ {print $2}' "/proc/$p/status" 2>/dev/null || echo 0)
  total=$((total + ${rss:-0}))
done
echo "$((total / 1024))"
