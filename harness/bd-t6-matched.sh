#!/bin/bash
# MATCHED CONTROL ON test6: did v1314 introduce the six capture failures, or is
# the box the variable? v1306's capture on THIS HOST had zero failures; v1314's
# had six, three of them in the bd_jobs/remote_job_registry subsystem row 243
# rewrote at v1307. One capture is one sample, and a load difference would
# explain it just as well -- so alternate the arms on the same host.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25
OUT=$A/inflight/t6-matched-1314; mkdir -p "$OUT"; L="$OUT/summary.txt"
IP=10.0.70.249; R=/home/mboyle/BulkDownloader
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
FILES="tests/test_v3_66_1026_heavy_collectors_bounded_for_real.py tests/test_v3_66_1040_remote_job_registry.py tests/test_v3_66_1142_fleet_run_is_hermetic.py tests/test_v3_66_1191_a_run_root_records_its_own_outcome.py"
# v1306 is the last capture on this host with zero failures; v1314 is current.
BASE=$(git -C "$R" rev-list -n1 --grep='v3\.66\.1306' origin/main || true)
CUR=$(git -C "$R" rev-parse origin/main)
[ -n "$BASE" ] || { say "cannot resolve the v1306 commit -- UNKNOWN, refusing"; exit 1; }
say "control=$(echo $BASE|cut -c1-8) (v1306)  candidate=$(echo $CUR|cut -c1-8) (v1314)"
for r in 1 2 3; do
  for arm in control candidate; do
    [ "$arm" = control ] && SHA=$BASE || SHA=$CUR
    say "$arm round $r starting at $(echo $SHA|cut -c1-8)"
    timeout 1800 ssh -n -o BatchMode=yes -o StrictHostKeyChecking=no "$IP" \
      "cd ~/BulkDownloader && git fetch -q origin 2>/dev/null; git checkout -q --detach $SHA 2>/dev/null && \
       env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest $FILES \
         -n 24 --dist loadfile --timeout=240 --timeout-method=signal -p no:randomly -q 2>&1 | tail -25" \
      > "$OUT/$arm-r$r.log" 2>&1
    say "=== $arm round $r rc=$?"
    grep -E '^FAILED |passed|failed' "$OUT/$arm-r$r.log"|tail -3|sed 's/^/    /'|tee -a "$L"
  done
done
say "=== T6 MATCHED EXPERIMENT COMPLETE ==="
