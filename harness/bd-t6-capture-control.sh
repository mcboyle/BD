#!/bin/bash
# FULL-CAPTURE MATCHED CONTROL. The 4-file experiment passed 6/6 on both arms
# and proves nothing: the six failures appeared in a CAPTURE of 18,499 tests
# with every file co-resident, and four files cannot express a cross-file or
# load-dependent mechanism. That under-powered shape is exactly the error row
# 324 made -- measuring at a width the defect cannot surface at, then reading
# green as evidence. So run the real thing at the control version on the SAME
# host the candidate capture ran on.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25
OUT=$A/inflight/t6-capture-control; mkdir -p "$OUT"; L="$OUT/summary.txt"
IP=10.0.70.249; R=/home/mboyle/BulkDownloader
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
BASE=$(git -C "$R" rev-list -n1 --grep='v3\.66\.1306' origin/main || true)
[ -n "$BASE" ] || { say "cannot resolve v1306 -- UNKNOWN, refusing"; exit 1; }
say "control capture at $(echo $BASE|cut -c1-8) (v1306) on test6"
timeout 5400 ssh -n -o BatchMode=yes -o StrictHostKeyChecking=no "$IP" \
  "cd ~/BulkDownloader && git fetch -q origin 2>/dev/null && git checkout -q --detach $BASE && \
   ls -1td /tmp/bd_capture-* 2>/dev/null | head -1 > /tmp/bd-cap-before.txt; \
   bash capture.sh >/tmp/bd-cap-control.log 2>&1; echo rc=\$?; tail -3 /tmp/bd-cap-control.log" \
  > "$OUT/run.log" 2>&1
say "capture rc: $(grep -o 'rc=[0-9]*' "$OUT/run.log"|head -1)"
timeout 120 ssh -n -o BatchMode=yes -o StrictHostKeyChecking=no "$IP" '
  t=$(ls -1td /tmp/bd_capture-*.tar.gz|head -1); echo "bundle=$(basename $t)"
  tar xzOf "$t" --wildcards "*10_VERDICT.txt" 2>/dev/null|head -3
  tar xzOf "$t" --wildcards "*02_SUMMARY.txt" 2>/dev/null|sed -n "1,6p"' >> "$L" 2>&1
# RESTORE THE HOST. Leaving a runner detached at an old version would make the
# next reader think the fleet is behind.
timeout 60 ssh -n -o BatchMode=yes -o StrictHostKeyChecking=no "$IP" \
  'cd ~/BulkDownloader && git checkout -q main 2>/dev/null && git pull -q --ff-only 2>/dev/null; git rev-parse --short HEAD' >> "$L" 2>&1
say "=== CONTROL CAPTURE COMPLETE, host returned to main ==="
