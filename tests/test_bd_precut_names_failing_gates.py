"""bd-precut must NAME the underived gates that failed, and KEEP the record.

THE DEFECT (HARNESS_BACKLOG H389, filed by bd-integrator-B 2026-09-14 09:32Z).
Lane train-1540b printed `PRECUT_RC=3` and named ZERO failing nodes. Two
independent causes, and both are in this tool:

  * `_run_underived_gates` runs pytest with `-q`, whose progress row ends
    WITHOUT a newline, so the next thing printed is concatenated onto it. The
    lane's log read `.....F...RESULT: NOT CUT-READY` -- the verdict was legible
    only because a human knew where to split it, and nothing named the F.
  * the JUnit record that DOES hold the names is written into a `mkdtemp`
    scratch directory which the function's own `finally` removes. The one
    artifact that could answer "which gate?" was deleted by the run that
    produced it, every time, including on failure.

`_underived_detail` already summarises up to SIX failing cases into the RESULT
line, and that is not a substitute for either half: it is capped, it is prose
inside a semicolon-joined blob, and it is gone the moment the RESULT line is
truncated -- which is exactly what the lane log shows happened.

WHAT IS PROVEN HERE, and why each is separate:
  1. a failing record yields one `PRECUT-FAIL <nodeid>` line per failing OR
     erroring testcase, and the count is the number of such testcases;
  2. a passing record yields ZERO `PRECUT-FAIL` lines -- absence of names must
     mean "nothing failed", which is only true if the printer is silent on a
     clean record;
  3. the block is preceded by a newline, so a `-q` progress row without a
     trailing newline cannot swallow the first line;
  4. the record is COPIED OUT before the scratch directory is removed, for
     PASSING runs too. The artifact's presence is not the signal -- a reader
     who only gets evidence on failure cannot tell a clean run from a lost one;
  5. an unreadable or absent record says so in its own distinct token and does
     NOT emit a `PRECUT-FAIL` line. COULD NOT LOOK is a third outcome, and a
     zero here must not be readable as "no failures";
  6. LIMB 3: a run the 1800 s wall cut off NAMES THE GATE FILES STILL IN FLIGHT,
     from a per-node record the recorder plugin appends in the xdist WORKER --
     because with `--dist loadfile` xdist prints a node id only AFTER it
     reports, so a started-and-unfinished node never appears in the progress
     stream at all and output parsing would answer "none" for exactly the hangs
     this exists to explain;
  7. LIMB 4 (O445): when the wall falls in `pytest_sessionfinish` the per-node
     OUTCOMES ARE NOT UNKNOWN -- the tests reported them, only the summary
     writer died -- so the failures are named and a verdict is DERIVED from the
     records. THE DERIVATION IS GATED ON A CLOSED DENOMINATOR, FINISH ==
     COLLECTED, recorded at collection time. The adjudicator refuted the looser
     reading by measurement: on the 1540b log, 2864 outcomes reported against a
     collected total in (2895, 2925] means between 31 and 61 nodes NEVER
     REPORTED, and an undispatched node leaves no in-flight record either, so
     "nothing in flight" would otherwise read as a pass. Below a closed
     denominator the result stays UNKNOWN and names the shortfall. NAMING is
     approved unconditionally; DERIVING A VERDICT is not.

DEVIATION FROM THE BRIEF, RECORDED RATHER THAN TAKEN SILENTLY. The brief asks
for `PRECUT-FAIL <classname>::<name>`. This prints `_junit_nodeid`'s answer
instead, which IS `classname::name` when the file cannot be resolved and is the
pasteable `tests/test_x.py::test_y` when it can. That helper's own docstring
records the measurement: pytest's default xunit2 family writes no `file`
attribute, so a raw `classname::name` "cannot be pasted back into a rerun and
does not match the gate list either". Printing the raw form would reintroduce
the defect the helper exists to fix. Both shapes are asserted below.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
from importlib.machinery import SourceFileLoader

import pytest

BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parents[1]
PRECUT = REPO / "toolchain" / "bin" / "bd-precut"

KNOWN = ["tests/test_row357_mutant_anchors_are_not_fragile.py"]

FAILING_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="2" tests="4">
<testcase classname="tests.test_row357_mutant_anchors_are_not_fragile" name="test_alpha">
  <failure message="assert 1 == 2">boom</failure></testcase>
<testcase classname="tests.test_row357_mutant_anchors_are_not_fragile.TestGroup" name="test_beta">
  <failure message="assert 0">boom</failure></testcase>
<testcase classname="tests.test_not_in_the_gate_list" name="test_gamma">
  <error message="fixture 'x' not found">boom</error></testcase>
<testcase classname="tests.test_row357_mutant_anchors_are_not_fragile" name="test_ok"/>
</testsuite></testsuites>
"""

