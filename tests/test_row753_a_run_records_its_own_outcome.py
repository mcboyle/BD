"""Row 753, acceptance clause 3: a run's own record says what the run DID.

WHAT CLAUSES 1 AND 2 ALREADY COVER, so this file does not re-derive them: the
reap boundary was named and instrumented in ``toolchain/bin/bd-wedge-hunt`` --
the channel reader's window is now computed on its owner's clock, a window
collapsed by lateness still drains what is buffered, and it reports
``S:ERROR-LATE`` / ``C:UNKNOWN`` rather than the ``TIMEOUT`` an honestly empty
channel earns (tests/mutants/row753_terminal_reader_owner_clock.json,
row753_channel_reader_anchor_handoff.json, row753_collapsed_window_drain.json).

WHAT CLAUSE 3 STILL ASKS FOR, verbatim from the row: "a 3x run under the SAME
-n 24 --dist loadfile schedule on a comparably loaded host either reproduces it,
establishing the rate, or fails to and THAT NEGATIVE RESULT IS RECORDED WITH ITS
EXACT COMMAND, DENOMINATORS, HOST AND LOAD."

RUNNING those three canonical suites is the integrator's, not a worker's --
COMMON.md forbids a worker the canonical command, and the row's own amendment
says so. What is in the tree, and what this gate is about, is the INSTRUMENT
that would make such a run mean anything. Measured on this base before a line
was written: the run record carries the machine (host, cores, workers, dist,
load at start and end, signal masks) and NOT the command, and not one bit of
what the run DID. So the record of a run where the row's test failed is the
record of a run where it passed, and a reproduction attempt that comes back
green cannot be told from one that never exercised the contention at all.

That is the third state CLAUDE.md A2 names: UNKNOWN reported as PASS. A negative
result whose conditions and outcome are not in one artifact is not a negative
result, it is a silence -- and clause 3 is the clause that has to read it.
"""

import json
import os
import pathlib
import sys

import pytest

BD_GATE_SCOPE = "repo-wide"

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "tests"))

import _run_context as rc                                        # noqa: E402


def _conftest():
    """The conftest module THIS run already loaded, not a second copy of it.

    pytest has imported tests/conftest.py by the time any test runs, and the
    seam under measurement is a function inside it. Re-importing the file would
    build a parallel module whose hooks are not the ones that ran, so the object
    is fetched out of sys.modules by its file, and the lookup FAILS LOUDLY
    rather than falling back to a copy.
    """
    wanted = str(_REPO / "tests" / "conftest.py")
    for module in list(sys.modules.values()):
        if getattr(module, "__file__", None) == wanted:
            return module
    raise AssertionError(
        "the loaded conftest module was not found in sys.modules; this gate "
        "measures the seam that ran, never a re-imported copy of it")


class _Report:
    def __init__(self, nodeid):
        self.nodeid = nodeid


class _Reporter:
    """A terminal reporter with exactly the surface the seam uses."""

    def __init__(self, stats="omit"):
        self.lines = []
        if stats != "omit":
            self.stats = stats

    def write_line(self, text=""):
        self.lines.append(text)


class _Option:
    numprocesses = 24
    dist = "loadfile"


class _Config:
    """A master config: no ``workerinput``, so the seam does not early-return."""

    def __init__(self):
        self.option = _Option()


def _record(tmp_path, monkeypatch, reporter, files=("tests/test_a.py",)):
    """Drive the REAL seam and return the record it wrote.

    Not a reimplementation: conftest._write_run_context is the one caller of
    the recorder in the tree, and this hands it a directory of its own instead
    of the live run's.
    """
    monkeypatch.setattr(rc, "sink_dir", lambda: pathlib.Path(tmp_path))
    conftest = _conftest()
    config = _Config()
    directory = conftest._run_context_dir(config)
    for index, name in enumerate(files):
        rc.note_file(directory, "gw%d" % index, name)
    # PRECONDITION: the chains exist, so the seam reaches write_assignment at
    # all. A record that was never written would satisfy every assertion below
    # by absence.
    assert rc.read_chains(directory), "the fixture seeded no chain to record"
    conftest._write_run_context(reporter, config)
    path = pathlib.Path(directory) / "assignment.json"
    assert path.is_file(), "the seam wrote no assignment.json at %s" % path
    return json.loads(path.read_text(encoding="utf-8"))


def _outcome(record):
    """The record's outcome block, or a RED that names what the record has.

    Reached through a named assertion rather than a subscript on purpose: a
    KeyError reads as a broken test, and the defect here is that the artifact
    does not carry the field at all.
    """
    assert "outcome" in record, (
        "the run record does not say what the run did, only what it ran on: %s"
        % sorted(record))
    return record["outcome"]


# ── clause 3: the exact command ──────────────────────────────────────────────


def test_the_run_record_carries_the_exact_command_it_was_started_with():
    """"...recorded with its EXACT COMMAND..." -- the record has no command."""
    context = rc.context(_Config())
    assert "command" in context, sorted(context)
    # Derived independently of the recorder: the interpreter's own argv.
    assert context["command"] == list(sys.argv), context["command"]
    assert context["command"], "an empty command line is not a record of one"
    assert context.get("executable") == sys.executable, context.get("executable")


# ── clause 3: the outcome, and the third state ───────────────────────────────


