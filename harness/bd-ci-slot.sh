#!/bin/bash
# N PRs IN CI AT ONCE, N=3 by default. Replaces bd-merge-lane.sh's single flock.
#
# WHY THE SINGLE LANE EXISTED: a dozen concurrent cuts fire 22 checks each into a
# shared GitHub Actions budget. The resulting queue makes bd-ship.sh's 30-minute
# wait expire into UNKNOWN, and an UNKNOWN REFUSES the merge -- so pushing
# everything at once costs merges rather than buying them.
# WHY 3 IS DIFFERENT: ~66 checks in flight, not ~264. Operator ruling 2026-08-26:
# run 3, and DROP TO 2 IF IT DEGRADES. The signature to watch is not a red check
# -- it is a poll count that stalls with checks stuck non-terminal.
#
#   usage: bd-ci-slot.sh <cmd...>          env: SLOTS (default 3), LANE_WAIT
# Each slot is its own lockfile; we take the first free one. Non-blocking probe
# per slot, then a blocking wait on slot 1 so nobody spins.
set -u
SLOTS="${SLOTS:-3}"
WAIT="${LANE_WAIT:-7200}"
D=/home/mboyle/.bd-ci-slots; mkdir -p "$D"
for i in $(seq 1 "$SLOTS"); do
  exec 9>"$D/slot$i"
  if flock -n 9; then
    echo "$(date -u +%H:%M:%S) ci slot $i/$SLOTS acquired by $*"
    "$@"; RC=$?
    echo "$(date -u +%H:%M:%S) ci slot $i/$SLOTS released rc=$RC"
    exit $RC
  fi
  exec 9>&-
done
# every slot busy -- block on slot 1 rather than spin
exec 9>"$D/slot1"
if ! flock -w "$WAIT" 9; then echo "all $SLOTS ci slots busy -- gave up"; exit 75; fi
echo "$(date -u +%H:%M:%S) ci slot 1/$SLOTS acquired after wait by $*"
"$@"; RC=$?
echo "$(date -u +%H:%M:%S) ci slot 1/$SLOTS released rc=$RC"
exit $RC
