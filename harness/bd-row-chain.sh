#!/bin/bash
# ANCHORED LOOSELY: bd-qa-row.sh may append a REASON after the code, e.g.
# "QA_RC=0 (no test files changed -- doc/backlog only)", which is CORRECT for a
# register/mutant-only cut. "^QA_RC=0$" refused row 287 -- a PASSING QA read as a
# failure, halting the whole lane. Match the code, allow a trailing reason.
# One row, QA -> integrate -> verify -> ship, refusing loudly at each non-green
# step. Written 2026-08-26: bd-queue-run.sh does integrate->verify->ship but
# assumes rows are ALREADY QA-green and waits on a bd-tail session from an
# earlier run. This is the unattended per-row driver.
#   usage: bd-row-chain.sh <row> <version-int> <slug> "<changelog title>"
# Version is a HINT: bd-integrate-row.sh RE-DERIVES it from main and hands the
# real one back via $A/.integrated-<row>. Never trust the hint after this point.
set -u
ROW="$1"; VER="$2"; SLUG="$3"; TITLE="$4"
A=/home/mboyle/fleet-run-artifacts/2026-08-25; I="$A/inflight"; mkdir -p "$I"
L="$I/chain-$ROW.log"
say(){ printf '%s [chain %s] %s\n' "$(date -u +%H:%M:%S)" "$ROW" "$*" | tee -a "$L"; }
die(){ say "REFUSED: $*"; exit 1; }

# bd-qa-row.sh writes to ITS OWN file and prints nothing to stdout. Reading a
# redirect of its stdout gives an EMPTY file, which greps as "not failed" and
# would pass a cut nothing ever QA'd. Read the file the tool actually writes.
# A cut may name SEVERAL rows ("247 248"). bd-integrate-row.sh uses the FIRST to
# name the worktree, the QA log and the report; do the same or every path breaks.
R1="${ROW%% *}"
QAL=/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts/row$R1.qa.log
# EVERY ROW IN A BATCH MUST BE QA-GREEN, NOT JUST THE ONE THAT NAMES THE CUT.
# R1 exists because the first row names the artifacts and the PR body, and the
# QA check below reads only R1's log. When a batch of six shares one version,
# that would certify one row and ship six. Assert all of them here, before any
# integrate work happens.
for _r in $ROW; do
  _q=/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts/row$_r.qa.log
  grep -qE "^QA_RC=0([[:space:]]|$)" "$_q" 2>/dev/null || {
    echo "batch member row $_r is not QA-green (see $_q) -- refusing the whole batch" >&2
    exit 9; }
done
say "=== QA ==="
# bd-qa-row.sh writes QA_RC=<n> as its verdict line.
if ! grep -qE "^QA_RC=0([[:space:]]|$)" "$QAL" 2>/dev/null; then
  bash /home/mboyle/bd-qa-row.sh "$ROW" >/dev/null 2>&1
fi
[ -s "$QAL" ] || die "QA produced NO EVIDENCE at $QAL -- empty is UNKNOWN, not pass"
# NONZERO failures only. "[0-9]+ failed" also matches "0 failed", and a bare
# "ERROR" matches any log line containing the word -- including a test NAMED for
# error handling. Both refuse a green cut.
grep -qE "[1-9][0-9]* failed|[1-9][0-9]* error|^FAILED " "$QAL" && die "QA has failures -- see $QAL"
grep -qE "^QA_RC=0([[:space:]]|$)" "$QAL" || die "QA did not report QA_RC=0 -- see $QAL"
# A NONZERO denominator is required ONLY when the cut HAS tests. bd-qa-row.sh
# writes "QA_RC=0 (no test files changed -- doc/backlog only)" for a register-only
# or mutant-spec-only cut, which is CORRECT: row 287 is two mutant specs and row
# 280 is a MOOT register row. Demanding "N passed" there refused two legitimately
# green cuts and halted the lane. The guard still bites where it matters: a cut
# WITH tests and no passing count means the tests did not run -- UNKNOWN, not pass.
if grep -qE "^QA_RC=0 \(no test files changed" "$QAL"; then
  say "QA ok (no test files in this cut -- denominator legitimately zero)"
