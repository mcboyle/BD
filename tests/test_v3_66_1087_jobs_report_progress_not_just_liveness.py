"""v3.66.1087 -- backlog 3: a long job's liveness is not its progress.

THE GAP, and it is measured rather than imagined. `bd-jobs` answers "is this
process still there" with alive(), which compares /proc start times. That is the
right predicate for the orphan problem the tool was built for, and it is the
WRONG one for the question an operator actually asks about a long run, which is
"is it still doing anything".

MEASURED ON test6, 2026-08-13, at f154aef, during a full-suite sweep at -n 48:
a pytest master sat at `[ 99%]` for 462 seconds with `[gw37] node down: Not
properly terminated` as its last output, one defunct child it never reaped, and
a 1-minute load average of 0.06. alive() said LIVE for every second of that,
truthfully. Two of three samples at that worker count ended the same way. That
is exactly CLAUDE.md section 10's paragraph -- "a long job reports 99% and then
hangs there forever, which is exactly what a dead xdist worker looks like" --
reproduced on demand.

WHY A LOG MTIME AND NOT A PERCENTAGE. Section 10 bans estimated progress and
says to report the countable instead: "the last stage the log actually recorded,
and the wall-clock since the last line was written". A percentage is a
prediction; a file's mtime is a fact, it needs no cooperation from the job, and
it is precisely the signal that distinguished a wedged run from a slow one here.

THREE STATES, AND THE THIRD IS THE POINT. UNKNOWN is not a soft PROGRESSING: a
job with no log recorded, or whose log has gone, cannot be judged, and saying
so is the whole of CLAUDE.md section 0. Each of the three is asserted reachable
below, because a branch nothing can reach is dead code that reads as a safety
feature.

WHAT THIS DOES NOT DO. STALLED is a REPORT, not a verdict -- `list` stays
read-only and exits 0 either way. A legitimately quiet job (one that computes
for ten minutes without printing) reads as STALLED, which is why it must not
gate anything: an over-sensitive gate gets switched off, and section 0 calls
that a soundness bug rather than a safe default.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import importlib.machinery
import importlib.util
import os
import pathlib
import subprocess
import time

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-jobs"


def _load(name="bd_jobs_progress"):
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(_TOOL)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def jobs(tmp_path):
    mod = _load()
    mod.JOBS_DIR = tmp_path / "bd-jobs"
    mod.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    return mod


def test_progress_is_unknown_when_no_log_was_recorded(jobs):
    """An entry from before this feature, or a job launched without a log.
    UNKNOWN is a third state and it must not read as healthy."""
    state, age = jobs.progress({"pid": os.getpid()})
    assert state == "UNKNOWN", state
    assert age is None


def test_progress_is_unknown_when_the_log_has_gone(jobs, tmp_path):
    """A recorded path that no longer resolves cannot be judged either. This is
    the branch that would be easiest to collapse into PROGRESSING by accident,
    and collapsing it would mean a deleted log reads as a healthy job."""
    state, age = jobs.progress({"pid": os.getpid(),
                                "log": str(tmp_path / "not-there.log")})
    assert state == "UNKNOWN", state
    assert age is None


def test_progress_is_progressing_for_a_log_written_just_now(jobs, tmp_path):
    log = tmp_path / "fresh.log"
    log.write_text("still going\n", encoding="utf-8")
    state, age = jobs.progress({"pid": os.getpid(), "log": str(log)})
    assert state == "PROGRESSING", state
    assert age is not None and age < 60, age


def test_progress_is_stalled_for_a_log_nothing_has_touched(jobs, tmp_path):
    """The wedge, reproduced without waiting for one: backdate the mtime past
    the threshold. This is the state that was invisible on test6 for 462s."""
    log = tmp_path / "wedged.log"
    log.write_text("[ 99%]\n", encoding="utf-8")
    old = time.time() - 4000
    os.utime(log, (old, old))
    state, age = jobs.progress({"pid": os.getpid(), "log": str(log)},
                               stalled_after=300)
    assert state == "STALLED", state
    assert age is not None and age > 300, age


def test_the_threshold_is_a_parameter_not_a_constant(jobs, tmp_path):
    """The same log is PROGRESSING or STALLED depending on what you asked, so a
    caller with a legitimately quiet job can widen it rather than switch the
    signal off. Both directions asserted: a threshold that only ever tightens
    would be the over-sensitive failure section 0 names."""
    log = tmp_path / "quiet.log"
    log.write_text("x\n", encoding="utf-8")
    old = time.time() - 500
    os.utime(log, (old, old))
    assert jobs.progress({"pid": os.getpid(), "log": str(log)},
                         stalled_after=300)[0] == "STALLED"
    assert jobs.progress({"pid": os.getpid(), "log": str(log)},
                         stalled_after=900)[0] == "PROGRESSING"


def test_a_locally_launched_job_gets_a_log_and_records_its_path(jobs, tmp_path):
    """THE SEAM, not the components. cmd_run sent stdout and stderr to DEVNULL,
    so a registered job produced no output anywhere -- nothing to read, and
    nothing for progress() to measure. Assert the file exists AND holds what
    the job printed, because a path recorded over an empty file would satisfy a
    weaker check while measuring nothing."""
    class _Args:
        host = None
        purpose = "test-heartbeat"
        command = ["--", "echo hello-from-the-job; sleep 2"]
        script = None

    rc = jobs.cmd_run(_Args())
    assert rc == 0, rc

    entries = jobs.load_all()
    assert len(entries) == 1, entries
    entry = entries[0]
    assert entry.get("log"), "cmd_run recorded no log path: %r" % (entry,)

    log = pathlib.Path(entry["log"])
    deadline = time.time() + 20
    while time.time() < deadline and "hello-from-the-job" not in log.read_text(
            encoding="utf-8", errors="replace"):
        time.sleep(0.2)
    body = log.read_text(encoding="utf-8", errors="replace")
    assert "hello-from-the-job" in body, (
        "the job's stdout did not reach the recorded log: %r" % body)

    state, age = jobs.progress(entry)
    assert state == "PROGRESSING", (state, age)

    try:
        os.kill(entry["pid"], 9)
    except (ProcessLookupError, PermissionError):
        pass


def test_list_names_the_state_and_the_countable_never_a_percentage(jobs, tmp_path, capsys):
    """Section 10: report the countable. The rendered line must carry the
    seconds since the last write, and must not invent a percentage."""
    log = tmp_path / "wedged.log"
    log.write_text("[ 99%]\n", encoding="utf-8")
    old = time.time() - 4000
    os.utime(log, (old, old))

    entry = jobs.register(os.getpid(), "a wedged run", "pytest tests/",
                          log=str(log))
    assert entry.get("log") == str(log)

    class _Args:
        pass

    jobs.cmd_list(_Args())
    out = capsys.readouterr().out
    assert "STALLED" in out, out
    assert "4000s" in out or "3999s" in out or "4001s" in out, (
        "the line does not carry the wall-clock since the last write: %r" % out)
    assert "%" not in out.replace("[ 99%]", ""), (
        "list invented a percentage; section 10 forbids estimated progress")
