#!/bin/bash
# Re-run the cuts the first batched pass SKIPPED, after it finishes. The skips
# were integrator-side (regen ledger re-pins), not defects in the work, so the
# rows are retried rather than dropped.
set -u
for _ in $(seq 1 720); do
  out=$(/home/mboyle/bd-ps.sh bd-queue-run.sh 2>/dev/null)
  [ -z "$out" ] && break
  sleep 30
done
exec bash /home/mboyle/bd-queue-run.sh
