#!/usr/bin/env bash
# Append a measured checkpoint block every 10 minutes, and immediately after any
# merge. bd-checkpoint-write measures every number at write time -- nothing is
# passed in -- so this only decides WHEN, never WHAT.
#
# The merge trigger watches FINISH.log's line count rather than grepping for a
# marker: logs are appended across attempts, so an earlier run's "MERGED" line
# satisfies a naive grep forever and the checkpoint stops tracking reality.
set -uo pipefail
R=/home/mboyle
FIN=$R/fleet-run-artifacts/2026-08-25/FINISH.log
LOG=$R/fleet-run-artifacts/2026-08-25/checkpoint-loop.log
INT="${BD_CHECKPOINT_INTERVAL:-600}"
seen=$(wc -l < "$FIN" 2>/dev/null || echo 0)
last=0

while true; do
  now=$(date +%s)
  cur=$(wc -l < "$FIN" 2>/dev/null || echo 0)
  merged=0
  if [ "$cur" -gt "$seen" ]; then
    # only the lines that appeared SINCE the last look
    if tail -n +"$((seen + 1))" "$FIN" 2>/dev/null | grep -qE 'MERGED|DEPLOY OK'; then
      merged=1
    fi
    seen=$cur
  fi
  if [ "$merged" = 1 ] || [ $((now - last)) -ge "$INT" ]; then
    out=$(bash "$R/bd-checkpoint-write" 2>&1 | tail -1)
    printf '%s checkpoint (%s): %s\n' "$(date -u +%H:%M:%SZ)" \
      "$([ "$merged" = 1 ] && echo after-merge || echo interval)" "$out" >> "$LOG"
    last=$now
  fi
  sleep 30
done
