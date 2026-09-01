#!/bin/bash
# Resume batch 1: the 13 queued rows in the brief-specified order.
# 285 precedes 291 (shared file). KEEP_OPEN_ROWS keeps 243/245 open (row-174 transfer gate).
# bd-serial-lane.sh stops on first failure by design.
cd /home/mboyle || exit 1
export KEEP_OPEN_ROWS="243,245,285"
setsid nohup bash bd-serial-lane.sh \
  "281|ui-wrappers-judge-delegation|the UI wrapper gates judge delegation, not shape" \
  "283|bd-claim-survives-concurrency|bd-claim survives concurrent readers and adders" \
  "284|settlement-budget-honours-its-bound|the ambiguous-launch settlement budget honours its governing bound" \
  "287|application-findings-refuted|four application findings are refuted with tracked mutants" \
  "280|toolchain-measurement-row-closes-moot|row 280 closes MOOT: v3.66.1268 already fixed all five sites" \
  "282|bd-opv-isolates-every-store|bd-opv isolates every store its own checks mutate" \
  "286|ipv4-fallback-cannot-clear-killswitch|an IPv4 fallback can no longer clear the VPN kill switch" \
  "288|harness-state-is-owned|test harnesses own the state they mutate" \
  "289|inherited-signals-are-environment-identity|inherited signal disposition is recorded environment identity" \
  "243|owned-pytest-launches-register|owned pytest launches register themselves" \
  "261|lifecycle-locks-are-measured|lifecycle locks are measured, not asserted from source text" \
  "285|deploy-fails-closed|deploy and capture-service steps fail closed on unavailable state" \
  "291|descriptor-control-proves-its-precondition|the descriptor negative control proves its own precondition" \
  >> /home/mboyle/fleet-run-artifacts/2026-08-25/FINISH.log 2>&1 < /dev/null &
echo "lane launched pid $!"
