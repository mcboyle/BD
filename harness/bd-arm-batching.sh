#!/bin/bash
# One-shot: wait for row 320 to merge, then apply the operator-authorized
# deferral+batching change exactly once and exit. The apply script carries its
# own version guard, so a spurious wake cannot apply it early.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/FINISH.log"
AP="$A/operator-actions/apply-batching-2026-08-27.sh"
say(){ printf '%s [arm] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
say "armed: will apply deferral+batching when origin/main reaches v3.66.1294"
for _ in $(seq 1 720); do          # 6h ceiling, then give up loudly
  git -C /home/mboyle/BulkDownloader fetch origin main -q 2>/dev/null
  V=$(git -C /home/mboyle/BulkDownloader show origin/main:bulk_downloader/__init__.py 2>/dev/null | grep -oE '3\.66\.[0-9]+' | head -1)
  if [ "$V" = "3.66.1294" ]; then
    say "row 320 merged at $V -- applying deferral+batching"
    if bash "$AP" >>"$L" 2>&1; then say "APPLIED: risky four deferred, BATCH_CAP=5"; else say "APPLY FAILED -- see $L, batching NOT enabled"; fi
    exit 0
  fi
  sleep 30
done
say "GAVE UP after 6h: origin/main never reached v3.66.1294; batching NOT enabled"
