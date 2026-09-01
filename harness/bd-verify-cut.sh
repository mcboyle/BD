#!/usr/bin/env bash
# Verify one immutable release candidate.  The denominator is the complete net
# cut, merge-base(fresh origin/main,candidate)..candidate, not the final commit.
# All potentially mutating regeneration runs first and alone in a disposable
# exact-SHA worktree; every later reader runs only after that tree is re-proved.
set -u
umask 077

if [ "$#" -ne 2 ]; then
  echo "usage: bd-verify-cut.sh <candidate-worktree> <artifact-tag>" >&2
  exit 2
fi

WORK_INPUT=$1
TAG=$2
case "$TAG" in
  ''|*[!A-Za-z0-9._-]*)
    echo "UNKNOWN, not permission: artifact tag must match [A-Za-z0-9._-]+" >&2
    exit 2
    ;;
esac

ART=${BD_VERIFY_CUT_ARTIFACT_DIR:-/home/mboyle/fleet-run-artifacts/2026-08-25/inflight}
PREPUSH=${BD_VERIFY_CUT_PREPUSH:-/home/mboyle/bd-prepush.sh}
BAND_REMOTE=${BD_VERIFY_CUT_BAND_REMOTE:-/home/mboyle/bd-band-remote.sh}
FETCH_TIMEOUT=${BD_VERIFY_CUT_FETCH_TIMEOUT:-300}
PREPUSH_TIMEOUT=${BD_VERIFY_CUT_PREPUSH_TIMEOUT:-3600}
PRECUT_TIMEOUT=${BD_VERIFY_CUT_PRECUT_TIMEOUT:-3600}
BAND_TIMEOUT=${BD_VERIFY_CUT_BAND_TIMEOUT:-5400}
BROWSER_TIMEOUT=${BD_VERIFY_CUT_BROWSER_TIMEOUT:-1800}
BAND_WORKERS=${BAND_WORKERS:-24}
AUDIT_PYTHON=${BD_VERIFY_CUT_AUDIT_PYTHON:-/usr/bin/python3}
BASELINE_INPUT=${BD_VERIFY_CUT_BASELINE:-}

SOURCE=
TMP_ROOT=
CHECKOUT=
LOCK_DIR=
LOCK_HELD=0
WT_LOCK=
WT_LOCK_HELD=0
PREFLIGHT=${BD_VERIFY_CUT_PREFLIGHT:-/home/mboyle/bd-denom-preflight}
VCACHE=${BD_VERIFY_CUT_VCACHE:-/home/mboyle/bd-verdict-cache}
PREFLIGHT_RC=98
CANDIDATE_SHA=UNKNOWN
CANDIDATE_TREE=UNKNOWN
ORIGIN_MAIN_SHA=UNKNOWN
BASE_SHA=UNKNOWN
BASE_TREE=UNKNOWN
CH_N=0
BAND_N=0
PRECUT_RC=98
PREPUSH_RC=98
BAND_DERIVE_RC=98
BAND_AUDIT_RC=98
BAND_RC=98
BROWSER_RC=0
BASELINE_PATH=UNSET
BASELINE_SHA256=UNSET
RED_REASONS=()
UNKNOWN_REASONS=()
BAND_PREEXISTING=0
BAND_ATTRIBUTED=()
BAND_RAN_REMOTE=0
# Declared here so the attribution block can read it under `set -u` even
# when the band never ran.
REST=()

record_red() {
  RED_REASONS+=("$*")
  echo "RED: $*"
}

record_unknown() {
  UNKNOWN_REASONS+=("$*")
  echo "UNKNOWN, not permission: $*"
}

