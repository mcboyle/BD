#!/bin/bash
# Ship the fleet batch once its re-verify clears, then hand the rest to the queue.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight
L=/home/mboyle/fleet-run-artifacts/2026-08-25/QUEUE.log
say(){ echo "$(date -u +%H:%M:%S) $*" | tee -a "$L"; }
for _ in $(seq 1 180); do grep -q 'VERDICT' "$A/1250-fix3-verify.log" 2>/dev/null && break; sleep 20; done
grep -E 'PRECUT_RC|PREPUSH_RC|BAND_RC|VERDICT' "$A/1250-fix3-verify.log" | tee -a "$L"
if grep -q 'ALL GREEN -- shippable' "$A/1250-fix3-verify.log"; then
  CHECK_FLOOR=20 /home/mboyle/bd-merge-lane.sh /home/mboyle/bd-ship.sh \
    cut/1250-fleet-and-runtime-truth \
    "v3.66.1250 fleet and runtime state is measured rather than assumed" \
    "$A/pr-body-174.md" >> "$L" 2>&1 && say "ROWS 174 221 235 241 MERGED (v3.66.1250)" \
    || say "fleet batch SHIP FAILED"
else say "fleet batch NOT SHIPPABLE"; fi
say "=== handing the remainder to the queue ==="
exec bash /home/mboyle/bd-queue-run.sh
