"""A zombie in a just-exited tracer's group is not residue (backlog row 214).

THE DEFECT, MEASURED. `bd-writerec._reap_group` waited for the tracer, then let
ONE `killpg(pgid, 0)` reading decide permanently. If anything was visible at that
instant it sent SIGTERM and latched `residual_terminated=True`, which the caller
publishes as `complete=false` / `result=UNKNOWN` and turns into exit 125 -- even
when the group emptied naturally microseconds later. Reproduced on test6 with the
exact fork/exit command: 45 FALSE REFUSALS IN 300 TRIALS, with the residue
visible for roughly 80-460 MICROSECONDS.

WHY ONE READING WAS ENOUGH TO BE WRONG. `killpg(pgid, 0)` SUCCEEDS FOR A ZOMBIE.
A zombie is an exit status nobody has collected yet, still holding a slot in the
process table; it cannot open a file, write a byte, or outlive its reaper. The
probe cannot tell it from a running child, so an orphan mid-exit read identically
to a live process that had escaped the capture.

WHY THIS FILE DOES NOT CHASE THE RACE. A test that runs the tool 300 times hoping
to land inside a 200us window is a flake, not a test. Every case below CONSTRUCTS
the condition: `_zombie_group` forks a child that makes itself a process-group
leader and exits, and the parent deliberately does not reap it, so the group holds
exactly one member and that member is in state Z for as long as the test needs.
The preconditions -- state Z, `killpg` succeeding, the tool's own `_group_gone`
answering "not gone" -- are asserted BEFORE any verdict, so a case that failed to
build its zombie fails as UNKNOWN instead of passing for the wrong reason.

BOTH DIRECTIONS, BECAUSE ONE ALONE IS INDISTINGUISHABLE FROM DELETING THE CHECK.
The function's docstring says observing an orphan and leaving it alive is the
failure it exists to prevent, and that is still true: a genuinely surviving
process is still TERMed, still escalated, and still reported. `_reap_group`'s own
end-to-end control lives next door in
tests/test_v3_66_1194_write_recorder.py::test_live_residual_process_group_is_terminated_and_proven_gone.

AND THE CENSUS IS BOUNDED. Reading process states means walking /proc, whose cost
scales with unrelated system load (backlog row 231 is an open row about exactly
that). The census therefore runs AT MOST ONCE per `_reap_group` call, after the
settle window has elapsed; the window itself is polled with the O(1) `killpg`
probe. `test_the_state_census_runs_once_not_once_per_poll` asserts that count
exactly, so the cheap-poll/expensive-census structure is pinned rather than
described.

AN UNREADABLE /proc IS RESIDUE, NOT INNOCENCE. `_group_is_only_residue` returns
False -- "there is residue" -- when it cannot read or cannot parse what it found,
so an unmeasurable group is terminated and reported. UNKNOWN must never be
laundered into "harmless"; that is the fail-open this repository keeps finding.
"""
from __future__ import annotations

import contextlib
import importlib.machinery
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "toolchain/bin/bd-writerec"
mod = importlib.machinery.SourceFileLoader("bd_writerec_row214", str(TOOL)).load_module()

# A live process parked in its own session, so its pgid is its own pid and nothing
# else can be in the group.  It closes the inherited pipe and is orphaned on purpose:
# init reaps it the instant it dies, which is what happens to a real escapee whose
# tracer has already exited.
_SURVIVOR = """
import os, sys, time
pid = os.fork()
if pid == 0:
    os.setsid()
    fd = os.open(os.devnull, os.O_RDWR)
    os.dup2(fd, 0); os.dup2(fd, 1); os.dup2(fd, 2)
    time.sleep(300)
    os._exit(0)
sys.stdout.write("%d\\n" % pid)
sys.stdout.flush()   # os._exit skips flushing, and an unflushed pid is no pid
os._exit(0)
"""


class _ExitedTracer:
    """A tracer that has already exited cleanly.  `wait` must never be called."""

    def __init__(self, code: int = 0):
        self._code = code
        self.returncode = code

    def poll(self):
        return self._code

    def wait(self, timeout=None):  # pragma: no cover - a called wait is a failed test
        raise AssertionError("the tracer had already exited; wait() must not be called")


