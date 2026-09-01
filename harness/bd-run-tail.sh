#!/bin/bash
# Autonomous tail of the queue: ship 1241 (already rebased+verifying), then run
# 1242 and 1243 through the full pipeline, then batch-deploy the fleet.
# Each step refuses rather than proceeding on UNKNOWN. Stops the chain on any
# non-zero so a bad cut cannot drag the next one onto a poisoned base.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight
L=/home/mboyle/fleet-run-artifacts/2026-08-25/TAIL.log
say(){ echo "$(date -u +%H:%M:%S) $*" | tee -a "$L"; }

say "=== waiting for 1241 verification ==="
for _ in $(seq 1 540); do grep -q '^VERDICT 1241-rebased-r2' "$A/1241-rebased-r2-verify.log" 2>/dev/null && break; sleep 20; done
grep -E '^(PRECUT_RC|PREPUSH_RC|BAND_FILES|BAND_RC|VERDICT)' "$A/1241-rebased-r2-verify.log" | tee -a "$L"
if ! grep -q 'ALL GREEN -- shippable' "$A/1241-rebased-r2-verify.log"; then
  say "1241 NOT SHIPPABLE -- chain stopped, nothing pushed"; exit 3; fi

say "=== shipping 1241 (row 237) ==="
CHECK_FLOOR=20 /home/mboyle/bd-merge-lane.sh /home/mboyle/bd-ship.sh cut/1241-owner-observation-deadline \
  "v3.66.1241 an observation gets its own clock, and the split lands" \
  "$A/pr-body-1241.md" > "$A/1241-ship.log" 2>&1
RC=$?; tail -6 "$A/1241-ship.log" | tee -a "$L"
[ $RC -ne 0 ] && { say "1241 SHIP FAILED rc=$RC -- chain stopped"; exit 4; }
say "ROW 237 MERGED (v3.66.1241)"

say "=== 1242 (row 182) ==="
/home/mboyle/bd-pipeline.sh /home/mboyle/bd-cuts/cut/1242-t2-history-runtime 3.66.1242 \
  cut/1242-t2-history-runtime \
  "v3.66.1242 the history gate drives the route instead of reading it" \
  "$A/pr-body-1242.md" 2>&1 | tee -a "$L"
[ "${PIPESTATUS[0]}" -ne 0 ] && { say "1242 stopped"; exit 5; }
say "ROW 182 MERGED (v3.66.1242)"

say "=== 1243 (row 185) ==="
/home/mboyle/bd-pipeline.sh /home/mboyle/bd-cuts/cut/1243-t8-cluster-runtime 3.66.1243 \
  cut/1243-t8-cluster-runtime \
  "v3.66.1243 the cluster gate drives the route instead of reading it" \
  "$A/pr-body-1243.md" 2>&1 | tee -a "$L"
[ "${PIPESTATUS[0]}" -ne 0 ] && { say "1243 stopped"; exit 6; }
say "ROW 185 MERGED (v3.66.1243)"

# NO DEPLOY HERE. Operator ruling 2026-08-25 17:15: ONE deploy at the very end,
# alongside the single sanctioned full suite, rather than a batch every 3 merges.
# The fleet therefore runs a stale version for the rest of this run ON PURPOSE.
say "=== trio merged; NOT deploying (deploy is once, at the end) ==="
say "=== tail complete ==="
