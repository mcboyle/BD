#!/usr/bin/env bash
# bd-cut -- start an integrator cut in its OWN git worktree.
#
# WHY. Overlapping two cuts on one working tree put an edit on the wrong branch
# at v3.66.1239: the _DECLARED entry for that cut's own gate was written while
# the tree had been switched to the next cut, so CI refused a candidate whose
# fix existed but was on a neighbouring branch. The detached push copy added
# earlier removed the PUSH coupling; this removes the EDIT coupling, which is
# the half that actually lost work.
#
# The subagents have had this isolation all along. The integrator should not be
# the only worker editing a shared tree.
#
# Usage: bd-cut.sh <branch-name>   -> prints the worktree path
set -uo pipefail
BR="$1"
R=/home/mboyle/BulkDownloader
W=/home/mboyle/bd-cuts/$BR
mkdir -p "$(dirname "$W")"
git -C "$R" fetch --quiet origin
git -C "$R" worktree prune 2>/dev/null
[ -d "$W" ] && { echo "$W already exists -- reuse it or remove it deliberately" >&2; exit 2; }
git -C "$R" worktree add --quiet -b "$BR" "$W" origin/main || exit 1
ln -sfn "$R/venv" "$W/venv"
ln -sfn "$R/frontend/node_modules" "$W/frontend/node_modules" 2>/dev/null
echo "$W"
