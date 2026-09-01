#!/bin/bash
# WHICH CUT INTRODUCED THE SIX CAPTURE FAILURES? Same host, same command, one
# version at a time. The control capture proved it is not load: v1306 gave 0
# failures and v1314 gave 6 on test6. Three of the six are in bd_jobs /
# remote_job_registry, which row 243 rewrote at v1307, so that is the first
# suspect -- but a suspect is not a finding, and the honest instrument is to run
# the four named files at each version and watch where the count changes.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25
OUT=$A/inflight/bisect-1314; mkdir -p "$OUT"; L="$OUT/summary.txt"
IP=10.0.70.249; R=/home/mboyle/BulkDownloader
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
FILES="tests/test_v3_66_1026_heavy_collectors_bounded_for_real.py tests/test_v3_66_1040_remote_job_registry.py tests/test_v3_66_1142_fleet_run_is_hermetic.py tests/test_v3_66_1191_a_run_root_records_its_own_outcome.py"
say "bisect on test6; four files that failed in the v1314 capture"
for v in 1306 1307 1308 1310 1312 1314; do
  SHA=$(git -C "$R" rev-list -n1 --grep="v3\.66\.$v" origin/main || true)
  [ -n "$SHA" ] || { say "v$v: cannot resolve -- UNKNOWN, skipped"; continue; }
  # THREE ROUNDS PER VERSION. One sample cannot distinguish a real change from a
  # schedule-sensitive one, and this whole investigation exists because a single
  # capture was read as proof.
  fails=0
  for r in 1 2 3; do
    timeout 900 ssh -n -o BatchMode=yes -o StrictHostKeyChecking=no "$IP" \
      "cd ~/BulkDownloader && git checkout -q --detach $SHA 2>/dev/null && \
       env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest $FILES \
         -n 24 --dist loadfile --timeout=240 --timeout-method=signal -p no:randomly -q 2>&1 | tail -4" \
      > "$OUT/v$v-r$r.log" 2>&1
    grep -qE '[0-9]+ failed' "$OUT/v$v-r$r.log" && fails=$((fails+1))
  done
  say "v$v ($(echo $SHA|cut -c1-8)): $fails of 3 rounds red -- $(grep -hoE '[0-9]+ (passed|failed)' "$OUT/v$v-r3.log"|tr '\n' ' ')"
done
timeout 60 ssh -n -o BatchMode=yes -o StrictHostKeyChecking=no "$IP" \
  'cd ~/BulkDownloader && git checkout -q main 2>/dev/null && git pull -q --ff-only 2>/dev/null; git rev-parse --short HEAD' >> "$L" 2>&1
say "=== BISECT COMPLETE, host returned to main ==="
