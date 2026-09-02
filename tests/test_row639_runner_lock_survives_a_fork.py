"""Row 639 -- a forked child can take every runner lock a scanned route takes.

``os.fork()`` carries ONLY the calling thread.  A lock another thread held at
the instant of the fork is inherited LOCKED by a child that contains no thread
able to release it, so the child blocks forever the first time it wants that
lock.  v3.66.1430 closed that for ``_watch_registry_lock`` -- the forking
thread now holds it across every fork -- and closed only half the hazard.

THE OTHER HALF IS ``runner._lock``.  A per-route lock census over the
secret-display gate's own 588 concretized GET targets measured eleven routes
taking it (/api/capacity, /api/dashboard, /api/dashboard/v2, /api/health,
/api/health/v2, /api/queue/preflight, /api/queue/v2, /api/sites/v2,
/api/status, /api/widgets/data, /metrics), against three for the registry
lock.  Its only background holder is the auto-retry scanner
(``runner_scheduler.SchedulerMixin._auto_retry_loop``), awake once per 60s, so
the window is small -- but the mechanism is identical, it PRE-DATES the cut
that exposed the registry lock, and a gate that forks children out of a booted
multi-threaded app is unsound while any such lock exists.

THE REMEDY IS THE OPPOSITE ONE, DELIBERATELY.  The registry lock is HELD across
the fork; runner locks are REBOUND in the child.  Holding cannot work here:
``Runner._lock`` is a non-reentrant ``threading.Lock``, so a thread that forks
while holding one would self-deadlock inside its own ``before`` hook, and
``before`` hooks run in reverse registration order, which would invert the
lock order every route uses.  Rebinding in the child has neither failure mode,
touches the parent not at all, and is what CPython does to its own logging
locks.  ``app_state._reinit_runner_locks_in_child`` carries the full argument.

WHAT THIS FILE PROVES, in order: the hook rebinds exactly the held plain locks
and nothing else; a child forked mid-hold can take the lock; a lock of the SAME
construction that the hook cannot reach still hangs its child (teeth); and a
churned fleet delivers an EXACT shard count rather than a smaller one.
"""
from __future__ import annotations

import os
import pathlib
import select
import signal
import sys
import threading
import time

import pytest

BD_GATE_SCOPE = "module"

_FORK_HOLD_S = 3.0
_FORK_CHILD_DEADLINE_S = 20.0
_TEETH_DEADLINE_S = 6.0
_SHARD_DEADLINE_S = 25.0
_SHARDS = 16


class _FakeRunner:
    """A runner as the fork hook sees one: an object whose instance dict holds
    the locks.  Nothing here is imported from runner.py, so a runner that grows
    a __slots__ or a property cannot make this fixture lie about the hook."""

    def __init__(self):
        self._lock = threading.Lock()
        self._worker_heartbeats_lock = threading.Lock()
        self._run_lifecycle_lock = threading.RLock()
        self.jobs = {}
        self.not_a_lock = threading.Lock()      # wrong NAME, right type


@pytest.fixture()
def live_registry():
    """The LIVE app_state.runners, restored exactly afterwards.  Teardown must
    never be what makes a test green, so every verdict is asserted first."""
    from bulk_downloader import app_state
    original = dict(app_state.runners)
    try:
        yield app_state.runners
    finally:
        app_state.runners.clear()
        app_state.runners.update(original)


def test_the_hook_and_its_registration_are_present():
    """Precondition for every verdict below: the change-specific symbol exists
    and POSIX fork hooks are available on this host at all."""
    from bulk_downloader import app_state
    assert callable(app_state._reinit_runner_locks_in_child), (
        "the child-side fork hook is absent, so nothing below is a measurement "
        "of it")
    assert hasattr(os, "register_at_fork"), (
        "no os.register_at_fork on this platform: the hazard cannot exist and "
        "the fix cannot be measured -- UNKNOWN, not OK")