def _proc_state(pid: int):
    """The single-letter state from /proc/<pid>/stat, or None once it is gone."""
    try:
        raw = Path("/proc/%d/stat" % pid).read_bytes()
    except (FileNotFoundError, ProcessLookupError):
        return None
    close = raw.rfind(b")")
    assert close != -1, "unparseable /proc/%d/stat" % pid
    return raw[close + 2:].split()[0].decode("ascii")


def _await_state(pid: int, want, limit: float = 5.0):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        state = _proc_state(pid)
        if state in want:
            return state
        time.sleep(.001)
    return _proc_state(pid)


@contextlib.contextmanager
def _zombie_group():
    """A process group whose ONLY member is a zombie, built deterministically.

    The child makes itself a process-group leader and exits immediately; this
    process is its parent and does not reap it, so the exit status stays
    uncollected and the group stays visible to `killpg` for the whole test.
    """
    pid = os.fork()
    if pid == 0:  # pragma: no cover - the child never returns to pytest
        try:
            os.setpgid(0, 0)
        finally:
            os._exit(0)
    try:
        state = _await_state(pid, ("Z",))
        assert state == "Z", (
            "the fixture did not produce a zombie (state=%r); nothing below is "
            "evidence about row 214" % state)
        assert os.getpgid(pid) == pid, "the child did not lead its own process group"
        yield pid
    finally:
        with contextlib.suppress(ChildProcessError, PermissionError):
            os.waitpid(pid, 0)


@contextlib.contextmanager
def _survivor_group():
    """A genuinely running, orphaned process alone in its own process group."""
    run = subprocess.run([sys.executable, "-c", _SURVIVOR],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    pid = int(run.stdout.strip())
    try:
        state = _await_state(pid, ("S", "R", "D"))
        assert state in ("S", "R", "D"), (
            "the survivor is not running (state=%r)" % state)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and os.getpgid(pid) != pid:
            time.sleep(.001)
        assert os.getpgid(pid) == pid, "the survivor did not lead its own group"
        yield pid
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)


def _fake_procfs(root: Path, pgid: int) -> Path:
    """A minimal /proc holding one Z-state member of `pgid` and nothing else."""
    proc = root / "proc"
    (proc / "100").mkdir(parents=True)
    # pid (comm) state ppid pgrp session ...  -- comm may contain spaces and ')'
    (proc / "100" / "stat").write_bytes(
        b"100 (a b) c) Z 1 %d 1 0 -1 4194560 0 0 0 0 0 0 0 0 20 0 1 0 0\n" % pgid)
    (proc / "self").mkdir()  # a non-numeric entry the walk must skip
    return proc


# ---------------------------------------------------------------------------
# The defect itself.
# ---------------------------------------------------------------------------

@pytest.mark.timeout(60)
def test_a_real_zombie_in_the_group_does_not_latch_a_refusal():
    """THE ROW-214 CASE, CONSTRUCTED RATHER THAN RACED.

    The tracer exited 0 and the only thing left in its group is an uncollected
    exit status.  Before the fix this returned residual_terminated=True, which
    the caller turns into complete=false and exit 125 on a capture that was
    completely correct.
    """
    with _zombie_group() as pgid:
        # PRECONDITION 1: this really is a zombie, not a live child.
        assert _proc_state(pgid) == "Z"
        # PRECONDITION 2: killpg SUCCEEDS on it -- the whole reason one probe was
        # not enough.  This is the fact the old code misread as "still alive".
        os.killpg(pgid, 0)
        # PRECONDITION 3: the tool's own probe agrees the group is not gone, so
        # the branch under test is genuinely reached.
        assert mod._group_gone(pgid) is False

        started = time.monotonic()
        reaped, residual = mod._reap_group(
            _ExitedTracer(0), pgid, escalate=False, hard_after=0.05, timeout=2.0)
        elapsed = time.monotonic() - started

        assert residual is False, (
            "a group holding only a zombie latched residual_group_terminated; the "
            "caller publishes that as complete=false and exit 125 on a correct "
            "capture (row 214: 45 false refusals in 300 trials)")
        assert reaped is True, (
            "an emptied group was not reported as reaped, so the capture is still "
            "refused for residue that cannot write a byte")
        assert elapsed < 1.0, (
            "the reap spent %.3fs waiting on an uncollected exit status" % elapsed)
        # The zombie was never a target: it is still there, unsignalled, for this
        # test's own waitpid to collect.
        assert _proc_state(pgid) == "Z"


