#!/bin/bash
# THE LANE DIED SILENTLY FOR 3.5 HOURS ON 2026-08-28 and the heartbeat that
# should have reported it died in the same event, so its silence read as health.
# That is the fail-open shape this whole run has been fixing: an absent
# measurement presented as an OK one. This watchdog is deliberately SEPARATE
# from the driver it guards, and it says so out loud when it acts.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25
say(){ printf '%s [watchdog] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$A/FINISH.log"; }
say "armed; guarding night, codex-pool, codex-refill, att-guard, width-restore"
while true; do
  for s in bd-night.sh bd-att-guard.sh bd-autorebase.sh bd-persist-loop.sh bd-checkpoint-loop.sh bd-unstale-loop.sh; do
    # Match how the process was LAUNCHED, and exclude this shell's own argv --
    # an absolute-path matcher missed a relative-path launch earlier tonight.
    n=$(ps -eo args= | grep -v shell-snapshots | grep -v '[w]atchdog' \
        | grep -cE "^bash (/home/mboyle/)?$s")
    if [ "$n" -eq 0 ]; then
      say "$s IS NOT RUNNING -- restarting it"
      ( cd /home/mboyle && setsid nohup bash "$s" >/dev/null 2>&1 < /dev/null & )
      sleep 10
    fi
  done
  sleep 120
done
