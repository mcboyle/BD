#!/usr/bin/env bash
# Run codex against an EXISTING worktree, WITHOUT destroying it.
#
# WHY THIS EXISTS. bd-codex-cut.sh begins `rm -rf "$W"` and re-adds the worktree
# from origin/main -- correct for starting a NEW row, catastrophic for repairing
# an existing one. Dispatching seven repair tasks through it on 2026-08-29 wiped
# seven worktrees; they were recovered only because ~/bd-persist/codex holds both
# the tracked patch and a tar of the untracked files. This script is the repair
# path: it REFUSES to run if the worktree is missing, and never deletes anything.
set -uo pipefail
ROW="$1"; BRIEF="$2"
W=/home/mboyle/bd-codex-wt/row$ROW
OUT=/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts/row$ROW.repair.txt

[ -d "$W" ]   || { echo "REFUSING: no worktree at $W -- use bd-codex-cut.sh for a new row"; exit 2; }
[ -f "$BRIEF" ] || { echo "REFUSING: no brief at $BRIEF"; exit 2; }

# A repair against an EMPTY worktree would silently become a rewrite. Require
# that the work actually be present before handing it to a worker.
N=$(git -C "$W" status --porcelain 2>/dev/null | grep -vc node_modules || echo 0)
if [ "${N:-0}" -lt 3 ]; then
  echo "REFUSING: $W has only $N changed file(s) -- the work is not there to repair."
  echo "Restore it first from ~/bd-persist/codex/row$ROW.patch (+ .newfiles.tar)."
  exit 3
fi
echo "row $ROW: repairing IN PLACE, $N changed file(s) present"

ln -sfn /home/mboyle/BulkDownloader/venv "$W/venv" 2>/dev/null
ln -sfn /home/mboyle/BulkDownloader/frontend/node_modules "$W/frontend/node_modules" 2>/dev/null

tmux kill-session -t "cx-row$ROW" 2>/dev/null
tmux new-session -d -s "cx-row$ROW" \
  "/home/mboyle/.local/bin/codex exec --dangerously-bypass-approvals-and-sandbox \
     -C '$W' --skip-git-repo-check \
     -m gpt-5.6-sol \
     -c 'model_reasoning_effort=\"ultra\"' \
     -c 'service_tier=\"fast\"' - < '$BRIEF' 2>&1 | tee '$OUT'"
echo "row $ROW: dispatched into cx-row$ROW (worktree preserved)"
