#!/bin/bash
# KEEP THE CODEX POOL FED. bd-codex-pool.sh dispatches from a queue file but
# never REFILLS it, so when every brief has been taken the pool sits idle with
# capacity -- observed 19:45-19:57, two finished agents and an empty queue.
# This tops the queue up from a standing worklist every 10 minutes.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25
Q="$A/night/codex-pool-queue.txt"; W=/home/mboyle/bd-codex-worklist.txt
D=/home/mboyle/bd-codex-briefs; L="$A/inflight/codex-refill.log"
say(){ printf '%s [refill] %s\n' "$(date -u +%H:%M:%S)" "$*" >> "$L"; }
while :; do
  live=$(tmux ls 2>/dev/null | grep -c '^cx-row')
  queued=$(grep -cvE '^[[:space:]]*(#|$)' "$Q" 2>/dev/null); queued=${queued:-0}
  cap=$(cat "$A/night/pool-max" 2>/dev/null || echo 4)
  want=$(( cap - live - queued ))
  if [ "$want" -gt 0 ] && [ -f "$W" ]; then
    n=0
    while IFS='|' read -r row brief; do
      case "$row" in ''|'#'*) continue;; esac
      [ "$n" -ge "$want" ] && break
      # never re-dispatch a row that already has a worktree or a live session
      [ -d "/home/mboyle/bd-codex-wt/row$row" ] && continue
      tmux has-session -t "cx-row$row" 2>/dev/null && continue
      grep -q "^$row " "$Q" 2>/dev/null && continue
      [ -f "$D/$brief" ] || { say "row $row: brief $brief MISSING"; continue; }
      echo "$row $brief" >> "$Q"; n=$((n+1)); say "queued row $row ($brief)"
    done < "$W"
    [ "$n" -gt 0 ] && say "topped up $n; live=$live queued=$queued cap=$cap"
  fi
  sleep 600
done
