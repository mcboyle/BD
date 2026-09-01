#!/bin/bash
# ARCHIVE THEN DELETE finished worker worktrees. Operator-approved 2026-08-28.
#
# A WORKTREE IS ONLY FINISHED IF ALL THREE HOLD, checked per tree, never assumed:
#   1. its row is CLOSED on origin/main (the work actually landed), and
#   2. it has NO uncommitted content beyond scaffolding (nothing unshipped), and
#   3. no codex session is writing to it right now.
# Anything else is KEPT. A stale queue nearly rm -rf'd two worktrees the lane
# still needed on an earlier run; the archive exists so that even a mistake here
# is recoverable rather than terminal.
set -uo pipefail
R=/home/mboyle/BulkDownloader
A=/home/mboyle/fleet-run-artifacts/2026-08-25
DEST=/home/mboyle/bd-archive; mkdir -p "$DEST"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TAR="$DEST/worktrees-$STAMP.tar.zst"
L="$A/inflight/worktree-archive.log"
say(){ printf '%s [wt-archive] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
DRY=${DRY_RUN:-0}

git -C "$R" fetch -q origin 2>/dev/null
REG=$(git -C "$R" show origin/main:project-knowledge/IMPROVEMENT_BACKLOG.md 2>/dev/null)
[ -n "$REG" ] || { say "cannot read the register -- UNKNOWN, refusing to delete anything"; exit 3; }

finished=(); kept=0
for W in /home/mboyle/bd-codex-wt/row*; do
  [ -d "$W" ] || continue
  r=$(basename "$W"); r=${r#row}
  case "$r" in ''|*[!0-9]*) say "row $r: non-numeric name -- KEEPING"; kept=$((kept+1)); continue;; esac
  if tmux has-session -t "cx-row$r" 2>/dev/null; then
    say "row $r: codex session LIVE -- KEEPING"; kept=$((kept+1)); continue
  fi
  if ! printf '%s' "$REG" | grep -qE "^\| *$r *\|[[:space:]]*CLOSED"; then
    say "row $r: not CLOSED on main -- KEEPING"; kept=$((kept+1)); continue
  fi
  diff=$(git -C "$W" diff origin/main --name-only \
         -- . ':(exclude)venv' ':(exclude)frontend/node_modules' ':(exclude)frontend/dist' 2>/dev/null)
  untracked=$(git -C "$W" status --porcelain 2>/dev/null \
              | grep -cE '^\?\? (tests|tools|scripts|bulk_downloader|toolchain|docs)/')
  real=$(printf '%s\n' "$diff" | grep -v '^$' | grep -v '^project-knowledge/IMPROVEMENT_BACKLOG.md$' | wc -l)
  if [ "$real" -gt 0 ] || [ "$untracked" -gt 0 ]; then
    say "row $r: CLOSED but $real changed + $untracked untracked source path(s) -- KEEPING (may be unshipped)"
    kept=$((kept+1)); continue
  fi
  # the register difference is only redundant if main really carries this row
  printf '%s' "$REG" | grep -qE "^\| *$r *\|" || {
    say "row $r: CLOSED status but no row $r on main -- KEEPING (contradiction)"
    kept=$((kept+1)); continue; }
  finished+=("$r")
done

say "finished=${#finished[@]} kept=$kept"
[ "${#finished[@]}" -gt 0 ] || { say "nothing to archive"; exit 0; }
if [ "$DRY" = 1 ]; then say "DRY_RUN -- would archive: ${finished[*]}"; exit 0; fi

# ARCHIVE BEFORE REMOVING, AND PROVE THE ARCHIVE READS BACK. An irreversible
# step must prove its evidence record is writable before it acts.
cd /home/mboyle/bd-codex-wt || exit 3
paths=(); for r in "${finished[@]}"; do paths+=("row$r"); done
tar --exclude=venv --exclude=node_modules --exclude=frontend/dist \
    -I 'zstd -19 -T0' -cf "$TAR" "${paths[@]}" 2>>"$L" || { say "tar FAILED -- nothing deleted"; exit 4; }
n=$(tar -I zstd -tf "$TAR" 2>/dev/null | grep -cE '^row[0-9]+/$')
say "archive $TAR $(du -h "$TAR" | cut -f1), $n top-level tree(s) read back"
[ "$n" -eq "${#finished[@]}" ] || { say "archive holds $n of ${#finished[@]} -- REFUSING to delete"; exit 4; }

for r in "${finished[@]}"; do
  git -C "$R" worktree remove --force "/home/mboyle/bd-codex-wt/row$r" >>"$L" 2>&1 \
    || rm -rf "/home/mboyle/bd-codex-wt/row$r"
done
git -C "$R" worktree prune >>"$L" 2>&1
say "removed ${#finished[@]} worktree(s); $(ls -d /home/mboyle/bd-codex-wt/row* 2>/dev/null | wc -l) remain"
