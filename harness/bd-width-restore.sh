#!/bin/bash
# CLIMB THE BATCH WIDTH BACK AFTER THE LADDER DEMOTES IT.
# bd-night demotes 8->3->2->1 when a batch fails as a unit, which is right: one
# bad member must not keep killing innocent rows. But nothing ever climbs BACK,
# so a single unrelated failure taxes every later cut with single-row batches --
# and cost is per CUT, not per row (verify->ship measures ~9.5 min either way).
# Two consecutive clean merges is evidence the tree is healthy again.
#
# This does NOT touch bd-night.sh, which re-reads the width from this file every
# pass by design. Nothing is relaxed: the ladder still demotes on the very next
# failure, and the grouper still requires file-disjointness.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25
CAP="$A/night/batch-cap"; L="$A/inflight/width-restore.log"; F="$A/FINISH.log"
CEIL=${BD_WIDTH_CEILING:-8}
say(){ printf '%s [width] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
N=$(wc -l < "$F"); clean=0
while :; do
  sleep 60
  M=$(wc -l < "$F")
  [ "$M" -le "$N" ] && continue
  new=$(tail -n +$((N+1)) "$F"); N=$M
  # A REFUSAL RESETS THE STREAK. Only consecutive clean merges count as evidence.
  if printf '%s' "$new" | grep -qE 'REFUSED|SKIP|BLOCKED'; then
    [ "$clean" -gt 0 ] && say "streak reset by a refusal"
    clean=0; continue
  fi
  printf '%s' "$new" | grep -q 'MERGED' || continue
  clean=$((clean+1))
  cur=$(cat "$CAP" 2>/dev/null); case "$cur" in ''|*[!0-9]*) cur=1;; esac
  say "clean merge #$clean at width $cur"
  if [ "$clean" -ge 2 ] && [ "$cur" -lt "$CEIL" ]; then
    nxt=$(( cur * 2 )); [ "$nxt" -gt "$CEIL" ] && nxt=$CEIL
    echo "$nxt" > "$CAP"; clean=0
    say "two clean merges -- width $cur -> $nxt (ceiling $CEIL)"
  fi
done
