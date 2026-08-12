"""The killswitch's auto-cycle thread must not outlive the test that armed it.

WHAT THIS COST, measured 2026-08-12 during a fleet parity run at `c817321`.
Two of three hosts hung a full-suite run at ~99% and never returned. The logs
name the sequence exactly:

    [vpn-killswitch] fx_site: auto-cycling tunnel (attempt #1)
    [vpn-killswitch] fx_site: cycle FAILED, holding killed
    [vpn-runtime] kill switch tripped on fx_site; affected sites: []
    ..............[gw1] node down: Not properly terminated

`kill_tunnel()` schedules `_auto_cycle_worker` on a DAEMON thread that sleeps
`CYCLE_BACKOFF_S` (30s) and then calls the real `vpn.cycle_tunnel()` ->
`stop_tunnel()` + `start_tunnel()`. Thirty seconds is long enough that the test
which armed the killswitch has finished and an UNRELATED test is running on
that worker when the tunnel teardown fires. The worker dies, and xdist waits
for it forever.

THE HANG IS UNBOUNDED, AND NO EXISTING GUARD CAN CATCH IT. `--timeout` is
enforced by pytest-timeout INSIDE the worker process; when the worker itself
dies there is nothing left to fire it. Measured: load 0.00 with pytest still
resident, and no further output for 44 minutes. A run that hangs at 99% looks
like a slow suite, not like a defect, which is why this survived.

`BD_DISABLE_KEEPALIVE` is the shipped flag for precisely this class -- CLAUDE.md
describes it as stopping "background threads outliving a test run", every band
sets it, and `tests/conftest.py` sets it in an autouse fixture. It did not gate
this thread. That is the whole defect: a thread that is exactly what the flag
describes, not consulted by the flag.

WHY THIS WAS NEVER CAUGHT BY A TEST: it had none. At `c817321`, no file under
tests/ referenced `_schedule_auto_cycle`, `_auto_cycle_worker`, `auto_cycle` or
`cycle_attempts`. The path that kills xdist workers was entirely uncovered, so
there was no denominator in which its absence of a guard could show up.

BOTH DIRECTIONS ARE ASSERTED HERE, deliberately. A fix that simply deleted
auto-cycle would pass the gate below and destroy a real feature, so the control
proves the thread IS still scheduled when the flag is absent. CLAUDE.md section
6: closing a gap means proving RED with the defect and GREEN without it, and
asserting the over-sensitive direction in the same breath.
"""

from __future__ import annotations

import os
import threading

import pytest

from bulk_downloader import vpn_kill_switch as ks

_TUNNEL = "test_1050_tunnel"


@pytest.fixture(autouse=True)
def _isolate_killswitch():
    """Reset module state around every test.

    The killswitch keeps process-global dicts, so a leftover state entry makes
    `kill_tunnel` return early at its already-killed branch and the test below
    would assert over a call that never reached the scheduler -- passing
    because nothing happened, which is the failure mode this file is about.
    """
    ks._reset_for_tests()
    try:
        yield
    finally:
        ks._reset_for_tests()


@pytest.fixture
def scheduled(monkeypatch):
    """Replace the worker with a recorder BEFORE anything can schedule it.

    The real `_auto_cycle_worker` calls `vpn.cycle_tunnel()`, which stops and
    restarts a tunnel for real. A test that let it run would perform the very
    system operation this file exists to keep out of the suite -- so the
    recorder is not a convenience, it is the thing that makes these tests safe
    to run at all. `_schedule_auto_cycle` reads the module global as the
    thread's target, so patching the attribute is sufficient.
    """
    started: list[str] = []

    def _recorder(tunnel_id: str) -> None:
        started.append(tunnel_id)

    monkeypatch.setattr(ks, "_auto_cycle_worker", _recorder)
    return started


def _cycle_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate()
            if t.name.startswith("bd-killswitch-cycle-")]


def test_the_flag_is_actually_set_in_this_run(monkeypatch):
    """Denominator, asserted before the verdict.

    conftest.py sets BD_DISABLE_KEEPALIVE in an autouse fixture and every band
    exports it. If that ever stops being true, the gate below would pass
    because the flag path was never exercised -- a check reporting OK over a
    condition it never met.
    """
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    assert os.environ.get("BD_DISABLE_KEEPALIVE")


def test_no_auto_cycle_thread_is_scheduled_under_the_keepalive_flag(
    monkeypatch, scheduled
):
    """THE GATE. RED before the fix: the thread is scheduled regardless."""
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")

    ks.kill_tunnel(_TUNNEL, reason="1050 gate")

    for t in _cycle_threads():
        t.join(timeout=5)

    assert scheduled == [], (
        "kill_tunnel scheduled an auto-cycle worker while BD_DISABLE_KEEPALIVE "
        "was set. That thread sleeps CYCLE_BACKOFF_S=%d seconds and then "
        "performs a real tunnel stop/start, by which time an unrelated test "
        "owns this worker -- which is how a full-suite run dies at 99%% with "
        "'node down: Not properly terminated' and hangs forever."
        % ks.CYCLE_BACKOFF_S
    )
    assert _cycle_threads() == [], "a bd-killswitch-cycle thread is still alive"


def test_auto_cycle_is_still_scheduled_when_the_flag_is_absent(
    monkeypatch, scheduled
):
    """THE OVER-SENSITIVITY CONTROL, and it is the more important half.

    Without it, deleting auto-cycle outright would pass the gate above. That
    would be a fix reproducing the shape of the defect it repairs: silently
    removing a capability while a green test certifies the removal.

    POP the variable rather than merely not setting it -- conftest.py's autouse
    fixture sets it for every test in this run, so `refraining` from setting it
    would leave the inherited value in place and this control would assert over
    the flagged path twice.
    """
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    assert "BD_DISABLE_KEEPALIVE" not in os.environ

    ks.kill_tunnel(_TUNNEL, reason="1050 control")

    for t in _cycle_threads():
        t.join(timeout=5)

    assert scheduled == [_TUNNEL], (
        "with the flag absent the auto-cycle worker should still be scheduled "
        "-- production recovery is a real feature and this cut must not delete "
        f"it. recorded={scheduled!r}"
    )
