#!/bin/bash
# Dispatch the remaining queue to Codex, ORDERED BY SPEED/EFFICIENCY/ROBUSTNESS:
# small bounded rows first so they land while the large ones are still thinking,
# research and the expensive Vite-build row last. Every job gets its own git
# worktree and its own tmux session (cx-row<N>) so it is attachable, not silent.
# Staggered because concurrent `git worktree add` contends on the index lock.
set -u
ORDER="176 235 174 175 121 221 241 26 27 229"
D=/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts
mkdir -p "$D"
for r in $ORDER; do
  B=/home/mboyle/bd-codex-briefs/row$r.md
  [ -f "$B" ] || { echo "SKIP row $r -- no brief"; continue; }
  tmux has-session -t "cx-row$r" 2>/dev/null && { echo "SKIP row $r -- already live"; continue; }
  setsid nohup bash /home/mboyle/bd-codex-cut.sh "$r" "$B" > "$D/row$r.dispatch.log" 2>&1 < /dev/null &
  echo "dispatched row $r"
  sleep 12
done
echo "fleet dispatch complete: $(tmux ls 2>/dev/null | grep -c '^cx-row') codex session(s) live"
