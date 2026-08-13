#!/usr/bin/env bash
# tree_state.sh -- is this working tree safe to measure against?
#
# ONE PREDICATE, TWO CALLERS. capture.sh asks before a forty-minute run;
# backlog 35's pre-commit check asks before a commit. Writing it inline in
# either would guarantee a second copy in the other, and scripts/lib/system_deps.sh
# already carries what three copies of one fact cost this project.
#
# WHY capture.sh CARES. On the master box the working tree IS the deployed
# tree, and capture step [2b] rebuilds a source graph and compares its hash
# against a pin written at deploy time. Uncommitted files therefore fail the
# graph gate, and capture_verdict.py turns that stage exit into a whole-capture
# FAIL -- measured twice, at v3.66.1063 (four uncommitted files, unit
# 15709/0/0/26 and live 34/2/0 underneath a red verdict) and again at
# v3.66.1069. The gate was right both times. What was missing is saying so at
# the START, where it costs a second, instead of in a verdict line forty
# minutes later that reads like a code defect.
#
# UNKNOWN IS A THIRD STATE. Outside a git repository the question cannot be
# answered, and answering "clean" would be this check reporting OK over a
# subject it cannot see -- the failure CLAUDE.md section 0 is entirely about.
# It refuses instead, and says which of the two it is.

# bd_tree_state_check <dir>
#   0  clean, or explicitly overridden
#   1  dirty -- names the paths
#   2  unknown -- not a git repository, or git unavailable
bd_tree_state_check() {
  local dir="${1:-$PWD}"

  if ! command -v git >/dev/null 2>&1; then
    printf 'tree state UNKNOWN: git is not on PATH, so "clean" cannot be\n' >&2
    printf '  established. Refusing rather than assuming.\n' >&2
    return 2
  fi

  if ! git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    printf 'tree state UNKNOWN: %s is not a git working tree, so cleanliness\n' "$dir" >&2
    printf '  cannot be established. Refusing rather than assuming.\n' >&2
    return 2
  fi

  local changed
  changed="$(git -C "$dir" status --porcelain --untracked-files=all 2>/dev/null)"

  if [ -z "$changed" ]; then
    return 0
  fi

  # Report BEFORE honouring the override, so an overridden run still records
  # what it was overriding. A silent override is indistinguishable from a
  # clean tree in the log someone reads afterwards.
  printf 'WORKING TREE IS NOT CLEAN:\n' >&2
  printf '%s\n' "$changed" | sed 's/^/    /' >&2
  printf '\n' >&2
  printf 'This matters here because the source GRAPH hash is rebuilt from the\n' >&2
  printf '  working tree and compared against a pin written at deploy time, so\n' >&2
  printf '  uncommitted files fail that gate and the whole run is graded FAIL --\n' >&2
  printf '  measured twice, on a tree whose suite was entirely green.\n' >&2
  printf '  Do NOT stash mid-run: collection has already happened, so removing\n' >&2
  printf '  files leaves a collected-but-inconsistent state and the suite fails\n' >&2
  printf '  instead. Commit, stash BEFORE starting, or set CAPTURE_ALLOW_DIRTY=1\n' >&2
  printf '  if you meant it.\n' >&2

  if [ "${CAPTURE_ALLOW_DIRTY:-0}" = "1" ]; then
    printf '\nCAPTURE_ALLOW_DIRTY=1 -- proceeding anyway, as instructed.\n' >&2
    return 0
  fi
  return 1
}

