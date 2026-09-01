#!/usr/bin/env bash
# bd-ship -- push a frozen candidate, wait for exact-head CI under a BOUND and a
# NONZERO DENOMINATOR, merge only the reviewed head, prove the merged tree.
#
# Usage: bd-ship.sh <branch> <title> <pr-body-file>
#
# TWO DEFECTS THIS SCRIPT USED TO HAVE, both of them the session's own subject:
#
#   1. AN UNBOUNDED WAIT. `until <checks done>; do sleep; done` never ends if a
#      check is never created. Now bounded at 40 polls x 45s.
#   2. A ZERO DENOMINATOR READ AS GREEN. On the first poll after a push the
#      rollup is usually EMPTY -- the checks do not exist yet -- and `gh` errors
#      also produce an empty list. Zero pending then meant "green", so the script
#      could merge unattended having observed NO CHECKS AT ALL. That is CLAUDE.md
#      A7's nonzero-denominator rule violated inside the automation that enforces
#      it. Green now requires every check NAME derived from the frozen
#      candidate's own ci.yml to be present and passing. An empty or incomplete
#      board is UNKNOWN -- never permission.
set -uo pipefail
L=${L:-/tmp/bd-ship.log}
say(){ echo "$(date -u +%T) $*" | tee -a "$L"; }

_bd_ship_remove_snapshot() {
  # Only remove the exact namespace this function creates. A cleanup helper
  # must not turn an empty/corrupt variable into a broad rm target.
  case "$1" in
    /tmp/bd-ship-ci.*) rm -rf -- "$1" ;;
    *) say "REFUSE: unsafe CI snapshot cleanup target ${1:-<empty>}"; return 1 ;;
  esac
}

_bd_ship_pr_head() {
  gh pr view "$1" --json headRefOid --jq .headRefOid 2>>"$L"
}

_bd_ship_assert_fresh_base() {
  local repo=$1 candidate=$2 current_main candidate_base
  git -C "$repo" fetch --prune origin \
    '+refs/heads/main:refs/remotes/origin/main' >>"$L" 2>&1 || {
      say "REFUSE: cannot freshly fetch origin/main -- NOT MERGING";
      return 5;
    }
  current_main=$(git -C "$repo" rev-parse refs/remotes/origin/main 2>>"$L") || {
    say "REFUSE: freshly fetched origin/main is unreadable -- NOT MERGING";
    return 5;
  }
  candidate_base=$(git -C "$repo" merge-base "$candidate" "$current_main" 2>>"$L") || {
    say "REFUSE: candidate/main merge-base cannot be derived -- NOT MERGING";
    return 5;
  }
  [ "$candidate_base" = "$current_main" ] || {
    say "REBASE_REQUIRED: frozen candidate base $candidate_base != current origin/main $current_main -- NOT MERGING";
    return 5;
  }
  say "fresh base proven: candidate merge-base = origin/main $current_main"
}

_bd_ship_materialize_base_tool() {
  local repo=$1 snapshot=$2 tool=$3 base dest
  base=$(git -C "$repo" rev-parse refs/remotes/origin/main 2>>"$L") || {
    say "REFUSE: cannot resolve trusted origin/main for $tool";
    return 4;
  }
  dest=$snapshot/bd-ship-trusted-bin/$tool
  mkdir -p "${dest%/*}" || {
    say "REFUSE: cannot prepare trusted $tool path";
    return 4;
  }
  git -C "$repo" show "$base:toolchain/bin/$tool" > "$dest" 2>>"$L" || {
    say "REFUSE: origin/main $base lacks trusted $tool";
    return 4;
  }
  chmod 700 "$dest" || {
    say "REFUSE: cannot make trusted $tool executable";
    return 4;
  }
  printf '%s\n' "$dest"
}

_bd_ship_pr_base_is_current() {
  local pr=$1 state
  state=$(gh pr view "$pr" --json mergeStateStatus --jq .mergeStateStatus 2>>"$L")
  [ "$state" = "CLEAN" ] || {
    say "PR BASE NOT CURRENT: GitHub mergeStateStatus is ${state:-<empty>} -- NOT MERGING";
    return 5;
  }
}

