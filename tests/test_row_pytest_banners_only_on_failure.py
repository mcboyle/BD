"""The replay pointers belong to failures; the measurements belong to every run.

Four lines close a pytest session on this fleet. Two of them are MEASUREMENTS --
``socket recorder [stage 1]:`` states what the recorder saw and what it cannot
see, ``run context:`` states the machine the failure count came from -- and both
are pinned on a CLEAN run by pre-existing CI gates (1044, 1256), because a
recorder that prints nothing when it finds nothing is indistinguishable from one
that was never armed. The other two are POINTERS into an investigation:
``replay one worker exactly:`` and ``assignment: ... RECORDED``. On a green run
nobody replays anything, so those two are gated and the measurements are not.

The assignment RECORD is still written on a green run. Suppressing the pointer
must not suppress the evidence it points at.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_CONFTEST = _REPO / "tests" / "conftest.py"

# The measurements. Gating either of these deletes a measurement, not noise.
_MEASUREMENTS = (
    "socket recorder [stage 1]:",
    "run context:",
)
# The replay pointers. These are what this row gates.
_POINTERS = (
    "replay one worker exactly:",
    "assignment: assignment.json -- RECORDED",
)
_ALL = _MEASUREMENTS + _POINTERS


def _conftest_module():
    """The tests/conftest.py ALREADY LOADED by this session, found by its file.

    Not re-imported: that module has import-time side effects and a second copy
    would answer about a different object than the one the hook runs from. The
    predicate is `__file__`, which is the loaded module's real identity, and it
    can return NO -- this raises rather than skipping if conftest is not there.
    """
    for module in list(sys.modules.values()):
        if getattr(module, "__file__", None) == str(_CONFTEST):
            return module
    raise AssertionError(
        "tests/conftest.py is not in sys.modules, so the predicate under test "
        "was never loaded and this file cannot judge it")


class _FakeReporter:
    """Only what `_should_emit_session_banners` reads: `stats`."""

    def __init__(self, **stats):
        self.stats = dict(stats)


def _run_inner(tmp_path: Path, mode: str, *, banners: bool = False,
               collect_only: bool = False) -> tuple[str, list, list]:
    """Run a real nested pytest over a throwaway subject IN ITS OWN TMPDIR.

    THE SUBJECT LIVES OUTSIDE THE CHECKOUT. An earlier draft of this file
    generated it into `tests/`, and every repo-wide gate that enumerates the
    tree walked straight into it: the import-graph gate refused a whole band
    with "1 file(s) unparseable -- refusing" naming a transient
    `tests/.pytest_banners_*/test_subject.py`. A test that litters the tree it
    is measuring races every other gate in the lane. 1256 already solved this
    -- generate outside, then load the repository's real hooks explicitly with
    `-p conftest` and `PYTHONPATH=<repo>/tests`. That is the same conftest this
    row patches, loaded as a plugin, not a copy of it.

    THE CHILD GETS A PRIVATE SINK. `_run_context.sink_dir()` is
    `tempfile.gettempdir()/bd-runctx` resolved in the child at import, so
    handing the child `TMPDIR=<fresh dir>` puts its records somewhere no other
    process on this host can write. Globbing the host-global /tmp/bd-runctx
    instead made this file pass on a neighbour's record and fail under the
    isolated TMPDIR that CLAUDE.md A6 requires of every lane (r8 refute).
    """
    subject = "def test_nested_banner_subject():\n    assert %s\n" % (
        "True" if mode == "green" else "False, 'intentional nested failure'")
    suite = tmp_path / "banners-inner"
    suite.mkdir(exist_ok=True)
    test_path = suite / "test_subject.py"
    test_path.write_text(subject, encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="bd-banners-sink-") as sink:
        env = os.environ.copy()
        env.pop("BD_INSTALL_DIR", None)
        env.pop("BD_RUN_BANNERS", None)
        env["BD_PYTEST_BANNER_INNER"] = mode
        env["BD_NESTED_PYTEST"] = "1"
        env["BD_DISABLE_KEEPALIVE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["TMPDIR"] = sink
        env["PYTHONPATH"] = (str(_REPO / "tests") + os.pathsep
                             + env.get("PYTHONPATH", ""))
        if banners:
            env["BD_RUN_BANNERS"] = "1"
        argv = [sys.executable, "-m", "pytest", "-p", "conftest",
                str(test_path), "-vv", "-p", "no:randomly"]
        if collect_only:
            argv.append("--collect-only")
        result = subprocess.run(argv, cwd=_REPO, env=env, text=True,
                                capture_output=True, timeout=120)
        output = result.stdout + result.stderr
        records = sorted(Path(sink).glob("bd-runctx/*/assignment.json"))
        payloads = [p.read_text(encoding="utf-8") for p in records]
    assert not list((_REPO / "tests").glob(".pytest_banners_*")), (
        "this helper must not leave a generated suite inside tests/ -- a "
        "repo-wide gate walking the tree will parse it")
    collected = re.search(r"collected (\d+) item(?:s)?", output)
    assert collected and int(collected.group(1)) > 0, (
        "the nested pytest collected no tests, so its banner output is not "
        "evidence about a completed test session:\n%s" % output[-3000:])
    if mode == "red" and not collect_only:
        assert result.returncode != 0, output[-3000:]
    else:
        assert result.returncode == 0, output[-3000:]
    return output, records, payloads


def _counts(output: str, prefixes) -> dict:
    return {prefix: output.count(prefix) for prefix in prefixes}


def _assert_each_once(output: str, prefixes) -> None:
    counts = _counts(output, prefixes)
    assert counts == {prefix: 1 for prefix in prefixes}, (
        "each banner must appear EXACTLY once, not zero and not twice:\n"
        "%r\n%s" % (counts, output[-3000:]))


# ── the end-to-end contract, measured on a real nested session ───────────────

def test_a_green_session_keeps_its_measurements_and_drops_its_replay_pointers(tmp_path):
    output, _records, _payloads = _run_inner(tmp_path, "green")
    _assert_each_once(output, _MEASUREMENTS)
    pointers = _counts(output, _POINTERS)
    assert pointers == {prefix: 0 for prefix in _POINTERS}, (
        "a green session printed replay pointers nobody will replay: %r\n%s"
        % (pointers, output[-3000:]))


def test_a_red_session_keeps_every_banner_exactly_once(tmp_path):
    output, _records, _payloads = _run_inner(tmp_path, "red")
    _assert_each_once(output, _ALL)


def test_an_explicit_banner_request_keeps_every_banner_on_green(tmp_path):
    output, _records, _payloads = _run_inner(tmp_path, "green", banners=True)
    _assert_each_once(output, _ALL)


def test_a_green_session_still_writes_the_record_the_pointer_points_at(tmp_path):
    """The pointer is suppressed; the evidence is not."""
    _output, records, payloads = _run_inner(tmp_path, "green")
    assert len(records) == 1, (
        "a green session left %d assignment record(s) in its own sink: %r"
        % (len(records), [str(p) for p in records]))
    assert "test_subject.py" in payloads[0], (
        "the record does not name the throwaway subject, so it is somebody "
        "else's record:\n%s" % payloads[0][:2000])


def test_a_collect_only_session_records_no_assignment(tmp_path):
    _output, records, _payloads = _run_inner(tmp_path, "green", collect_only=True)
    assert records == [], [str(p) for p in records]


# ── the predicate itself: one test per arm, so one mutant per arm dies ───────

def _emit(reporter, exitstatus):
    return _conftest_module()._should_emit_session_banners(reporter, exitstatus)


def test_a_quiet_green_session_is_not_actionable(monkeypatch):
    """The negative control. Without it every arm below passes vacuously."""
    monkeypatch.delenv("BD_RUN_BANNERS", raising=False)
    assert _emit(_FakeReporter(passed=[object()]), 0) is False


@pytest.mark.parametrize("stats", [{"failed": [object()]},
                                   {"error": [object()]}])
def test_a_failure_or_a_collection_error_is_actionable(monkeypatch, stats):
    """`error` is its own arm: a session that COLLAPSED IN COLLECTION has zero
    failed tests, and it is exactly the session these pointers exist for."""
    monkeypatch.delenv("BD_RUN_BANNERS", raising=False)
    assert _emit(_FakeReporter(**stats), 0) is True


def test_a_nonzero_exit_with_no_failed_tests_is_actionable(monkeypatch):
    """`exitstatus` is its own arm too: an internal error, an interrupt or a
    usage error exits nonzero while `stats` holds no failure at all."""
    monkeypatch.delenv("BD_RUN_BANNERS", raising=False)
    assert _emit(_FakeReporter(passed=[object()]), 1) is True


def test_an_explicit_request_is_actionable_on_an_otherwise_quiet_session(
        monkeypatch):
    monkeypatch.setenv("BD_RUN_BANNERS", "1")
    assert _emit(_FakeReporter(passed=[object()]), 0) is True


def test_only_the_exact_opt_in_value_counts(monkeypatch):
    """`BD_RUN_BANNERS=0` is a request for the default, not for banners."""
    monkeypatch.setenv("BD_RUN_BANNERS", "0")
    assert _emit(_FakeReporter(passed=[object()]), 0) is False


def test_a_reporter_that_cannot_say_what_happened_is_actionable(monkeypatch):
    """THE THIRD STATE: unmeasurable is not quiet.

    tests/test_row753_a_run_records_its_own_outcome.py drives this same seam
    with a reporter that has no ``stats`` attribute at all, because "I never
    got to look" and "I looked and nothing failed" lead to opposite actions.
    A predicate that reached straight for ``.stats`` would raise AttributeError
    inside the seam -- taking row753 down with it -- and a predicate that read
    the absence as a quiet session would silence the pointers on the one run
    whose reader has the least to go on. It fails CLOSED instead.
    """
    monkeypatch.delenv("BD_RUN_BANNERS", raising=False)

    class _CannotSupplyStats:
        """row753's shape exactly: the seam's surface, minus ``stats``."""

        def __init__(self):
            self.lines = []

        def write_line(self, text=""):
            self.lines.append(text)

    assert not hasattr(_CannotSupplyStats(), "stats"), (
        "this fixture is only a control if it really cannot supply stats")
    assert _emit(_CannotSupplyStats(), 0) is True


def test_the_seam_itself_survives_a_reporter_that_cannot_supply_stats(
        tmp_path, monkeypatch):
    """END TO END, not just the predicate: `_write_run_context` is what row753
    calls, and the gate lives inside it. The AttributeError this pins was a
    real escape -- the remote precut caught it as row753's
    test_a_reporter_that_cannot_supply_stats_records_unknown_not_a_clean_run
    failing with "'_Reporter' object has no attribute 'stats'"."""
    conftest = _conftest_module()
    run_context = conftest._run_context
    monkeypatch.setattr(run_context, "sink_dir", lambda: tmp_path)

    class _Option:
        numprocesses = 24
        dist = "loadfile"

    class _Config:
        def __init__(self):
            self.option = _Option()

    class _CannotSupplyStats:
        def __init__(self):
            self.lines = []

        def write_line(self, text=""):
            self.lines.append(text)

    config = _Config()
    directory = conftest._run_context_dir(config)
    run_context.note_file(directory, "gw0", "tests/test_a.py")
    assert run_context.read_chains(directory), "the fixture seeded no chain"

    reporter = _CannotSupplyStats()
    conftest._write_run_context(reporter, config)

    printed = "\n".join(reporter.lines)
    assert "run context:" in printed, printed
    assert "replay one worker exactly:" in printed, (
        "an unmeasurable session is the one that needs the replay pointer; it "
        "was dropped: %r" % (printed,))
