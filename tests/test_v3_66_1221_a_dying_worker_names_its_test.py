"""A worker that dies mid-test leaves the nodeid it was running.

THE NIGHT THIS COST. On 2026-08-24 a worker was killed by a timeout at 99% of
the sanctioned suite and the session then livelocked for 19 minutes. Every
artifact that would have named the guilty test failed at once:

  * pytest-timeout wrote its `+++ Timeout +++` banner and every thread's stack
    to `item.config.get_terminal_writer()` -- the worker's own stdout -- and
    execnet points every xdist worker's fd 1 at /dev/null. Measured with a
    positive control: 0 banners under `-n 2`, 2 banners and 34 stack lines from
    the identical subject run serially.
  * xdist's synthetic crash report DOES carry the nodeid, but it renders only in
    the final summary, and a livelocked session never reaches one.
  * `-q` drops the recovery narration entirely, because `report_line` is guarded
    on `verbose >= 0`.

What survived was BD's own `.chain` file -- and it records the FILE, deduped,
because that is what replaying a worker's sequence needs. The file it named held
51 candidate items. The investigation got there, but by measuring a 221s nested
pytest rather than by being told.

WHAT THIS ADDS, and why the CLEARING half is not an optimisation. The marker is
written before the test body runs and removed when the test finishes, so its
PRESENCE is the signal. Without the clearing, a clean run would leave every
worker pointing at whatever it happened to run last and "died here" would read
exactly like "finished here" -- an instrument that answers the same way whatever
happened, which is the shape this repository keeps finding in its own gates.

AND WHY ATOMICITY IS NOT PEDANTRY. Backlog row 222 is an entire row about a pid
file read between its create and its write, yielding `int('')`. A truncated
nodeid is worse than none: it names a test that does not exist and sends the
next investigation somewhere real tests do not live.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
import _run_context  # noqa: E402


def test_a_test_can_read_its_own_marker_so_the_write_precedes_the_body():
    """POSITIVE CONTROL for ordering, taken from inside the window itself.

    If the marker were written after the test body -- or not at all -- this test
    could not see itself. Nothing else here can prove the ordering, because every
    other arm inspects the marker only after the fact.
    """
    import conftest  # the live plugin, not a copy

    config = conftest._BD_CONFIG
    assert config is not None, "the conftest never captured its config"
    worker = getattr(config, "workerinput", None)
    worker_id = worker["workerid"] if worker else "main"
    directory = conftest._run_context_dir(config)

    marker = _run_context.current_path(directory, worker_id)
    assert marker.is_file(), (
        "no marker exists while this test is RUNNING, so it is not written "
        "before the body and a worker dying here would leave nothing")
    assert "test_a_test_can_read_its_own_marker" in marker.read_text(encoding="utf-8"), (
        "the marker names %r, not this test" % marker.read_text(encoding="utf-8"))


def test_the_marker_is_never_readable_half_written():
    """ROW 222's LESSON, applied before it can bite again.

    A reader that catches a partial marker learns a truncated nodeid, which
    points at a test that does not exist. The write is temp + os.replace, so a
    concurrent reader must see either the complete previous value, the complete
    new one, or nothing -- never a fragment.
    """
    names = ["tests/test_%s.py::test_%s" % ("x" * n, "y" * n) for n in range(1, 60)]
    seen: list[str] = []
    stop = threading.Event()

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)

        def reader():
            while not stop.is_set():
                try:
                    seen.append(_run_context.current_path(d, "gw0").read_text(
                        encoding="utf-8"))
                except (FileNotFoundError, OSError):
                    pass

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        for _ in range(40):
            for n in names:
                _run_context.note_current(d, "gw0", n)
        stop.set()
        t.join(timeout=10)

    assert seen, "the reader never observed a marker, so this proves nothing"
    complete = {n + "\n" for n in names}
    partial = [s for s in seen if s not in complete]
    assert not partial, (
        "a reader observed %d NON-ATOMIC marker value(s); the first is %r. A "
        "truncated nodeid names a test that does not exist."
        % (len(partial), partial[0][:120]))


def test_the_logfinish_hook_actually_removes_the_marker(monkeypatch, tmp_path):
    """THE CLEARING HALF, driven through the REAL hook.

    THIS REPLACES A VACUOUS TEST, and the reason is worth keeping. The first
    version looked for an EARLIER test's marker surviving into this one. It
    never could: there is exactly one `.current` file per worker and every
    `logstart` overwrites it, so mid-run the marker always names the current
    test whether or not clearing happens. A mutation battery caught it --
    disabling `clear_current` entirely left that test green.

    Clearing only becomes observable at the END, when nothing overwrites the
    marker again. So the hook itself is called here, against a private
    directory, and the file must be gone afterwards.
    """
    import conftest

    config = conftest._BD_CONFIG
    assert config is not None
    worker = getattr(config, "workerinput", None)
    worker_id = worker["workerid"] if worker else "main"
    monkeypatch.setattr(conftest, "_run_context_dir", lambda cfg: tmp_path)

    nodeid = "tests/test_x.py::test_that_finished"
    _run_context.note_current(tmp_path, worker_id, nodeid)
    marker = _run_context.current_path(tmp_path, worker_id)
    assert marker.is_file(), "precondition: the marker must exist to be cleared"

    conftest.pytest_runtest_logfinish(nodeid, ("tests/test_x.py", 1,
                                               "test_that_finished"))

    assert not marker.exists(), (
        "logfinish left the marker on disk. Every test that FINISHED would then "
        "be reported as a worker that died there, and the one fact this "
        "instrument exists to carry would mean nothing.")


def test_one_marker_per_worker_rather_than_an_accumulating_pile(monkeypatch,
                                                                tmp_path):
    """The marker is a CURRENT-position pointer, not a log.

    An accumulating set would grow with the run and turn the stranded report
    into a list of every test the worker ever ran.
    """
    import conftest

    worker_id = "gw3"
    for nodeid in ("tests/a.py::t1", "tests/a.py::t2", "tests/b.py::t3"):
        _run_context.note_current(tmp_path, worker_id, nodeid)

    files = sorted(p.name for p in tmp_path.glob("*.current"))
    assert files == ["gw3.current"], (
        "markers accumulated instead of being overwritten: %r" % files)
    assert _run_context.read_current(tmp_path) == {"gw3": "tests/b.py::t3"}, (
        "the marker does not name the LAST test started")
    leftovers = sorted(p.name for p in tmp_path.glob("*.tmp"))
    assert not leftovers, (
        "the atomic write left temp files behind: %r" % leftovers)


def test_read_current_reports_a_stranded_marker_and_ignores_a_cleared_one():
    """The read side, both directions, on a directory built for the purpose.

    A worker that dies is exactly a worker whose `clear_current` never ran, so
    the death case IS the un-cleared case; simulating the death by omitting the
    clear is the honest model rather than a shortcut. The cleared arm is what
    stops the reporter naming a worker that finished normally.
    """
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _run_context.note_current(d, "gw0", "tests/test_x.py::test_that_died")
        _run_context.note_current(d, "gw1", "tests/test_y.py::test_that_finished")
        assert _run_context.clear_current(d, "gw1") is True

        stranded = _run_context.read_current(d)

    assert stranded == {"gw0": "tests/test_x.py::test_that_died"}, (
        "read_current must report exactly the worker whose marker survived: %r"
        % stranded)


def test_clearing_a_marker_that_is_not_there_is_not_an_error():
    """A worker killed before its first test never wrote one, and teardown must
    not turn that into a second failure on top of the death."""
    with tempfile.TemporaryDirectory() as td:
        assert _run_context.clear_current(Path(td), "gw9") is False


def test_the_summary_actually_prints_the_stranded_worker(monkeypatch, tmp_path):
    """THE PAYOFF LINE, exercised rather than assumed.

    The durable marker is the evidence; this line is what puts it in front of
    whoever is staring at a run that stopped. An unexercised reporting path is
    how a gate ends up unable to see the thing it claims to judge, so it is
    driven here with a stub reporter and a directory holding one stranded
    marker.
    """
    import conftest

    _run_context.note_current(tmp_path, "gw7", "tests/test_a.py::test_boom")
    monkeypatch.setattr(conftest, "_run_context_dir", lambda config: tmp_path)

    lines = []

    class _Reporter:
        def write_line(self, text=""):
            lines.append(text)

    config = conftest._BD_CONFIG
    if getattr(config, "workerinput", None) is not None:
        pytest.skip("the summary is master-only by construction")

    conftest._write_run_context(_Reporter(), config)
    blob = "\n".join(lines)

    assert "DIED MID-TEST" in blob, (
        "the stranded worker was not announced at all:\n%s" % blob)
    assert "gw7: tests/test_a.py::test_boom" in blob, (
        "the announcement does not name the worker and its test:\n%s" % blob)


def test_the_summary_stays_quiet_when_nothing_stranded(monkeypatch, tmp_path):
    """OVER-SENSITIVITY CONTROL. A line printed on every clean run is a line
    nobody reads, and this one has to mean something when it appears."""
    import conftest

    monkeypatch.setattr(conftest, "_run_context_dir", lambda config: tmp_path)
    lines = []

    class _Reporter:
        def write_line(self, text=""):
            lines.append(text)

    config = conftest._BD_CONFIG
    if getattr(config, "workerinput", None) is not None:
        pytest.skip("the summary is master-only by construction")

    conftest._write_run_context(_Reporter(), config)
    assert "DIED MID-TEST" not in "\n".join(lines), (
        "the stranded-worker banner printed on a clean run")
