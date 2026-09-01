#!/bin/bash
# FAN-OUT LANE. Integrate serially (unavoidable), VERIFY IN PARALLEL ACROSS THE
# FLEET, ship in version order.
#
# WHY: only two things are actually serial -- integrate (it derives the version
# from main) and the merge order. VERIFY is per-candidate and shares nothing, yet
# bd-row-chain welds all three together, so 9.6 min of band ran on test5 while six
# 48-core hosts sat at load 0.00. That is ~288 idle vCPUs per cut.
#
# Ship stays strictly in version order: the changelog entry for N anchors on N-1.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/FINISH.log"; R=/home/mboyle/BulkDownloader
say(){ printf '%s [fanout] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
HOSTS=(10.0.70.83 10.0.70.95 10.0.70.80 10.0.70.249 10.0.70.84 10.0.70.85)
export KEEP_OPEN_ROWS="243,245,285"

BASE=$(git -C "$R" show origin/main:bulk_downloader/__init__.py | grep -oE '3\.66\.[0-9]+' | head -1 | cut -d. -f3)
say "main at $BASE; $# cut(s); verify fans out over ${#HOSTS[@]} hosts"

declare -a VER=() WT=() ROWID=()
n=0
# ---- PHASE 1: integrate serially, assigning contiguous versions ----------------
for spec in "$@"; do
  IFS='|' read -r ROW SLUG TITLE <<<"$spec"
  n=$((n+1)); V=$((BASE+n))
  say "integrate $ROW -> v3.66.$V"
  if ! bash /home/mboyle/bd-integrate-row.sh "$ROW" "$V" "$SLUG" "$TITLE" >> "$A/inflight/integrate-$ROW.log" 2>&1; then
    say "INTEGRATE FAILED for $ROW -- stopping before anything ships"; exit 4
  fi
  W="/home/mboyle/bd-cuts/cut/$V-$SLUG"
  [ -d "$W" ] || { say "candidate worktree missing: $W"; exit 5; }
  VER+=("$V"); WT+=("$W"); ROWID+=("$ROW")
done
say "all $n integrated; candidates frozen"

# ---- PHASE 2: verify every candidate CONCURRENTLY, one per host ---------------
for i in "${!WT[@]}"; do
  H="${HOSTS[$((i % ${#HOSTS[@]}))]}"
  W="${WT[$i]}"; V="${VER[$i]}"
  say "verify v$V on $H"
  (
    rsync -a --delete --exclude venv --exclude node_modules --exclude .git/hooks \
      "$W/" "$H:/tmp/verify-$V/" >/dev/null 2>&1 || { echo "RSYNC_FAIL" > "$A/inflight/$V.fanout"; exit 1; }
    ssh -o BatchMode=yes "$H" "cd /tmp/verify-$V || exit 90
      ln -sfn /home/mboyle/BulkDownloader/venv venv
      ln -sfn /home/mboyle/BulkDownloader/frontend/node_modules frontend/node_modules 2>/dev/null
      env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest \
        \$(venv/bin/python toolchain/bin/bd-band-derive --work /tmp/verify-$V --emit 2>/dev/null | sed 's/^ *bd-band *//') \
        -n 24 --dist loadfile --timeout=240 --timeout-method=signal -p no:randomly -q 2>&1 | tail -5" \
      > "$A/inflight/$V.fanout" 2>&1
  ) &
done
wait
say "all verifies returned"

# ---- PHASE 3: ship IN VERSION ORDER ------------------------------------------
for i in "${!VER[@]}"; do
  V="${VER[$i]}"; W="${WT[$i]}"; ROW="${ROWID[$i]}"
  if grep -qE '[0-9]+ failed|RSYNC_FAIL|FATAL' "$A/inflight/$V.fanout" 2>/dev/null; then
    say "STOP: v$V (row $ROW) verify NOT clean -- $A/inflight/$V.fanout"; exit 6
  fi
  say "ship v$V (row $ROW)"
  BR="cut/$V-$(basename "$W" | cut -d- -f2-)"
  git -C "$W" branch -f "$BR" HEAD 2>/dev/null
  B="$A/inflight/pr-body-$V.md"; [ -f "$B" ] || printf 'Row %s. Integrated as v3.66.%s.\n' "$ROW" "$V" > "$B"
  bash /home/mboyle/bd-merge-lane.sh bash /home/mboyle/bd-ship.sh "$BR" "v3.66.$V" "$B" \
    >> "$A/inflight/$V-ship.log" 2>&1 || { say "STOP: ship failed for v$V"; exit 7; }
  say "MERGED v3.66.$V (row $ROW)"
done
say "FANOUT COMPLETE: main $BASE -> $((BASE+n))"
