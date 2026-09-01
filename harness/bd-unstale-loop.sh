#!/usr/bin/env bash
# Keep blocked worker trees free of STALE GENERATED ARTIFACTS, after every merge.
#
# WHY A LOOP AND NOT A ONE-SHOT. Every merge moves origin/main, which re-stales
# DEPENDENCY_GRAPH.json / FUNCTION_INDEX.md / source_window_hashes.json in every
# worker tree that has not merged yet. Those files are rebuilt by
# bd-regen-order, so a worker's copy carries no information -- but the
# integrator's 3-way apply still conflicts on them and refuses the row. Five
# rows were marked BLOCKED overnight for exactly that, none for a real content
# conflict. Restoring them to main after each merge keeps the queue moving.
#
# It watches the MERGE COUNT, not a grep for a marker: FINISH.log is appended
# across attempts, so an earlier run's "MERGED" line would satisfy a naive grep
# forever and the loop would fire once and never again.
set -uo pipefail
R=/home/mboyle
FIN=$R/fleet-run-artifacts/2026-08-25/FINISH.log
LOG=$R/fleet-run-artifacts/2026-08-25/unstale-loop.log
ROWS="${BD_UNSTALE_ROWS:-366 369 370 371 374 375 376 377 378}"

seen=$(grep -c "MERGED" "$FIN" 2>/dev/null || echo 0)
while true; do
  now=$(grep -c "MERGED" "$FIN" 2>/dev/null || echo 0)
  if [ "$now" -gt "$seen" ]; then
    seen=$now
    sleep 20                      # let the merge settle and origin/main update
    out=$(python3 "$R/bd-persist/harness/bd-unstale-generated.py" $ROWS 2>&1 \
          | grep -c "restored" || echo 0)
    printf '%s after merge #%s: re-based generated artifacts in %s worktree(s)\n' \
      "$(date -u +%H:%M:%SZ)" "$now" "$out" >> "$LOG"
  fi
  sleep 30
done