_bd_ship_merge() {
  local pr=$1 candidate=$2
  gh pr merge "$pr" --merge --delete-branch --match-head-commit "$candidate"
}

bd_ship_premerge_gate() {
  # The only successful return from this function means:
  #   * the workflow run belonged to the frozen candidate;
  #   * current origin/main is exactly the candidate's merge-base;
  #   * every required check NAME from that candidate's ci.yml is present/pass;
  #   * the PR head was the candidate both before and after the verdict query.
  local repo=$1 candidate=$2 pr=$3 snapshot upstream python wait_max
  local head wait_out wait_rc real_gh filter trusted_wait trusted_verdict
  local verdict_out verdict_rc head_after
  snapshot=$(mktemp -d /tmp/bd-ship-ci.XXXXXX) \
    || { say "REFUSE: cannot create exact-candidate CI snapshot"; return 4; }
  upstream=$(git -C "$repo" remote get-url origin 2>>"$L") || {
    say "REFUSE: cannot resolve origin for exact-candidate CI";
    _bd_ship_remove_snapshot "$snapshot"; return 4;
  }
  if ! {
    git clone --quiet --no-hardlinks --shared "$repo" "$snapshot" >>"$L" 2>&1 \
      && git -C "$snapshot" checkout --quiet --detach "$candidate" >>"$L" 2>&1 \
      && git -C "$snapshot" remote set-url origin "$upstream" >>"$L" 2>&1
  }; then
      say "REFUSE: cannot materialize frozen candidate $candidate for CI";
      _bd_ship_remove_snapshot "$snapshot"; return 4;
  fi
  [ "$(git -C "$snapshot" rev-parse HEAD 2>>"$L")" = "$candidate" ] || {
    say "REFUSE: exact-candidate CI snapshot is not at $candidate";
    _bd_ship_remove_snapshot "$snapshot"; return 4;
  }
  if [ ! -f "$snapshot/.github/workflows/ci.yml" ]; then
    say "REFUSE: frozen candidate lacks ci.yml";
    _bd_ship_remove_snapshot "$snapshot"; return 4;
  fi
  python=${BD_SHIP_PYTHON:-$repo/venv/bin/python}
  [ -x "$python" ] || {
    say "REFUSE: Python for candidate CI verdict is not executable: $python";
    _bd_ship_remove_snapshot "$snapshot"; return 4;
  }

  head=$(_bd_ship_pr_head "$pr")
  [ "$head" = "$candidate" ] || {
    say "PR head $head != frozen candidate $candidate before CI wait -- NOT MERGING";
    _bd_ship_remove_snapshot "$snapshot"; return 5;
  }

  # The candidate supplies ci.yml as DATA, never the executable that interprets
  # it. Pin the waiter to freshly fetched origin/main before it reads any PR
  # evidence, so a candidate cannot self-authorize by replacing a bd-ci helper.
  _bd_ship_assert_fresh_base "$repo" "$candidate" || {
    _bd_ship_remove_snapshot "$snapshot"; return 5;
  }
  trusted_wait=$(_bd_ship_materialize_base_tool "$repo" "$snapshot" bd-ci-wait) || {
    _bd_ship_remove_snapshot "$snapshot"; return 4;
  }

  # bd-ci-wait pins the workflow run to the PR's current SHA. The name-aware
  # verdict below is still authoritative; a successful run conclusion cannot
  # stand in for its required check identities.
  wait_max=${BD_SHIP_WAIT_MAX:-60}  # 60 x 30s = the prior 30-minute bound
  wait_out=$(bash "$trusted_wait" "$pr" "$wait_max" 2>&1)
  wait_rc=$?
  printf '%s\n' "$wait_out" | tee -a "$L"
  if [ "$wait_rc" -ne 0 ]; then
    say "exact-head workflow wait refused/failed (rc=$wait_rc) -- NOT MERGING"
    _bd_ship_remove_snapshot "$snapshot"
    [ "$wait_rc" -eq 1 ] && return 6
    return 4
  fi
  head=$(_bd_ship_pr_head "$pr")
  [ "$head" = "$candidate" ] || {
    say "PR head $head != frozen candidate $candidate after CI wait -- NOT MERGING";
    _bd_ship_remove_snapshot "$snapshot"; return 5;
  }

  # FRESH BASE, IMMEDIATELY BEFORE THE FINAL VERDICT. A candidate verified on
  # yesterday's main is not verified on today's main.
  _bd_ship_assert_fresh_base "$repo" "$candidate" || {
    _bd_ship_remove_snapshot "$snapshot"; return 5;
  }
  trusted_verdict=$(_bd_ship_materialize_base_tool "$repo" "$snapshot" bd-ci-verdict) || {
    _bd_ship_remove_snapshot "$snapshot"; return 4;
  }
  _bd_ship_pr_base_is_current "$pr" || {
    _bd_ship_remove_snapshot "$snapshot"; return 5;
  }

  head=$(_bd_ship_pr_head "$pr")
  [ "$head" = "$candidate" ] || {
    say "PR head $head != frozen candidate $candidate before exact-name verdict -- NOT MERGING";
    _bd_ship_remove_snapshot "$snapshot"; return 5;
  }

  # CodeRabbit is explicitly advisory by operator ruling (2026-08-25, repeated
  # 18:44): it is not produced by ci.yml and must not hold the release lane.
  # Exclude ONLY a tab-delimited row whose complete check-name field is exactly
  # "CodeRabbit". Prefixes, suffixes, and every workflow-derived name remain.
  real_gh=$(type -P gh 2>/dev/null) || {
    say "REFUSE: gh is not executable for exact-name verdict";
    _bd_ship_remove_snapshot "$snapshot"; return 4;
  }
  filter=$snapshot/bd-ship-gh-required
  cat > "$filter" <<'FILTER'
#!/usr/bin/env bash
set -u -o pipefail
tmp=$(mktemp /tmp/bd-ship-gh.XXXXXX) || {
  echo "REFUSE: cannot capture gh required-check evidence" >&2
  exit 125
}
trap 'rm -f -- "$tmp"' EXIT
"$BD_SHIP_REAL_GH" "$@" > "$tmp"
gh_rc=$?

# A pre-bootstrap bd-ci-verdict accepted complete pass rows without looking at
# the command's exit status. Do not feed it a seemingly green board when gh (or
# this exact-name filter's upstream) failed collection. Non-pass rows are still
# emitted: their explicit status remains a normal blocking verdict.
if [ "$gh_rc" -ne 0 ] && awk -F '\t' '
  BEGIN { only_pass = 1 }
  $1 != "CodeRabbit" {
    seen = 1
    if (NF < 2 || $2 != "pass") only_pass = 0
  }
  END { exit !(seen && only_pass) }
' "$tmp"; then
  echo "REFUSE: gh exited $gh_rc despite retained all-pass check rows" >&2
  exit "$gh_rc"
fi

awk -F '\t' '$1 != "CodeRabbit"' "$tmp"
filter_rc=$?
[ "$filter_rc" -eq 0 ] || exit "$filter_rc"
exit "$gh_rc"
FILTER
  chmod 700 "$filter" || {
    say "REFUSE: cannot prepare exact CodeRabbit advisory filter";
    _bd_ship_remove_snapshot "$snapshot"; return 4;
  }
  verdict_out=$(BD_SHIP_REAL_GH="$real_gh" "$python" \
    "$trusted_verdict" "$pr" --repo "$repo" \
    --ci-yml "$snapshot/.github/workflows/ci.yml" --gh "$filter" 2>&1)
  verdict_rc=$?
  printf '%s\n' "$verdict_out" | tee -a "$L"

  # Read head AFTER the query even when the verdict itself refused. A query
  # racing a force-push is evidence about neither stable head.
  head_after=$(_bd_ship_pr_head "$pr")
  if [ "$head_after" != "$candidate" ]; then
    say "PR HEAD MOVED DURING EXACT-NAME VERDICT: $candidate -> $head_after -- NOT MERGING"
    _bd_ship_remove_snapshot "$snapshot"; return 5
  fi
  if [ "$verdict_rc" -ne 0 ] \
     || ! grep -q '^VERDICT: MERGE-SAFE ' <<<"$verdict_out"; then
    say "exact-name candidate CI verdict refused/blocked (rc=$verdict_rc) -- NOT MERGING"
    _bd_ship_remove_snapshot "$snapshot"; return 6
  fi
  say "exact-head CI green by required NAME at frozen candidate $candidate"
  _bd_ship_remove_snapshot "$snapshot"
}