@pytest.mark.timeout(60)
def test_a_real_survivor_is_still_terminated_and_still_reported():
    """THE OVER-SENSITIVITY CONTROL, AND THE MUTANT THAT MATTERS.

    The settle window must not become a way to launder real residue into success.
    A process that is genuinely running when the tracer exits is orphaned by
    definition; it is terminated, proven gone, and the run is still refused.
    """
    with _survivor_group() as pgid:
        assert _proc_state(pgid) in ("S", "R", "D")
        os.killpg(pgid, 0)
        assert mod._group_gone(pgid) is False
        if hasattr(mod, "_group_is_only_residue"):
            assert mod._group_is_only_residue(pgid) is False, (
                "a live child was classified as harmless residue, so a real "
                "escapee would be left running and the capture reported clean")

        started = time.monotonic()
        reaped, residual = mod._reap_group(
            _ExitedTracer(0), pgid, escalate=False, hard_after=0.05, timeout=5.0)
        elapsed = time.monotonic() - started

        assert residual is True, (
            "a live orphan was NOT reported as residual termination; the fix has "
            "hidden the failure it must still catch")
        assert reaped is True, "the terminated group was not proven empty"
        assert elapsed < 2.0, (
            "the survivor was not terminated promptly (%.3fs); the settle window "
            "is meant to be bounded, not a grace period" % elapsed)
        assert _proc_state(pgid) is None, "the survivor outlived the reap"
        with pytest.raises(ProcessLookupError):
            os.killpg(pgid, 0)


# ---------------------------------------------------------------------------
# The shape of the fix: a bounded window, and a census that runs once.
# ---------------------------------------------------------------------------

def test_the_settle_window_exists_and_is_short_enough_to_be_safe():
    """A window long enough to hide a real survivor would trade one defect for a
    worse one, so its size is asserted rather than assumed."""
    assert hasattr(mod, "SETTLE_SECONDS"), (
        "the settle window is gone; _reap_group is judging the group on a single "
        "reading again (row 214)")
    assert 0.005 <= mod.SETTLE_SECONDS <= 0.5, (
        "SETTLE_SECONDS=%r is outside the range this fix was measured for: too "
        "small re-opens the 80-460us race, too large delays terminating a real "
        "orphan" % mod.SETTLE_SECONDS)
    default = mod._reap_group.__defaults__[-1]
    assert mod.SETTLE_SECONDS < default / 20, (
        "the settle window is not negligible against the overall %ss timeout" % default)


@pytest.mark.timeout(60)
def test_the_state_census_runs_once_not_once_per_poll(monkeypatch):
    """REQUIREMENT (a): the /proc walk is not on the polling path.

    Reading every process's state costs time that scales with unrelated system
    load.  Polling it every millisecond of a 50ms window would mean up to 50 full
    /proc scans per capture.  The window is polled with the O(1) `killpg` probe
    and the census runs exactly once, after it.
    """
    assert hasattr(mod, "_group_is_only_residue"), "the row 214 fix is absent"
    census_states: list = []
    probe_calls = {"n": 0}
    real_census = mod._group_is_only_residue
    real_gone = mod._group_gone

    with _zombie_group() as pgid:
        def counting_census(target, *args, **kwargs):
            # Record what the group actually looked like AT census time, so a run
            # whose zombie was reaped early fails as UNKNOWN instead of passing.
            census_states.append(_proc_state(pgid))
            return real_census(target, *args, **kwargs)

        def counting_probe(target):
            probe_calls["n"] += 1
            return real_gone(target)

        monkeypatch.setattr(mod, "_group_is_only_residue", counting_census)
        monkeypatch.setattr(mod, "_group_gone", counting_probe)

        reaped, residual = mod._reap_group(
            _ExitedTracer(0), pgid, escalate=False, hard_after=0.05, timeout=2.0)

        assert (reaped, residual) == (True, False)
        assert census_states == ["Z"], (
            "the /proc census ran %d time(s) %r; it must run exactly once, after "
            "the settle window, and it must see the zombie it is judging"
            % (len(census_states), census_states))
        assert probe_calls["n"] >= 2, (
            "the settle window did not poll with the cheap probe (%d call(s)), so "
            "either the window is gone or it is sleeping blind" % probe_calls["n"])


