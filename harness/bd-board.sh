#!/bin/bash
# LIVE TASK BOARD. Redraws every 15s. Shows WHAT each job is doing right now --
# its current shell command or last reported step -- not just that it is alive.
# A heartbeat says "something moved"; this says "row 121 is running pytest on
# test_login_flow.py". Attach: tmux attach -t bd-board
set -u
CC=/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts
IN=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight
R=/home/mboyle/BulkDownloader
BOARD=$IN/board.txt

titles(){ case "$1" in
 26) echo "vacuous-test detector: one more decidable slice";;
 27) echo "over-sensitivity controls: one more decidable slice";;
 121) echo "derive_login_flow emits a plan that cannot log in";;
 174) echo "fleet residue owners: one approved removal + 3 filings";;
 175) echo "parallel capture services";;
 176) echo "reptyle fixture mislabel";;
 183) echo "t3/t4 gate: no-space {overwrite:true} evades";;
 184) echo "t5/t6 gate: lowercase method:\"post\" evades (SECURITY)";;
 221) echo "runner degrades under fork starvation";;
 229) echo "SPA base proved by source, not by build (Vite)";;
 235) echo "bd-jobs has two owned transport funnels";;
 241) echo "W1 two more concurrency legs -- DIAGNOSE ONLY";;
 242) echo "release gate cannot see prose above newest header";;
 *) echo "row $1";; esac; }

# what is this codex job doing right now: last `exec` command it launched
doing(){ local f=$CC/row$1.txt
  [ -f "$f" ] || { echo "-"; return; }
  local d
  d=$(tail -400 "$f" 2>/dev/null | grep -oE '(venv/bin/python -m pytest [^"]{0,60}|bd-mutate[^"]{0,40}|git [a-z-]+ [^"]{0,40}|rg [^"]{0,40}|sed -n [^"]{0,30})' | tail -1)
  [ -n "$d" ] && echo "${d:0:62}" || echo "thinking"; }

while true; do
  {
  printf '\033[H\033[2J'
  echo "BULKDOWNLOADER RUN BOARD   $(date -u +%Y-%m-%dT%H:%M:%SZ)   load $(cut -d' ' -f1 /proc/loadavg)"
  echo "main $(git -C $R rev-parse --short origin/main 2>/dev/null)   open PRs $(gh pr list --state open --json number --jq length 2>/dev/null || echo '?')"
  echo
  printf '%-5s %-8s %8s %-6s %s\n' ROW STATE SIZE AGE "SUBJECT / DOING"
  printf '%s\n' "----------------------------------------------------------------------------------------"
  for r in 184 183 242 176 235 174 175 121 221 241 26 27 229; do
    f=$CC/row$r.txt
    if tmux has-session -t "cx-row$r" 2>/dev/null; then st=RUNNING; else
      [ -s "$f" ] && st=returned || st=queued; fi
    sz=$([ -f "$f" ] && du -h "$f" 2>/dev/null | cut -f1 || echo -)
    ag=$([ -f "$f" ] && echo "$(( ( $(date +%s) - $(stat -c %Y "$f") ) / 60 ))m" || echo -)
    printf '%-5s %-8s %8s %-6s %s\n' "$r" "$st" "$sz" "$ag" "$(titles $r)"
    [ "$st" = RUNNING ] && printf '%-5s %-8s %8s %-6s   -> %s\n' "" "" "" "" "$(doing $r)"
  done
  echo
  echo "INTEGRATOR LANE (serial -- every cut touches the version trio)"
  printf '%s\n' "----------------------------------------------------------------------------------------"
  tmux has-session -t bd-tail 2>/dev/null && ts=RUNNING || ts=idle
  echo "  trio chain   $ts"
  tail -3 "$IN/1241-chain.log" 2>/dev/null | sed 's/^/    /'
  echo
  echo "  1241 row 237  $( [ -f "$IN/1241-final-verify.log" ] && grep -hoE 'VERDICT.*' "$IN/1241-final-verify.log" | tail -1 || echo 'verifying' )"
  echo "  1242 row 182  frozen, rebases after 1241 merges"
  echo "  1243 row 185  frozen, rebases after 1242 merges"
  echo "  228  row 228  patch ready, renumber to 1244"
  echo
  echo "W1 MATCHED EXPERIMENT"
  printf '%s\n' "----------------------------------------------------------------------------------------"
  grep -E '^=== ' "$IN/w1-matched/summary.txt" 2>/dev/null | sed 's/^/  /'
  echo "  VERDICT: same test failed on BOTH arms -> pre-existing on main, 1241 does not own it"
  } > "$BOARD" 2>/dev/null
  cat "$BOARD"
  sleep 15
done
