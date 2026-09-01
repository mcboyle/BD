#!/bin/bash
# One cut, end to end: rebase onto current main -> verify -> ship.
# Refuses loudly at every non-green step. UNKNOWN is never permission.
# Usage: bd-pipeline.sh <worktree> <version> <branch> <title> <pr-body>
set -u
W="$1"; V="$2"; BR="$3"; TITLE="$4"; BODY="$5"
TAG="$(basename "$W" | tr '/' '-')"
A=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight
say(){ echo "$(date -u +%H:%M:%S) [$V] $*"; }

say "rebasing onto origin/main"
python3 /home/mboyle/bd-rebase-cut.py --work "$W" --version "$V" > "$A/$TAG-rebase.log" 2>&1
if [ $? -ne 0 ]; then say "REBASE REFUSED -- see $A/$TAG-rebase.log"; tail -5 "$A/$TAG-rebase.log"; exit 2; fi
tail -3 "$A/$TAG-rebase.log"

say "verifying"
bash /home/mboyle/bd-verify-cut.sh "$W" "$TAG-final" > "$A/$TAG-final-verify.log" 2>&1
VER=$(grep -c 'ALL GREEN -- shippable' "$A/$TAG-final-verify.log")
grep -E '^(PRECUT_RC|PREPUSH_RC|BAND_FILES|BAND_RC|VERDICT)' "$A/$TAG-final-verify.log"
if [ "$VER" -ne 1 ]; then say "NOT SHIPPABLE -- stopping, PR not opened"; exit 3; fi

say "shipping"
CHECK_FLOOR=20 /home/mboyle/bd-merge-lane.sh /home/mboyle/bd-ship.sh "$BR" "$TITLE" "$BODY" > "$A/$TAG-ship.log" 2>&1
RC=$?
tail -6 "$A/$TAG-ship.log"
if [ $RC -ne 0 ]; then say "SHIP FAILED rc=$RC -- PR left open, main untouched"; exit 4; fi
say "MERGED"
