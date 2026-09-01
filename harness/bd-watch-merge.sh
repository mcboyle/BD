#!/bin/bash
# Report one row's merge phases, reading ONLY the current lane's lines.
# ANCHORED ON PURPOSE: FINISH.log accumulates across runs, and an unanchored
# grep matched "14:22 SKIPPED 260" from a lane four hours dead and reported it
# as this run's verdict. A stale log read as a live verdict is the same defect
# class this whole run is about.
set -u
R="${1:?row}"; FROM="${2:?first line of the current lane}"
L=/home/mboyle/fleet-run-artifacts/2026-08-25/FINISH.log
last=""
while :; do
  cur=$(tail -n +"$FROM" "$L" 2>/dev/null)
  line=$(printf '%s\n' "$cur" | grep "\[chain $R\]" | tail -1)
  if [ -n "$line" ] && [ "$line" != "$last" ]; then echo "$line"; last="$line"; fi
  done_line=$(printf '%s\n' "$cur" | grep -E "OK $R merged|SKIPPED $R " | tail -1)
  if [ -n "$done_line" ]; then echo "$done_line"; exit 0; fi
  sleep 20
done