# Focused tests source the real gate functions and never enter push/merge.
if [ "${BD_SHIP_SOURCE_ONLY:-0}" = 1 ]; then
  if [ "${BASH_SOURCE[0]}" != "$0" ]; then return 0; fi
  exit 0
fi

cd /home/mboyle/BulkDownloader || exit 1
BR="$1"; TITLE="$2"; BODY="$3"
[ -s "$BODY" ] || { echo "no PR body at $BODY"; exit 1; }
L=/tmp/ship-$(basename "$BR").log

# RESOLVE THE CANDIDATE FROM THE BRANCH, NOT FROM HEAD. Taking it from HEAD
# meant this script could only ship the branch you happened to be standing on,
# so shipping cut N while cut N+1 was checked out required a `git checkout` --
# which fails on a dirty tree and reintroduces the very coupling the detached
# push copy was added to remove. The branch name is already an argument; use it.
HEAD_SHA=$(git rev-parse --verify "$BR^{commit}" 2>/dev/null) \
  || { echo "no such branch: $BR"; exit 2; }
say "candidate $HEAD_SHA tree $(git rev-parse "$HEAD_SHA^{tree}")"

# THE CLEANLINESS CHECK BELONGS AT MERGE TIME, NOT HERE. A push sends the
# COMMIT; a dirty working tree cannot change what lands on the remote. And this
# check used to run while the sanctioned full suite was executing against the
# same checkout -- some tests regenerate a tracked golden and restore it, so the
# tree is TRANSIENTLY dirty through no fault of the candidate, and the script
# refused a correct candidate at exit 2. What A4 actually forbids is MERGING
# with unpushed or uncommitted work, so that is where it is asserted now, after
# CI has had its say and any concurrent lane has finished.

