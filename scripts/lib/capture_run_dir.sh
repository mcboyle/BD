#!/usr/bin/env bash
# capture_run_dir.sh -- where does THIS capture write, and what removes the old ones?
#
# BACKLOG 5. capture.sh wrote a fixed /tmp/bd_capture, so consecutive captures
# overwrote each other in place. The evidence from a failing round survived only
# if somebody copied it out before the next one started -- which is exactly what
# happened on 2026-08-13, twice, by hand.
#
# WHY THIS IS A LIBRARY AND NOT FOUR LINES INLINE. The same reason as
# tree_state.sh: a test can SOURCE this and RUN it. Pruning logic asserted as
# source TEXT is asserted at the level where it is easiest to get wrong -- "keep
# the newest five" has an off-by-one, a mtime-versus-name ordering question and
# a does-it-delete-the-current-run question, and none of those are visible in a
# grep.
#
# THE RETENTION RULE, and why it prunes at START rather than at END:
# a run that CRASHES leaves the most valuable directory on the box. Pruning on
# the way out would delete it as part of the failure, which is the one moment
# nobody wants tidiness. Pruning on the way IN means the newest N always
# survive, including a crashed one, until N more runs have happened.

# bd_capture_run_id [dir]
#   Prints a run id that is stable within a run and unique across runs.
#
# The timestamp is UTC and second-resolution; the short commit is there so a
# human reading `ls /tmp` can tell which tree a bundle describes without opening
# it. Neither is a uniqueness guarantee on its own -- two captures of the same
# commit in the same second would collide -- so the PID is appended when the
# directory already exists rather than assumed away.
bd_capture_run_id() {
  local dir="${1:-$PWD}"
  local stamp sha id
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  sha="$(git -C "$dir" rev-parse --short HEAD 2>/dev/null || printf 'nogit')"
  id="${stamp}-${sha}"
  if [ -e "/tmp/bd_capture-${id}" ]; then
    id="${id}-$$"
  fi
  printf '%s' "$id"
}

# Apply the test-root policy on the way into a long-running harness. The tool
# owns classification and object-bound removal; this shell wrapper owns no
# destructive primitive and propagates the diagnostic exit status.
bd_test_root_gc() {
  local repo="${1:?bd_test_root_gc needs the repository root}"
  "$repo/venv/bin/python" "$repo/toolchain/bin/bd-gc" \
    --apply --older-than 1440 --only classified
}

# bd_capture_prune <keep> [glob]
#   Remove all but the newest <keep> capture directories and their tarballs.
#   Prints what it removed, one per line, so the run's own log records it.
#
# NEWEST BY MODIFICATION TIME, not by name. The names sort correctly today
# because they lead with a UTC timestamp, but that is a property of the current
# format rather than of the retention rule, and a format change would silently
# reorder a name-sorted prune into deleting the wrong thing.
bd_capture_prune() {
  local keep="${1:?bd_capture_prune needs a keep count}"
  local glob="${2:-/tmp/bd_capture-*}"

  if ! [ "$keep" -ge 1 ] 2>/dev/null; then
    printf 'bd_capture_prune: refusing keep=%s -- a retention of zero would\n' "$keep" >&2
    printf '  delete the evidence this function exists to preserve.\n' >&2
    return 2
  fi

  local d n=0
  # -maxdepth 0 so the glob's own matches are the subjects, never their
  # contents; -printf with %T@ gives a sortable mtime that does not depend on
  # the name format.
  while IFS= read -r d; do
    n=$((n + 1))
    if [ "$n" -gt "$keep" ]; then
      rm -rf -- "$d" "${d}.tar.gz"
      printf 'pruned %s\n' "$d"
    fi
  done <<EOF
$(find /tmp -maxdepth 1 -name "$(basename "$glob")" -type d -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn | cut -d' ' -f2-)
EOF
}