else
  grep -qE "[1-9][0-9]* passed" "$QAL" || die "QA log shows no passing denominator -- see $QAL"
  say "QA ok ($(grep -oE "[0-9]+ passed" "$QAL" | tail -1))"
fi

# bd-integrate-row.sh writes its marker into the INFLIGHT dir, not the run root,
# and the marker is TWO lines: version then worktree path. Read both properly --
# `cat` into one variable yields "3.66.1256\n/path" and every later use is wrong.
MARK="$I/.integrated-$R1"
say "=== INTEGRATE (hint v$VER) ==="
if [ -f "$MARK" ] && [ -d "$(sed -n 2p "$MARK")" ]; then
  say "already integrated -- reusing $MARK (delete it to force a re-integrate)"
else
  # ONE WRITER PER WORKTREE (2026-08-29). This step READS the worker worktree
  # and freezes a candidate from it, so an integrator repair landing mid-read
  # produces a candidate that silently lacks the fix -- twice on row 374, for
  # 26 minutes of band time. bd-wt-lane.sh flocks row $R1 for the duration.
  bash /home/mboyle/bd-wt-lane.sh "$R1" \
    bash /home/mboyle/bd-integrate-row.sh "$ROW" "$VER" "$SLUG" "$TITLE" >> "$I/integrate-$ROW.log" 2>&1 \
    || die "integrate failed -- see $I/integrate-$ROW.log"
fi
[ -f "$MARK" ] || die "integrate produced no version marker at $MARK"
REAL=$(sed -n 1p "$MARK"); W=$(sed -n 2p "$MARK")
[ -n "$REAL" ] || die "marker has no version"
[ -n "$W" ] && [ -d "$W" ] || die "marker names no usable worktree: $W"
say "integrated as v$REAL at $W"
say "worktree $W head=$(git -C "$W" rev-parse --short HEAD)"

# THE WORK MUST BE PRESENT. A cut can be green and empty.
N=$(git -C "$W" diff --stat HEAD~1 2>/dev/null | tail -1)
say "cut contents: $N"

# ---- OVERLAP: OPEN THE PR FIRST, SO CI RUNS WHILE THE BAND RUNS -----------
# The band and exact-head CI judge the SAME frozen SHA and are independent, but
# this lane ran them in series: ~11 min band, then ~10 min CI. Pushing first and
# running the band concurrently takes a cut from ~25 min to ~15.
#
# NO GATE IS RELAXED. The merge below still requires BOTH the band's ALL GREEN
# verdict (asserted before the merge phase is ever called) and exact-head CI over
# a nonzero denominator. Both still judge this exact SHA. What changes is only
# that a candidate the band rejects burns a CI run -- and that its PR is then
# CLOSED and its remote branch DELETED, because a green unmerged PR blocks every
# retry of that version.
CSHA=$(git -C "$W" rev-parse --short HEAD) || die "cannot read candidate SHA in $W"
[ -n "$CSHA" ] || die "candidate SHA empty -- UNKNOWN, not a tag"
BR="cut/${REAL##*.}-$SLUG"
git -C "$W" branch -f "$BR" HEAD 2>/dev/null || true
BODY="$I/pr-body-$R1-${REAL##*.}-$CSHA.md"
[ -s "$BODY" ] || printf 'Row %s. %s\n\nIntegrated as v%s (candidate %s).\n' "$ROW" "$TITLE" "$REAL" "$CSHA" > "$BODY"
[ -s "$BODY" ] || die "could not write a PR body at $BODY"
SHIPLOG="$I/${REAL##*.}-$CSHA-ship.log"
# The PUSH phase does NOT take the merge lane lock -- holding it across an
# 11-minute band would serialise the very thing this change parallelises.
say "=== PUSH (CI starts now; band runs concurrently) ==="
BD_SHIP_PHASE=push bash /home/mboyle/bd-ship.sh "$BR" "v$REAL $TITLE" "$BODY" >> "$SHIPLOG" 2>&1 \
  || die "push/PR failed -- see $SHIPLOG"
