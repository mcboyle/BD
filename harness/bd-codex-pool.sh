#!/bin/bash
# BOUNDED codex pool. Not "non-stop": five workers plus a band drove load to 25
# on 2026-08-28, and load is itself a failure cause here -- it is what flips the
# hunt family and the T3T4 budget. Throughput that manufactures failures costs
# more time than it saves, so this holds the pool at MAXW and pauses entirely
# while a band runs, which is the one thing that must not be perturbed.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25
Q="$A/night/codex-pool-queue.txt"; D=/home/mboyle/bd-codex-briefs
L="$A/FINISH.log"; CAPF="$A/night/pool-max"; [ -f "$CAPF" ] || echo 3 > "$CAPF"
say(){ printf '%s [pool] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
live(){ tmux ls 2>/dev/null | grep -c '^cx-row'; }
band(){ ps -eo args= | grep -v shell-snapshots | grep -qE 'bd-verify-cut\.sh|[p]ytest .*--dist loadfile'; }
say "pool armed, cap from $CAPF, queue $Q"
while true; do
  [ -s "$Q" ] || { sleep 60; continue; }
  if band; then
    [ "$(live)" -ge 2 ] && { sleep 45; continue; }
    BANDCAP=2
  else
    BANDCAP=""
  fi
  MAXW=$(cat "$CAPF" 2>/dev/null)
  [ -n "$MAXW" ] && [ -z "${MAXW//[0-9]/}" ] || MAXW=3
  [ -n "$BANDCAP" ] && MAXW="$BANDCAP"
  n=$(live); if [ "$n" -ge "$MAXW" ]; then sleep 45; continue; fi
  row=$(head -1 "$Q"); [ -z "$row" ] && { sleep 60; continue; }
  sed -i 1d "$Q"
  set -- $row; r="$1"; brief="$2"
  if [ ! -f "$D/$brief" ]; then say "row $r: brief $brief MISSING -- skipped"; continue; fi
  if tmux has-session -t "cx-row$r" 2>/dev/null; then say "row $r already running"; continue; fi
  say "dispatch row $r ($brief); $((n+1))/$MAXW busy"
  setsid nohup bash /home/mboyle/bd-codex-cut.sh "$r" "$D/$brief" >/dev/null 2>&1 < /dev/null &
  sleep 30
done
