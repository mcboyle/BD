#!/bin/bash
# Re-dispatch any codex worker that ends having produced no work. The usual
# cause is the worker stopping at a design proposal to await approval that will
# never come; the briefs now say so explicitly, but a worker started before that
# was appended still carries the old text.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/FINISH.log"; D=/home/mboyle/bd-codex-briefs
say(){ printf '%s [stall-watch] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
declare -A TRIES
say "watching for codex workers that finish with zero changed paths"
for _ in $(seq 1 720); do
  for r in 243 292 317 321 322 323; do
    tmux has-session -t "cx-row$r" 2>/dev/null && continue
    W=/home/mboyle/bd-codex-wt/row$r; [ -d "$W" ] || continue
    B=$(git -C "$W" merge-base HEAD origin/main 2>/dev/null) || continue
    t=$(git -C "$W" diff "$B" --name-only 2>/dev/null | wc -l)
    u=$(git -C "$W" status --porcelain 2>/dev/null | grep '^??' \
        | grep -vE ' (venv|frontend/node_modules|frontend/dist)/?$' | grep -c .)
    [ "$((t+u))" -gt 0 ] && continue
    n=${TRIES[$r]:-0}
    [ "$n" -ge 2 ] && continue
    b=$(ls "$D"/row$r-*.md 2>/dev/null | head -1); [ -n "$b" ] || continue
    TRIES[$r]=$((n+1))
    say "row $r finished with ZERO changed paths -- re-dispatching (try $((n+1))/2)"
    ( cd /home/mboyle && bash bd-codex-cut.sh "$r" "$b" >/dev/null 2>&1 & )
    sleep 20
  done
  sleep 30
done
say "stall watch ended"
