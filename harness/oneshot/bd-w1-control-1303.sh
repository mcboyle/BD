#!/bin/bash
# MATCHED CONTROL for the v1303 band failures.
# One green serial sample does not retire a schedule-sensitive failure (A5).
# Runs the IDENTICAL band file list on CLEAN origin/main in the same
# -n 24 --dist loadfile shape, so candidate and control differ only by the cut.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25
OUT=$A/inflight/w1-control-1303; mkdir -p "$OUT"
L="$OUT/summary.txt"
R=/home/mboyle/BulkDownloader
CTL=/home/mboyle/bd-cuts/control/main-1303
BAND=$A/inflight/1303-2d0d1c6-a1-band-list.txt
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }

# RE-CHECKED BEFORE EVERY ROUND, NOT ONCE AT THE START, and it SAYS WHAT IT SAW.
# A single check at startup raced the previous run's teardown once already and
# put two 24-worker bands on 48 cores simultaneously -- contention that corrupts
# the very comparison this exists to make. Silence about the reason is what made
# that misfire take three tool calls to diagnose.
wait_quiet(){
  local i busy
  for i in $(seq 1 180); do
    busy=$(ps -eo args= | grep -v 'shell-snapshots' \
             | grep -E 'bd-verify-cut\.sh|[p]ytest .*--dist loadfile' | head -2)
    [ -z "$busy" ] && { say "lane quiet (load $(cut -d' ' -f1 /proc/loadavg))"; return 0; }
    [ "$i" = 1 ] && say "waiting: $(printf '%s' "$busy" | head -1 | cut -c1-70)"
    sleep 20
  done
  say "STILL BUSY after 60m -- refusing to measure under contention"; return 1
}

wait_quiet || exit 1
say "building clean control worktree"
rm -rf "$CTL"; git -C "$R" worktree prune 2>/dev/null
git -C "$R" fetch -q origin 2>/dev/null
git -C "$R" worktree add --quiet --detach "$CTL" origin/main || { say "CONTROL WORKTREE FAILED"; exit 1; }
ln -sfn "$R/venv" "$CTL/venv"
ln -sfn "$R/frontend/node_modules" "$CTL/frontend/node_modules" 2>/dev/null
say "control at $(git -C "$CTL" rev-parse --short HEAD) (origin/main)"

# THE BAND LIST NAMES THE CUT'S OWN NEW FILES, which clean main does not have.
# Dropping them is what makes the runs comparable; naming which were dropped is
# what keeps the denominator honest.
FILES=""; DROPPED=""
while read -r f; do
  [ -z "$f" ] && continue
  if [ -e "$CTL/$f" ]; then FILES="$FILES $f"; else DROPPED="$DROPPED $f"; fi
done < <(tr ' ' '\n' < "$BAND" | sed 's/^ *//;s/ *$//' | grep -E '^tests/.*\.py$' | sort -u)
say "control denominator: $(printf '%s\n' $FILES | grep -c .) file(s); dropped (cut-only):${DROPPED:- none}"
[ -n "$FILES" ] || { say "ZERO control files -- UNKNOWN, refusing"; exit 1; }

for r in 1 2 3; do
  wait_quiet || exit 1
  say "control round $r starting"
  ( cd "$CTL" && env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 \
      venv/bin/python -m pytest $FILES -n 24 --dist loadfile --timeout=240 \
      --timeout-method=signal --max-worker-restart=0 -p no:randomly ) \
    > "$OUT/control-r$r.log" 2>&1
  say "=== control round $r rc=$?"
  grep -E '^FAILED |passed|failed' "$OUT/control-r$r.log" | tail -4 | sed 's/^/    /' | tee -a "$L"
done
say "=== MATCHED CONTROL COMPLETE ==="
