#!/bin/bash
# Dispatch row 317's build ONLY when the lane is idle.
# On 2026-08-27 three concurrent codex workers pushed load to 23 and broke the
# timing tests in a band; the cut had to be re-run on a quiet box. Codex builds
# and verify bands must not overlap.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/FINISH.log"; R=/home/mboyle/BulkDownloader
say(){ printf '%s [arm-317] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
say "armed: dispatch row 317 build once 295+261 have merged and the lane is idle"
for _ in $(seq 1 480); do
  git -C "$R" fetch origin main -q 2>/dev/null
  REG=$(git -C "$R" show origin/main:project-knowledge/IMPROVEMENT_BACKLOG.md 2>/dev/null)
  if [ -n "$REG" ] \
     && printf '%s' "$REG" | grep -qE '^\| 295 \|[[:space:]]*CLOSED' \
     && printf '%s' "$REG" | grep -qE '^\| 261 \|[[:space:]]*CLOSED'; then
    for _w in $(seq 1 60); do
      pgrep -f 'bd-verify-cut\.sh|bd-row-chain\.sh|bd-integrate-row\.sh' >/dev/null 2>&1 || break
      sleep 20
    done
    say "295 and 261 merged, lane idle -- dispatching row 317 build"
    bash /home/mboyle/bd-codex-cut.sh 317 "/home/mboyle/bd-codex-briefs/row317-parallelise-mutation-spec-gate.md" \
      >/dev/null 2>&1 || true
    say "row 317 worker dispatched (tmux: cx-row317)"
    exit 0
  fi
  sleep 30
done
say "GAVE UP after 4h: 295/261 never both merged; row 317 NOT dispatched"