def test_the_hook_rebinds_exactly_the_held_plain_locks(live_registry):
    """The hook's own denominator.  A held lock is replaced with a free one;
    a free lock, an RLock and an unregistered runner are left alone."""
    from bulk_downloader import app_state

    held_a, held_b, free = _FakeRunner(), _FakeRunner(), _FakeRunner()
    unregistered = _FakeRunner()
    live_registry.clear()
    live_registry.update({"a": held_a, "b": held_b, "free": free})

    old_a, old_b = held_a._lock, held_b._lock
    old_heartbeats = held_b._worker_heartbeats_lock
    old_free = free._lock
    old_rlock = held_a._run_lifecycle_lock
    old_unregistered = unregistered._lock

    assert old_a.acquire(blocking=False)
    assert old_b.acquire(blocking=False)
    assert old_heartbeats.acquire(blocking=False)
    assert old_unregistered.acquire(blocking=False)
    assert old_rlock.acquire(blocking=False)
    assert not free._lock.locked(), "precondition: the control lock is free"

    rebound = app_state._reinit_runner_locks_in_child()

    assert rebound == 3, (
        "expected exactly three rebindings (two _lock, one "
        "_worker_heartbeats_lock, all held, all on REGISTERED runners); got %r"
        % rebound)
    for label, runner, old in (("a", held_a, old_a), ("b", held_b, old_b)):
        assert runner._lock is not old, (
            "runner %s kept the lock it inherited locked" % label)
        assert not runner._lock.locked(), (
            "runner %s's replacement lock is itself locked" % label)
        assert old.locked(), (
            "the hook RELEASED runner %s's old lock instead of replacing it; "
            "releasing a lock the child does not own is how a half-written "
            "critical section is published as complete" % label)
    assert held_b._worker_heartbeats_lock is not old_heartbeats
    assert free._lock is old_free, (
        "a lock that was NOT held was replaced, so the hook mutates state it "
        "has no reason to touch")
    assert held_a._run_lifecycle_lock is old_rlock, (
        "an RLock was replaced; its owner and recursion count cannot be "
        "reconstructed, so it is deliberately outside this hook")
    assert unregistered._lock is old_unregistered, (
        "a runner absent from the registry was reached, so the hook's "
        "population is not the registry snapshot it claims")

    # Leave the process as we found it: these are fixture locks, and the ones
    # the hook replaced are unreachable now, but the survivors are not.
    old_rlock.release()
    old_unregistered.release()


def test_the_real_runner_class_is_walkable_and_writable_by_the_hook():
    """The fixtures above are fakes, and the hook has two fail-open ``continue``
    arms -- ``vars()`` raising TypeError on ``__slots__``, and ``setattr`` being
    refused.  A real ``Runner`` that grew either would leave every test in this
    file green while the hook silently reached nothing, so the three properties
    the hook depends on are asserted against the SHIPPED class."""
    import ast as _ast
    from bulk_downloader.runner import SiteRunner

    assert "__slots__" not in vars(SiteRunner), (
        "SiteRunner grew __slots__, so vars(runner) raises TypeError and the "
        "hook's except-TypeError arm now skips the whole fleet in silence")
    assert not isinstance(vars(SiteRunner).get("_lock"), property), (
        "SiteRunner._lock became a property, so setattr is refused and the "
        "hook's except-Exception arm swallows it")

    init = next(n for n in _ast.walk(_ast.parse(
        pathlib.Path(_runner_source()).read_text(encoding="utf-8")))
        if isinstance(n, _ast.FunctionDef) and n.name == "__init__"
        and any(isinstance(t, _ast.Attribute) and t.attr == "_lock"
                for s in _ast.walk(n)
                if isinstance(s, _ast.Assign) for t in s.targets))
    constructions = [
        _ast.unparse(s.value) for s in _ast.walk(init)
        if isinstance(s, _ast.Assign)
        and any(isinstance(t, _ast.Attribute) and t.attr == "_lock"
                for t in s.targets)]
    assert constructions == ["threading.Lock()"], (
        "SiteRunner._lock is no longer a plain threading.Lock (%r); the hook "
        "filters "
        "on that exact type and would now skip it" % (constructions,))


def _runner_source():
    from bulk_downloader import runner as _runner_mod
    return _runner_mod.__file__


