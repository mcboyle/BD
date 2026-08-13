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