cleanup() {
  local rc=$?
  trap - EXIT
  if [ -n "$CHECKOUT" ] && [ -n "$SOURCE" ] && [ -n "$TMP_ROOT" ]; then
    case "$CHECKOUT" in
      "$TMP_ROOT"/*) git -C "$SOURCE" worktree remove --force -- "$CHECKOUT" >/dev/null 2>&1 || true ;;
    esac
  fi
  if [ -n "$TMP_ROOT" ] && [ "$TMP_ROOT" != / ] && [ -d "$TMP_ROOT" ]; then
    rm -rf -- "$TMP_ROOT"
  fi
  if [ "$LOCK_HELD" -eq 1 ] && [ -n "$LOCK_DIR" ]; then
    rmdir -- "$LOCK_DIR" >/dev/null 2>&1 || true
  fi
  if [ "$WT_LOCK_HELD" -eq 1 ] && [ -n "$WT_LOCK" ]; then
    rm -f -- "$WT_LOCK/holder" >/dev/null 2>&1 || true
    rmdir -- "$WT_LOCK" >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

finish() {
  date -u +'   end %Y-%m-%dT%H:%M:%SZ'
  echo "VERDICT $TAG: precut=$PRECUT_RC prepush=$PREPUSH_RC derive=$BAND_DERIVE_RC audit=$BAND_AUDIT_RC band=$BAND_RC over $BAND_N file(s)"
  if [ "${#UNKNOWN_REASONS[@]}" -gt 0 ]; then
    echo "NOT SHIPPABLE"
    printf '  UNKNOWN: %s\n' "${UNKNOWN_REASONS[@]}"
    exit 2
  fi
  if [ "${#RED_REASONS[@]}" -gt 0 ]; then
    echo "NOT SHIPPABLE"
    printf '  RED: %s\n' "${RED_REASONS[@]}"
    exit 1
  fi
  if [ "$PRECUT_RC" -eq 0 ] && [ "$PREPUSH_RC" -eq 0 ] \
      && [ "$BAND_DERIVE_RC" -eq 0 ] && [ "$BAND_AUDIT_RC" -eq 0 ] \
      && [ "$BAND_RC" -eq 0 ] && [ "$BAND_N" -gt 0 ]; then
    echo "ALL GREEN -- shippable"
    exit 0
  fi
  # An attributed band is shippable, and is NEVER reported as green: the words
  # name what was inherited so nobody reads this as a clean run.
  if [ "${BAND_PREEXISTING:-0}" -eq 1 ] && [ "$PRECUT_RC" -eq 0 ] \
      && [ "$PREPUSH_RC" -eq 0 ] && [ "$BAND_DERIVE_RC" -eq 0 ] \
      && [ "$BAND_AUDIT_RC" -eq 0 ] && [ "$BAND_N" -gt 0 ]; then
    echo "SHIPPABLE WITH INHERITED FAILURES -- not green"
    printf '  inherited from %s: %s\n' "${BASE_SHA:0:12}" "${BAND_ATTRIBUTED[@]}"
    exit 0
  fi
  record_unknown "verifier reached an incomplete terminal state"
  echo "NOT SHIPPABLE"
  exit 2
}

positive_integer() {
  case "$1" in ''|*[!0-9]*|0) return 1;; *) return 0;; esac
}

for setting in "$FETCH_TIMEOUT" "$PREPUSH_TIMEOUT" "$PRECUT_TIMEOUT" \
               "$BAND_TIMEOUT" "$BROWSER_TIMEOUT" "$BAND_WORKERS"; do
  if ! positive_integer "$setting"; then
    record_unknown "timeouts and BAND_WORKERS must be positive integers"
    finish
  fi
done

if ! mkdir -p -- "$ART"; then
  record_unknown "cannot create artifact directory $ART"
  finish
fi
LOCK_DIR=$ART/$TAG.lock
if ! mkdir -- "$LOCK_DIR"; then
  record_unknown "verification tag is already active or its lock cannot be acquired: $TAG"
  finish
fi
LOCK_HELD=1

# THE TAG LOCK IS NOT ENOUGH. On 2026-08-31 four bd-verify-cut runs contended
# over ONE candidate worktree under four different tags: each took its own tag
# lock and none could see the others. They interleaved regeneration and pytest
# in the same tree for eleven minutes. The lock has to be keyed on the SUBJECT,
# which is the worktree, not on the artifact namespace.
#
# The holder record carries the PID and that PID's /proc start time. A lock is
# reclaimed ONLY when the PID is gone, or is present with a different start time
# (PID reuse). A live holder is a refusal, not a wait -- UNKNOWN naming it.
WT_REAL=$(readlink -f -- "$WORK_INPUT" 2>/dev/null || printf '%s' "$WORK_INPUT")
WT_KEY=$(printf '%s' "$WT_REAL" | sha256sum | cut -d' ' -f1)
WT_LOCK=$ART/.worktree-$WT_KEY.lock
holder_start() { awk '{print $22}' "/proc/$1/stat" 2>/dev/null; }
if ! mkdir -- "$WT_LOCK" 2>/dev/null; then
  H_PID=$(sed -n 's/^pid=//p' "$WT_LOCK/holder" 2>/dev/null | head -1)
  H_START=$(sed -n 's/^start=//p' "$WT_LOCK/holder" 2>/dev/null | head -1)
  H_TAG=$(sed -n 's/^tag=//p' "$WT_LOCK/holder" 2>/dev/null | head -1)
  NOW_START=$(holder_start "${H_PID:-0}")
  if [ -n "$H_PID" ] && [ -n "$NOW_START" ] && [ "$NOW_START" = "$H_START" ]; then
    record_unknown "another bd-verify-cut is ALREADY RUNNING against this worktree: pid $H_PID, tag ${H_TAG:-unknown}, $WT_REAL"
    finish
  fi
  # Dead holder, or an unreadable record: reclaim, saying so.
  echo "   reclaimed a stale worktree lock (holder pid ${H_PID:-unknown} is gone)"
  rm -f -- "$WT_LOCK/holder" >/dev/null 2>&1 || true
  if ! rmdir -- "$WT_LOCK" 2>/dev/null || ! mkdir -- "$WT_LOCK" 2>/dev/null; then
    record_unknown "worktree lock $WT_LOCK cannot be acquired or reclaimed"
    finish
  fi
fi
WT_LOCK_HELD=1
printf 'pid=%s\nstart=%s\ntag=%s\nworktree=%s\nutc=%s\n' \
  "$$" "$(holder_start $$)" "$TAG" "$WT_REAL" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$WT_LOCK/holder"

# A reused tag must never expose a prior attempt's denominator or gate tail as
# evidence for this attempt.  Every target is an exact, tag-qualified artifact.
#
# attribute.log JOINED THIS LIST on 2026-09-01, found by an audit. It was the
# ONE per-tag verdict artifact missing from it, and every write to it is `>>`.
# So a reused tag read a PREVIOUS attempt's replay: a node this cut broke could
# be excused as inherited, and the denominator guard -- which compares the
# replay's collected count against the failing set -- would be satisfied by the
# earlier attempt's numbers. That is the merge gate laundering its own history.
for stale_suffix in source.err fetch.log fetch-final.log merge-base.err worktree.log \
                    prepush.log band-derive.json band-derive.err band-audit.log \
                    denominator.json baseline.err precut.log band.log browser.log \
                    attribute.log publish.err publish.out prbody.md precut.rc; do
  stale_path=$ART/$TAG-$stale_suffix
  if ! rm -f -- "$stale_path" || [ -e "$stale_path" ] || [ -L "$stale_path" ]; then
    record_unknown "cannot clear stale artifact $stale_path"
    finish
  fi
done

if ! SOURCE=$(git -C "$WORK_INPUT" rev-parse --show-toplevel 2>"$ART/$TAG-source.err"); then
  record_unknown "candidate worktree is not a readable Git worktree: $WORK_INPUT"
  finish
fi

if ! CANDIDATE_SHA=$(git -C "$SOURCE" rev-parse --verify 'HEAD^{commit}' 2>/dev/null) \
    || ! CANDIDATE_TREE=$(git -C "$SOURCE" rev-parse --verify 'HEAD^{tree}' 2>/dev/null); then
  record_unknown "candidate HEAD or tree cannot be resolved"
  finish
fi

prove_source() {
  local phase=$1 got_sha got_tree tracked
  if ! got_sha=$(git -C "$SOURCE" rev-parse --verify 'HEAD^{commit}' 2>/dev/null) \
      || ! got_tree=$(git -C "$SOURCE" rev-parse --verify 'HEAD^{tree}' 2>/dev/null) \
      || ! tracked=$(git -C "$SOURCE" status --porcelain=v1 --untracked-files=no 2>/dev/null); then
    record_unknown "candidate source could not be proved during $phase"
    return 1
  fi
  if [ "$got_sha" != "$CANDIDATE_SHA" ] || [ "$got_tree" != "$CANDIDATE_TREE" ]; then
    record_unknown "candidate source HEAD/tree moved during $phase (sha=$got_sha tree=$got_tree)"
    return 1
  fi
  if [ -n "$tracked" ]; then
    record_unknown "candidate source tracked tree is dirty during $phase"
    printf '%s\n' "$tracked" | sed -n '1,8p'
    return 1
  fi
  return 0
}

echo "== verify $TAG =="
echo "   host $(hostname)"
echo "CANDIDATE_SHA=$CANDIDATE_SHA"
echo "CANDIDATE_TREE=$CANDIDATE_TREE"
date -u +'   start %Y-%m-%dT%H:%M:%SZ'

if ! prove_source freeze; then
  finish
fi

if ! TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/bd-verify-cut.${TAG}.XXXXXX"); then
  record_unknown "cannot create disposable verification directory"
  finish
fi

fetch_origin_main() {
  local phase=$1 log=$2 rc resolved
  timeout "$FETCH_TIMEOUT" git -C "$SOURCE" fetch --quiet --no-tags origin \
    '+refs/heads/main:refs/remotes/origin/main' >"$log" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    record_unknown "fresh origin/main fetch failed during $phase (rc=$rc)"
    return 1
  fi
  if ! resolved=$(git -C "$SOURCE" rev-parse --verify 'refs/remotes/origin/main^{commit}' 2>/dev/null); then
    record_unknown "fresh origin/main cannot be resolved during $phase"
    return 1
  fi
  FETCHED_ORIGIN_MAIN=$resolved
  return 0
}

FETCHED_ORIGIN_MAIN=
if ! fetch_origin_main freeze "$ART/$TAG-fetch.log"; then
  finish
fi
ORIGIN_MAIN_SHA=$FETCHED_ORIGIN_MAIN
echo "ORIGIN_MAIN_SHA=$ORIGIN_MAIN_SHA"

if ! prove_source after-origin-fetch; then
  finish
fi

BASES_FILE=$TMP_ROOT/merge-bases.txt
if ! git -C "$SOURCE" merge-base --all "$ORIGIN_MAIN_SHA" "$CANDIDATE_SHA" >"$BASES_FILE" 2>"$ART/$TAG-merge-base.err"; then
  record_unknown "merge-base(origin/main,candidate) could not be derived"
  finish
fi
mapfile -t MERGE_BASES < "$BASES_FILE"
if [ "${#MERGE_BASES[@]}" -ne 1 ] || [ -z "${MERGE_BASES[0]:-}" ]; then
  record_unknown "merge-base(origin/main,candidate) is absent or ambiguous (${#MERGE_BASES[@]} result(s))"
  finish
fi
BASE_SHA=${MERGE_BASES[0]}
if ! BASE_TREE=$(git -C "$SOURCE" rev-parse --verify "$BASE_SHA^{tree}" 2>/dev/null) \
    || ! git -C "$SOURCE" merge-base --is-ancestor "$BASE_SHA" "$ORIGIN_MAIN_SHA" \
    || ! git -C "$SOURCE" merge-base --is-ancestor "$BASE_SHA" "$CANDIDATE_SHA"; then
  record_unknown "derived merge base is not a provable ancestor of both frozen tips"
  finish
fi
echo "BASE_SHA=$BASE_SHA"
echo "BASE_TREE=$BASE_TREE"
echo "DIFF_RANGE=$BASE_SHA..$CANDIDATE_SHA"

CHANGED_FILE=$TMP_ROOT/changed.z
if ! git -C "$SOURCE" diff --no-ext-diff --no-renames --name-only -z \
    "$BASE_SHA..$CANDIDATE_SHA" >"$CHANGED_FILE"; then
  record_unknown "full-cut changed-path denominator could not be read"
  finish
fi
mapfile -d '' -t CHANGED < "$CHANGED_FILE"
if ! rm -f -- "$CHANGED_FILE" || [ -e "$CHANGED_FILE" ] || [ -L "$CHANGED_FILE" ]; then
  record_unknown "cannot clear pre-candidate changed-path scratch"
  finish
fi
CHANGED_FILE=
CH_N=${#CHANGED[@]}
echo "CHANGED_FILES=$CH_N"
if [ "$CH_N" -eq 0 ]; then
  record_unknown "full-cut denominator is empty"
  finish
fi
for changed_path in "${CHANGED[@]}"; do
  case "$changed_path" in
    -*) record_unknown "changed path cannot be passed losslessly to bd-band-derive: $changed_path"; finish ;;
  esac
done

CHECKOUT=$TMP_ROOT/exact-candidate
if ! git -C "$SOURCE" worktree add --quiet --detach "$CHECKOUT" "$CANDIDATE_SHA" \
    >"$ART/$TAG-worktree.log" 2>&1; then
  record_unknown "disposable exact-SHA worktree could not be created"
  finish
fi

# Only dependency scaffolding is shared.  Never manufacture frontend/dist: its
# absence is itself the subject of several gates.
if [ ! -e "$CHECKOUT/venv" ] && [ ! -L "$CHECKOUT/venv" ] \
    && { [ -e "$SOURCE/venv" ] || [ -L "$SOURCE/venv" ]; }; then
  ln -s -- "$SOURCE/venv" "$CHECKOUT/venv"
fi
if { [ -e "$SOURCE/frontend/node_modules" ] || [ -L "$SOURCE/frontend/node_modules" ]; } \
    && [ ! -e "$CHECKOUT/frontend/node_modules" ] && [ ! -L "$CHECKOUT/frontend/node_modules" ]; then
  mkdir -p -- "$CHECKOUT/frontend"
  ln -s -- "$SOURCE/frontend/node_modules" "$CHECKOUT/frontend/node_modules"
fi

PYTHON=${BD_VERIFY_CUT_PYTHON:-$CHECKOUT/venv/bin/python}
resolve_executable() {
  case "$1" in
    */*) [ -x "$1" ] && printf '%s\n' "$1" ;;
    *) command -v "$1" 2>/dev/null ;;
  esac
}
if ! PYTHON=$(resolve_executable "$PYTHON"); then
  record_unknown "candidate Python is unavailable: ${BD_VERIFY_CUT_PYTHON:-$CHECKOUT/venv/bin/python}"
  finish