def test_the_record_of_a_run_that_failed_is_not_the_record_of_a_clean_one(
    tmp_path, monkeypatch
):
    """The whole of clause 3 in one assertion.

    A reproduction attempt asks ONE question -- did the named test fail this
    time -- and the artifact that carries the conditions has to carry the
    answer, or the two runs are indistinguishable afterwards.
    """
    failed = _record(tmp_path / "a", monkeypatch, _Reporter({
        "passed": [_Report("tests/test_ok.py::test_one")],
        "failed": [_Report(
            "tests/test_v3_66_1132_the_hunt_reaps_what_it_abandons.py"
            "::test_terminal_frame_without_eof_never_enters_an_unbounded_child_wait")],
    }))
    clean = _record(tmp_path / "b", monkeypatch, _Reporter({
        "passed": [_Report("tests/test_ok.py::test_one")],
    }))

    assert _outcome(failed) != _outcome(clean), (
        "a run that failed and a run that passed wrote the same record, so a "
        "reproduction attempt cannot be read after the fact")
    assert _outcome(failed)["failed"] == [
        "tests/test_v3_66_1132_the_hunt_reaps_what_it_abandons.py"
        "::test_terminal_frame_without_eof_never_enters_an_unbounded_child_wait"
    ], _outcome(failed)["failed"]
    assert _outcome(clean)["failed"] == [], _outcome(clean)["failed"]


def test_a_reporter_that_cannot_supply_stats_records_unknown_not_a_clean_run(
    tmp_path, monkeypatch
):
    """NEGATIVE CONTROL, and the third state.

    "I looked and nothing failed" and "I never got to look" lead to opposite
    actions -- accept the negative result, or re-run it -- so an unmeasurable
    run must never write the clean run's record.
    """
    unknown = _record(tmp_path / "u", monkeypatch, _Reporter())
    clean = _record(tmp_path / "c", monkeypatch, _Reporter({
        "passed": [_Report("tests/test_ok.py::test_one")]}))

    assert _outcome(unknown)["recorded"] is False, _outcome(unknown)
    assert _outcome(unknown)["failed"] is None, (
        "an empty failed list from a run that was never measured reads as a "
        "clean run: %r" % (_outcome(unknown),))
    assert _outcome(unknown).get("why"), _outcome(unknown)
    assert _outcome(clean)["recorded"] is True, _outcome(clean)
    assert _outcome(unknown) != _outcome(clean)


def test_the_recorded_counts_are_the_reporters_own_and_are_nonzero(
    tmp_path, monkeypatch
):
    """EXACT COUNTS: the denominators clause 3 asks the record to carry."""
    record = _record(tmp_path, monkeypatch, _Reporter({
        "passed": [_Report("tests/test_a.py::t%d" % i) for i in range(3)],
        "failed": [_Report("tests/test_b.py::t%d" % i) for i in range(2)],
        "skipped": [_Report("tests/test_c.py::t0")],
        "": [_Report("tests/test_d.py::t0")],
    }))
    counts = _outcome(record)["counts"]
    assert counts == {"passed": 3, "failed": 2, "skipped": 1}, counts
    assert sum(counts.values()) == 6, counts
    assert _outcome(record)["failed"] == [
        "tests/test_b.py::t0", "tests/test_b.py::t1"], _outcome(record)["failed"]


def test_an_error_is_a_failure_in_the_record_and_a_nameless_report_is_unknown(
    tmp_path, monkeypatch
):
    """A collection error is a run that did not answer, not a run that passed."""
    record = _record(tmp_path, monkeypatch, _Reporter({
        "error": [_Report("tests/test_broken.py"), _Report(None)],
        "passed": [_Report("tests/test_a.py::t0")],
    }))
    assert _outcome(record)["failed"] == [
        "UNKNOWN", "tests/test_broken.py"], _outcome(record)["failed"]
    assert _outcome(record)["counts"]["error"] == 2, _outcome(record)["counts"]


def test_a_record_written_without_an_outcome_is_unknown_not_a_clean_run(tmp_path):
    """The recorder's own default, exercised directly.

    ``write_assignment`` takes the outcome optionally so that its existing
    caller in tests/test_v3_66_1044 keeps working. An optional field whose
    default is a clean-looking record is the fail-open version of this whole
    row, so the default is asserted here rather than assumed.
    """
    record = json.loads(rc.write_assignment(
        tmp_path, {"gw0": ["tests/test_a.py"]}, {"cores": 4}
    ).read_text(encoding="utf-8"))
    assert _outcome(record)["recorded"] is False, _outcome(record)
    assert _outcome(record)["counts"] is None, _outcome(record)
    assert _outcome(record)["failed"] is None, _outcome(record)
    assert _outcome(record).get("why"), _outcome(record)


def test_the_record_still_carries_the_machine_it_always_carried(
    tmp_path, monkeypatch
):
    """The denominators clause 3 names beside the new ones -- host and load."""
    record = _record(tmp_path, monkeypatch, _Reporter({"passed": []}))
    context = record["context"]
    for key in ("host", "cores", "workers", "workers_from", "dist",
                "load_at_start", "load_at_end", "command"):
        assert key in context, (key, sorted(context))
    assert context["workers"] == 24 and context["dist"] == "loadfile", context
    assert record["chains"] and record["assignment"], record


def test_row753_transform_control_import_only():
    """Imports the recorder without asserting what it records."""
    assert rc.__name__ == "_run_context"
    assert callable(rc.write_assignment)
