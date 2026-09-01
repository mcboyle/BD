#!/bin/bash
# One-shot: deploy the fleet once v3.66.1299 (rows 296+243) is merged.
# Operator ruling 2026-08-27: deploy after 296+243, not waiting on the codex
# rebuilds of 292/295/261/308.
#
# test5 is the INTEGRATOR and is never deployed; bd-fleet-deploy.sh excludes it
# structurally via the `local` marker in ~/.config/bd/hosts. This script does
# not name hosts at all -- it defers to that tool.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/FINISH.log"; R=/home/mboyle/BulkDownloader
say(){ printf '%s [arm-deploy] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
say "armed: fleet deploy fires when origin/main reaches v3.66.1299"
for _ in $(seq 1 720); do
  git -C "$R" fetch origin main -q 2>/dev/null
  V=$(git -C "$R" show origin/main:bulk_downloader/__init__.py 2>/dev/null | grep -oE '3\.66\.[0-9]+' | head -1)
  [ -z "$V" ] && { sleep 30; continue; }          # unread version proves nothing
  MV=${V##*.}
  if [ "${MV:-0}" -ge 1299 ] 2>/dev/null; then
    # A LANE STILL RUNNING MEANS THE TREE CAN MOVE UNDER THE DEPLOY.
    for _w in $(seq 1 60); do
      pgrep -f 'bd-row-chain\.sh|bd-verify-cut\.sh|bd-integrate-row\.sh' >/dev/null 2>&1 || break
      sleep 20
    done
    say "main is $V and the lane is quiet -- deploying the fleet"
    if bash /home/mboyle/bd-fleet-deploy.sh >>"$A/deploy/arm-$(date -u +%Y%m%dT%H%M%SZ).log" 2>&1; then
      say "FLEET DEPLOY COMPLETE at $V"
    else
      say "FLEET DEPLOY REPORTED FAILURES -- see $A/deploy/, NOT claiming health"
    fi
    exit 0
  fi
  sleep 30
done
say "GAVE UP after 6h: main never reached v3.66.1299; NO deploy performed"