fi
if ! AUDIT_PYTHON=$(resolve_executable "$AUDIT_PYTHON"); then
  record_unknown "trusted audit Python is unavailable"
  finish
fi

baseline_sha256() {
  "$AUDIT_PYTHON" - "$1" <<'PY'
import hashlib
import os
import stat
import sys
import zipfile

path = sys.argv[1]
try:
    mode = os.stat(path).st_mode
    if not stat.S_ISREG(mode):
        raise ValueError("not a regular file")
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    with zipfile.ZipFile(path) as archive:
        archive.infolist()
except Exception as exc:
    print(f"baseline validation failed: {exc}", file=sys.stderr)
    raise SystemExit(2)
print(digest.hexdigest())
PY
}

if [ -n "$BASELINE_INPUT" ]; then
  if ! BASELINE_PATH=$(realpath -e -- "$BASELINE_INPUT" 2>/dev/null); then
    record_unknown "baseline cannot be resolved: $BASELINE_INPUT"
    finish
  fi
  if ! BASELINE_SHA256=$(baseline_sha256 "$BASELINE_PATH" 2>"$ART/$TAG-baseline.err"); then
    record_unknown "baseline is not a readable regular ZIP: $BASELINE_PATH"
    sed -n '1,8p' "$ART/$TAG-baseline.err" 2>/dev/null || true
    finish
  fi
fi
echo "BASELINE_PATH=$BASELINE_PATH"
echo "BASELINE_SHA256=$BASELINE_SHA256"

prove_checkout() {
  local phase=$1 got_sha got_tree tracked
  if ! got_sha=$(git -C "$CHECKOUT" rev-parse --verify 'HEAD^{commit}' 2>/dev/null) \
      || ! got_tree=$(git -C "$CHECKOUT" rev-parse --verify 'HEAD^{tree}' 2>/dev/null) \
      || ! tracked=$(git -C "$CHECKOUT" status --porcelain=v1 --untracked-files=no 2>/dev/null); then
    record_unknown "isolated checkout could not be proved during $phase"
    return 1
  fi
  if [ "$got_sha" != "$CANDIDATE_SHA" ] || [ "$got_tree" != "$CANDIDATE_TREE" ] || [ -n "$tracked" ]; then
    record_unknown "isolated checkout changed during $phase (sha=$got_sha tree=$got_tree)"
    [ -n "$tracked" ] && printf '%s\n' "$tracked" | sed -n '1,8p'
    return 1
  fi
  return 0
}

if ! prove_checkout initialization; then
  finish
fi
echo "ISOLATED_SHA=$CANDIDATE_SHA"
echo "ISOLATED_TREE=$CANDIDATE_TREE"
echo "TRACKED_TREE_CLEAN=1"
echo "GATE_SEQUENCE=preflight-then-prepush-then-derive-then-precut-then-band"

