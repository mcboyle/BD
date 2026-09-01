#!/bin/bash
# Find lines a held candidate REMOVES that a merge in the window ADDED.
#
# Written 2026-08-26 after row 268 silently reverted v3.66.1271: its worker was
# dispatched before 1271 merged, edited the pre-1271 text, and the patch put the
# defect back. The band could not see it in general -- bd-band-derive selects on
# CHANGED PATHS, so a revert in a file the cut does not otherwise touch has no
# test in its band at all. This scans the diff itself.
#
# IT IS A REVIEW TRIGGER, NOT A VERDICT. It flags any removed line that a window
# commit introduced, which includes a line EDITED IN PLACE. Measured on the first
# run: 3 flags, 1 real revert (268 putting back bd-fullsuite's 300s floor) and 2
# legitimate extensions (243 widening 1265's awk pattern to count bd-pytest; 268
# adding BD_INSTALL_DIR to 1261's env list). Read every flag; do not auto-reject.
set -u
R=/home/mboyle/BulkDownloader
CAND="${1:?candidate worktree}"
WINDOW="${2:-30}"          # how many commits back on main to treat as "recent"
BASE=$(git -C "$R" rev-parse "origin/main~$WINDOW" 2>/dev/null) || { echo "cannot resolve window"; exit 2; }

# Only files the candidate touches AND a window commit touched -- the rest cannot revert anything.
mapfile -t FILES < <(git -C "$CAND" diff origin/main --name-only 2>/dev/null \
  | grep -vE 'CHANGELOG|PIN_INDEX|__init__|STATIC_KB|IMPROVEMENT_BACKLOG|slice4')
hits=0; checked=0
for f in "${FILES[@]}"; do
  git -C "$R" log --oneline "$BASE..origin/main" -- "$f" >/dev/null 2>&1 || continue
  n=$(git -C "$R" log --oneline "$BASE..origin/main" -- "$f" 2>/dev/null | wc -l)
  [ "$n" -eq 0 ] && continue
  checked=$((checked+1))
  # removed lines of real substance only
  while IFS= read -r line; do
    body="${line:1}"
    [ "${#body}" -lt 24 ] && continue
    case "$body" in ''|*[!\ ]*) ;; *) continue;; esac
    # was this exact line INTRODUCED by a window commit?
    c=$(git -C "$R" log --oneline -S"$body" "$BASE..origin/main" -- "$f" 2>/dev/null | head -1)
    if [ -n "$c" ]; then
      echo "  *** SUSPECTED REVERT in $f"
      echo "      removes: ${body:0:96}"
      echo "      added by: $c"
      hits=$((hits+1))
    fi
  done < <(git -C "$CAND" diff origin/main -- "$f" 2>/dev/null | grep '^-' | grep -v '^---')
done
echo "  scanned $checked file(s) that a window commit also touched; $hits suspected revert(s)"
[ "$hits" -gt 0 ] && exit 1 || exit 0
