#!/bin/bash
# bd-integrate-row.sh keys THREE things off one argument: the worktree
# bd-codex-wt/row<ARG>, the QA log codex-cuts/row<ARG>.qa.log, and the register
# row it closes. Tasks dispatched under a descriptive NAME file a NUMBERED row,
# so the name has to be bridged to the number or integrate fails twice: first
# "row <name> not in the register", then "no changelog body extracted".
#   usage: bd-bridge-named-row.sh <brief-name>
# Prints the row id it bridged to.
set -u
N="$1"; W=/home/mboyle/bd-codex-wt/row$N; C=/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts
[ -d "$W" ] || { echo "no worktree at $W" >&2; exit 1; }
# Compare row-id SETS, never the diff. A worktree that branched before another
# cut merged shows THAT cut's rows as "+" lines too -- reading the diff picked up
# row 246 for a task that actually filed 251.
ids(){ grep -oE '^\| *[0-9]+ *\|' | tr -d '| ' ; }
ROW=$(comm -13 \
  <(git -C "$W" show origin/main:project-knowledge/IMPROVEMENT_BACKLOG.md 2>/dev/null | ids | sort -u) \
  <(cat "$W/project-knowledge/IMPROVEMENT_BACKLOG.md" 2>/dev/null | ids | sort -u) \
  | sort -n | tail -1)
[ -n "$ROW" ] || { echo "worker filed no numbered row in $N" >&2; exit 2; }
# refuse to clobber a different task's bridge
if [ -L "/home/mboyle/bd-codex-wt/row$ROW" ]; then
  cur=$(readlink "/home/mboyle/bd-codex-wt/row$ROW")
  [ "$cur" = "$W" ] || { echo "row$ROW already bridged to $cur -- refusing" >&2; exit 3; }
elif [ -e "/home/mboyle/bd-codex-wt/row$ROW" ]; then
  echo "bd-codex-wt/row$ROW exists and is not a symlink -- refusing" >&2; exit 3
fi
ln -sfn "$W" "/home/mboyle/bd-codex-wt/row$ROW"
[ -f "$C/row$N.qa.log" ] && cp -p "$C/row$N.qa.log" "$C/row$ROW.qa.log"
[ -f "$C/row$N.txt" ]    && cp -p "$C/row$N.txt"    "$C/row$ROW.txt"
echo "$ROW"