cd "$CHECKOUT" || {
  record_unknown "cannot enter disposable exact-SHA worktree"
  finish
}

# THE 36-SECOND GATE THAT RUNS BEFORE THE 12-MINUTE ONES.
# Ruled 2026-08-31: "a 14-minute run must never start against a tree a 21-second
# check rejects". Both of that day's expensive re-runs were an added file
# breaking a pinned denominator elsewhere, and every gate that can see that
# shape is cheap. A RED here ABORTS -- a deliberate exception to A5's
# complete-failure-information rule, because these gates judge the TREE and
# their verdict says nothing about the band's.
if [ -x "$PREFLIGHT" ]; then
  timeout 900 bash "$PREFLIGHT" "$CHECKOUT" 2>&1 | sed 's/^/   /'
  PREFLIGHT_RC=${PIPESTATUS[0]}
  echo "PREFLIGHT_RC=$PREFLIGHT_RC"
  case "$PREFLIGHT_RC" in
    0) ;;
    1) record_red "cheap denominator preflight failed -- expensive gates not started"; finish ;;
    *) record_unknown "denominator preflight could not be measured (rc=$PREFLIGHT_RC)"; finish ;;
  esac
else
  record_unknown "denominator preflight is not executable at $PREFLIGHT"
  finish
fi

# PUBLISH THE CANDIDATE SO CI RUNS INSIDE THIS WINDOW.
#
# CI only starts on a pull request. On 2026-08-31 a candidate sat pushed with no
# CI for four minutes because opening the PR was a separate thing to remember,
# and the ruled workflow -- "push before verifying so CI runs inside the local
# verify window" -- depends on nobody forgetting. So the verifier does it, right
# after the 47-second preflight agrees the tree is worth spending a CI run on
# and before the twelve expensive minutes begin.
#
# It never forces. A remote branch that has diverged is REPORTED and the local
# verify continues; overwriting someone else's ref is not this tool's authority
# (A4). Set BD_VERIFY_CUT_NO_PUBLISH=1 to keep the run entirely local.
publish_candidate() {
  [ "${BD_VERIFY_CUT_NO_PUBLISH:-0}" = "1" ] && { echo "PUBLISH skipped (BD_VERIFY_CUT_NO_PUBLISH=1)"; return 0; }
  command -v gh >/dev/null 2>&1 || { echo "PUBLISH skipped: gh is not on PATH"; return 0; }
  local branch head
  branch=$(git -C "$WORK_INPUT" rev-parse --abbrev-ref HEAD 2>/dev/null)
  if [ -z "$branch" ] || [ "$branch" = HEAD ]; then
    echo "PUBLISH skipped: the candidate worktree is detached, so there is no branch to push"
    return 0
  fi
  head=$(git -C "$WORK_INPUT" rev-parse HEAD 2>/dev/null)
  if [ "$head" != "$CANDIDATE_SHA" ]; then
    echo "PUBLISH skipped: $branch is at ${head:0:12}, not the candidate ${CANDIDATE_SHA:0:12}"
    return 0
  fi
  if ! git -C "$WORK_INPUT" push -q origin "$branch" 2>"$ART/$TAG-publish.err"; then
    echo "PUBLISH FAILED for $branch -- CI will not see this candidate; local verify continues"
    sed 's/^/    /' "$ART/$TAG-publish.err" | head -4
    return 0
  fi
  echo "PUBLISHED $branch at ${CANDIDATE_SHA:0:12}"
  local existing
  existing=$(gh pr list --head "$branch" --state open --json number --jq '.[0].number' 2>/dev/null)
  if [ -n "$existing" ] && [ "$existing" != "null" ]; then
    echo "   PR #$existing already open; CI re-runs on the new head"
    return 0
  fi
  local title body
  title=$(git -C "$WORK_INPUT" log -1 --pretty=%s "$CANDIDATE_SHA")
  body=$ART/$TAG-prbody.md
  {
    git -C "$WORK_INPUT" log --reverse --pretty='### %s%n%n%b' "$BASE_SHA..$CANDIDATE_SHA"
    printf '\n---\nOpened by bd-verify-cut at %s from %s.\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(hostname)"
  } > "$body"
  if gh pr create --base main --head "$branch" --title "$title" --body-file "$body" >"$ART/$TAG-publish.out" 2>&1; then
    echo "   opened $(tail -1 "$ART/$TAG-publish.out")"
  else
    echo "   PR could not be opened; CI may not run:"
    sed 's/^/    /' "$ART/$TAG-publish.out" | head -4
  fi
}
publish_candidate

# Regeneration is the only intentionally mutable reader.  Run it first, alone,
# then refuse the candidate if it left even one tracked byte different.
# PASS-ONLY VERDICT CACHE (ruling 41). A rerun against an UNCHANGED tree is
# the resume case: prepush and precut print REUSED and name the run they came
# from, and the gate that actually failed runs again. Nothing but PASS is ever
# stored, so a hit can only skip work that already passed on this exact tree.
cache_key() { [ -x "$VCACHE" ] && "$VCACHE" key "$@" 2>/dev/null; }
PREPUSH_KEY=$(cache_key prepush "$CANDIDATE_TREE" "$BASE_SHA" "$PREPUSH" "$PREFLIGHT")
PREPUSH_REUSED=0
if [ -n "$PREPUSH_KEY" ] && PREPUSH_HIT=$("$VCACHE" get "$PREPUSH_KEY" --expect-tree "$CANDIDATE_TREE" 2>/dev/null); then
  PREPUSH_RC=0
  PREPUSH_REUSED=1
  echo "REUSED prepush=PASS from $(printf '%s' "$PREPUSH_HIT" | sed -n 's/^tag=//p') at $(printf '%s' "$PREPUSH_HIT" | sed -n 's/^utc=//p') (same tree ${CANDIDATE_TREE:0:12})" \
    | tee "$ART/$TAG-prepush.log"
else
  # PREPUSH OFF THE INTEGRATOR. It was the last expensive gate pinned here, and
  # with precut it capped concurrency at three cuts while six band hosts sat at
  # load 0.00. It is the same shape as the band -- read a frozen checkout at an
  # exact SHA, run something, return an exit code -- so it goes through the same
  # mirror/slot/worktree mechanism rather than a second implementation of it.
  #
  # THE REMOTE RUN IS THE SAME EXPERIMENT: a fresh worktree at the candidate
  # SHA, the repository's own venv, the pinned Gitleaks binary, and the shipped
  # script written OUTSIDE the worktree so it cannot be mistaken for the cut's
  # own untracked drift (it was, on the first attempt).
  PREPUSH_RAN_REMOTE=0
  if [ -f "$BAND_REMOTE" ]; then
    BD_REMOTE_MODE=prepush timeout "$PREPUSH_TIMEOUT" bash "$BAND_REMOTE" "$CANDIDATE_SHA" \
      >"$ART/$TAG-prepush.log" 2>&1
    PREPUSH_RC=$?
    if [ "$PREPUSH_RC" -eq 64 ]; then
      echo "   prepush: no capacity host free -- running on the integrator"
      PREPUSH_RC=98
    else
      PREPUSH_RAN_REMOTE=1
      echo "   prepush: ran REMOTE at ${CANDIDATE_SHA:0:12}"
    fi
  fi
  if [ "$PREPUSH_RAN_REMOTE" -eq 0 ]; then
    timeout "$PREPUSH_TIMEOUT" bash "$PREPUSH" "$CHECKOUT" \
      >"$ART/$TAG-prepush.log" 2>&1
    PREPUSH_RC=$?
  fi
