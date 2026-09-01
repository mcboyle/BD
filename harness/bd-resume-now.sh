#!/bin/bash
# THE GO BUTTON. One probe, then either resume the whole merge lane or say why not.
# No polling. Run this when the operator says Actions is back, or hourly.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/ACTIONS_WATCH.log"
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
REPO=mcboyle/BD
FORCE="${1:-}"

SPECS=(
 "262|every-gate-reaches-a-shard|every declared gate is reachable by a CI shard"
 "268|child-tests-cannot-reach-a-live-install|child test launches cannot inherit a live install dir"
 "260|secret-gate-denominator|the shipped-surface secret gate refuses a zero real-file denominator"
 "243|owned-pytest-launches-register|owned pytest launches register themselves"
 "261|lifecycle-locks-are-measured|lifecycle locks are measured, not asserted from source text"
 "269|bitrot-clean-install-is-zero|a clean install reports zero integrity issues, not unknown"
)

PAGE=$(curl -s --max-time 15 https://www.githubstatus.com/api/v2/components.json 2>/dev/null \
  | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('UNREACHABLE'); raise SystemExit
for c in d.get('components',[]):
    if c.get('name')=='Actions': print(c.get('status','UNKNOWN')); raise SystemExit
print('UNKNOWN')" 2>/dev/null || echo UNREACHABLE)
say "actions page = $PAGE"

if [ "$PAGE" != "operational" ] && [ "$FORCE" != "--force" ]; then
  say "still down -- not resuming. Use --force to resume anyway (the ship script"
  say "  will refuse on its own if CI genuinely cannot dispatch, so --force is safe)."
  exit 1
fi

# Re-fire the stalled PR so it actually gets a run; the outage swallowed its event.
N=$(gh pr list --repo "$REPO" --state open --json number,headRefName \
    -q '.[] | select(.headRefName|startswith("cut/")) | .number' 2>/dev/null | head -1)
if [ -n "$N" ]; then
  say "re-firing PR #$N so the swallowed pull_request event is replayed"
  gh pr close "$N" --repo "$REPO" >/dev/null 2>&1; sleep 5
  gh pr reopen "$N" --repo "$REPO" >/dev/null 2>&1
fi

git -C /home/mboyle/BulkDownloader fetch origin main -q 2>/dev/null
declare -a TODO=()
for spec in "${SPECS[@]}"; do
  R1="${spec%%|*}"
  if git -C /home/mboyle/BulkDownloader show origin/main:project-knowledge/IMPROVEMENT_BACKLOG.md 2>/dev/null \
     | grep -qE "^\| $R1 \| CLOSED"; then say "skip row $R1: already CLOSED on main"
  else TODO+=("$spec"); fi
done
say "=== resuming lane with ${#TODO[@]} cut(s) ==="
[ "${#TODO[@]}" -eq 0 ] && { say "nothing left to merge"; exit 0; }
KEEP_OPEN_ROWS="243,245" setsid nohup bash /home/mboyle/bd-retry-lane.sh "${TODO[@]}" \
  >>"$A/FINISH.log" 2>&1 < /dev/null &
say "lane relaunched as pid $! -- watch $A/FINISH.log"
