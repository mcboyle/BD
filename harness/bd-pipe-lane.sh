#!/bin/bash
# DEPTH-2 PIPELINED LANE. Overlaps cut N's CI wait with cut N+1's verify.
#
# WHAT IS ACTUALLY SERIAL, measured: only integrate (it derives the version from
# main, so two concurrent integrates both claim main+1) and the merge itself.
# Verify runs against an already-integrated candidate worktree and shares nothing.
# bd-merge-lane.sh's flock already serialises ship, so CI waits overlap safely.
#
# COST IF A CUT FAILS: the next cut's version hint is stale and it must be
# re-integrated. That is ~82s of rework, measured -- not a correctness loss.
# GUARD: after every merge this asserts main advanced to the expected version and
# STOPS if it did not, so a stale-version cut can never stack on a failure.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/FINISH.log"
say(){ printf '%s [pipe] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
R=/home/mboyle/BulkDownloader
export KEEP_OPEN_ROWS="243,245,285"

cur_ver(){ git -C "$R" fetch origin main -q 2>/dev/null
           git -C "$R" show origin/main:bulk_downloader/__init__.py 2>/dev/null \
           | grep -oE '3\.66\.[0-9]+' | head -1 | cut -d. -f3; }

BASE=$(cur_ver); [ -n "$BASE" ] || { say "cannot read main version"; exit 2; }
say "main is at $BASE; pipelining $# cut(s) depth 2"

n=0
for spec in "$@"; do
  IFS='|' read -r ROW SLUG TITLE <<<"$spec"
  n=$((n+1)); WANT=$((BASE+n))
  # Hold at depth 2: never more than two chains alive at once.
  while [ "$(pgrep -fc 'bd-row-chain\.sh' 2>/dev/null || echo 0)" -ge 2 ]; do sleep 20; done
  # Before launching, confirm main has not fallen behind what we assumed.
  HAVE=$(cur_ver)
  if [ "$HAVE" -ge "$WANT" ]; then
    say "main already at $HAVE but $ROW was assigned $WANT -- STOPPING, versions diverged"; exit 3
  fi
  say "launch $ROW as v3.66.$WANT (main $HAVE)"
  setsid nohup bash /home/mboyle/bd-row-chain.sh "$ROW" "$WANT" "$SLUG" "$TITLE" \
    >> "$L" 2>&1 < /dev/null &
  sleep 90   # let integrate finish before the next chain reads main
done

# drain, then assert every version landed
while pgrep -f 'bd-row-chain\.sh' >/dev/null 2>&1; do sleep 30; done
FIN=$(cur_ver); EXP=$((BASE+n))
if [ "$FIN" -eq "$EXP" ]; then say "PIPELINE COMPLETE: main $BASE -> $FIN, all $n cut(s) landed"
else say "PIPELINE INCOMPLETE: main is $FIN, expected $EXP -- $((EXP-FIN)) cut(s) did not land"; fi
