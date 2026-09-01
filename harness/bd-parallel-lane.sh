#!/bin/bash
# PIPELINED lane: run up to N cuts through bd-row-chain.sh CONCURRENTLY.
#
# WHAT IS AND IS NOT PARALLEL:
#   integrate  SERIAL, always. bd-integrate-row.sh derives the version from MAIN,
#              so two concurrent integrates both claim main+1 and collide. The
#              chain takes $INTEGRATE_LOCK around that step.
#   QA/verify  PARALLEL. Local CPU, independent per candidate.
#   ship/CI    up to SLOTS concurrent via bd-ci-slot.sh (operator: 3, drop to 2
#              if Actions starvation reappears -- the signature is a poll count
#              stalling with checks non-terminal, not a red check).
#
# THE COST, ACCEPTED BY THE OPERATOR: cut N+1 is integrated onto main BEFORE cut
# N merges, so when N lands, N+1's parent is stale and it needs a rebase plus a
# partial re-verify. bd-rebase-cut.py resolves the release-trio collision and
# REFUSES a backlog row changed on both sides rather than picking a side.
#
#   usage: bd-parallel-lane.sh "row|slug|title" ...      env: SLOTS (3)
set -u
SLOTS="${SLOTS:-3}"
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/PARALLEL_LANE.log"
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" >> "$L"; }
say "=== parallel lane start: $# cut(s), SLOTS=$SLOTS ==="
pids=""
for spec in "$@"; do
  # throttle: never more than SLOTS chains alive at once
  while [ "$(jobs -rp | wc -l)" -ge "$SLOTS" ]; do sleep 20; done
  IFS='|' read -r ROW SLUG TITLE <<<"$spec"
  say "--- launching $ROW ---"
  ( bash /home/mboyle/bd-row-chain.sh "$ROW" 0 "$SLUG" "$TITLE" \
      && say "OK $ROW merged" || say "FAILED $ROW -- see $A/inflight/chain-$ROW.log" ) &
  pids="$pids $!"
  sleep 25   # let this one take the integrate lock before the next tries
done
for p in $pids; do wait "$p" 2>/dev/null; done
say "=== parallel lane complete ==="
