#!/usr/bin/env bash
# Run one codex implementation task inside its OWN git worktree.
# Writes are confined to that worktree; the integrator remains sole writer to
# /home/mboyle/BulkDownloader and owns every merge and deploy.
set -uo pipefail
ROW="$1"; BRIEF="$2"
R=/home/mboyle/BulkDownloader
W=/home/mboyle/bd-codex-wt/row$ROW
OUT=/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts/row$ROW.txt
mkdir -p "$(dirname "$OUT")" /home/mboyle/bd-codex-wt
rm -rf "$W"; git -C "$R" worktree prune 2>/dev/null
git -C "$R" fetch --quiet origin 2>/dev/null
git -C "$R" worktree add --quiet --detach "$W" origin/main || { echo "WORKTREE FAILED row $ROW"; exit 1; }
ln -sfn "$R/venv" "$W/venv"
ln -sfn "$R/frontend/node_modules" "$W/frontend/node_modules" 2>/dev/null
# RUN INSIDE A NAMED TMUX SESSION. Detached nohup work is invisible to the
# operator -- 2026-08-25 three codex jobs ran for ten minutes and the box looked
# idle from outside. `tmux attach -t cx-row<N>` now shows the live run.
tmux kill-session -t "cx-row$ROW" 2>/dev/null
tmux new-session -d -s "cx-row$ROW" \
  "/home/mboyle/.local/bin/codex exec --dangerously-bypass-approvals-and-sandbox \
     -C '$W' --skip-git-repo-check \
     -m gpt-5.6-sol \
     -c 'model_reasoning_effort=\"ultra\"' \
     -c 'service_tier=\"fast\"' - < '$BRIEF' 2>&1 | tee '$OUT'"
while tmux has-session -t "cx-row$ROW" 2>/dev/null; do sleep 20; done
echo "rc=$? row=$ROW out=$OUT worktree=$W" >> /home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts/dispatch.log
