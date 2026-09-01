#!/bin/bash
# Build a worker patch CORRECTLY. Three traps, all of which have already cost
# this run real time:
#
#  1. UNTRACKED FILES ARE INVISIBLE TO `git diff`. Row 281 created its test and
#     BOTH mutant specs, declared the test in ci.yml and the shard gate, and
#     never staged them. The patch dropped all three, so the cut would have
#     shipped a CI shard entry naming a file that does not exist. `git add -N`
#     first -- exactly what bd-qa-row.sh already does for the same reason.
#  2. A STALE WORKTREE MUST NOT BE DIFFED AGAINST CURRENT origin/main. Rows 243
#     and 261 were 10 and 32 commits behind; diffing them against current main
#     rendered every merge since as a DELETION (261 showed 6,606). Diff against
#     the worktree's own merge-base and let the apply do the 3-way.
#  3. THE RELEASE TRIO AND GENERATED ARTIFACTS BELONG TO THE INTEGRATOR. A worker
#     that stages them collides with every other cut.
set -uo pipefail
WT="${1:?worktree}"; OUT="${2:?output patch path}"
R=/home/mboyle/BulkDownloader

git -C "$WT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "not a git worktree: $WT"; exit 2; }

# 1. make new files index-visible (intent-to-add only -- no content staged)
NEW=$(git -C "$WT" status --porcelain 2>/dev/null | grep '^??' \
      | grep -vE 'venv|node_modules|\.pyc|__pycache__' | sed 's/^?? //')
if [ -n "$NEW" ]; then
  echo "  staging $(printf '%s\n' $NEW | wc -l) untracked path(s) with add -N:"
  printf '    %s\n' $NEW
  # shellcheck disable=SC2086
  git -C "$WT" add -N -- $NEW 2>/dev/null || { echo "  add -N FAILED"; exit 3; }
fi

# 2. diff against the worktree's OWN base, never assume origin/main
BASE=$(git -C "$WT" merge-base HEAD origin/main 2>/dev/null)
[ -n "$BASE" ] || { echo "cannot resolve merge-base"; exit 4; }
OM=$(git -C "$R" rev-parse origin/main)
BEHIND=$(git -C "$WT" rev-list --count "$BASE..origin/main" 2>/dev/null || echo '?')
echo "  base ${BASE:0:7}, origin/main ${OM:0:7}, behind by $BEHIND commit(s)"

git -C "$WT" diff "$BASE" --binary -- . \
  ':(exclude)venv' ':(exclude)frontend/node_modules' \
  ':(exclude)bulk_downloader/__init__.py' ':(exclude)tests/test_settings_center_slice4.py' \
  ':(exclude)CHANGELOG.md' ':(exclude)PIN_INDEX.json' \
  ':(exclude)project-knowledge/STATIC_KB_MANIFEST.json' \
  ':(exclude)project-knowledge/IMPROVEMENT_BACKLOG.md' > "$OUT" 2>/dev/null

INS=$(grep -c '^+' "$OUT" 2>/dev/null || echo 0); DEL=$(grep -c '^-' "$OUT" 2>/dev/null || echo 0)
echo "  $(wc -c <"$OUT") bytes, +$INS -$DEL"
[ -s "$OUT" ] || { echo "  EMPTY PATCH -- legitimate only for a register-only row; caller must decide"; exit 0; }
if [ "$DEL" -gt "$INS" ]; then
  echo "  *** REFUSING: more deletions than insertions -- revert-shaped, check the base ***"; exit 5
fi
exit 0