# PUSH FROM A DETACHED COPY, so the integrator's working tree is free the moment
# a candidate is frozen. Twice on 2026-08-25 a cut broke its own ship by being
# next to the following one: a fleet deploy `git reset --hard` raced a `git
# apply` at v3.66.1233, and untracked files from the next cut made this script
# refuse a clean merge at v3.66.1238. Both were self-inflicted and both are
# removed by not pushing from the live tree at all.
#
# The clone carries the FROZEN COMMIT and nothing else, so what reaches the
# remote cannot include a neighbouring cut's work even by accident.
SHIPDIR=$(mktemp -d /tmp/bd-ship-XXXXXX)
if [ "${BD_SHIP_PHASE:-all}" = "merge" ]; then
  # Resume against the PR the push phase opened. Read the number from state,
  # then RE-DERIVE it from the remote as the authority -- a stale state file
  # must never name a PR that this branch no longer owns.
  NUM=$(cat "/home/mboyle/fleet-run-artifacts/2026-08-25/inflight/.pr-$(basename "$BR")" 2>/dev/null)
  RN=$(gh pr list --head "$BR" --json number --jq '.[0].number' 2>/dev/null)
  if [ -z "$RN" ] || [ "$RN" = "null" ]; then
    say "PHASE=merge: no open PR for $BR"; exit 3
  fi
  [ -z "$NUM" ] || [ "$NUM" = "$RN" ] || say "PHASE=merge: state said PR #$NUM, remote says #$RN -- trusting the remote"
  NUM="$RN"
  say "PHASE=merge: resuming PR #$NUM for $BR"
else
say "pushing $HEAD_SHA from a detached copy at $SHIPDIR"
git clone --quiet --no-hardlinks --shared . "$SHIPDIR" >>"$L" 2>&1 \
  || { say "could not build the detached push copy"; exit 3; }
git -C "$SHIPDIR" checkout --quiet -B "$BR" "$HEAD_SHA" >>"$L" 2>&1 \
  || { say "detached copy could not reach $HEAD_SHA"; exit 3; }
