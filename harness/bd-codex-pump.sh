#!/bin/bash
# Keep N codex workers busy continuously: when a cx- session ends, dispatch the
# next queued brief. Written 2026-08-26 -- bd-codex-fleet.sh dispatches a FIXED
# list and stops; bd-queue-run.sh is the serial MERGE lane. Neither refills.
# Queue is a directory so it survives a restart and the operator can inspect it.
#   pending/NN-name.md -> running/ -> done/
set -u
Q=/home/mboyle/bd-codex-queue
LOG=/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts/pump.log
MAX="${MAX:-4}"
mkdir -p "$Q"/{pending,running,done} "$(dirname "$LOG")"
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" >> "$LOG"; }
say "pump start MAX=$MAX pending=$(ls "$Q/pending" 2>/dev/null|wc -l)"
while true; do
  # reap: a running brief whose tmux session is gone is done
  for f in "$Q"/running/*.md; do
    [ -e "$f" ] || continue
    n=$(basename "$f" .md)
    # bd-codex-cut.sh names the session cx-row<ROW>, NOT cx-<ROW>. Checking the
    # wrong name reaps every brief to done/ on the first pass while it is still
    # running, so running/ stays empty and done/ lies. MAX was still correct
    # because that counts live cx- sessions directly.
    tmux has-session -t "cx-row$n" 2>/dev/null || { mv "$f" "$Q/done/" 2>/dev/null && say "done $n"; }
  done
  live=$(tmux ls 2>/dev/null | grep -c '^cx-')
  if [ "$live" -lt "$MAX" ]; then
    f=$(ls "$Q"/pending/*.md 2>/dev/null | head -1)
    if [ -n "${f:-}" ]; then
      n=$(basename "$f" .md)
      mv "$f" "$Q/running/$n.md"
      setsid nohup bash /home/mboyle/bd-codex-cut.sh "$n" "$Q/running/$n.md" >/dev/null 2>&1 </dev/null &
      say "dispatch $n (live was $live/$MAX)"
      sleep 8   # git worktree add contends on the index lock
      continue
    fi
    # DO NOT exit on an empty queue. Work arrives asynchronously -- the
    # integrator files new briefs as findings land -- and an exited pump leaves
    # them sitting unprocessed until somebody notices. Idle instead.
    if [ "$live" -eq 0 ] && [ "${IDLE_SAID:-0}" != 1 ]; then
      say "queue empty, nothing live -- idling, not exiting"; IDLE_SAID=1
    fi
    [ "$live" -gt 0 ] && IDLE_SAID=0
  fi
  sleep 30
done
