#!/bin/bash
# As each Codex row returns, run integrator QA on it automatically. A worker's
# claim that its battery passed is DATA, not evidence, until it is re-run here
# (A5). Runs once per row, records a QA_RC, and never touches main.
set -u
CC=/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts
ROWS="184 183 242 176 235 174 175 121 221 241 26 27 229"
while true; do
  live=0
  for r in $ROWS; do
    if tmux has-session -t "cx-row$r" 2>/dev/null; then live=$((live+1)); continue; fi
    [ -s "$CC/row$r.txt" ] || continue           # never dispatched / no output
    [ -f "$CC/row$r.qa.log" ] && continue        # already QA'd
    tmux has-session -t "qa-row$r" 2>/dev/null && continue
    echo "$(date -u +%H:%M:%S) row $r returned -- starting QA"
    tmux new-session -d -s "qa-row$r" "bash /home/mboyle/bd-qa-row.sh $r"
    sleep 5
  done
  [ "$live" -eq 0 ] && { echo "$(date -u +%H:%M:%S) all rows returned; QA dispatch complete"; break; }
  sleep 60
done
