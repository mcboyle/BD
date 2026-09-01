#!/bin/bash
# Swap in the overlapped lane ONLY when no chain is mid-flight. bash reads a
# script incrementally, so replacing bd-row-chain.sh in place while it runs makes
# the running shell resume at a byte offset in NEW text -- a hazard already
# recorded in this session's notes. mv is atomic: the running process keeps the
# old inode and the next chain gets the new file.
set -u
L=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight/install-overlap.log
say(){ printf '%s [install] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
while :; do
  n=$(ps -eo args | grep -cE '[b]d-(row-chain|verify-cut|ship|merge-lane)\.sh')
  [ "$n" -eq 0 ] && break
  sleep 20
done
cp -f /home/mboyle/bd-ship.sh      /home/mboyle/bd-ship.sh.preoverlap
cp -f /home/mboyle/bd-row-chain.sh /home/mboyle/bd-row-chain.sh.preoverlap
mv -f /home/mboyle/bd-ship.sh.new      /home/mboyle/bd-ship.sh
mv -f /home/mboyle/bd-row-chain.sh.new /home/mboyle/bd-row-chain.sh
say "overlapped lane installed; rollback: mv *.preoverlap back"
say "ship phases: $(grep -c BD_SHIP_PHASE /home/mboyle/bd-ship.sh) refs; chain abandon-path: $(grep -c _abandon /home/mboyle/bd-row-chain.sh) refs"