PASSING_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="0" tests="2">
<testcase classname="tests.test_row357_mutant_anchors_are_not_fragile" name="test_ok"/>
<testcase classname="tests.test_row357_mutant_anchors_are_not_fragile" name="test_also_ok"/>
</testsuite></testsuites>
"""


def _load():
    loader = SourceFileLoader("bd_precut_h389", str(PRECUT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader, PRECUT
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def precut():
    return _load()


def _fail_lines(captured: str) -> list[str]:
    return [ln for ln in captured.splitlines() if ln.startswith("PRECUT-FAIL ")]


def _evidence_lines(captured: str) -> list[str]:
    return [ln for ln in captured.splitlines() if ln.startswith("PRECUT-EVIDENCE ")]


def test_a_failing_record_names_every_failing_and_erroring_testcase(
        precut, tmp_path, capsys):
    """One line per failing OR erroring case -- and an ERROR counts."""
    junit = tmp_path / "underived.xml"
    junit.write_text(FAILING_JUNIT, encoding="utf-8")

    count = precut._print_underived_failures(str(junit), KNOWN)

    lines = _fail_lines(capsys.readouterr().out)
    assert count == 3, count
    # The two shapes, side by side on purpose (see the DEVIATION note above):
    # a classname the gate list RESOLVES becomes a pasteable node id; one it
    # does not resolve is returned as the dotted form rather than laundered
    # into a plausible-looking path.
    assert lines == [
        "PRECUT-FAIL tests/test_row357_mutant_anchors_are_not_fragile.py::test_alpha",
        "PRECUT-FAIL tests/test_row357_mutant_anchors_are_not_fragile.py::TestGroup::test_beta",
        "PRECUT-FAIL tests.test_not_in_the_gate_list::test_gamma",
    ], lines


def test_a_passing_record_names_nothing_and_the_count_is_zero(precut, tmp_path, capsys):
    """The POSITIVE CONTROL for the test above is that test; this is its NEGATIVE
    one. The printer must be silent on a clean record, or a zero count means
    nothing."""
    junit = tmp_path / "underived.xml"
    junit.write_text(PASSING_JUNIT, encoding="utf-8")

    count = precut._print_underived_failures(str(junit), KNOWN)

    out = capsys.readouterr().out
    assert count == 0, count
    assert _fail_lines(out) == [], out


def test_an_unreadable_record_says_so_and_does_not_emit_a_failure_line(
        precut, tmp_path, capsys):
    """COULD NOT LOOK is a third outcome. An absent record must not read as a
    clean one, and its diagnostic must not carry the PRECUT-FAIL prefix -- a
    reader grepping for that prefix would otherwise count a missing record as a
    failing test."""
    missing = tmp_path / "there-is-no-record-here.xml"

    count = precut._print_underived_failures(str(missing), KNOWN)

    out = capsys.readouterr().out
    assert count == 0, count
    assert _fail_lines(out) == [], out
    assert "PRECUT-NAMES-UNAVAILABLE" in out, out
    assert "no JUnit record was written" in out, out


def _fake_run(precut, monkeypatch, *, rc: int, body: str | None):
    """Replace the pytest subprocess with one that writes BODY to --junitxml."""
    seen = {}

    class _Completed:
        def __init__(self, returncode):
            self.returncode = returncode

    def fake(argv, **kwargs):
        junit = next(a.split("=", 1)[1] for a in argv
                     if str(a).startswith("--junitxml="))
        seen["junit"] = junit
        seen["scratch"] = os.path.dirname(junit)
        seen["argv"] = list(argv)
        seen["env"] = dict(kwargs.get("env") or {})
        # A COMPLETE in-flight record, so limbs 1 and 2 are read against a run
        # that finished: an empty one would make every test below incidentally
        # exercise the COULD NOT LOOK branch of limb 3 instead.
        inflight = seen["env"].get("BD_PRECUT_INFLIGHT_LOG")
        if inflight:
            pathlib.Path(inflight).write_text(
                "START tests/a.py::test_one\nFINISH tests/a.py::test_one\n",
                encoding="utf-8")
        if body is not None:
            pathlib.Path(junit).write_text(body, encoding="utf-8")
        return _Completed(rc)

    monkeypatch.setattr(precut.subprocess, "run", fake)
    return seen


@pytest.mark.parametrize("rc,body,expect_fail_lines", [
    (1, FAILING_JUNIT, 3),
    (0, PASSING_JUNIT, 0),
], ids=["a-failing-run", "a-passing-run"])
def test_the_evidence_outlives_the_scratch_directory_that_held_it(
        precut, monkeypatch, tmp_path, capsys, rc, body, expect_fail_lines):
    """The whole point of H389: the record must still be on disk AFTER the
    function returns, on a PASSING run as well as a failing one."""
    evidence_dir = tmp_path / "art"
    monkeypatch.setenv("BD_PRECUT_EVIDENCE_DIR", str(evidence_dir))
    seen = _fake_run(precut, monkeypatch, rc=rc, body=body)

    got_rc, _detail = precut._run_underived_gates(
        str(REPO), [(KNOWN[0], "why")], dict(os.environ))

    out = capsys.readouterr().out
    assert got_rc == rc, got_rc
    assert len(_fail_lines(out)) == expect_fail_lines, out
    # The scratch directory really is gone -- so this is not passing because
    # the cleanup silently stopped happening.
    assert not os.path.exists(seen["scratch"]), seen
    evidence = _evidence_lines(out)
    assert len(evidence) == 1, out
    path = evidence[0].split(" ", 1)[1]
    assert os.path.isfile(path), path
    assert pathlib.Path(path).read_text(encoding="utf-8") == body
    assert str(evidence_dir) == os.path.dirname(path), path


def test_the_block_begins_on_its_own_line_so_a_q_progress_row_cannot_eat_it(
        precut, monkeypatch, tmp_path, capsys):
    """The lane log read `.....F...RESULT: NOT CUT-READY`. pytest -q leaves its
    progress row unterminated, so whatever this function prints first must
    start with a newline of its own."""
    monkeypatch.setenv("BD_PRECUT_EVIDENCE_DIR", str(tmp_path / "art"))
    _fake_run(precut, monkeypatch, rc=1, body=FAILING_JUNIT)

    precut._run_underived_gates(str(REPO), [(KNOWN[0], "why")], dict(os.environ))

    out = capsys.readouterr().out
    assert out.startswith("\n"), repr(out[:80])
    assert out.splitlines()[1].startswith("PRECUT-FAIL "), repr(out[:160])


def test_without_the_env_var_the_evidence_lands_under_the_working_directory(
        precut, monkeypatch, tmp_path, capsys):
    """A lane that forgot to set BD_PRECUT_EVIDENCE_DIR still gets a record; the
    default is derived from the cwd, never from the scratch dir."""
    monkeypatch.delenv("BD_PRECUT_EVIDENCE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _fake_run(precut, monkeypatch, rc=1, body=FAILING_JUNIT)

    precut._run_underived_gates(str(REPO), [(KNOWN[0], "why")], dict(os.environ))

    path = _evidence_lines(capsys.readouterr().out)[0].split(" ", 1)[1]
    assert os.path.isfile(path), path
    assert pathlib.Path(path).parent == tmp_path / ".bd-precut-evidence", path


# --- LIMB 3: WHAT WAS IN FLIGHT WHEN THE 1800 s WALL CUT THE RUN OFF ----------
# Added 10:1xZ on int-B's correction: the 1540b RED was a HANG, so pytest was
# KILLED and wrote no JUnit at all. Limb 1 then correctly names nothing, which
# is honest and useless. These prove the tool still names the gates that had
# STARTED and not finished.

SLEEPER = (
    "BD_GATE_SCOPE = 'repo-wide'\n"
    "import time\n"
    "def test_that_never_returns():\n"
    "    time.sleep(600)\n"
)
FINISHER = (
    "BD_GATE_SCOPE = 'repo-wide'\n"
    "def test_that_returns_at_once():\n"
    "    assert True\n"
)


def _gate_root(tmp_path, rel: str, body: str) -> pathlib.Path:
    root = tmp_path / "root"
    (root / "tests").mkdir(parents=True)
    (root / rel).write_text(body, encoding="utf-8")
    return root


def test_the_inflight_record_separates_unfinished_from_finished(precut, tmp_path):
    """The parse, in isolation: FINISH cancels START, and the answer is a set of
    FILES, because that is what a reader reruns."""
    rec = tmp_path / "inflight.log"
    rec.write_text(
        "START tests/a.py::test_one\n"
        "FINISH tests/a.py::test_one\n"
        "START tests/a.py::test_two\n"          # started, never finished
        "START tests/b.py::TestX::test_three\n"  # started, never finished
        "START tests/c.py::test_four\n"
        "FINISH tests/c.py::test_four\n",
        encoding="utf-8")

    files, reason, stats = precut._inflight_files(str(rec))

    assert reason is None, reason
    assert files == ["tests/a.py", "tests/b.py"], files
    # `collected` is None here BECAUSE this record carries no COLLECTED line:
    # an absent denominator is UNKNOWN, never 0, and never "closed".
    assert stats == {"started": 4, "finished": 2, "collected": None}, stats


def test_a_missing_inflight_record_is_could_not_look_not_nothing_in_flight(
        precut, tmp_path, capsys):
    """A run killed before the recorder wrote anything must not read as "nothing
    was in flight". The diagnostic carries its own token so that a reader
    grepping PRECUT-INFLIGHT does not count it as a named gate."""
    files, reason, _stats = precut._inflight_files(str(tmp_path / "absent.log"))
    assert files is None
    assert reason == "no in-flight record was written", reason

    count = precut._print_inflight(str(tmp_path / "absent.log"))

    out = capsys.readouterr().out
    assert count == 0, count
    assert [ln for ln in out.splitlines() if ln.startswith("PRECUT-INFLIGHT ")] == [], out
    assert "PRECUT-INFLIGHT-UNAVAILABLE" in out, out


@pytest.mark.timeout(300)
def test_a_run_cut_off_by_the_wall_names_the_gate_that_was_still_running(
        precut, monkeypatch, tmp_path, capsys):
    """ACCEPTANCE (a). A REAL pytest child, a real sleeping test, a real wall.

    The budget is monkeypatched down from 1800 s; nothing else is faked, because
    the thing under test is precisely what survives a SIGKILL.
    """
    rel = "tests/test_h389_sleeper.py"
    root = _gate_root(tmp_path, rel, SLEEPER)
    monkeypatch.setenv("BD_PRECUT_EVIDENCE_DIR", str(tmp_path / "art"))
    monkeypatch.setattr(precut, "_UNDERIVED_BUDGET_S", 25)

    rc, detail = precut._run_underived_gates(str(root), [(rel, "sleeps")],
                                             dict(os.environ))

    out = capsys.readouterr().out
    assert rc == 124, (rc, detail)
    assert "HANG" in detail, detail
    inflight = [ln for ln in out.splitlines() if ln.startswith("PRECUT-INFLIGHT ")]
    assert inflight == ["PRECUT-INFLIGHT " + rel], out
    # And limb 1 is silent here BECAUSE there is no JUnit -- the killed child
    # never wrote one. That is the exact gap limb 3 exists to fill.
    assert _fail_lines(out) == [], out


@pytest.mark.timeout(300)
def test_a_finishing_run_names_nothing_in_flight(
        precut, monkeypatch, tmp_path, capsys):
    """ACCEPTANCE (b), the NEGATIVE CONTROL for the test above. Same machinery,
    same plugin, a test that returns: zero PRECUT-INFLIGHT lines -- and NOT the
    COULD NOT LOOK token either, so this is a measured none rather than a
    question that was never asked."""
    rel = "tests/test_h389_finisher.py"
    root = _gate_root(tmp_path, rel, FINISHER)
    monkeypatch.setenv("BD_PRECUT_EVIDENCE_DIR", str(tmp_path / "art"))
    monkeypatch.setattr(precut, "_UNDERIVED_BUDGET_S", 300)

    rc, detail = precut._run_underived_gates(str(root), [(rel, "returns")],
                                             dict(os.environ))

    out = capsys.readouterr().out
    assert rc == 0, (rc, detail)
    assert [ln for ln in out.splitlines() if ln.startswith("PRECUT-INFLIGHT")] == [], out
    assert _fail_lines(out) == [], out
    assert len(_evidence_lines(out)) == 1, out
    # O445 ACCEPTANCE (iv), on a REAL run rather than a synthetic record: a
    # normal run prints NONE of the derived-from-records vocabulary.
    for token in ("PRECUT-COMPLETE", "PRECUT-INCOMPLETE",
                  "PRECUT-CUT-READY-FROM-RECORDS",
                  "PRECUT-NAMES-FROM-RECORDS-UNAVAILABLE"):
        assert token not in out, (token, out)


def test_the_recorder_reaches_pytest_without_writing_into_the_tree(
        precut, monkeypatch, tmp_path, capsys):
    """The plugin is delivered by PYTHONPATH out of the scratch dir, never by a
    file dropped in the repo -- a tool that writes into the tree it is judging
    changes the object under test (rule 13)."""
    monkeypatch.setenv("BD_PRECUT_EVIDENCE_DIR", str(tmp_path / "art"))
    seen = _fake_run(precut, monkeypatch, rc=0, body=PASSING_JUNIT)

    precut._run_underived_gates(str(REPO), [(KNOWN[0], "why")], dict(os.environ))
    capsys.readouterr()

    argv = seen["argv"]
    assert "-p" in argv and precut._INFLIGHT_PLUGIN_NAME in argv, argv
    scratch = seen["scratch"]
    assert seen["env"]["PYTHONPATH"].split(os.pathsep)[0] == scratch, seen["env"]
    assert seen["env"]["BD_PRECUT_INFLIGHT_LOG"].startswith(scratch), seen["env"]
    assert not os.path.exists(scratch), scratch


def test_the_default_evidence_directory_is_ignored_by_git(precut):
    """A tool must not dirty the tree it is judging.

    H338 is the precedent and it is not hypothetical: three precut `.log.remote`
    files were collected into row802's lens patch because the tool wrote them
    into the worktree. The default destination here is `<cwd>/.bd-precut-evidence/`
    and the cwd of a precut run IS a worktree, so the ignore is part of the fix
    rather than housekeeping alongside it.
    """
    monkeypatched_free_default = precut._evidence_destination()
    assert ".bd-precut-evidence" in monkeypatched_free_default, \
        monkeypatched_free_default

    probe = REPO / ".bd-precut-evidence" / "underived-probe.xml"
    ignored = subprocess.run(["git", "check-ignore", "-q", str(probe)],
                             cwd=str(REPO), capture_output=True)
    # POSITIVE CONTROL: a path that is NOT ignored must come back non-zero, or
    # this probe would pass against a git that ignores everything.
    tracked = subprocess.run(["git", "check-ignore", "-q",
                              str(REPO / "toolchain" / "bin" / "bd-precut")],
                             cwd=str(REPO), capture_output=True)
    assert ignored.returncode == 0, (probe, ignored.stderr)
    assert tracked.returncode != 0, tracked


def test_a_timed_out_run_that_reported_every_collected_node_says_teardown(
        precut, tmp_path, capsys):
    """THE ZERO IS THE FINDING -- but only when the DENOMINATOR IS CLOSED.

    O445: the outcomes of a run whose wall fell in `pytest_sessionfinish` are
    NOT unknown, because the tests themselves already reported them; only the
    summary writer died. So "nothing in flight" may be read as "the hang is in
    teardown" -- PROVIDED every COLLECTED node reported. Here two were collected
    and two finished, so the claim is earned. The case where it is not earned is
    the next test, and that is the control the integrator measures at collect.
    """
    rec = tmp_path / "inflight.log"
    rec.write_text(
        "COLLECTED 2\n"
        "START tests/a.py::test_one\nFINISH tests/a.py::test_one\n"
        "START tests/b.py::test_two\nFINISH tests/b.py::test_two\n",
        encoding="utf-8")

    count = precut._print_inflight(str(rec), timed_out=True)

    out = capsys.readouterr().out
    assert count == 0, count
    assert [ln for ln in out.splitlines() if ln.startswith("PRECUT-INFLIGHT ")] == [], out
    assert "PRECUT-COMPLETE 2-of-2" in out, out
    assert "teardown/sessionfinish" in out, out
    assert "PRECUT-INCOMPLETE" not in out, out


def test_a_collected_node_that_never_reported_forbids_the_teardown_claim(
        precut, tmp_path, capsys):
    """O445 ACCEPTANCE (ii), THE CONTROL THAT MATTERS -- and the one this cut
    would have got WRONG as briefed.

    MEASURED by the adjudicator on
    fleet-run-artifacts/2026-08-25/inflight/train-1540b-precut.log: 2864 reported
    outcomes against a collected total in (2895, 2925], so BETWEEN 31 AND 61
    NODES NEVER REPORTED. A node the controller never dispatched leaves no
    in-flight record either -- its channel was already closed -- so "nothing in
    flight" is ALSO what dozens of never-run nodes look like. Reading that as a
    pass is a COULD NOT LOOK dressed as a verdict (the O413 class; FLEET_RULE 6).
    Same shape as the test above, ONE difference: the denominator is 3, not 2.
    """
    rec = tmp_path / "inflight.log"
    rec.write_text(
        "COLLECTED 3\n"
        "START tests/a.py::test_one\nFINISH tests/a.py::test_one\n"
        "START tests/b.py::test_two\nFINISH tests/b.py::test_two\n",
        encoding="utf-8")

    count = precut._print_inflight(str(rec), timed_out=True)

    out = capsys.readouterr().out
    assert count == 0, count
    assert "PRECUT-COMPLETE" not in out, out
    assert "PRECUT-INCOMPLETE 2-of-3" in out, out
    assert "1 collected node(s) never reported" in out, out
    assert "UNKNOWN, not a pass" in out, out
    # And the verdict half REFUSES to derive, which is the whole condition.
    assert precut._derive_from_records(str(rec)) is None


def test_an_unrecorded_denominator_is_unknown_and_never_closed(
        precut, tmp_path, capsys):
    """A record with no COLLECTED line cannot show that every collected node
    reported. It must say UNKNOWN rather than assume the started set was all of
    them -- which is exactly the assumption O445 refuted."""
    rec = tmp_path / "inflight.log"
    rec.write_text(
        "START tests/a.py::test_one\nFINISH tests/a.py::test_one\n",
        encoding="utf-8")

    precut._print_inflight(str(rec), timed_out=True)

    out = capsys.readouterr().out
    assert "PRECUT-INCOMPLETE 1-of-UNKNOWN" in out, out
    assert "PRECUT-COMPLETE" not in out, out
    assert precut._derive_from_records(str(rec)) is None


def test_the_teardown_line_is_absent_when_the_run_was_not_cut_off(
        precut, tmp_path, capsys):
    """NEGATIVE CONTROL for the three tests above, and O445 ACCEPTANCE (iv). The
    same COMPLETE record on a run that FINISHED must say nothing at all -- the
    teardown reading is a claim about a WALL, and making it on every green run
    would make it worthless."""
    rec = tmp_path / "inflight.log"
    rec.write_text(
        "COLLECTED 1\n"
        "START tests/a.py::test_one\nFINISH tests/a.py::test_one\n",
        encoding="utf-8")

    count = precut._print_inflight(str(rec), timed_out=False)

    out = capsys.readouterr().out
    assert count == 0, count
    assert "PRECUT-INFLIGHT" not in out, out
    assert "PRECUT-COMPLETE" not in out, out
    assert "PRECUT-INCOMPLETE" not in out, out


# --- H389 LIMB 4 (O445): DERIVE FROM THE RECORDS, ON A CLOSED DENOMINATOR ONLY -

RECORD_CLOSED_CLEAN = (
    "COLLECTED 2\n"
    "START tests/a.py::test_one\nREPORT setup passed tests/a.py::test_one\n"
    "REPORT call passed tests/a.py::test_one\nFINISH tests/a.py::test_one\n"
    "START tests/b.py::test_two\nREPORT call passed tests/b.py::test_two\n"
    "FINISH tests/b.py::test_two\n"
)
RECORD_CLOSED_RED = (
    "COLLECTED 3\n"
    "START tests/a.py::test_one\nREPORT call failed tests/a.py::test_one\n"
    "FINISH tests/a.py::test_one\n"
    "START tests/b.py::test_two\nREPORT setup failed tests/b.py::test_two\n"
    "FINISH tests/b.py::test_two\n"
    "START tests/c.py::test_three\nREPORT call passed tests/c.py::test_three\n"
    "FINISH tests/c.py::test_three\n"
)


def test_the_denominator_is_closed_only_when_finish_equals_collected(precut):
    """The gate itself, in isolation, all three states side by side."""
    assert precut._denominator({"finished": 3, "collected": 3}) == (True, 3, 3)
    assert precut._denominator({"finished": 2, "collected": 3}) == (False, 2, 3)
    assert precut._denominator({"finished": 3, "collected": None}) == (False, 3, None)
    # AND THE ONE THAT BIT ME: an empty denominator is arithmetically closed and
    # semantically empty. A run that collected nothing must not derive a pass.
    assert precut._denominator({"finished": 0, "collected": 0}) == (False, 0, 0)


def test_a_hang_that_reported_every_collected_node_derives_a_verdict(
        precut, tmp_path):
    """O445 ACCEPTANCE (i). The wall fell after every collected node reported and
    none failed, so the run IS derivable and the derivation is green."""
    rec = tmp_path / "inflight.log"
    rec.write_text(RECORD_CLOSED_CLEAN, encoding="utf-8")

    assert precut._derive_from_records(str(rec)) == (0, "")


def test_a_closed_denominator_with_failures_derives_red_and_counts_them(
        precut, tmp_path, capsys):
    """The other half of ACCEPTANCE (i), and the one that matters on 1540b: a
    derivable hang is not automatically a PASS. Two of three collected nodes
    reported `failed` -- one in `call` (a FAILURE), one in `setup` (an ERROR),
    which is the rule pytest's own summary applies -- so the derived verdict is
    RED and names both."""
    rec = tmp_path / "inflight.log"
    rec.write_text(RECORD_CLOSED_RED, encoding="utf-8")

    rc, detail = precut._derive_from_records(str(rec))
    named = precut._print_records_failures(str(rec))

    out = capsys.readouterr().out
    assert rc == 1, (rc, detail)
    assert "2 of 3 collected node(s) reported a NON-PASSING outcome" in detail, detail
    assert named == 2, named
    assert _fail_lines(out) == [
        "PRECUT-FAIL tests/a.py::test_one",
        "PRECUT-FAIL tests/b.py::test_two",
    ], out


def test_a_failing_node_is_named_even_when_the_denominator_is_open(
        precut, tmp_path, capsys):
    """O445 ACCEPTANCE (iii), and the ONE thing the ruling approves without any
    condition: an outcome that was reported is evidence that EXISTS. The
    denominator here is open (3 collected, 2 finished), so no VERDICT may be
    derived -- but the failure that did report is still named, because leaving
    41 reported non-passing outcomes unnamed is the defect H389 was filed for."""
    rec = tmp_path / "inflight.log"
    rec.write_text(
        "COLLECTED 3\n"
        "START tests/a.py::test_one\nREPORT call failed tests/a.py::test_one\n"
        "FINISH tests/a.py::test_one\n"
        "START tests/b.py::test_two\nREPORT call passed tests/b.py::test_two\n"
        "FINISH tests/b.py::test_two\n",
        encoding="utf-8")

    named = precut._print_records_failures(str(rec))

    out = capsys.readouterr().out
    assert named == 1, named
    assert _fail_lines(out) == ["PRECUT-FAIL tests/a.py::test_one"], out
    # ...and the verdict is STILL withheld. Naming and deriving are separate.
    assert precut._derive_from_records(str(rec)) is None


def test_a_skip_is_not_a_failure_in_the_derived_names(precut, tmp_path, capsys):
    """NEGATIVE CONTROL for the namer: `skipped` is a reported outcome too, and
    counting it would turn every xfail-heavy hang into a false red."""
    rec = tmp_path / "inflight.log"
    rec.write_text(
        "COLLECTED 1\n"
        "START tests/a.py::test_one\nREPORT call skipped tests/a.py::test_one\n"
        "FINISH tests/a.py::test_one\n",
        encoding="utf-8")

    named = precut._print_records_failures(str(rec))

    assert named == 0, named
    assert _fail_lines(capsys.readouterr().out) == [], "a skip was counted"
    assert precut._derive_from_records(str(rec)) == (0, "")


def test_an_absent_record_cannot_derive_and_says_so(precut, tmp_path, capsys):
    """COULD NOT LOOK, a third time and in the limb-4 vocabulary: no record means
    no derivation AND no silent zero."""
    missing = str(tmp_path / "absent.log")

    named = precut._print_records_failures(missing)

    out = capsys.readouterr().out
    assert named == 0, named
    assert "PRECUT-NAMES-FROM-RECORDS-UNAVAILABLE" in out, out
    assert _fail_lines(out) == [], out
    assert precut._derive_from_records(missing) is None


def test_the_recorder_writes_the_denominator_and_the_outcomes(precut, tmp_path):
    """END TO END on the PLUGIN ITSELF, with a real pytest child.

    The two limb-4 inputs -- the collected total and the per-node outcomes --
    are useless unless pytest actually emits them where the reader looks, so
    this runs the generated plugin exactly as `_run_underived_gates` does:
    written into a scratch dir, reached through PYTHONPATH, enabled with `-p`.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / (precut._INFLIGHT_PLUGIN_NAME + ".py")).write_text(
        precut._INFLIGHT_PLUGIN_SOURCE, encoding="utf-8")
    root = _gate_root(tmp_path, "tests/test_h389_mixed.py",
                      "def test_passes():\n    assert True\n"
                      "def test_fails():\n    assert 0\n")
    record = tmp_path / "inflight.log"
    env = dict(os.environ)
    env[precut._INFLIGHT_ENV] = str(record)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(scratch)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))

    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:randomly",
                    "-p", precut._INFLIGHT_PLUGIN_NAME,
                    "tests/test_h389_mixed.py"],
                   cwd=str(root), env=env, timeout=180,
                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    rec, reason = precut._inflight_records(str(record))
    assert reason is None, reason
    # THE DENOMINATOR pytest itself counted, not one this tool inferred -- and
    # this assertion is load-bearing: reading `session.testscollected` in this
    # hook records 0, because pytest assigns it only after the hook returns.
    assert rec["collected"] == 2, rec["collected"]
    assert len(rec["finished"]) == 2, rec["finished"]
    assert precut._denominator(
        {"finished": len(rec["finished"]), "collected": rec["collected"]})[0] is True
    # ...and the outcome of the one that failed, by name.
    assert precut._records_nonpassing(rec) == [
        "tests/test_h389_mixed.py::test_fails"], rec["reports"]
    assert precut._derive_from_records(str(record))[0] == 1


def test_the_hang_result_no_longer_claims_that_no_gate_reported(precut):
    """int-B 10:32Z: `toolchain/bin/bd-precut` carried the literal "it names no
    gate because none reported" in its own HANG result, and it is FALSE -- the
    1540b hang reported 38 failures and 3 errors, 41 non-passing outcomes, with
    zero summary lines. The sentence is removed here, and the replacement says
    what is actually true: the SUMMARY was never written.
    """
    source = PRECUT.read_text(encoding="utf-8")

    assert "it names no gate because none reported" not in source
    # NOTE the wrapped literal: the source breaks the sentence across two
    # string fragments, so the searchable span is the second fragment.
    assert "SUMMARY was never written" in source
    # POSITIVE CONTROL: the probe reads the file it claims to read, so the
    # absence above is a measurement and not an empty string search.
    assert "OUTCOMES WERE REPORTED; NAMES WERE NEVER WRITTEN" in source