fi
echo "PREPUSH_RC=$PREPUSH_RC"
grep -E 'FAIL|PRE-PUSH|UNKNOWN' "$ART/$TAG-prepush.log" | head -12 || true
case "$PREPUSH_RC" in
  0) ;;
  1) record_red "prepush gate failed" ;;
  *) record_unknown "prepush did not produce a test verdict (rc=$PREPUSH_RC)" ;;
esac
SUBJECT_VALID=1
if ! prove_checkout prepush-regeneration; then
  SUBJECT_VALID=0
fi
# Store only after the checkout is re-proved: a PASS about a tree that moved
# under the gate is not a PASS about this tree.
if [ "$PREPUSH_RC" -eq 0 ] && [ "$SUBJECT_VALID" -eq 1 ] && [ "$PREPUSH_REUSED" -eq 0 ] && [ -n "$PREPUSH_KEY" ]; then
  "$VCACHE" put "$PREPUSH_KEY" PASS "$TAG" "$ART/$TAG-prepush.log" "$CANDIDATE_TREE" "$BASE_SHA" prepush 2>/dev/null || true
fi

# The trusted auditor recomputes the denominator after candidate-controlled
# derivation. Its only stdout is NUL-delimited band data consumed in-memory by
# the parent shell; candidate code never gets a mutable file handoff to alter.
AUDIT_CODE=$(cat <<'PY'
import fnmatch
import json
import os
import pathlib
import subprocess
import sys

(derive_json, evidence_file, work, base_sha, base_tree, candidate_sha,
 candidate_tree, origin_main_sha, baseline_path, baseline_sha256) = sys.argv[1:]

def refuse(message):
    print(f"DENOMINATOR UNKNOWN: {message}", file=sys.stderr)
    raise SystemExit(2)

try:
    raw_payload = sys.stdin.buffer.read()
    payload = json.loads(raw_payload.decode("utf-8"))
except Exception as exc:
    refuse(f"deriver JSON is unreadable: {exc}")

changed_result = subprocess.run(
    ["git", "-C", work, "diff", "--no-ext-diff", "--no-renames", "--name-only", "-z",
     f"{base_sha}..{candidate_sha}"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
)
if changed_result.returncode != 0:
    refuse(f"trusted full-cut changed-path derivation failed (rc={changed_result.returncode})")
raw_changed = changed_result.stdout.split(b"\0")
if raw_changed and raw_changed[-1] == b"":
    raw_changed.pop()
changed = [os.fsdecode(item) for item in raw_changed]
if not isinstance(payload, dict) or payload.get("changed") != changed:
    refuse("deriver did not account for the exact changed set")

band = payload.get("band")
if not isinstance(band, list) or not band or not all(isinstance(p, str) for p in band):
    refuse("deriver returned no executable band")
if len(band) != len(set(band)):
    refuse("deriver returned duplicate band paths")

for rel in band:
    pure = pathlib.PurePosixPath(rel)
    if pure.is_absolute() or ".." in pure.parts:
        refuse(f"unsafe band path: {rel!r}")
    if not rel.startswith("tests/") or not fnmatch.fnmatch(pure.name, "test_*.py"):
        refuse(f"non-suite band path: {rel!r}")
    if not pathlib.Path(work, rel).is_file():
        refuse(f"band path is absent from candidate: {rel!r}")
    tracked = subprocess.run(
        ["git", "-C", work, "ls-files", "--error-unmatch", "--", rel],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    if tracked.returncode != 0:
        refuse(f"band path is not tracked by candidate: {rel!r}")

changed_tests = [
    rel for rel in changed
    if rel.startswith("tests/") and fnmatch.fnmatch(pathlib.PurePosixPath(rel).name, "test_*.py")
]
present_changed_tests = [rel for rel in changed_tests if pathlib.Path(work, rel).is_file()]
deleted_changed_tests = [rel for rel in changed_tests if rel not in present_changed_tests]
missing = [rel for rel in present_changed_tests if rel not in band]
if missing:
    refuse("band omits changed test path(s): " + ", ".join(missing))

changed_test_support = [rel for rel in changed if rel.startswith("tests/") and rel not in changed_tests]
changed_non_tests = [rel for rel in changed if not rel.startswith("tests/")]
accounting = {}
for rel in changed:
    if rel in present_changed_tests:
        accounting[rel] = "executed-in-band"
    elif rel in deleted_changed_tests:
        accounting[rel] = "deleted-test-explicitly-recorded"
    else:
        accounting[rel] = "exact-deriver-input"
if list(accounting) != changed:
    refuse("not every changed path received an accounting disposition")

evidence = {
    "schema": 1,
    "origin_main_sha": origin_main_sha,
    "base_sha": base_sha,
    "base_tree": base_tree,
    "candidate_sha": candidate_sha,
    "candidate_tree": candidate_tree,
    "diff_range": f"{base_sha}..{candidate_sha}",
    "changed": changed,
    "changed_tests": changed_tests,
    "present_changed_tests": present_changed_tests,
    "deleted_changed_tests": deleted_changed_tests,
    "changed_test_support": changed_test_support,
    "changed_production_or_support": changed_non_tests,
    "accounting": accounting,
    "band": band,
    "baseline_path": None if baseline_path == "UNSET" else baseline_path,
    "baseline_sha256": None if baseline_sha256 == "UNSET" else baseline_sha256,
}
target = pathlib.Path(evidence_file)
temporary = target.with_name(target.name + f".tmp.{os.getpid()}")
temporary.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, target)
derive_target = pathlib.Path(derive_json)
derive_temporary = derive_target.with_name(derive_target.name + f".tmp.{os.getpid()}")
derive_temporary.write_bytes(raw_payload)
os.replace(derive_temporary, derive_target)
print(f"ACCOUNTED_CHANGED_FILES={len(accounting)}", file=sys.stderr)
print(f"CHANGED_TEST_FILES={len(changed_tests)}", file=sys.stderr)
print(f"AUDITED_BAND_FILES={len(band)}", file=sys.stderr)
sys.stdout.buffer.write(b"".join(os.fsencode(rel) + b"\0" for rel in band))
PY
)

BAND_READY=0
if [ "$SUBJECT_VALID" -eq 1 ]; then
  BAND=()
  had_lastpipe=0
  shopt -q lastpipe && had_lastpipe=1
  shopt -s lastpipe
  "$PYTHON" toolchain/bin/bd-band-derive --work "$CHECKOUT" \
    --files "${CHANGED[@]}" --json 2>"$ART/$TAG-band-derive.err" | \
    "$AUDIT_PYTHON" -c "$AUDIT_CODE" "$ART/$TAG-band-derive.json" "$ART/$TAG-denominator.json" \
      "$CHECKOUT" "$BASE_SHA" "$BASE_TREE" "$CANDIDATE_SHA" "$CANDIDATE_TREE" \
      "$ORIGIN_MAIN_SHA" "$BASELINE_PATH" "$BASELINE_SHA256" 2>"$ART/$TAG-band-audit.log" | \
    while IFS= read -r -d '' suite; do
      BAND+=("$suite")
    done
  BAND_PIPE_RCS=("${PIPESTATUS[@]}")
  [ "$had_lastpipe" -eq 1 ] || shopt -u lastpipe
  BAND_DERIVE_RC=${BAND_PIPE_RCS[0]:-98}
  BAND_AUDIT_RC=${BAND_PIPE_RCS[1]:-98}
  echo "BAND_DERIVE_RC=$BAND_DERIVE_RC"
  if [ "$BAND_DERIVE_RC" -ne 0 ]; then
    BAND_AUDIT_RC=98
    sed -n '1,16p' "$ART/$TAG-band-derive.err" 2>/dev/null || true
    record_unknown "band derivation did not complete (rc=$BAND_DERIVE_RC)"
  else
    cat "$ART/$TAG-band-audit.log"
    if [ "$BAND_AUDIT_RC" -ne 0 ]; then
      record_unknown "changed-path/band accounting audit failed"
    elif [ "${#BAND[@]}" -eq 0 ]; then
      record_unknown "changed-path/band accounting audit emitted no executable band"
    else
      BAND_N=${#BAND[@]}
      BAND_READY=1
    fi
  fi
  if ! prove_checkout band-derivation; then
    SUBJECT_VALID=0
    BAND_READY=0
  fi
else
  echo "BAND_DERIVE_RC=$BAND_DERIVE_RC"
fi
echo "BAND_FILES=$BAND_N"

# Precut is a reader and therefore follows the mutation/re-proof boundary.
if [ "$SUBJECT_VALID" -eq 1 ]; then
  PRECUT_READY=1
  PRECUT_BASELINE_ARGS=()
  if [ "$BASELINE_PATH" != UNSET ]; then
    CURRENT_BASELINE_SHA256=
    if ! CURRENT_BASELINE_SHA256=$(baseline_sha256 "$BASELINE_PATH" 2>>"$ART/$TAG-precut.log") \
        || [ "$CURRENT_BASELINE_SHA256" != "$BASELINE_SHA256" ]; then
      PRECUT_READY=0
      SUBJECT_VALID=0
      BAND_READY=0
      record_unknown "baseline changed or is no longer a readable regular ZIP before precut"
    else
      PRECUT_BASELINE_ARGS=(--baseline "$BASELINE_PATH")
    fi
  fi
  PRECUT_KEY=
  PRECUT_REUSED=0
  PRECUT_PID=
  if [ "$PRECUT_READY" -eq 1 ]; then
    # bd-precut reads the whole tree and its own derived baseline, so the tree
    # SHA plus the baseline digest is the complete experiment identity.
    PRECUT_KEY=$(cache_key precut "$CANDIDATE_TREE" "$BASE_SHA" "$BASELINE_SHA256" toolchain/bin/bd-precut)
    if [ -n "$PRECUT_KEY" ] && PRECUT_HIT=$("$VCACHE" get "$PRECUT_KEY" --expect-tree "$CANDIDATE_TREE" 2>/dev/null); then
      PRECUT_RC=0
      PRECUT_REUSED=1
      echo "REUSED precut=PASS from $(printf '%s' "$PRECUT_HIT" | sed -n 's/^tag=//p') at $(printf '%s' "$PRECUT_HIT" | sed -n 's/^utc=//p') (same tree ${CANDIDATE_TREE:0:12})" \
        | tee "$ART/$TAG-precut.log"
      echo "PRECUT_RC=$PRECUT_RC"
    else
      # PRECUT AND THE BAND ARE TWO READERS OF ONE FROZEN CHECKOUT, and running
      # them one after the other cost 5 minutes of every 14-minute verify for no
      # reason. Neither writes to the tree -- prepush is the only mutating step
      # and it has already finished and been re-proved -- so precut runs in the
      # background while the band runs, and its verdict is collected afterwards.
      # Both verdicts are still recorded IN FULL: A5 forbids cancelling one lane
      # because another failed, and nothing here cancels anything.
      rm -f -- "$ART/$TAG-precut.rc"
      (
        env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 timeout "$PRECUT_TIMEOUT" \
          "$PYTHON" toolchain/bin/bd-precut --gate "${PRECUT_BASELINE_ARGS[@]}" \
          >"$ART/$TAG-precut.log" 2>&1
        echo $? > "$ART/$TAG-precut.rc"
      ) &
      PRECUT_PID=$!
      echo "PRECUT_LAUNCHED pid=$PRECUT_PID (concurrent with the affected band)"
    fi
  fi
else
  echo "PRECUT_RC=$PRECUT_RC"
fi

# Collect precut's verdict. Called AFTER the band so the two overlap; it is
# safe to call when precut was never launched or was served from the cache.
collect_precut() {
  if [ -n "${PRECUT_PID:-}" ]; then
    wait "$PRECUT_PID" 2>/dev/null || true
    if [ -f "$ART/$TAG-precut.rc" ]; then
      PRECUT_RC=$(cat "$ART/$TAG-precut.rc")
    else
      # A missing rc file is an unobserved measurement, never a pass.
      PRECUT_RC=97
      record_unknown "precut left no exit status; its verdict is UNOBSERVED"
    fi
    echo "PRECUT_RC=$PRECUT_RC"
    tail -n 8 "$ART/$TAG-precut.log" 2>/dev/null || true
  fi
  [ "${PRECUT_READY:-0}" -eq 1 ] || return 0
  case "$PRECUT_RC" in
    0) ;;
    1|3) record_red "precut gate failed (rc=$PRECUT_RC)" ;;
    *) record_unknown "precut did not produce a test verdict (rc=$PRECUT_RC)" ;;
  esac
  if [ "$PRECUT_RC" -eq 0 ] && grep -Eq '^RESULT:.*UNKNOWN' "$ART/$TAG-precut.log"; then
    record_unknown "precut reported one or more checks UNKNOWN"
  fi
  if ! prove_checkout precut; then
    SUBJECT_VALID=0
  fi
  if [ "$PRECUT_RC" -eq 0 ] && [ "$SUBJECT_VALID" -eq 1 ] && [ "$PRECUT_REUSED" -eq 0 ] \
     && [ -n "$PRECUT_KEY" ] && [ "${#UNKNOWN_REASONS[@]}" -eq 0 ]; then
    "$VCACHE" put "$PRECUT_KEY" PASS "$TAG" "$ART/$TAG-precut.log" "$CANDIDATE_TREE" "$BASE_SHA" precut 2>/dev/null || true
  fi
}