def test_an_uncontended_fleet_is_not_touched_at_all(live_registry):
    """The hook is a no-op when nothing is held -- an ordinary fork must not
    silently reseat live state."""
    from bulk_downloader import app_state
    quiet = _FakeRunner()
    live_registry.clear()
    live_registry["quiet"] = quiet
    before = quiet._lock
    assert app_state._reinit_runner_locks_in_child() == 0
    assert quiet._lock is before


# ── the fork seam ─────────────────────────────────────────────────────


def _fork_while_held(lock, child_body, deadline=_FORK_CHILD_DEADLINE_S,
                     hold_s=_FORK_HOLD_S):
    """Fork a child while ANOTHER thread holds ``lock``; return its verdict.

    The holder proves it is holding before the fork is taken and releases on
    its own timer, never on a signal from the forking thread -- a protection
    that makes the forking thread WAIT for the holder would otherwise deadlock
    against a holder waiting for the fork.  Returns
    ``(verdict, exitcode)`` with verdict in {"COMPLETED", "CHILD-ERROR",
    "HUNG"}; a hung child is killed, never left.  Shaped after row 449's
    harness so the two halves of this hazard are measured the same way.
    """
    holding = threading.Event()
    holds = []

    def holder():
        with lock:
            holds.append(1)
            holding.set()
            time.sleep(hold_s)

    thread = threading.Thread(target=holder, daemon=True,
                              name="row639-fork-lock-holder")
    thread.start()
    assert holding.wait(10), (
        "precondition failed: the holder thread never acquired the lock, so "
        "the fork below would not have been taken while it was held")
    assert holds == [1], (
        "precondition failed: expected exactly one hold, observed %r" % (holds,))
    assert lock.locked(), (
        "precondition failed: the lock reports free at the instant of the fork")

    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:                                    # pragma: no cover - child
        code = 4
        try:
            code = child_body()
        except BaseException:
            code = 4
        finally:
            os._exit(code)

    waited = time.monotonic()
    status = None
    while time.monotonic() - waited < deadline:
        done, raw = os.waitpid(pid, os.WNOHANG)
        if done:
            status = raw
            break
        time.sleep(0.02)
    if status is None:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        thread.join(timeout=hold_s + 10)
        return "HUNG", None
    thread.join(timeout=hold_s + 10)
    return (("COMPLETED" if os.waitstatus_to_exitcode(status) == 0
             else "CHILD-ERROR"), os.waitstatus_to_exitcode(status))


def test_a_forked_child_can_take_a_runner_lock_another_thread_held(
        live_registry):
    """The subject: the child of a fork taken while a NON-surviving thread held
    a registered runner's lock must still be able to take that lock."""
    runner = _FakeRunner()
    live_registry.clear()
    live_registry["row639-site"] = runner
    assert "row639-site" in live_registry, (
        "precondition: the runner is not in the live registry, so the child "
        "hook would have nothing to reach")

    def child():                                    # pragma: no cover - child
        # Fresh attribute lookup: the hook REBINDS the attribute, so a lock
        # captured before the fork would be the wrong object to ask.
        if not runner._lock.acquire(blocking=False):
            return 5                        # inherited LOCKED by a dead thread
        runner._lock.release()
        return 0

    verdict, exitcode = _fork_while_held(runner._lock, child)
    assert verdict == "COMPLETED", (
        "a child forked while another thread held a runner's _lock could not "
        "take that lock: verdict=%s exitcode=%r. exitcode 5 means the child "
        "inherited the lock LOCKED with no thread alive to release it; HUNG "
        "means it blocked forever, which is the 'collected_shards' shortfall "
        "in tests/test_secret_display_never.py" % (verdict, exitcode))


def test_a_lock_of_the_same_construction_outside_the_registry_still_hangs():
    """Teeth, and the hook's reach in one.  An identical ``threading.Lock`` on
    a runner the registry does not hold is NOT covered, and must still hang its
    child -- otherwise the test above could pass because forking is harmless
    here, or because the detector cannot see a hang at all."""
    orphan = _FakeRunner()

    def child():                                    # pragma: no cover - child
        orphan._lock.acquire()          # inherited LOCKED: blocks forever
        orphan._lock.release()
        return 0

    verdict, exitcode = _fork_while_held(orphan._lock, child,
                                         deadline=_TEETH_DEADLINE_S)
    assert verdict == "HUNG", (
        "an unreachable inherited lock did NOT hang its child (verdict=%s "
        "exitcode=%r); the fork-hazard detector has no teeth, so the subject "
        "test proves nothing" % (verdict, exitcode))