@pytest.mark.timeout(60)
def test_the_census_runs_once_even_when_it_answers_no(monkeypatch):
    """THE OTHER HALF OF REQUIREMENT (a), AND THE ONE A ZOMBIE CANNOT PROVE.

    A census that says "only residue" ends the wait on its first call, so the
    zombie case would look bounded even if the walk were on the polling path.
    A LIVE member makes the census answer False, which is exactly when a
    per-poll census would run it again and again -- measured on test5 at 39ms
    per walk over 856 pids, against 0.003ms for the killpg probe it replaces.
    """
    assert hasattr(mod, "_group_is_only_residue"), "the row 214 fix is absent"
    census_calls = {"n": 0}
    real_census = mod._group_is_only_residue

    with _survivor_group() as pgid:
        def counting_census(target, *args, **kwargs):
            census_calls["n"] += 1
            return real_census(target, *args, **kwargs)

        monkeypatch.setattr(mod, "_group_is_only_residue", counting_census)
        reaped, residual = mod._reap_group(
            _ExitedTracer(0), pgid, escalate=False, hard_after=0.05, timeout=5.0)

        assert (reaped, residual) == (True, True), "the live orphan case changed shape"
        assert census_calls["n"] == 1, (
            "the /proc census ran %d times on one reap; walking every process in "
            "the polling loop makes the settle window cost scale with unrelated "
            "system load (backlog row 231)" % census_calls["n"])


@pytest.mark.timeout(60)
def test_a_group_that_empties_during_the_census_is_not_signalled(monkeypatch):
    """THE RACE THE CENSUS ITSELF INTRODUCES.

    Between the probe that said "not gone" and the census that reads /proc, the
    group can drain.  The census then sees no members at all, which is UNKNOWN
    and not innocence -- so before latching a refusal the code re-probes.  Without
    that re-probe the fix reproduces the very defect it removes, one window later.
    """
    assert hasattr(mod, "_group_is_only_residue"), "the row 214 fix is absent"
    signals: list = []
    real_census = mod._group_is_only_residue

    with _zombie_group() as pgid:
        def draining_census(target, *args, **kwargs):
            # Collect the exit status, exactly as init would, DURING the census.
            with contextlib.suppress(ChildProcessError):
                os.waitpid(pgid, 0)
            verdict = real_census(target, *args, **kwargs)
            assert verdict is False, (
                "precondition: a census of an empty group must report no members "
                "(UNKNOWN), otherwise this test is not exercising the re-probe")
            return verdict

        monkeypatch.setattr(mod, "_group_is_only_residue", draining_census)
        monkeypatch.setattr(mod, "_signal_group",
                            lambda target, sig: signals.append(sig))

        reaped, residual = mod._reap_group(
            _ExitedTracer(0), pgid, escalate=False, hard_after=0.05, timeout=2.0)

        assert signals == [], (
            "a group that had already drained was signalled %r and refused" % signals)
        assert (reaped, residual) == (True, False)


# ---------------------------------------------------------------------------
# The census against real and synthetic /proc, in both directions.
# ---------------------------------------------------------------------------

