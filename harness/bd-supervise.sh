#!/bin/bash
# Detached supervisor: if bd-drain.sh dies (e.g. its parent session shell is
# HUP'd on a compact -- measured SigIgn=0x4, SIGHUP NOT ignored), relaunch it with
# ONLY the rows still OPEN on main. Never double-runs: it refuses while a drain or
# chain is alive. Match on comm, never `pgrep -f` (that self-matches).
set -uo pipefail
R=/home/mboyle/BulkDownloader; L=/home/mboyle/fleet-run-artifacts/2026-08-25/FINISH.log
declare -A SPEC=(
 [285]="285|deploy-fails-closed|deploy and capture-service steps fail closed on unavailable state"
 [291]="291|descriptor-control-proves-its-precondition|the descriptor negative control proves its own precondition"
 [287]="287|application-findings-refuted|four application findings are refuted with tracked mutants"
 [280]="280|toolchain-measurement-row-closes-moot|row 280 closes MOOT: v3.66.1268 already fixed all five sites"
 [294]="294|bd-opv-isolates-every-store|bd-opv isolates every store its own checks mutate"
 [295]="295|bd-claim-locking-and-atomic-publish|bd-claim keeps row 283 locking and gains an atomic publish"
 [289]="289|inherited-signals-are-environment-identity|inherited signal disposition is recorded environment identity"
 [243]="243|owned-pytest-launches-register|owned pytest launches register themselves"
)
ORDER="285 291 287 280 294 295 289 243"
while :; do
  alive=$(ps -eo comm=,args= | awk '$1=="bash" && /bd-(drain|row-chain)\.sh/ && !/shell-snapshots/' | wc -l)
  if [ "$alive" -eq 0 ]; then
    git -C "$R" fetch origin main -q 2>/dev/null
    reg=$(git -C "$R" show origin/main:project-knowledge/IMPROVEMENT_BACKLOG.md 2>/dev/null)
    [ -n "$reg" ] || { sleep 60; continue; }   # unreadable register is UNKNOWN, not "all done"
    # A ROW IN KEEP_OPEN_ROWS NEVER GOES CLOSED. 243 and 285 stay OPEN by operator
    # ruling, so "CLOSED" is not a completion signal for them and this loop would
    # relaunch them forever. Completion = the cut's SLUG appears in a merge commit
    # on main. Checked for every row, so one rule covers both kinds.
    log=$(git -C "$R" log --oneline origin/main 2>/dev/null | head -60)
    [ -n "$log" ] || { sleep 60; continue; }
    todo=()
    for r in $ORDER; do
      slug=$(printf '%s' "${SPEC[$r]}" | cut -d'|' -f2)
      printf '%s' "$log" | grep -q -- "$slug" && continue          # its cut merged
      printf '%s' "$reg" | grep -qE "^\| $r \| *CLOSED" && continue
      todo+=("${SPEC[$r]}")
    done
    if [ "${#todo[@]}" -eq 0 ]; then
      printf '%s [supervise] all rows closed on main -- exiting\n' "$(date -u +%H:%M:%S)" | tee -a "$L"; exit 0
    fi
    printf '%s [supervise] drain is DEAD -- relaunching %s remaining row(s)\n' "$(date -u +%H:%M:%S)" "${#todo[@]}" | tee -a "$L"
    KEEP_OPEN_ROWS="243,245,285" bash /home/mboyle/bd-drain.sh "${todo[@]}" >>"$L" 2>&1
  fi
  sleep 45
done
