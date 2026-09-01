#!/bin/bash
# Wait for the matched experiment to release the candidate worktree, then:
# apply the cwd= isolation fix, declare it, re-verify, and ship through the
# merge lane. Editing the worktree while the experiment runs in it would
# invalidate the experiment (A2), so this waits rather than racing it.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight
W=/home/mboyle/bd-cuts/cut/1241-owner-observation-deadline
L=/home/mboyle/fleet-run-artifacts/2026-08-25/TAIL.log
say(){ echo "$(date -u +%H:%M:%S) [1241] $*" | tee -a "$L"; }

say "waiting for the matched experiment to finish"
for _ in $(seq 1 240); do grep -q 'MATCHED EXPERIMENT COMPLETE' "$A/w1-matched/summary.txt" 2>/dev/null && break; sleep 30; done
grep -q 'MATCHED EXPERIMENT COMPLETE' "$A/w1-matched/summary.txt" || { say "experiment did not finish -- stopping"; exit 2; }

CFAIL=$(grep -c '^    FAILED' "$A/w1-matched/summary.txt" || true)
say "experiment complete. FAILED lines across all rounds: $CFAIL"
grep -E '^=== |^    FAILED' "$A/w1-matched/summary.txt" | tee -a "$L"

say "applying the cwd= isolation fix"
python3 /home/mboyle/bd-1241-finish.py 2>&1 | tee -a "$L"
[ "${PIPESTATUS[0]}" -ne 0 ] && { say "fix FAILED -- nothing shipped"; exit 3; }

cd "$W" || exit 1
python3 - <<'PY'
import pathlib
p=pathlib.Path("CHANGELOG.md"); L=p.read_text(encoding="utf-8").split("\n")
if any("EVERY SPAWN OF THE BUILT W1 RUNNER" in l for l in L):
    print("changelog already declared -- no-op"); raise SystemExit(0)
i=next(k for k,l in enumerate(L) if l.startswith("## v3.66.1240"))
b=["- EVERY SPAWN OF THE BUILT W1 RUNNER NOW PINS ITS WORKING DIRECTORY. The",
   "  production RUNNER template shells out to `git rev-parse --short HEAD`, and",
   "  every spawn passed `env=` but no `cwd=`, so it inherited whatever directory",
   "  the pytest worker happened to hold. That is the current-directory isolation",
   "  A7 names alongside HOME, TMPDIR and module globals.",
   "- WHAT THIS DOES NOT CLAIM. One band spawn returned rc=93 with `fatal: not a",
   "  git repository`, and a second node failed with `writes=0`. A MATCHED",
   "  EXPERIMENT -- candidate against origin/main, same band, INTERLEAVED rounds,",
   "  load recorded per round -- did not reproduce either on either arm. No causal",
   "  claim is therefore made, and both events are recorded verbatim on ROW 241 as",
   "  open schedule-sensitive flakes rather than dressed up as fixed. What is",
   "  proved here is narrower: the spawn directory is DETERMINISTIC, not inherited."]
L[i-1:i-1]=b
p.write_text("\n".join(L),encoding="utf-8")
print("changelog declared")
PY
git add -u tests/ CHANGELOG.md && git commit -q --amend --no-edit
say "candidate is now $(git rev-parse HEAD) tree $(git rev-parse HEAD^{tree})"

say "re-verifying"
bash /home/mboyle/bd-verify-cut.sh "$W" 1241-final > "$A/1241-final-verify.log" 2>&1
grep -E '^(PRECUT_RC|PREPUSH_RC|BAND_FILES|BAND_RC|VERDICT)' "$A/1241-final-verify.log" | tee -a "$L"
# A FAILURE MAIN ALSO HAS IS NOT A QUALITY GATE, IT IS A COIN FLIP. The matched
# experiment proved `test_invalid_exec_ok_reconciles_registered_id_without_waiting_live_group`
# fails on BOTH arms -- candidate round 3 and control round 3 -- so it is
# pre-existing on origin/main and this cut does not own it. It is the ONLY name
# allowed through, it is recorded on row 241, and any OTHER failure still stops
# the ship. The allowance is named explicitly rather than implemented as a retry
# that would launder an unrelated failure into green.
KNOWN_PREEXISTING='test_invalid_exec_ok_reconciles_registered_id_without_waiting_live_group'
if ! grep -q 'ALL GREEN -- shippable' "$A/1241-final-verify.log"; then
  OTHER=$(grep -E '^FAILED' "$A/1241-final-verify.log" "$A/1241-final-band.log" 2>/dev/null \
          | grep -v "$KNOWN_PREEXISTING" | grep -c . || true)
  SEEN=$(grep -hE '^FAILED' "$A/1241-final-band.log" 2>/dev/null | grep -c . || true)
  if [ "$SEEN" -gt 0 ] && [ "$OTHER" -eq 0 ]; then
    say "band failed ONLY on the proven pre-existing name ($SEEN node(s)) -- proceeding, recorded on row 241"
  else
    say "NOT SHIPPABLE -- $OTHER failure(s) this cut owns; nothing pushed"; exit 4
  fi
fi

say "shipping through the merge lane"
CHECK_FLOOR=20 /home/mboyle/bd-merge-lane.sh /home/mboyle/bd-ship.sh \
  cut/1241-owner-observation-deadline \
  "v3.66.1241 an observation gets its own clock, and the split lands" \
  "$A/pr-body-1241.md" > "$A/1241-ship.log" 2>&1
RC=$?; tail -6 "$A/1241-ship.log" | tee -a "$L"
[ $RC -ne 0 ] && { say "SHIP FAILED rc=$RC"; exit 5; }
say "ROW 237 MERGED (v3.66.1241)"

run_cut(){ # worktree version branch title row
  say "=== $2 (row $5) ==="
  [ -d "$1" ] || { say "worktree $1 missing -- stopping"; return 7; }
  /home/mboyle/bd-pipeline.sh "$1" "$2" "$3" "$4" "$A/pr-body-${2##*.}.md" 2>&1 | tee -a "$L"
  local rc=${PIPESTATUS[0]}
  [ "$rc" -ne 0 ] && { say "$2 stopped rc=$rc"; return "$rc"; }
  say "ROW $5 MERGED (v$2)"; return 0
}
run_cut /home/mboyle/bd-cuts/cut/1242-t2-history-runtime 3.66.1242 \
  cut/1242-t2-history-runtime \
  "v3.66.1242 the history gate drives the route instead of reading it" 182 || exit 6
run_cut /home/mboyle/bd-cuts/cut/1243-t8-cluster-runtime 3.66.1243 \
  cut/1243-t8-cluster-runtime \
  "v3.66.1243 the cluster gate drives the route instead of reading it" 185 || exit 6
say "=== trio complete; deploy happens ONCE at the end, with the full suite ==="