@pytest.mark.timeout(60)
def test_the_census_reads_real_proc_states_in_both_directions():
    """Against the real /proc: a zombie is residue, a live child is not, and a
    group nothing belongs to is not "only residue" either."""
    assert hasattr(mod, "_group_is_only_residue"), "the row 214 fix is absent"
    with _zombie_group() as pgid:
        assert mod._group_is_only_residue(pgid) is True, (
            "a real zombie was not recognised as an uncollected exit status")
    with _survivor_group() as pgid:
        assert mod._group_is_only_residue(pgid) is False, (
            "a real running process was classified as harmless residue")
    # No members at all is not "only residue"; _group_gone answers that question,
    # and answering it here would let an unreadable /proc masquerade as an empty one.
    assert mod._group_is_only_residue(999_000_000) is False


def test_an_unreadable_or_unparseable_proc_is_residue_not_innocence(tmp_path):
    """THE UNKNOWN CONTROL, TWO-SIDED.

    Each flaw is introduced into a fixture that is otherwise KNOWN to answer
    True, so a False verdict is attributable to the flaw and not to a fixture
    that never saw the group at all.
    """
    assert hasattr(mod, "_group_is_only_residue"), "the row 214 fix is absent"
    pgid = 424242
    proc = _fake_procfs(tmp_path, pgid)

    # The two-sided baseline: this exact tree, unmodified, is "only residue".
    assert mod._group_is_only_residue(pgid, procfs=str(proc)) is True, (
        "the fixture is not a valid positive control, so the negatives below "
        "would prove nothing")

    # (1) an entry whose stat cannot be READ -- a directory raises EISDIR for
    # every user, including root, so this control cannot silently stop testing.
    (proc / "101" / "stat").mkdir(parents=True)
    assert mod._group_is_only_residue(pgid, procfs=str(proc)) is False, (
        "an unreadable /proc entry was laundered into 'harmless'")
    (proc / "101" / "stat").rmdir()
    (proc / "101").rmdir()
    assert mod._group_is_only_residue(pgid, procfs=str(proc)) is True

    # (2) an entry whose stat cannot be PARSED.  It might belong to the group;
    # skipping it would fail open on exactly the process that matters.
    (proc / "102").mkdir()
    (proc / "102" / "stat").write_bytes(b"not a stat line at all\n")
    assert mod._group_is_only_residue(pgid, procfs=str(proc)) is False, (
        "an unparseable /proc entry was skipped instead of refused")
    (proc / "102" / "stat").write_bytes(b"102 (x) Z 1 %d 1 0\n" % pgid)
    assert mod._group_is_only_residue(pgid, procfs=str(proc)) is True

    # (3) a truncated stat line: the state is there but the group id is not.
    (proc / "102" / "stat").write_bytes(b"102 (x) Z\n")
    assert mod._group_is_only_residue(pgid, procfs=str(proc)) is False

    # (4) a non-numeric group id.
    (proc / "102" / "stat").write_bytes(b"102 (x) Z 1 not-a-number 1 0\n")
    assert mod._group_is_only_residue(pgid, procfs=str(proc)) is False

    # (5) the whole /proc is unlistable.
    assert mod._group_is_only_residue(pgid, procfs=str(tmp_path / "absent")) is False, (
        "an unreadable /proc reported the group harmless")


def test_a_running_member_is_residue_even_beside_a_zombie(tmp_path):
    """Mixed groups follow the live member, not the majority: one running process
    among any number of zombies is still something that can write a byte."""
    assert hasattr(mod, "_group_is_only_residue"), "the row 214 fix is absent"
    pgid = 424243
    proc = _fake_procfs(tmp_path, pgid)
    assert mod._group_is_only_residue(pgid, procfs=str(proc)) is True
    (proc / "103").mkdir()
    (proc / "103" / "stat").write_bytes(b"103 (live) S 1 %d 1 0\n" % pgid)
    assert mod._group_is_only_residue(pgid, procfs=str(proc)) is False
    # A live process in a DIFFERENT group is none of this function's business.
    (proc / "103" / "stat").write_bytes(b"103 (live) S 1 %d 1 0\n" % (pgid + 1))
    assert mod._group_is_only_residue(pgid, procfs=str(proc)) is True