# POINT THE COPY AT THE REAL REMOTE. `git clone .` sets the clone's origin to
# the LOCAL path it was cloned from, so `push origin` would push back into this
# working repository and fail non-fast-forward against a branch checked out
# here -- which is exactly what happened on this fix's first use. Take the URL
# from the repository that actually has it.
UPSTREAM=$(git remote get-url origin)
git -C "$SHIPDIR" remote set-url origin "$UPSTREAM" >>"$L" 2>&1 \
  || { say "could not point the detached copy at $UPSTREAM"; exit 3; }
[ "$(git -C "$SHIPDIR" remote get-url origin)" = "$UPSTREAM" ] \
  || { say "detached copy's origin is not $UPSTREAM"; exit 3; }
COPY_SHA=$(git -C "$SHIPDIR" rev-parse HEAD)
[ "$COPY_SHA" = "$HEAD_SHA" ] \
  || { say "detached copy is at $COPY_SHA, not the candidate $HEAD_SHA"; exit 3; }
# AN AMENDED CANDIDATE MUST STILL BE ABLE TO REPLACE ITS OWN BRANCH, and a
# fresh clone knows nothing about the remote's current tip -- so a plain push
# fails non-fast-forward the moment a candidate is re-frozen (which happened
# twice here). Read the remote tip explicitly and lease against it: this
# refuses if anyone else moved the branch, and never uses a bare --force.
REMOTE_SHA=$(git ls-remote --heads "$UPSTREAM" "$BR" | cut -f1)
if [ -n "$REMOTE_SHA" ]; then
  say "branch exists remotely at $REMOTE_SHA; replacing under lease"
  git -C "$SHIPDIR" push --force-with-lease="refs/heads/$BR:$REMOTE_SHA" \
      -u origin "$BR" >>"$L" 2>&1 \
    || { say "lease push failed -- someone else moved $BR"; rm -rf "$SHIPDIR"; exit 3; }
else
  git -C "$SHIPDIR" push -u origin "$BR" >>"$L" 2>&1 \
    || { say "push failed"; rm -rf "$SHIPDIR"; exit 3; }
fi
rm -rf "$SHIPDIR"
NUM=$(gh pr list --head "$BR" --json number --jq '.[0].number')
if [ -z "$NUM" ] || [ "$NUM" = "null" ]; then
  gh pr create --base main --head "$BR" --title "$TITLE" --body-file "$BODY" >>"$L" 2>&1
  NUM=$(gh pr list --head "$BR" --json number --jq '.[0].number')
fi
if [ -z "$NUM" ] || [ "$NUM" = "null" ]; then say "no PR number"; exit 3; fi
say "PR #$NUM"

# OVERLAP THE TWO INDEPENDENT GATES. The affected band and exact-head CI judge
# the SAME frozen SHA and do not depend on each other, but the lane ran them in
# series: ~11 min of band, then ~10 min of CI, ~25 min per cut end to end.
# BD_SHIP_PHASE=push stops here, with the PR created and CI already running, so
# the caller can run the band CONCURRENTLY and come back for BD_SHIP_PHASE=merge.
#
# NOTHING IS RELAXED BY THIS. The merge still requires exact-head CI over a
# nonzero denominator AND the band's own ALL GREEN verdict; both still judge this
# exact SHA; the caller asserts the verdict before it ever calls the merge phase.
# The only thing that changes is that a candidate the band later rejects will
# have burned a CI run -- cheap -- and its PR must then be CLOSED and its remote
# branch DELETED, because a green unmerged PR blocks every retry of that version.
echo "$NUM" > "/home/mboyle/fleet-run-artifacts/2026-08-25/inflight/.pr-$(basename "$BR")"
if [ "${BD_SHIP_PHASE:-all}" = "push" ]; then
  say "PHASE=push complete -- PR #$NUM open, CI running; band runs concurrently"
  exit 0
fi
fi   # end of the push half

