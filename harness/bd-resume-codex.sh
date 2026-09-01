#!/bin/bash
# Resume the codex workers suspended for the v1299 band, once the lane is quiet.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/FINISH.log"
say(){ printf '%s [codex-resume] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
say "armed: will SIGCONT the suspended codex workers when no verify is running"
for _ in $(seq 1 240); do
  if ! pgrep -f 'bd-verify-cut\.sh' >/dev/null 2>&1; then
    n=0
    for p in $(cat /tmp/bd-codex-stopped.pids 2>/dev/null); do
      [ -d "/proc/$p" ] && { kill -CONT "$p" 2>/dev/null && n=$((n+1)); }
    done
    say "resumed $n suspended codex process(es)"
    exit 0
  fi
  sleep 30
done
say "GAVE UP after 2h -- codex workers may still be suspended, check with: ps -o s= -p <pid>"
