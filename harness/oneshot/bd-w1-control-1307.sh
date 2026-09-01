#!/bin/bash
# MATCHED CONTROL for the v1307 (row 243) 1190 descendant-kill failure.
# Row 243 changes how bd-mutate launches pytest, and test_v3_66_1190 measures
# exactly that process tree -- so a single green band does not retire the one
# red band (A5). Alternating arms, same shape, same neighbours.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25
OUT=$A/inflight/w1-control-1307; mkdir -p "$OUT"; L="$OUT/summary.txt"
R=/home/mboyle/BulkDownloader
CTL=/home/mboyle/bd-cuts/control/main-1307
CAND=/home/mboyle/bd-cuts/cut/1307-owned-pytest-launches-register
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
FILES="tests/test_v3_66_1190_bd_mutate_kills_process_tree.py tests/test_desandbox_tool_verifiers.py tests/test_v3_66_1237_mutant_validity_is_not_assumed.py tests/test_v3_66_1184_mutation_specs_are_tracked.py"
wait_quiet(){
  local i busy
  for i in $(seq 1 180); do
    busy=$(ps -eo args= | grep -v 'shell-snapshots' | grep -E 'bd-verify-cut\.sh|[p]ytest .*--dist loadfile' | head -1)
    [ -z "$busy" ] && return 0
    [ "$i" = 1 ] && say "waiting: $(printf '%s' "$busy"|cut -c1-60)"
    sleep 20
  done
  say "STILL BUSY after 60m -- refusing to measure under contention"; return 1
}
wait_quiet || exit 1
rm -rf "$CTL"; git -C "$R" worktree prune 2>/dev/null; git -C "$R" fetch -q origin 2>/dev/null
git -C "$R" worktree add --quiet --detach "$CTL" origin/main || { say "CONTROL WORKTREE FAILED"; exit 1; }
ln -sfn "$R/venv" "$CTL/venv"; ln -sfn "$R/frontend/node_modules" "$CTL/frontend/node_modules" 2>/dev/null
say "control at $(git -C "$CTL" rev-parse --short HEAD) / candidate at $(git -C "$CAND" rev-parse --short HEAD)"
for r in 1 2 3; do
  for arm in control candidate; do
    [ "$arm" = control ] && D="$CTL" || D="$CAND"
    wait_quiet || exit 1
    say "$arm round $r starting (load $(cut -d' ' -f1 /proc/loadavg))"
    ( cd "$D" && env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 \
        venv/bin/python -m pytest $FILES -n 24 --dist loadfile --timeout=240 \
        --timeout-method=signal --max-worker-restart=0 -p no:randomly ) \
      > "$OUT/$arm-r$r.log" 2>&1
    say "=== $arm round $r rc=$?"
    grep -E '^FAILED |passed|failed' "$OUT/$arm-r$r.log"|tail -3|sed 's/^/    /'|tee -a "$L"
  done
done
say "=== MATCHED EXPERIMENT COMPLETE ==="
