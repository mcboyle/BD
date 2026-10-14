"""O1670 R5 flaky isolation: the conftest runtime teardown must not report a
stoppable folder watcher as surviving because earlier steps spent the budget.

THE FLAKE: test_secret_display_never.py::
test_no_endpoint_echoes_a_stored_secret_value failed on 10.0.70.186 in three
farm runs (O1620 baseline e626d248, O1634 farm2 and farm3), always in teardown
and always with exactly "configured runtime teardown was not proved within
2s: global folder watcher survived; global folder watcher remained live", and
passed alone (S48-A x2, flaky-2 x24, S49-C x7+ on .82).

THE CAUSE: _reset_loaded_app_site_runtime spends ONE 2s deadline in order:
runner retirement first, then the global watcher is signalled AND joined with
whatever is left. The watcher exits within ~0.2 ms of its signal (measured,
S49-C probe on .186), but when runner retirement uses the budget under host
load the watcher is signalled with ~0 s left, join(0) returns while the
just-woken thread is still alive, and a healthy watcher is reported as a
survivor.

The test plants a runner whose retirement legitimately succeeds at the end of
its bound and drives the real conftest helper against the real app watcher.
"""
import time

import pytest

BD_GATE_SCOPE = "module"


class _RetiresAtItsBound:
    """A runner whose scheduler retirement succeeds only as its bound expires
    (a join on a thread that exits right at the deadline)."""

    def __init__(self):
        self.timeouts = []

    def retire_scheduler(self, timeout=12.0):
        self.timeouts.append(timeout)
        time.sleep(timeout)
        return True

    def retire_auto_retry(self, timeout=2.0):
        return True

    def retire_workers(self, timeout=5.0):
        return True


def test_runner_retirement_using_the_budget_does_not_starve_the_watcher_proof(
        monkeypatch):
    import conftest as fixture_module
    import bulk_downloader.app as A

    A._watcher_boot_context.reopen_allowed = True
    try:
        assert A._start_watcher() is True
    finally:
        del A._watcher_boot_context.reopen_allowed
    generation = A._watcher_thread
    assert generation is not None and generation.is_alive()

    runner = _RetiresAtItsBound()
    monkeypatch.setitem(A.runners, "o1670-r5-retires-at-bound", runner)
    try:
        try:
            fixture_module._reset_loaded_app_site_runtime()
        except pytest.fail.Exception as exc:
            pytest.fail(
                "O1670_R5_WATCHER_PROOF_STARVED: runner retirement used the "
                "shared 2s budget (timeouts %r) and the folder watcher, "
                "signalled only after it, was judged with ~0s left: %s"
                % (runner.timeouts, exc), pytrace=False)
        assert runner.timeouts and runner.timeouts[0] > 1.5, (
            "control: the planted retirement did not receive the budget "
            "(timeouts %r), so the starvation path was not exercised"
            % runner.timeouts)
        assert not generation.is_alive()
    finally:
        A._stop_watcher(timeout=2, retire=True)


def test_a_watcher_that_ignores_its_signal_is_still_reported_survived(
        monkeypatch):
    """Negative control: signalling early must not hide a real survivor."""
    import conftest as fixture_module
    import bulk_downloader.app as A

    def ignores_stop(stop_event):
        time.sleep(3.0)

    monkeypatch.setattr(A, "_watcher_loop", ignores_stop)
    A._watcher_boot_context.reopen_allowed = True
    try:
        assert A._start_watcher() is True
    finally:
        del A._watcher_boot_context.reopen_allowed
    generation = A._watcher_thread
    try:
        with pytest.raises(pytest.fail.Exception,
                           match="global folder watcher survived"):
            fixture_module._reset_loaded_app_site_runtime()
    finally:
        generation.join(timeout=5.0)
        A._stop_watcher(timeout=2, retire=True)
