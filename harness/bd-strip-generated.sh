#!/bin/bash
# Drop regen-produced artifacts from a worker's worktree so its patch cannot
# conflict on them. The integrator regenerates these AFTER applying the patch, so
# a worker's copy is noise that only ever blocks the cut.
#   usage: bd-strip-generated.sh <worktree>...
# NOTE: `git checkout -- <path>` restores from the INDEX, so a STAGED copy
# survives it. Only `git restore --source=HEAD --staged --worktree` discards both.
set -u
GEN="PIN_INDEX.json project-knowledge/STATIC_KB_MANIFEST.json"
for W in "$@"; do
  [ -d "$W" ] || { echo "  $W: missing"; continue; }
  hit=""
  for f in $GEN; do
    git -C "$W" status --porcelain -- "$f" 2>/dev/null | grep -q . || continue
    git -C "$W" restore --source=HEAD --staged --worktree -- "$f" 2>/dev/null \
      || git -C "$W" checkout HEAD -- "$f" 2>/dev/null
    hit="$hit $f"
  done
  [ -n "$hit" ] && echo "  $(basename "$W"): stripped$hit" || echo "  $(basename "$W"): clean"
done
