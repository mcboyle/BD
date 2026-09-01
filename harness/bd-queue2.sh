#!/bin/bash
# Second queue pass: the rows not in the first SPECS list, plus the BATCHED
# 183+184 cut (operator ruling 2026-08-25: same-class rows land under one
# safety contract). Waits for the first queue to finish so the merge lane and
# the release trio stay serial.
set -u
for _ in $(seq 1 720); do tmux has-session -t bd-queue 2>/dev/null || break; sleep 30; done
export SPECS_OVERRIDE=1
exec bash /home/mboyle/bd-queue-run.sh
