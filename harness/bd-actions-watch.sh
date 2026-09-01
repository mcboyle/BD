#!/bin/bash
# Wait out a GitHub Actions outage, then resume the merge lane BY ITSELF.
#
# The status page is a HINT, never the verdict. A page that says "operational"
# while no run dispatches would resume the lane into the same wall and burn six
# cuts into UNKNOWN. So the page only decides WHEN TO PROBE; the probe -- an
# actual re-fire of the open PR that must produce a real run -- is the evidence.
# Unreachable page => keep waiting. UNKNOWN is not permission.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25
L="$A/ACTIONS_WATCH.log"
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }

REPO=mcboyle/BD
SPECS=(
 "262|every-gate-reaches-a-shard|every declared gate is reachable by a CI shard"
 "268|child-tests-cannot-reach-a-live-install|child test launches cannot inherit a live install dir"
 "260|secret-gate-denominator|the shipped-surface secret gate refuses a zero real-file denominator"
 "243|owned-pytest-launches-register|owned pytest launches register themselves"
 "261|lifecycle-locks-are-measured|lifecycle locks are measured, not asserted from source text"
 "269|bitrot-clean-install-is-zero|a clean install reports zero integrity issues, not unknown"
)

actions_status(){
  curl -s --max-time 15 https://www.githubstatus.com/api/v2/components.json 2>/dev/null \
  | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('UNREACHABLE'); raise SystemExit
for c in d.get('components',[]):
    if c.get('name')=='Actions': print(c.get('status','UNKNOWN')); raise SystemExit
print('UNKNOWN')" 2>/dev/null || echo UNREACHABLE
}

# Real evidence: does a pull_request run EXIST for the open cut PR?
runs_for_open_pr(){
  local br; br=$(gh pr list --repo "$REPO" --state open --json headRefName,number \
        -q '.[] | select(.headRefName|startswith("cut/")) | .headRefName' 2>/dev/null | head -1)
  [ -z "$br" ] && { echo "NOPR"; return; }
  gh run list --repo "$REPO" --branch "$br" --limit 1 --json databaseId -q 'length' 2>/dev/null || echo ERR
}

say "=== actions-watch armed; holding the merge lane until Actions really dispatches ==="
GREEN=0
while :; do
  S=$(actions_status)
  R=$(runs_for_open_pr)
  if [ "$S" = "operational" ]; then GREEN=$((GREEN+1)); else GREEN=0; fi
  say "status=$S consecutive_ok=$GREEN runs_for_open_pr=$R"

  # A run appearing on its own IS the recovery signal -- better than any page.
  if [ "$R" != "0" ] && [ "$R" != "NOPR" ] && [ "$R" != "ERR" ]; then
    say "*** a real run dispatched -- Actions is back ***"; break
  fi
  # Two clean polls AND the page agreeing lets us re-fire the stalled PR.
  if [ "$GREEN" -ge 2 ]; then
    N=$(gh pr list --repo "$REPO" --state open --json number,headRefName \
        -q '.[] | select(.headRefName|startswith("cut/")) | .number' 2>/dev/null | head -1)
    if [ -n "$N" ]; then
      say "page clean twice -- re-firing PR #$N to force a dispatch"
      gh pr close "$N" --repo "$REPO" >/dev/null 2>&1
      sleep 5
      gh pr reopen "$N" --repo "$REPO" >/dev/null 2>&1
      sleep 90
      R2=$(runs_for_open_pr)
      if [ "$R2" != "0" ] && [ "$R2" != "NOPR" ] && [ "$R2" != "ERR" ]; then
        say "*** re-fire produced a run -- Actions is back ***"; break
      fi
      say "re-fire produced NO run -- still down; UNKNOWN is not permission"
      GREEN=0
    fi
  fi
  sleep 120
done

# Never start a second lane on top of a live one.
for _ in $(seq 1 360); do
  pgrep -f 'bd-row-chain\.sh' >/dev/null 2>&1 || break
  sleep 20
done

# Skip anything that merged while we waited -- measured from main, not assumed.
git -C /home/mboyle/BulkDownloader fetch origin main -q 2>/dev/null
declare -a TODO=()
for spec in "${SPECS[@]}"; do
  R1="${spec%%|*}"
  if git -C /home/mboyle/BulkDownloader show origin/main:project-knowledge/IMPROVEMENT_BACKLOG.md 2>/dev/null \
     | grep -qE "^\| $R1 \| CLOSED"; then
    say "skip row $R1: already CLOSED on main"
  else
    TODO+=("$spec")
  fi
done
say "=== resuming lane with ${#TODO[@]} cut(s) ==="
[ "${#TODO[@]}" -eq 0 ] && { say "nothing left to merge"; exit 0; }
KEEP_OPEN_ROWS="243,245" nohup bash /home/mboyle/bd-retry-lane.sh "${TODO[@]}" >>"$A/FINISH.log" 2>&1 &
say "lane relaunched as pid $!"
