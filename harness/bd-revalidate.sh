#!/bin/bash
# REVALIDATE A PARKED CANDIDATE AGAINST THE CURRENT MAIN.
#
# WHY THIS EXISTS. On 2026-08-30 six worker candidates sat finished and
# unintegrated while main advanced two releases past their dispatch base. Their
# recorded evidence was real and no longer transferable: CLAUDE.md A4 says a
# result becomes stale when applicable source, tests, workflow or generated
# artifacts change under it. Answering "is this still good?" by hand took a
# heredoc per candidate, and A8 says a second hand-rolled heredoc is a missing
# bd-* tool. This is that tool.
#
# WHAT IT PROVES, AND WHAT IT DELIBERATELY DOES NOT. It rebases the candidate
# onto a named main in its OWN worktree and runs the test files IN ITS DIFF.
# That is a FLOOR, not the affected band: derive the band with bd-band-derive
# and run it with bd-band-remote before integrating. A clean result here means
# "still applies and its own tests still pass", never "ready to merge".
#
# It writes nothing outside its worktree and the log directory. No push, no
# merge, no register write, no deploy.
#
#   bd-revalidate.sh <main-sha> <row> <candidate-sha> [<row> <sha>]...
#   bd-revalidate.sh <main-sha> --from-file <file>   # "row sha" per line
#
# Exit 0 = every candidate revalidated green. 1 = any RED or conflict.
# 2 = nothing measurable (bad argument, missing SHA) -- UNKNOWN, never OK.
set -uo pipefail

R=${BD_REVAL_REPO:-/home/mboyle/BulkDownloader}
OUT=${BD_REVAL_OUT:-/home/mboyle/fleet-run-artifacts/$(date -u +%Y-%m-%d)/revalidate}

MAIN=${1:-}; shift || true
[[ "$MAIN" =~ ^[0-9a-f]{40}$ ]] || {
  echo "UNKNOWN: first argument must be a full lowercase main SHA" >&2; exit 2; }
git -C "$R" cat-file -e "$MAIN^{commit}" 2>/dev/null || {
  echo "UNKNOWN: $MAIN is not a commit in $R" >&2; exit 2; }

pairs=()
if [ "${1:-}" = "--from-file" ]; then
  [ -r "${2:-}" ] || { echo "UNKNOWN: cannot read ${2:-}" >&2; exit 2; }
  while read -r a b _; do [ -n "${a:-}" ] || continue; pairs+=("$a" "$b"); done < "$2"
else
  pairs=("$@")
fi
[ "${#pairs[@]}" -ge 2 ] || { echo "UNKNOWN: no <row> <sha> pairs given" >&2; exit 2; }
[ $(( ${#pairs[@]} % 2 )) -eq 0 ] || { echo "UNKNOWN: odd argument count" >&2; exit 2; }

mkdir -p "$OUT" || { echo "UNKNOWN: cannot create $OUT" >&2; exit 2; }
worst=0
i=0
while [ "$i" -lt "${#pairs[@]}" ]; do
  row=${pairs[$i]}; sha=${pairs[$((i+1))]}; i=$((i+2))
  log="$OUT/$row.log"
  verdict=UNKNOWN
  # A SUBSHELL, NOT A BRACE GROUP. `{ ...; exit 0; }` runs in the CURRENT
  # shell, so every UNKNOWN/CONFLICT path below would terminate the whole tool
  # with status 0 -- silently dropping the remaining candidates AND reporting
  # success for a run that measured nothing. The earlier hand-rolled version of
  # this loop did exactly that: a six-candidate pass stopped after the first
  # conflict and looked like a clean exit.
  (
    echo "== $row candidate=$sha onto main=$MAIN at $(date -u +%FT%TZ) on $(hostname)"
    if ! [[ "$sha" =~ ^[0-9a-f]{40}$ ]]; then
      echo "RESULT: $row UNKNOWN-bad-sha"; exit 0
    fi
    if ! git -C "$R" cat-file -e "$sha^{commit}" 2>/dev/null; then
      echo "RESULT: $row UNKNOWN-sha-absent"; exit 0
    fi
    wt="$R/.worktrees/reval-$row"; br="reval/$row"
    git -C "$R" worktree remove --force "$wt" 2>/dev/null
    git -C "$R" branch -D "$br" 2>/dev/null
    git -C "$R" worktree add -q -b "$br" "$wt" "$sha" || { echo "RESULT: $row UNKNOWN-worktree"; exit 0; }
    ln -sfn "$R/venv" "$wt/venv"
    # The vitest gate asserts a precondition nothing provisions in a fresh
    # worktree, so share the integrator's install rather than report a red that
    # belongs to provisioning (see the register row for that defect).
    [ -e "$R/frontend/node_modules" ] && ln -sfn "$R/frontend/node_modules" "$wt/frontend/node_modules"
    cd "$wt" || { echo "RESULT: $row UNKNOWN-cd"; exit 0; }
    if git rebase "$MAIN" >/dev/null 2>&1; then
      echo "REBASE: clean -> $(git rev-parse HEAD)"
    else
      git rebase --abort 2>/dev/null
      echo "REBASE: CONFLICT"
      echo "RESULT: $row NEEDS-MANUAL-REBASE"; exit 0
    fi
    echo "-- files vs main:"; git diff --name-only "$MAIN" HEAD | sed 's/^/     /'
    mapfile -t tf < <(git diff --name-only "$MAIN" HEAD | grep '^tests/.*\.py$')
    if [ "${#tf[@]}" -eq 0 ]; then
      # No test file in the diff is not a pass. It means this tool cannot
      # measure the candidate, and UNKNOWN is a failing third state.
      echo "RESULT: $row UNKNOWN-no-tests-in-diff"; exit 0
    fi
    echo "-- floor: ${#tf[@]} test file(s) from the diff (NOT the affected band)"
    env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest "${tf[@]}" -q 2>&1 | tail -6
    rc=${PIPESTATUS[0]}
    echo "PYTEST_RC=$rc"
    [ "$rc" -eq 0 ] && echo "RESULT: $row REVALIDATED-GREEN" || echo "RESULT: $row RED-ON-NEW-MAIN"
  ) > "$log" 2>&1
  verdict=$(grep -h '^RESULT:' "$log" | tail -1 | awk '{print $3}')
  case "$verdict" in
    REVALIDATED-GREEN) : ;;
    *) worst=1 ;;
  esac
  printf '%-10s %-24s %s\n' "$row" "${verdict:-UNKNOWN-no-verdict}" "$log"
done
exit "$worst"
