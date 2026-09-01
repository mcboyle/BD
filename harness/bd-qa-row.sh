#!/bin/bash
# Integrator QA on a returned Codex row, IN ITS OWN WORKTREE, before anything
# touches main. Runs the row's own new/changed tests plus a parse check. This is
# the "reviewer output is data until its cited facts are checked" step -- a
# worker's claim that its battery passed is not evidence until it is re-run here.
set -u
R="$1"; W=/home/mboyle/bd-codex-wt/row$R
O=/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts/row$R.qa.log
cd "$W" || { echo "no worktree row $R"; exit 1; }
{
echo "== QA row $R  $(date -u +%H:%M:%S)"
echo "-- changed paths --"
git status --porcelain=v1 | grep -v 'node_modules\|venv'
# NEW FILES MUST BE INDEX-VISIBLE BEFORE THE GATES RUN. Codex never commits, so
# a brand-new test file is UNTRACKED in its worktree, and
# test_every_sharded_suite_exists_and_is_tracked correctly refuses a shard entry
# whose file `git ls-files` cannot see. That is a true statement about the
# WORKTREE and a false one about the cut -- integration stages the file, so it
# will be tracked. `git add -N` makes it index-visible without staging content,
# which is the state the gate will actually judge. Without this the QA lane
# fails every row that adds a test: the harness manufacturing its own failure.
CHANGED=$(git status --porcelain=v1 | grep -v 'node_modules\|venv' | awk '{print $NF}')
[ -n "$CHANGED" ] && git add -N -- $CHANGED 2>/dev/null
echo "-- index-visible (git add -N): $(echo $CHANGED | wc -w) path(s) --"
T=$(printf '%s\n' $CHANGED | grep -E '^tests/test.*\.py$' | tr '\n' ' ')
# A ROW THAT ADDS A TEST FILE MUST BE JUDGED BY THE DECLARATION GATE TOO.
# bd-qa-row runs only the row's OWN tests, so row 339 -- which added a file
# marked BD_GATE_SCOPE = "repo-wide" and declared it in neither _DECLARED nor
# any CI shard -- passed QA and was only caught 11 minutes later by the
# tree-wide gate inside verify. That gate costs about seven seconds here. Its
# subject is the whole tree, so it is NOT in the row's own test set and
# bd-band-derive will never select it from the changed paths either.
NEWTESTS=$(printf '%s\n' $CHANGED | grep -E '^tests/test.*\.py$' | tr '\n' ' ')
# A ROW THAT EDITS A FILE SOME MUTANT ANCHORS ON MUST FACE THE ANCHOR GATE HERE.
# Mutant anchors are literal text. A row whose whole purpose is to RE-DERIVE a
# value -- row 339 moving vitest's testTimeout from 10_754 to 13_262 -- silently
# resolves a sibling row's anchor to zero, and the tree-wide gate refuses the cut
# 11 minutes later in precut. It cost two verify cycles on 2026-08-28, after the
# same class cost four on row 348's M4. This gate costs ~90s here.
ANCHORGATE=tests/test_v3_66_1184_mutation_specs_are_tracked.py
if [ -f "$ANCHORGATE" ] && [ -d tests/mutants ]; then
  _hit=""
  for _f in $CHANGED; do
    case "$_f" in tests/mutants/*|"$ANCHORGATE") continue;; esac
    # does any tracked spec name this path as a mutant subject?
    grep -lF "\"$_f\"" tests/mutants/*.json >/dev/null 2>&1 && { _hit="$_f"; break; }
  done
  if [ -n "$_hit" ]; then
    case " $T " in
      *" $ANCHORGATE "*) ;;
      *) T="$T $ANCHORGATE"
         echo "-- added $ANCHORGATE to the QA set: this row edits $_hit, which a"
         echo "-- tracked mutant spec anchors on by literal text" ;;
    esac
  fi
fi
GATE939=tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py
if [ -n "$NEWTESTS" ] && [ -f "$GATE939" ]; then
  case " $T " in
    *" $GATE939 "*) ;;                     # already in the row's own set
    *) T="$T $GATE939"
       echo "-- added $GATE939 to the QA set: this row adds test file(s), and a"
       echo "-- gate declared nowhere runs on no PR while every other check stays green" ;;
  esac
fi
echo "-- tests to run: ${T:-NONE} --"
if [ -z "$T" ]; then echo "QA_RC=0 (no test files changed -- doc/backlog only)"; exit 0; fi
PY="$W/venv/bin/python"
# QA has not measured tests until this worktree can execute its own interpreter.
# A bare git worktree does not carry the venv symlink that bd-codex-cut normally
# creates; treating the resulting shell 127 as a red test run burns the row's
# finite retry budget without ever collecting a test.
if [ ! -x "$PY" ]; then
  echo "QA_STATE=NOT_RUNNABLE"
  echo "QA_REASON=missing worktree interpreter $PY"
  echo "QA_RC=NOT_RUNNABLE"
  exit 0
fi
RAW=$(mktemp /tmp/bd-qa-row-XXXXXX.log)
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 timeout 1800 \
  "$PY" -m pytest $T -p no:randomly -q > "$RAW" 2>&1
RC=$?
# EMIT THE BODY *AFTER* the exception below decides, not before. bd-row-chain.sh
# greps this whole log for "N failed" / "^FAILED " independently of QA_RC, so a
# first-run tail printed above a green re-run still refuses the cut -- observed
# on row 353, 2026-08-28.
EMIT_FIRST_TAIL=1
# THE ONE FAILURE A WORKTREE CANNOT SATISFY BY CONSTRUCTION. A row that declares
# a new gate must raise _EXPECTED_DECLARED_GATE_COUNT, but that scalar is a
# WHOLE-TREE census owned by the integrator: bd-integrate-row.sh re-pins it on
# the cut tree and names the move in the changelog, precisely so two concurrent
# rows cannot each write a number that is wrong once the other lands. The
# worktree therefore reports declared == N+1 against main's N and QA refuses a
# row that is correct. Rows 344 and 347 were both blocked by this on 2026-08-28.
#
# THIS IS NOT A BLANKET EXEMPTION, and it must not become one. It applies only
# when the count assertion is the SOLE failure AND the gate's own coverage sets
# are BOTH EMPTY -- that is, every declared gate is scheduled and every
# scheduled gate is declared, so the only disagreement left is the scalar.
# A gate missing from CI, an extra shard entry, an undeclared confirmed safety
# gate, or any second failure all still refuse. And nothing is skipped: the same
# assertion runs again on the integrated cut tree, in bd-precut --gate, and in
# exact-head CI, where the count is correct and the tree is the real subject.
if [ "$RC" -ne 0 ]; then
  NFAILED=$(grep -c '^FAILED ' "$RAW")
  ONLY=$(grep -m1 '^FAILED ' "$RAW")
  if [ "$NFAILED" = "1" ] \
     && [ "${ONLY##*::}" = "test_declared_and_ci_executed_gate_denominators_are_exact" ] \
     && grep -q 'missing from CI: \[\]; extra in CI: \[\]' "$RAW"; then
    DELTA=$(grep -oE 'declared [0-9]+ gates, expected exactly [0-9]+' "$RAW" | head -1)
    NODE="tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py::test_declared_and_ci_executed_gate_denominators_are_exact"
    # DO NOT just relabel the verdict line. bd-row-chain.sh reads this log
    # INDEPENDENTLY and refuses on "N failed" or a "FAILED " line -- correctly,
    # because a QA_RC it cannot corroborate is not evidence. Editing the verdict
    # while leaving a red body would make the two disagree and, worse, teach the
    # chain to ignore a failure line. So RE-RUN the same file set with exactly
    # this one nodeid deselected, and let that real, green run be the log. The
    # first run's complete output is preserved beside it as .raw.
    cp -f "$RAW" "$O.raw"
    EMIT_FIRST_TAIL=0
    echo "-- the first run's SOLE failure was the integrator-owned whole-tree gate count ($DELTA)"
    echo "-- with BOTH coverage sets EMPTY: every declared gate is scheduled and every"
    echo "-- scheduled gate is declared, so the only disagreement left is the scalar that"
    echo "-- bd-integrate-row.sh re-pins on the cut tree. Re-running with that ONE nodeid"
    echo "-- deselected. It is NOT skipped for the cut: it re-runs on the integrated tree,"
    echo "-- in bd-precut --gate, and in exact-head CI. Full first-run output: $O.raw"
    echo "-- deselected: $NODE"
    env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 timeout 1800 \
      "$PY" -m pytest $T --deselect "$NODE" -p no:randomly -q > "$RAW" 2>&1
    RC=$?
    tail -25 "$RAW"
    if [ "$RC" -ne 0 ]; then
      cat "$O.raw"
      echo "QA_RC=$RC (deselected re-run STILL red -- the count was not the only problem)"
      PRINTED=1
    fi
  fi
fi
[ "${EMIT_FIRST_TAIL:-1}" = "1" ] && tail -25 "$RAW"
# The declaration-gate exception can run pytest a second time; classify its
# final measurement, not the superseded first-run status.
case "$RC" in
  0) QA_STATE=PASS ;;
  1) QA_STATE=TEST_FAILURE ;;
  2|3|4|5) QA_STATE=PYTEST_ERROR ;;
  *) QA_STATE=RUNNER_ERROR ;;
esac
[ "${PRINTED:-0}" = "1" ] || echo "QA_STATE=$QA_STATE"
[ "${PRINTED:-0}" = "1" ] || echo "QA_RC=$RC"
rm -f "$RAW"
} > "$O" 2>&1
tail -3 "$O"