# ── the exact shard count the row's acceptance names ──────────────────


def test_every_forked_shard_reports_back_while_a_runner_lock_churns(
        live_registry):
    """The acceptance: an exact shard count, not a smaller one.

    Sixteen children are forked while a background thread cycles a registered
    runner's ``_lock`` at roughly a 50% duty cycle, so the chance that NO fork
    is taken while the lock is held is about ``0.5 ** 16``.  Each child takes
    the lock and reports its shard id down a pipe.  A missing shard is named
    with its pid and its liveness rather than reported as a count, because a
    deadlocked child, a slow child and a child that died before writing lead to
    three different diagnoses.
    """
    runner = _FakeRunner()
    live_registry.clear()
    live_registry["row639-churn"] = runner

    stop = threading.Event()
    cycles = []

    def churn():
        while not stop.is_set():
            with runner._lock:
                cycles.append(1)
                time.sleep(0.004)
            time.sleep(0.004)

    churner = threading.Thread(target=churn, daemon=True,
                               name="row639-lock-churn")
    churner.start()
    time.sleep(0.05)
    assert cycles, (
        "precondition: the churn thread never took the lock, so no fork could "
        "have been taken while it was held")

    read_fd, write_fd = os.pipe()
    pids = {}
    try:
        for shard in range(_SHARDS):
            sys.stdout.flush()
            sys.stderr.flush()
            pid = os.fork()
            if pid == 0:                            # pragma: no cover - child
                code = 4
                try:
                    with runner._lock:
                        os.write(write_fd, b"%d\n" % shard)
                    code = 0
                except BaseException:
                    code = 4
                finally:
                    os._exit(code)
            pids[shard] = pid
            time.sleep(0.003)
        os.close(write_fd)
        write_fd = None

        # SELECT, NEVER A BARE READ.  Every child holds a copy of the write
        # end, so a fleet of BLOCKED children never closes the pipe and never
        # writes: a bare os.read() would then block past this deadline and hang
        # the run instead of reporting the shortfall it exists to report.
        deadline = time.monotonic() + _SHARD_DEADLINE_S
        buf = b""
        while buf.count(b"\n") < _SHARDS:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([read_fd], [], [], remaining)
            if not ready:
                break
            chunk = os.read(read_fd, 4096)
            if not chunk:
                break
            buf += chunk
        seen = {int(line) for line in buf.split() if line.strip()}
    finally:
        if write_fd is not None:
            os.close(write_fd)
        os.close(read_fd)
        stop.set()
        churner.join(timeout=5)
        missing_detail = []
        for shard, pid in pids.items():
            done, status = os.waitpid(pid, os.WNOHANG)
            if not done:
                missing_detail.append("shard %d pid %d ALIVE (blocked on the "
                                      "inherited lock)" % (shard, pid))
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            elif os.waitstatus_to_exitcode(status) != 0:
                missing_detail.append(
                    "shard %d pid %d exited %d" %
                    (shard, pid, os.waitstatus_to_exitcode(status)))

    assert len(cycles) >= 2, (
        "precondition: the lock churned only %d time(s) across %d forks, so "
        "this run did not test contention" % (len(cycles), _SHARDS))
    assert seen == set(range(_SHARDS)), (
        "the forked scan LOST %d of %d shards while a runner lock churned: "
        "missing %s. %s"
        % (_SHARDS - len(seen), _SHARDS,
           sorted(set(range(_SHARDS)) - seen),
           "; ".join(missing_detail) or "no child was still alive, so the "
           "loss was not a deadlock"))


def test_transform_control_imports_app_state_without_asserting_fork_safety():
    """The mutation transform control.  It touches the module and asserts
    nothing about the hook, so a mutant that keeps app_state importable must
    ESCAPE here -- which is what proves the catches above are assertion
    failures rather than import or collection errors."""
    from bulk_downloader import app_state
    assert app_state.runners is not None
