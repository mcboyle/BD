#!/bin/bash
# ONE PR IN CI AT A TIME. A dozen concurrent cuts would fire 22 checks each into
# a shared GitHub Actions budget; the queue that produces makes the ship script's
# 30-minute wait time out into an UNKNOWN, which REFUSES the merge -- so pushing
# everything at once costs merges rather than buying them. Builds stay parallel;
# only the push/merge step is serialized. The lockfile is the whole mechanism.
set -u
LOCK=/home/mboyle/.bd-merge-lane.lock
exec 9>"$LOCK"
if ! flock -w "${LANE_WAIT:-7200}" 9; then echo "merge lane busy -- gave up"; exit 75; fi
echo "$(date -u +%H:%M:%S) merge lane acquired by $*"
"$@"
RC=$?
echo "$(date -u +%H:%M:%S) merge lane released rc=$RC"
exit $RC
