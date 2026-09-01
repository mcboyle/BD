#!/bin/bash
# ONE LINE, MAX 100 CHARS, every 10 minutes (operator, 2026-08-25 19:00).
# Must still distinguish progressing from stale, so staleness wins the space:
# quiet rows are named with their age; progressing ones are just a count.
set -u
CC=/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts
IN=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight
N=/home/mboyle/fleet-run-artifacts/2026-08-25/night
INTERVAL=${HEARTBEAT_INTERVAL:-600}
declare -A PREV LAST
while true; do
  t=$(date -u +%s); live=$(tmux ls 2>/dev/null | sed -n 's/^cx-row\([0-9]*\):.*/\1/p')
  n=$(printf '%s\n' "$live" | grep -c .); prog=0; quiet=""
  for r in $live; do
    s=$([ -f "$CC/row$r.txt" ] && stat -c %s "$CC/row$r.txt" || echo 0); p=${PREV[$r]:-}
    if [ -z "$p" ]; then PREV[$r]=$s; LAST[$r]=$t; continue; fi
    PREV[$r]=$s
    if [ "$s" -gt "$p" ]; then LAST[$r]=$t; prog=$((prog+1))
    else quiet="$quiet $r:$(( (t - ${LAST[$r]:-$t}) / 60 ))m"; fi
  done
  # DENOMINATOR FROM THE LIVE SPEC, not from a drain's argv. The argv version
  # went to "?/?" the moment bd-drain exited, and before that a hardcoded 17
  # reported "17/17 merged, 0 left" for twenty-two hours while nine rows queued.
  # bd-night-spec.txt is what the lane is actually working from, so it is the
  # only honest denominator; a row counts DONE when its slug is in a merge commit.
  SPEC=/home/mboyle/bd-night-spec.txt
  TOT=$(grep -cvE '^\s*(#|$)' "$SPEC" 2>/dev/null || echo 0)
  ML=$(git -C /home/mboyle/BulkDownloader log --oneline origin/main 2>/dev/null | head -80)
  # PER-ROW MERGE EVIDENCE. Counting by CUT SLUG undercounts every batched
  # row: a 5-row cut is named for its FIRST row, so the other four never
  # appear in the log and read as unmerged forever. This line said "10/23
  # merged" while the register said 16/24 -- an instrument reporting a wrong
  # number straight to the operator. main's backlog closes each row by id.
  RG=$(git -C /home/mboyle/BulkDownloader show origin/main:project-knowledge/IMPROVEMENT_BACKLOG.md 2>/dev/null)
  if [ -z "$ML" ] || [ "${TOT:-0}" -eq 0 ]; then M='?'; R='?'; TOT='?'
  else
    M=0
    while IFS='|' read -r _r _slug _t; do
      case "$_r" in ''|'#'*) continue;; esac
      if { [ -n "$RG" ] && printf '%s' "$RG" | grep -qE "^\| $_r \|[[:space:]]*CLOSED"; } \
         || printf '%s' "$ML" | grep -q -- "$_slug"; then M=$((M+1)); fi
    done < "$SPEC"
    R=$(( TOT - M ))
  fi
  T=idle; tmux has-session -t bd-tail 2>/dev/null && T=trio
  tmux has-session -t bd-queue 2>/dev/null && T=queue
  L=$(cut -d' ' -f1 /proc/loadavg)
  # ELIGIBILITY, NOT JUST COUNTS. "3/24 merged, 21 left" reads IDENTICALLY
  # whether the lane is working through the queue or the queue has no eligible
  # row left at all. On 2026-08-27 every row hit its attempt cap at 07:31 and the
  # lane sat idle until 10:13 -- 2.5 hours -- while this line looked healthy.
  # A count is not a state. Say which.
  ELIG=0; BLOCKED=0
  if [ -f "$SPEC" ]; then
    while IFS='|' read -r _r _slug _t; do
      case "$_r" in ''|'#'*) continue;; esac
      { [ -n "$RG" ] && printf '%s' "$RG" | grep -qE "^\| $_r \|[[:space:]]*CLOSED"; } && continue
      printf '%s' "$ML" | grep -q -- "$_slug" && continue
      _a=$(cat "$N/att-$_r" 2>/dev/null || echo 0)
      if [ "${_a:-0}" -ge 3 ]; then BLOCKED=$((BLOCKED+1)); else ELIG=$((ELIG+1)); fi
    done < "$SPEC"
  fi
  line="$(date -u +%H:%M) ${M:-?}/${TOT:-?} merged, ${R:-?} left | cx${n} ok${prog}"
  if [ "$ELIG" -eq 0 ] && [ "$BLOCKED" -gt 0 ]; then
    line="$line | *** STALLED: 0 eligible, $BLOCKED at attempt cap ***"
  elif [ "$ELIG" -gt 0 ]; then
    line="$line | elig$ELIG"
    [ "$BLOCKED" -gt 0 ] && line="$line blk$BLOCKED"
  fi
  [ -n "$quiet" ] && line="$line stale:${quiet# }"
  line="$line | $T ld$L"
  echo "${line:0:100}"
  # STOP ONLY WHEN NOTHING AT ALL IS ARMED. Earlier this counted the queue and
  # trio sessions only, so it went silent twice while a manual verify and a QA
  # run were still going -- a heartbeat that stops during a lull between lanes
  # reports "done" when the honest answer is "between lanes".
  OTHER=$(tmux ls 2>/dev/null | grep -cE '^(qa-row|bd-queue|bd-tail|bd-fix)')
  LANE=$(ps -eo comm=,args= | awk '$1=="bash" && /bd-(drain|row-chain|supervise|night|fleet-deploy)\.sh/ && !/shell-snapshots/' | wc -l)
  if [ "$n" -eq 0 ] && [ "$OTHER" -eq 0 ] && [ "$LANE" -eq 0 ] \
     && [ -z "$(/home/mboyle/bd-ps.sh bd-verify-cut.sh 2>/dev/null)" ] \
     && [ -z "$(/home/mboyle/bd-ps.sh bd-ship.sh 2>/dev/null)" ]; then
    # REQUIRE THREE CONSECUTIVE EMPTY CHECKS, NOT ONE. A single sample lands in
    # every restart gap: killing and relaunching bd-night takes ~2s, and on
    # 2026-08-27 the heartbeat sampled inside that window and stopped itself for
    # good. A monitor that dies during a routine restart is worse than no
    # monitor, because its silence still reads as health.
    EMPTY=$((${EMPTY:-0} + 1))
    if [ "$EMPTY" -ge 3 ]; then
      echo "$(date -u +%H:%M) nothing armed for $EMPTY consecutive checks -- stopping"; break; fi
    echo "$(date -u +%H:%M) nothing armed ($EMPTY/3) -- holding, a restart gap is not an ending"
  else
    EMPTY=0
  fi
  sleep "$INTERVAL"
done