# bd_tree_state_snapshot <dir>
#   0  snapshot on stdout
#   2  unknown -- not a git working tree, or git unavailable
#
# WHY A SNAPSHOT AND NOT A SECOND `check`. `bd_tree_state_check` answers "is
# this tree clean?", which is the wrong question after a run has started. A
# capture may legitimately begin on a dirty tree (CAPTURE_ALLOW_DIRTY), and a
# mid-run COMMIT leaves the tree clean on both sides while moving the source out
# from under the measurement. The question that matters at the end is "is this
# the SAME tree we measured?", and only a comparison can answer it.
#
# WHY HEAD IS IN IT. `git status` is empty before and after a commit, so a
# porcelain-only snapshot cannot see the one mid-run change that leaves no
# trace in the working tree. The graph pin at step [2b] is derived from source,
# so it drifts either way.
#
# WHY NO TIMESTAMP, AND NOTHING ELSE THAT MOVES ON ITS OWN. This value is
# compared against itself twenty minutes later, so anything varying with the
# clock makes every capture report drift -- the over-sensitivity failure
# CLAUDE.md section 0 names, and the exact shape of the manifest pin that
# hashed a wall-clock `generated` field and made an unchanged tree "change"
# every run. A gate that cries wolf gets switched off.
#
# WHY UNKNOWN RETURNS NONZERO RATHER THAN AN EMPTY SNAPSHOT. Two unreadable
# snapshots compare EQUAL as strings, so a caller diffing stdout alone would
# find no difference and certify "the tree did not move" over a subject it
# could not see. That is section 0's whole subject, and it would be reproduced
# inside the check written to close a section 0 gap. Callers MUST branch on the
# status, not on the text.
bd_tree_state_snapshot() {
  local dir="${1:-$PWD}"

  if ! command -v git >/dev/null 2>&1; then
    printf 'tree snapshot UNKNOWN: git is not on PATH.\n' >&2
    return 2
  fi

  if ! git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    printf 'tree snapshot UNKNOWN: %s is not a git working tree.\n' "$dir" >&2
    return 2
  fi

  # A repository with no commits yet has no HEAD to name; that is a legitimate
  # shape (the synthetic capture directories several tests build), so it gets a
  # stable literal rather than an error.
  printf 'HEAD %s\n' "$(git -C "$dir" rev-parse HEAD 2>/dev/null || printf 'NONE')"
  git -C "$dir" status --porcelain --untracked-files=all 2>/dev/null
}

# bd_tree_state_drift <earlier-snapshot> <dir>
#   0  no drift -- nothing written to stdout
#   1  drift -- the differing lines on stdout
#   2  unknown -- the tree cannot be read now, so the question is unanswerable
#
# This lives here rather than inline in capture.sh so it can be RUN by a test.
# CLAUDE.md section 10's `bd-jobs` lesson is the reason: both sides were tested,
# both were correct, and the JOIN was what failed -- because every test either
# handed the callee an already-built value or inspected the result afterwards,
# and none asked what actually reached the shell. An inline comparison would
# only ever be assertable as source TEXT.
bd_tree_state_drift() {
  # NORMALISE THE TRAILING NEWLINE, or this reports drift on every run.
  # `now` below is assigned through command substitution, which strips ALL
  # trailing newlines; a caller who captured the earlier snapshot any other way
  # -- read from a file, piped, passed as an argument -- still has one. The two
  # then never compare equal, the check fires on every capture, and an
  # over-sensitive gate gets switched off (CLAUDE.md section 0). Round-tripping
  # through the same substitution is what makes the comparison symmetric.
  # Measured: this test failed on its first run for exactly this reason.
  local earlier
  earlier="$(printf '%s\n' "$1")"
  local dir="${2:-$PWD}"
  local now rc=0

  now="$(bd_tree_state_snapshot "$dir")" || rc=$?
  if [ "$rc" -ne 0 ]; then
    return 2
  fi

  if [ "$now" = "$earlier" ]; then
    return 0
  fi

  # Both directions, deliberately: a file that VANISHED mid-run moved the tree
  # exactly as much as one that appeared, and a one-sided comparison would miss
  # a mid-run `git stash` -- which CLAUDE.md section 9 records as the reflex
  # that produces a collected-but-inconsistent state.
  diff <(printf '%s\n' "$earlier") <(printf '%s\n' "$now") \
    | sed -n 's/^[<>] //p' | sort -u
  return 1
}
