#!/bin/bash
# Resume the trio at 1242 (changelog order repaired), then 1243.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight
L=/home/mboyle/fleet-run-artifacts/2026-08-25/TAIL.log
say(){ echo "$(date -u +%H:%M:%S) $*" | tee -a "$L"; }

say "=== 1242 (row 182) re-verify after changelog repair ==="
W=/home/mboyle/bd-cuts/cut/1242-t2-history-runtime
bash /home/mboyle/bd-verify-cut.sh "$W" 1242-fix > "$A/1242-fix-verify.log" 2>&1
grep -E '^(PRECUT_RC|PREPUSH_RC|BAND_FILES|BAND_RC|VERDICT)' "$A/1242-fix-verify.log" | tee -a "$L"
grep -q 'ALL GREEN -- shippable' "$A/1242-fix-verify.log" || { say "1242 still NOT SHIPPABLE"; exit 3; }
CHECK_FLOOR=20 /home/mboyle/bd-merge-lane.sh /home/mboyle/bd-ship.sh \
  cut/1242-t2-history-runtime \
  "v3.66.1242 the history gate drives the route instead of reading it" \
  "$A/pr-body-1242.md" > "$A/1242-ship.log" 2>&1
RC=$?; tail -4 "$A/1242-ship.log" | tee -a "$L"
[ $RC -ne 0 ] && { say "1242 SHIP FAILED rc=$RC"; exit 4; }
say "ROW 182 MERGED (v3.66.1242)"

say "=== 1243 (row 185) ==="
/home/mboyle/bd-pipeline.sh /home/mboyle/bd-cuts/cut/1243-t8-cluster-runtime 3.66.1243 \
  cut/1243-t8-cluster-runtime \
  "v3.66.1243 the cluster gate drives the route instead of reading it" \
  "$A/pr-body-1243.md" 2>&1 | tee -a "$L"
[ "${PIPESTATUS[0]}" -ne 0 ] && { say "1243 stopped"; exit 5; }
say "ROW 185 MERGED (v3.66.1243)"
say "=== trio complete -- restarting the codex queue ==="
tmux new-session -d -s bd-queue "bash /home/mboyle/bd-queue-run.sh"