classify_pytest_rc() {
  local label=$1 rc=$2
  case "$rc" in
    0) ;;
    1) record_red "$label failed" ;;
    *) record_unknown "$label did not produce a test verdict (rc=$rc)" ;;
  esac
}

# The selected band is held only in this shell. Re-prove the immutable checkout
# immediately before handing those selectors to either executor.
if [ "$SUBJECT_VALID" -eq 1 ] && [ "$BAND_READY" -eq 1 ] && [ "$BAND_N" -gt 0 ]; then
  if ! prove_checkout affected-band-launch; then
    SUBJECT_VALID=0
    BAND_READY=0
  else
    echo "BAND_REVALIDATED=1"
  fi
fi

if [ "$SUBJECT_VALID" -eq 1 ] && [ "$BAND_READY" -eq 1 ] && [ "$BAND_N" -gt 0 ]; then
  REST=()
  BROWSER=()
  REMOTE_SAFE=1
  for suite in "${BAND[@]}"; do
    case "$suite" in
      tests/test_e2e_smoke.py|tests/test_extension_live.py) BROWSER+=("$suite") ;;
      *)
        REST+=("$suite")
        case "$suite" in *[!A-Za-z0-9_./-]*) REMOTE_SAFE=0;; esac
        ;;
    esac
  done

  REST_RC=0
  if [ "${#REST[@]}" -gt 0 ]; then
    if [ "$REMOTE_SAFE" -eq 1 ] && [ -f "$BAND_REMOTE" ]; then
      timeout "$BAND_TIMEOUT" bash "$BAND_REMOTE" "$CANDIDATE_SHA" "${REST[@]}" \
        >"$ART/$TAG-band.log" 2>&1
      REST_RC=$?
    else
      REST_RC=64
      echo "remote band unavailable or path cannot be shell-transported safely" >"$ART/$TAG-band.log"
    fi
    if [ "$REST_RC" -eq 64 ]; then
      BAND_RAN_REMOTE=0
      echo "   band: no safe capacity host available -- running exact checkout locally" | tee -a "$ART/$TAG-band.log"
      env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 \
        timeout "$BAND_TIMEOUT" "$PYTHON" -m pytest "${REST[@]}" \
          -n "$BAND_WORKERS" --dist loadfile --timeout=240 --timeout-method=signal \
          --max-worker-restart=0 -p no:randomly >>"$ART/$TAG-band.log" 2>&1
      REST_RC=$?
    else
      BAND_RAN_REMOTE=1
      echo "   band: ran REMOTE at ${CANDIDATE_SHA:0:12} (see $ART/$TAG-band.log)"
    fi
    classify_pytest_rc "affected non-browser band" "$REST_RC"
  else
    echo "band contains browser-driving suites only" >"$ART/$TAG-band.log"
  fi

  BROWSER_RC=0
  if [ "${#BROWSER[@]}" -gt 0 ]; then
    echo "   browser-driving files run SERIALLY: ${BROWSER[*]}"
    env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 \
      timeout "$BROWSER_TIMEOUT" "$PYTHON" -m pytest "${BROWSER[@]}" \
        --timeout=240 --timeout-method=signal -p no:randomly \
        >"$ART/$TAG-browser.log" 2>&1
    BROWSER_RC=$?
    echo "BROWSER_RC=$BROWSER_RC"
    tail -n 3 "$ART/$TAG-browser.log" 2>/dev/null || true
    classify_pytest_rc "affected browser band" "$BROWSER_RC"
  fi

  BAND_RC=$REST_RC
  [ "$BROWSER_RC" -ne 0 ] && BAND_RC=$BROWSER_RC
  if ! prove_checkout affected-band; then
    SUBJECT_VALID=0
  fi
