#!/bin/bash
# Re-base every unmerged worker worktree onto current origin/main.
#
# WHY THIS RUNS BEFORE BATCHING. A batch fails as a UNIT, so a single member
# whose patch no longer applies to main kills five innocent rows with it. On
# 2026-08-27 batch 1 was PROVEN pairwise file-disjoint and still died on
# "conflicts outside the append-only set: tests/test_v3_66_1173..." -- not a
# collision between members, but row 292 alone being stale against a main that
# had moved. Serial cuts absorb that one row at a time; batches cannot.
#
# STASH, NOT `git rebase`: these worktrees hold their work UNCOMMITTED, and
# `git rebase` refuses a dirty tree outright.
set -u
R=/home/mboyle/BulkDownloader
A=/home/mboyle/fleet-run-artifacts/2026-08-25
OUT="$A/inflight/rebase-all.log"
: > "$OUT"
MAIN=$(git -C "$R" rev-parse origin/main)
ok=0; skipped=0; conflict=0
for spec in "$@"; do
  r=${spec%%|*}
  W=/home/mboyle/bd-codex-wt/row$r
  [ -d "$W" ] || { echo "row $r: no worktree" >>"$OUT"; continue; }
  H=$(git -C "$W" rev-parse HEAD 2>/dev/null)
  if [ "$H" = "$MAIN" ]; then echo "row $r: already on main" >>"$OUT"; ok=$((ok+1)); continue; fi
  # scaffolding symlinks must never be staged, or stash refuses the worktree
  for p in venv frontend/node_modules frontend/dist; do
    git -C "$W" ls-files --cached --error-unmatch "$p" >/dev/null 2>&1 \
      && git -C "$W" rm --cached --force --quiet "$p" >/dev/null 2>&1
  done
  # clear any leftover unmerged index entries from an earlier interrupted pop
  for f in $(git -C "$W" status --porcelain | awk '/^(DA|AA|UU|DU|UD) /{print $2}'); do
    git -C "$W" add "$f" >/dev/null 2>&1
  done
  # CLEAR INTENT-TO-ADD BEFORE STASHING. `git add -N` records a path in the
  # index with NO content, and `git stash` refuses the whole worktree with
  # "Entry '<path>' not uptodate. Cannot merge." Eight rows failed on exactly
  # this. `git reset` (no paths) unstages to HEAD WITHOUT touching the working
  # tree, so those files become plain untracked and `stash -u` takes them.
  # This is not `git add -A`, which the contract forbids -- nothing is staged.
  git -C "$W" reset -q >>"$OUT" 2>&1

  # PIN THE CANDIDATE BEFORE ANYTHING DESTRUCTIVE RUNS.
  #
  # On 2026-08-30 this block discarded six committed candidates. The shape is
  # exact and it is still here: `stash push -u` SUCCEEDS AND STASHES NOTHING
  # when the work is COMMITTED rather than dirty, and the checkout below then
  # moves the worktree off the candidate commit regardless. `stash pop` has
  # nothing to restore, the loop reports "re-based onto ..." and the committed
  # work is reachable only from the reflog, which expires.
  #
  # A ref does not expire. Pinning HEAD here makes the whole sequence
  # RECOVERABLE rather than merely careful, and costs one plumbing call. This
  # is deliberately not the full row-407 replay (which never touches the source
  # worktree at all); it is the smallest change that removes the data loss.
  SAFETY="refs/candidate-safety/row$r/$(date -u +%Y%m%dT%H%M%SZ)"
  if ! git -C "$W" update-ref "$SAFETY" "$H" >>"$OUT" 2>&1; then
    echo "row $r: REFUSING -- could not pin the candidate at ${H:0:8} before rebasing" >>"$OUT"
    skipped=$((skipped+1)); continue
  fi
  echo "row $r: pinned ${H:0:8} at $SAFETY before rebase" >>"$OUT"

  if ! git -C "$W" stash push -u -m "bd-rebase-all" >>"$OUT" 2>&1; then
    echo "row $r: STASH FAILED -- left untouched" >>"$OUT"; skipped=$((skipped+1)); continue
  fi
  # AN EMPTY STASH IS THE TELL. If nothing was stashed, the work is committed,
  # so a detaching checkout would move away from it and pop would restore
  # nothing -- the 2026-08-30 sequence exactly. Rebase the commit instead of
  # pretending the worktree was dirty.
  if ! git -C "$W" stash list 2>/dev/null | grep -q "bd-rebase-all"; then
    echo "row $r: stash is EMPTY -- the work is COMMITTED, not dirty; rebasing the commit" >>"$OUT"
    if git -C "$W" rebase "$MAIN" >>"$OUT" 2>&1; then
      AFTER=$(git -C "$W" rev-parse HEAD 2>/dev/null)
      if [ -z "$(git -C "$W" diff --name-only "$MAIN" "$AFTER" 2>/dev/null)" ]; then
        echo "row $r: REFUSING -- worktree is EMPTY against main after rebase; restoring ${H:0:8}" >>"$OUT"
        git -C "$W" rebase --abort >>"$OUT" 2>&1
        git -C "$W" checkout --detach "$H" >>"$OUT" 2>&1
        skipped=$((skipped+1)); continue
      fi
      echo "row $r: re-based commit onto ${MAIN:0:8} (pinned at $SAFETY)" >>"$OUT"; ok=$((ok+1)); continue
    fi
    git -C "$W" rebase --abort >>"$OUT" 2>&1
    echo "row $r: commit rebase CONFLICTED -- left at ${H:0:8}, pinned at $SAFETY" >>"$OUT"
    conflict=$((conflict+1)); echo "$r" >> "$A/inflight/rebase-conflicts.txt"; continue
  fi
  if git -C "$W" checkout --detach "$MAIN" >>"$OUT" 2>&1 && git -C "$W" stash pop >>"$OUT" 2>&1; then
    echo "row $r: re-based onto ${MAIN:0:8}" >>"$OUT"; ok=$((ok+1))
  else
    # THE INTEGRATOR OWNS THREE OF THESE FILES, SO A CONFLICT IN THEM IS NOISE.
    # The register merges BY ROW IDENTITY and ci.yml / the shard gate UNION-
    # resolve. Re-deriving them here is the same resolution the integrator would
    # perform, just earlier -- it is not a judgement call and not a shortcut.
    python3 /home/mboyle/bd-resolve-owned.py "$W" "$r" >>"$OUT" 2>&1
    u=$(git -C "$W" diff --name-only --diff-filter=U 2>/dev/null | tr '\n' ' ')
    if [ -z "$u" ]; then
      echo "row $r: re-based onto ${MAIN:0:8} (integrator-owned files re-derived)" >>"$OUT"
      ok=$((ok+1))
    else
      echo "row $r: CONFLICTS on real content: $u" >>"$OUT"
      conflict=$((conflict+1))
      echo "$r" >> "$A/inflight/rebase-conflicts.txt"
    fi
  fi
done
echo "rebase-all: ok=$ok conflict=$conflict skipped=$skipped" | tee -a "$OUT"
