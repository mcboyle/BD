#!/bin/bash
set -u
for _ in $(seq 1 240); do [ -z "$(/home/mboyle/bd-ps.sh bd-queue-run.sh 2>/dev/null)" ] && break; sleep 30; done
exec bash /home/mboyle/bd-queue-run.sh