else
  BAND_RC=98
  if [ "$BAND_READY" -ne 1 ] && [ "${#UNKNOWN_REASONS[@]}" -eq 0 ]; then
    record_unknown "affected band was not measurable"
  fi
fi
echo "BAND_RC=$BAND_RC"
tail -n 14 "$ART/$TAG-band.log" 2>/dev/null || true

collect_precut

# ---------------------------------------------------------------------------
# PRE-EXISTING FAILURE ATTRIBUTION
#
# A band failure answers "is this band green", not "did this cut break it".
# v3.66.1378's band went RED on exactly one node -- the bdctl --json defect
# already filed as row 459 -- in a cut that does not touch bdctl at all. With
# no way to say that, the verifier reported NOT SHIPPABLE for a defect the
# candidate inherited, and the only way past it was a human deciding to
# ignore the verifier. That is how a verdict stops meaning anything.
#
# So the claim is MEASURED instead: replay exactly the failing node ids on the
# MERGE BASE, in a disposable worktree at that exact SHA. This can only ever
# EXCUSE a failure that also fails on the base. It cannot turn a red node
# green, it cannot excuse a node it did not replay, and it refuses rather than
# guesses when the replay cannot run -- an unmeasurable attribution is UNKNOWN,
# never permission (A2). Every excused node is NAMED in the verdict, so a cut
# can never quietly accumulate inherited failures.
BAND_PREEXISTING=0
BAND_ATTRIBUTED=()
if [ "$BAND_RC" -ne 0 ] && [ "$BAND_RC" -ne 98 ] && [ -f "$ART/$TAG-band.log" ]; then
  mapfile -t FAILED_NODES < <(grep -hE '^(FAILED|ERROR) ' "$ART/$TAG-band.log" \
    "$ART/$TAG-browser.log" 2>/dev/null | sed -E 's/^(FAILED|ERROR) //; s/ - .*$//' \
    | grep '::' | sort -u)
  if [ "${#FAILED_NODES[@]}" -eq 0 ]; then
    # rc says red and the log names no node: the run died some other way
    # (timeout, worker abort, collection error). Attribution is impossible and
    # must not be attempted -- an empty failing set would otherwise "all fail
    # on the base" vacuously and excuse the entire run.
    record_unknown "band rc=$BAND_RC but no failing node id was parsed from the log -- attribution impossible, and a zero-length failing set must never read as attributed"
  elif [ "$BASE_SHA" = "UNKNOWN" ] || [ -z "$BASE_SHA" ]; then
    record_unknown "band is RED and the merge base is UNKNOWN, so no failure can be attributed"
  else
    # THE REPLAY MUST BE THE SAME EXPERIMENT, IN THE SAME PLACE.
    #
    # Twice on 2026-08-31 this told an operator a cut BROKE nodes it does not
    # touch. Both times the replay ran the FAILING NODE IDS, alone, on the
    # integrator -- while the band that produced them ran the whole 291-file
    # selector list on a capacity host. Two differences, each sufficient:
    #   - ISOLATION. Six password-vault-routing failures and one route-scan
    #     failure existed only inside the full band, under 12 workers sharing a
    #     tmpdir root. Replayed alone they pass, so the cut was blamed.
    #   - HOST. tests/test_v3_43_80_modules imports a tray app that needs a
    #     display. It fails on every capacity host and passes on the integrator,
    #     so a local replay "proves" the base clean and blames the cut for the
    #     band host's missing X11.
    # So the replay is now the WHOLE BAND, at the merge base, through the SAME
    # executor the band used. Failure SETS are compared. It costs one extra band
    # run, and only when the band is already RED.
    echo "   attributing ${#FAILED_NODES[@]} failing node(s) by REPLAYING THE WHOLE BAND at ${BASE_SHA:0:12}"
    BASEWT=$TMP_ROOT/exact-base
    if ! git -C "$SOURCE" worktree add --quiet --detach "$BASEWT" "$BASE_SHA" \
         >>"$ART/$TAG-attribute.log" 2>&1; then
      record_unknown "could not create a worktree at the merge base ${BASE_SHA:0:12} -- the band failure is UNATTRIBUTED, so it stands as RED"
    else
      # The base tree needs the same scaffolding the candidate tree has, or
      # every node fails there for the wrong reason and everything is excused.
      ln -sfn "$SOURCE/venv" "$BASEWT/venv" 2>/dev/null || true
      ln -sfn "$SOURCE/frontend/node_modules" "$BASEWT/frontend/node_modules" 2>/dev/null || true
      base_tree_actual=$(git -C "$BASEWT" rev-parse HEAD 2>/dev/null)
      if [ "$base_tree_actual" != "$BASE_SHA" ]; then
        record_unknown "the base worktree is at $base_tree_actual, not the merge base $BASE_SHA"
      else
        # A NEW TEST FILE DOES NOT EXIST AT THE MERGE BASE. The whole-band
        # replay passed the candidate's selector list unchanged, so pytest was
        # handed a path the base tree does not have, collected nothing, and
        # exited 5 -- which this code correctly refuses as UNATTRIBUTED, and
        # which blocked two green cuts on 2026-09-01. Replay only the selectors
        # the base actually carries; a file absent there cannot have failed
        # there, so dropping it cannot excuse anything.
        BASE_REST=()
        for _sel in "${REST[@]:-}"; do
          [ -n "$_sel" ] || continue
          if git -C "$SOURCE" cat-file -e "$BASE_SHA:${_sel%%::*}" 2>/dev/null; then
            BASE_REST+=("$_sel")
          fi
        done
        _dropped=$(( ${#REST[@]} - ${#BASE_REST[@]} ))
        [ "$_dropped" -eq 0 ] || echo "   replay drops $_dropped selector(s) absent from the merge base"
        echo "   replay executor: $( [ "$BAND_RAN_REMOTE" -eq 1 ] && echo REMOTE || echo local ), ${#BASE_REST[@]} selector(s)"
        if [ "$BAND_RAN_REMOTE" -eq 1 ] && [ -f "$BAND_REMOTE" ] && [ "${#BASE_REST[@]}" -gt 0 ]; then
          timeout "$BAND_TIMEOUT" bash "$BAND_REMOTE" "$BASE_SHA" "${BASE_REST[@]}" \
            >>"$ART/$TAG-attribute.log" 2>&1
          base_rc=$?
          if [ "$base_rc" -eq 64 ]; then
            # A replay in a DIFFERENT place is a different experiment, and
            # guessing from one is exactly what caused this.
            record_unknown "the merge-base replay could not reach a capacity host while the band ran on one -- a local replay would be a different experiment, so these failures are UNATTRIBUTED"
            base_rc=98
          fi
        else
          ( cd "$BASEWT" && env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 \
              timeout "$BAND_TIMEOUT" "$BASEWT/venv/bin/python" -m pytest "${BASE_REST[@]:-${FAILED_NODES[@]}}" \
                -n "$BAND_WORKERS" --dist loadfile \
                --timeout=240 --timeout-method=signal --max-worker-restart=0 -p no:randomly \
                >>"$ART/$TAG-attribute.log" 2>&1 )
          base_rc=$?
        fi
        # THE DENOMINATOR MUST RECONCILE. pytest exits 4 for a bad node id and
        # 5 for "no tests ran" -- both would otherwise look like "did not fail
        # on the base", which excuses nothing and reports the opposite.
        base_collected=$(grep -hoE '[0-9]+ (passed|failed)' "$ART/$TAG-attribute.log" \
                         | awk '{s+=$1} END {print s+0}')
        if [ "$base_rc" -ge 4 ] && [ "$base_rc" -ne 1 ]; then
          record_unknown "the merge-base replay exited $base_rc (bad node id, nothing collected, or an internal error) -- the failing nodes are UNATTRIBUTED"
        elif [ "$base_rc" -eq 98 ]; then
          : # already recorded UNKNOWN above
        elif [ "${base_collected:-0}" -lt "${#FAILED_NODES[@]}" ]; then
          record_unknown "the merge-base replay accounted for ${base_collected:-0} of ${#FAILED_NODES[@]} node(s) -- an incomplete replay cannot attribute anything"
        else
          mapfile -t BASE_FAILED < <(grep -hE '^(FAILED|ERROR) ' "$ART/$TAG-attribute.log" \
            | sed -E 's/^(FAILED|ERROR) //; s/ - .*$//' | grep '::' | sort -u)
          new_failures=()
          for node in "${FAILED_NODES[@]}"; do
            hit=0
            for b in "${BASE_FAILED[@]:-}"; do [ "$node" = "$b" ] && hit=1 && break; done
            if [ "$hit" = 1 ]; then BAND_ATTRIBUTED+=("$node"); else new_failures+=("$node"); fi
          done
          if [ "${#new_failures[@]}" -gt 0 ]; then
            record_red "this cut BREAKS ${#new_failures[@]} node(s) that pass on the merge base: ${new_failures[*]}"
          else
            BAND_PREEXISTING=1
            _kept=()
            for _r in "${RED_REASONS[@]:-}"; do
              case "$_r" in
                "affected non-browser band"*|"affected browser band"*) ;;
                "") ;;
                *) _kept+=("$_r");;
              esac
            done
            RED_REASONS=("${_kept[@]:-}")
            RED_REASONS=($(printf '%s\n' "${RED_REASONS[@]:-}" | grep -v '^$' || true))
            echo "   BAND FAILURE IS PRE-EXISTING: all ${#FAILED_NODES[@]} failing node(s) also fail at ${BASE_SHA:0:12}"
            echo "     the band's RED is withdrawn; every other RED and UNKNOWN stands"
            printf '     inherited: %s\n' "${BAND_ATTRIBUTED[@]}"
            echo "     these are DEFECTS ON MAIN, not regressions of this cut; each needs its own register row"
          fi
        fi
      fi
      git -C "$SOURCE" worktree remove --force "$BASEWT" >/dev/null 2>&1 || true
    fi
  fi
fi

prove_checkout final >/dev/null || true
prove_source final >/dev/null || true

FETCHED_ORIGIN_MAIN=
if fetch_origin_main final "$ART/$TAG-fetch-final.log"; then
  if [ "$FETCHED_ORIGIN_MAIN" != "$ORIGIN_MAIN_SHA" ]; then
    record_unknown "origin/main moved during verification ($ORIGIN_MAIN_SHA -> $FETCHED_ORIGIN_MAIN)"
  else
    FINAL_BASES=$TMP_ROOT/final-merge-bases.txt
    if ! git -C "$SOURCE" merge-base --all "$FETCHED_ORIGIN_MAIN" "$CANDIDATE_SHA" >"$FINAL_BASES" 2>/dev/null; then
      record_unknown "final merge-base proof failed"
    else
      mapfile -t FINAL_MERGE_BASES < "$FINAL_BASES"
      if [ "${#FINAL_MERGE_BASES[@]}" -ne 1 ] || [ "${FINAL_MERGE_BASES[0]:-}" != "$BASE_SHA" ]; then
        record_unknown "merge base changed during verification"
      fi
    fi
  fi
fi

finish
