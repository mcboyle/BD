#!/bin/bash
# CANDIDATE ARM. Runs after the control so the two arms see comparable load
# (both codex workers up). A one-sided comparison at different load is not
# matched evidence, which is the whole point of this experiment.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25
OUT=$A/inflight/w1-control-1303; L="$OUT/summary.txt"
CAND=/home/mboyle/bd-cuts/cut/1303-wait-for-content-not-existence
BAND=$A/inflight/1303-2d0d1c6-a1-band-list.txt
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
for _ in $(seq 1 240); do grep -q 'MATCHED CONTROL COMPLETE' "$L" 2>/dev/null && break; sleep 20; done
grep -q 'MATCHED CONTROL COMPLETE' "$L" 2>/dev/null || { say "control never completed -- candidate arm refusing"; exit 1; }
[ -d "$CAND" ] || { say "candidate worktree gone -- UNKNOWN"; exit 1; }
FILES=$(tr ' ' '\n' < "$BAND" | sed 's/^ *//;s/ *$//' | grep -E '^tests/.*\.py$' | sort -u | tr '\n' ' ')
say "candidate at $(git -C "$CAND" rev-parse --short HEAD); $(printf '%s\n' $FILES | grep -c .) file(s)"
for r in 1 2 3; do
  say "candidate round $r starting (load $(cut -d' ' -f1 /proc/loadavg))"
  ( cd "$CAND" && env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 \
      venv/bin/python -m pytest $FILES -n 24 --dist loadfile --timeout=240 \
      --timeout-method=signal --max-worker-restart=0 -p no:randomly ) \
    > "$OUT/candidate-r$r.log" 2>&1
  say "=== candidate round $r rc=$?"
  grep -E '^FAILED |passed|failed' "$OUT/candidate-r$r.log" | tail -4 | sed 's/^/    /' | tee -a "$L"
done
say "=== MATCHED EXPERIMENT COMPLETE ==="