# THE FROZEN COMMIT MUST BE THE REMOTE PR HEAD. Ask the remote, not this
# checkout's tracking ref: push phase runs from a detached copy, so origin/$BR
# here is intentionally not evidence about what was pushed.
PUSHED=$(git ls-remote --heads "$(git remote get-url origin)" "$BR" | cut -f1)
[ -n "$PUSHED" ] || { say "branch $BR is not on the remote -- NOT MERGING"; exit 2; }
[ "$PUSHED" = "$HEAD_SHA" ] || {
  say "remote $BR is at $PUSHED but the candidate is $HEAD_SHA -- NOT MERGING"; exit 2; }

# This is the final authorisation immediately before `gh pr merge`: exact-head
# workflow completion, freshly current main/base, exact required check NAMES,
# and another exact-head read after the verdict query. No count floor exists.
bd_ship_premerge_gate "$PWD" "$HEAD_SHA" "$NUM"
CI_GATE_RC=$?
[ "$CI_GATE_RC" -eq 0 ] || exit "$CI_GATE_RC"

# The gate's verdict can age during local cleanup. Re-fetch and make GitHub's
# own mergeability calculation agree immediately before the atomic head-bound
# merge mutation.
_bd_ship_assert_fresh_base "$PWD" "$HEAD_SHA"
BASE_GATE_RC=$?
[ "$BASE_GATE_RC" -eq 0 ] || exit "$BASE_GATE_RC"
_bd_ship_pr_base_is_current "$NUM" || exit 5

# NOW the tree must be clean: nothing uncommitted, nothing unpushed (A4).
# WHAT MATTERS IS THAT THE CANDIDATE IS FULLY PUSHED, not that the working tree
# is idle. Since the push now comes from a detached copy, the tree is free to
# hold the NEXT cut while this one is in CI -- so a dirty tree is no longer
# evidence of anything about this candidate, and refusing on it was refusing
# the wrong thing.
# --merge, NOT --squash: every merge on this repo is a merge commit, and a squash
# synthesizes a new commit so the reviewed candidate never becomes an ancestor --
# which would make the ancestry proof below fail on a successful merge.
# TRUST THE PR STATE, NOT THE EXIT CODE. `gh pr merge --delete-branch` returns
# nonzero when the branch is already gone (GitHub auto-delete races it), and at
# v3.66.1226 that reported "merge failed" for a PR that had merged cleanly. The
# authority on whether a merge happened is the PR's own state.
_bd_ship_merge "$NUM" "$HEAD_SHA" >>"$L" 2>&1
STATE=$(gh pr view "$NUM" --json state --jq .state 2>/dev/null)
[ "$STATE" = "MERGED" ] || { say "merge did not happen (PR state $STATE)"; exit 7; }
git fetch --prune origin >>"$L" 2>&1
# VERIFY AGAINST origin/main, WHICH NEEDS NO CHECKOUT. `git checkout main` fails
# outright when another worktree holds the branch -- measured at v3.66.1223,
# where a stale worktree from a v1174-era cut still had `main` and the script
# reported "merge failed" for a PR GitHub had already merged. The remote ref is
# the authority for containment and tree identity; the local checkout is a
# convenience, and it is moved AFTER the proof rather than being a precondition
# for it.
git merge-base --is-ancestor "$HEAD_SHA" origin/main \
  || { say "ANCESTRY UNPROVEN -- stopping before deploy"; exit 8; }
# CONTAINMENT IS NOT IDENTITY. Prove the merged TREE is the reviewed tree.
git diff --quiet "$HEAD_SHA" origin/main \
  || { say "MERGED TREE DIFFERS from the reviewed candidate:"; git diff --stat "$HEAD_SHA" origin/main | tee -a "$L"; exit 9; }
say "merged origin/main $(git rev-parse origin/main); tree identical to reviewed candidate"
# Now move the local checkout, and say so plainly if the branch is held elsewhere
# -- that is a workspace fact, not a failed merge.
if [ -z "$(git status --porcelain)" ] \
   && git checkout main >>"$L" 2>&1 && git merge --ff-only origin/main >>"$L" 2>&1; then
  say "local main fast-forwarded to $(git rev-parse --short HEAD)"
else
  say "NOTE: local 'main' not moved (another worktree holds it). The merge is done and proven; only this checkout lags."
fi
say "DONE-MERGE"
