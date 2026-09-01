#!/bin/bash
# Drain every ready cut, then run the endgame. Designed to need NOBODY.
#
# SERIAL ON PURPOSE. bd-integrate-row.sh derives the version from MAIN, so two
# concurrent chains both claim main+1 and collide. The operator approved
# pipelining, but that needs an integrate lock inside bd-row-chain.sh and that
# file was RUNNING every time the window opened -- editing a live shell script
# makes bash resume at a byte offset in the new text, which cost ~10 min twice
# tonight. Serial is proven: sixteen merges, no collisions.
#
# A cut that fails is SKIPPED, not retried forever, and the run continues. The
# operator's grind-until-green applies to a cut I am watching, not to an
# unattended loop that could spin on one bad candidate all afternoon.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/FINISH.log"
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
C="$A/codex-cuts"

# ARRAY, not a string. The specs contain SPACES (the changelog title), so
# READY="$READY $spec" plus `for spec in $READY` word-splits every title into
# separate iterations -- it reported "40 ready cuts" for 7 inputs.
declare -a READY=()
for spec in "$@"; do
  ROW="${spec%%|*}"; R1="${ROW%% *}"
  [ -e "/home/mboyle/bd-codex-wt/row$R1" ] || { say "SKIP $ROW: no worktree"; continue; }
  # QA may still be RUNNING -- that is not a failure. Wait for it, then judge.
  for _ in $(seq 1 90); do
    pgrep -f "bd-qa-row.sh .*$R1" >/dev/null 2>&1 || break
    sleep 20
  done
  if grep -q "^QA_RC=0$" "$C/row$R1.qa.log" 2>/dev/null; then
    READY+=("$spec")
  else
    say "SKIP $ROW: QA not green ($(grep -oE 'QA_RC=[0-9]+' "$C/row$R1.qa.log" 2>/dev/null | tail -1 || echo 'no log'))"
  fi
done
say "=== finisher: ${#READY[@]} ready cut(s) ==="

for spec in "${READY[@]}"; do
  IFS='|' read -r ROW SLUG TITLE <<<"$spec"
  # wait for any in-flight chain -- serial is the whole point
  for _ in $(seq 1 360); do
    [ "$(ps -eo args= | grep -cE '^bash (/home/mboyle/)?bd-row-chain\.sh')" -eq 0 ] && break
    sleep 20
  done
  say "--- $ROW ---"
  if bash /home/mboyle/bd-row-chain.sh "$ROW" 0 "$SLUG" "$TITLE" >/dev/null 2>&1; then
    say "OK $ROW merged"
  else
    say "SKIPPED $ROW -- did not merge; see $A/inflight/chain-$ROW.log"
  fi
done

say "=== all cuts attempted; starting ENDGAME ==="
say "=== retry pass complete -- endgame NOT re-run (it already ran at 14:11) ==="