PUSHED_PR=$(cat "$I/.pr-$(basename "$BR")" 2>/dev/null)
say "PR #${PUSHED_PR:-?} open, CI running"

# A BAND FAILURE MUST TAKE THE PR WITH IT. Without this the branch survives with
# CI eventually green, and the next attempt at this version cannot push.
_abandon(){
  say "band refused -- closing PR #${PUSHED_PR:-?} and deleting $BR so the version can be retried"
  [ -n "${PUSHED_PR:-}" ] && gh pr close "$PUSHED_PR" --delete-branch >>"$SHIPLOG" 2>&1
  git -C "$W" push origin --delete "$BR" >>"$SHIPLOG" 2>&1
  rm -f "$I/.pr-$(basename "$BR")"
}

say "=== VERIFY (concurrent with CI) ==="
# A FRESH TAG EVERY ATTEMPT. Reusing one tag means a retry reads the PREVIOUS
# attempt's stage logs until the new ones are written -- on 2026-08-26 that made
# a fixed cut look like it still failed, on a log whose mtime predated the fix.
# BD_TOOLING_INVENTORY.md already says stale-log reads gave four wrong answers in
# one night; this driver was reproducing that.
CSHA=$(git -C "$W" rev-parse --short HEAD) || die "cannot read candidate SHA in $W"
[ -n "$CSHA" ] || die "candidate SHA empty -- UNKNOWN, not a tag"
ATT=1
while [ -e "$I/${REAL##*.}-$CSHA-a$ATT-driver.log" ] || [ -e "$I/${REAL##*.}-$CSHA-a$ATT-band.log" ] || [ -e "$I/${REAL##*.}-$CSHA-a$ATT-prepush.log" ]; do
  ATT=$((ATT+1))
done
# The VERSION is not unique across cuts: a candidate integrated then parked
# leaves its version free, so the next cut takes the same number and its logs
# collide. Row 243 and row 266 both produced "1266-chain*" logs on 2026-08-26 and
# I read one cut's verdict for the other. Bind the tag to the CANDIDATE SHA.
TAG="${REAL##*.}-$CSHA-a$ATT"
say "verify attempt $ATT, tag $TAG"
BAND_WORKERS=${BAND_WORKERS:-24} bash /home/mboyle/bd-verify-cut.sh "$W" "$TAG" >> "$I/$TAG-driver.log" 2>&1
# VERDICT IS THE AUTHORITY. bd-verify-cut exits 0 even on NOT SHIPPABLE, and
# PRECUT_RC/BROWSER_RC/BAND_RC=98,99 reached NO gate here: 4 runs had precut=3 and
# 2 had BROWSER_RC=1 while this chain said "verify green".
V="$I/$TAG-driver.log"
grep -q "^VERDICT $TAG:" "$V" 2>/dev/null || { _abandon; die "verify wrote no VERDICT for $TAG -- UNKNOWN; see $V"; }
[ "$(grep -xE 'ALL GREEN -- shippable|NOT SHIPPABLE' "$V" | tail -1)" = "ALL GREEN -- shippable" ] || { _abandon; die "verdict NOT ALL GREEN -- $V"; }
for _g in PRECUT_RC PREPUSH_RC BAND_RC; do
  [ "$(grep -oE "^${_g}=[0-9]+" "$V" | tail -1)" = "${_g}=0" ] || { _abandon; die "$_g not green or unmeasured -- $I/$TAG-*.log"; }
done
say "verify green (ALL GREEN -- shippable)"

say "=== MERGE (band green; now wait out exact-head CI) ==="
# Only the MERGE half takes the merge-lane lock: one merge at a time, but many
# bands may run while their PRs sit in CI. The band verdict was asserted above,
# so reaching here means both gates are satisfied for this exact SHA.
BD_SHIP_PHASE=merge bash /home/mboyle/bd-merge-lane.sh bash /home/mboyle/bd-ship.sh "$BR" "v$REAL $TITLE" "$BODY" >> "$SHIPLOG" 2>&1 \
  || { say "merge phase failed -- see $SHIPLOG"; die "ship failed -- see $SHIPLOG"; }
say "MERGED v$REAL"
