#!/bin/bash
# Matched-environment experiment for the two W1 band failures at v3.66.1241.
# ARMS: candidate (cec963f) and control (origin/main). Same band list, same
# shape, INTERLEAVED rounds so ambient load is comparable rather than assumed.
# The control cannot run the lifecycle file -- it is new in this cut -- so per
# arm we record which files were actually collected. A failure that appears on
# BOTH arms is pre-existing and belongs to row 241; one that appears only on the
# candidate is a regression this cut owns.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight
OUT=$A/w1-matched
mkdir -p "$OUT"
CAND=/home/mboyle/bd-cuts/cut/1241-owner-observation-deadline
CTRL=/home/mboyle/bd-cuts/control-main
R=/home/mboyle/BulkDownloader

if [ ! -d "$CTRL" ]; then
  git -C "$R" worktree add --detach "$CTRL" origin/main >/dev/null 2>&1 || { echo "control worktree failed"; exit 1; }
  ln -sfn "$R/venv" "$CTRL/venv"
  ln -sfn "$R/frontend/node_modules" "$CTRL/frontend/node_modules" 2>/dev/null
fi

BAND=$(sed -e 's/^[[:space:]]*bd-band[[:space:]]*//' "$A/1241-rebased-r2-band-list.txt" | tr -s ' \n' ' ')

run_arm(){ # $1=dir $2=label $3=round
  cd "$1" || return 1
  local files=""; local n=0
  for f in $BAND; do [ -f "$1/$f" ] && { files="$files $f"; n=$((n+1)); }; done
  echo "$(date -u +%H:%M:%S) $2 r$3 collecting $n file(s) load=$(cut -d' ' -f1 /proc/loadavg)" >> "$OUT/summary.txt"
  # shellcheck disable=SC2086
  env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 \
    timeout 3000 venv/bin/python -m pytest $files -n 12 --dist loadfile \
    --timeout=240 --timeout-method=signal --max-worker-restart=0 -p no:randomly \
    > "$OUT/$2-r$3.log" 2>&1
  local rc=$?
  {
    echo "=== $2 round $3 rc=$rc files=$n"
    grep -E '^(FAILED|ERROR)' "$OUT/$2-r$3.log" | sed 's/^/    /'
    tail -1 "$OUT/$2-r$3.log" | sed 's/^/    /'
  } >> "$OUT/summary.txt"
}

for r in 1 2 3; do
  run_arm "$CAND" candidate "$r"
  run_arm "$CTRL" control   "$r"
done
echo "=== MATCHED EXPERIMENT COMPLETE ===" >> "$OUT/summary.txt"
grep -cE '^    FAILED' "$OUT/summary.txt" >> "$OUT/summary.txt"
