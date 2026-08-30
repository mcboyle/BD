"""P3-A — rate-limit auto-restart was dead code. After ``trigger_rate_limit``
sets ``self._stop`` and spawns ``_wait_rl_autostart``, the autostart thread
gated on ``if not self._stop.is_set()`` (runner.py L3894) — but ``_stop`` is set
by ``trigger_rate_limit`` and never cleared during cooldown, so the gate is
always False and ``start()`` is never called: a rate-limited site never
auto-resumes after its cooldown elapses.

Fix: a one-shot ``_rl_autostart`` flag (set in ``trigger_rate_limit``, cleared in
``stop()`` so an operator stop mid-cooldown cancels the resume); the autostart
path checks the flag and clears ``_stop`` before ``start()``.

Repro-first: tests 1 and 3 are proven RED on pristine v3.66.197. Runner
construction is avoided via the unbound-method-on-stub pattern (mirrors
test_bug_audit.py's F1 test) — no DB-backed SiteRunner needed.
"""
import queue as _queue
import threading
import time

from bulk_downloader.runner import SiteRunner


def test_p3a_autostart_fires_after_cooldown():
    """RED until P3-A: once the cooldown has elapsed, the site auto-resumes
    (start() is called and _stop is cleared). On pristine, _stop stays set and
    the gate suppresses the resume forever."""
    class _Stub:
        def __init__(self):
            self._rl_until = time.time() - 1      # cooldown already expired
            self._stop = threading.Event()
            self._stop.set()                       # as trigger_rate_limit leaves it
            self._rl_autostart = True              # set by trigger_rate_limit (fix)
            self._state = "rate_limited"
            self.started = []
        def start(self):
            self.started.append(True)

    stub = _Stub()
    SiteRunner._wait_rl_autostart(stub)
    assert stub.started == [True], (
        "rate-limited site did not auto-resume after cooldown — start() was "
        "never called (the _stop self-gate at runner.py L3894 is unreachable)"
    )
    assert not stub._stop.is_set(), "_stop was not cleared before resume"


def test_p3a_operator_stop_suppresses_autostart():
    """Guard: if the operator stopped the site during cooldown (which clears
    _rl_autostart), the cooldown elapsing must NOT silently re-resume it."""
    class _Stub:
        def __init__(self):
            self._rl_until = time.time() - 1
            self._stop = threading.Event()
            self._stop.set()
            self._rl_autostart = False             # operator stop cleared it
            self._state = "stopped"
            self.started = []
        def start(self):
            self.started.append(True)

    stub = _Stub()
    SiteRunner._wait_rl_autostart(stub)
    assert stub.started == [], (
        "auto-resume fired despite an operator stop during cooldown"
    )


def test_p3a_stop_clears_autostart_flag():
    """RED until P3-A: stop() must cancel a pending rate-limit autostart by
    clearing _rl_autostart. Exercises the real stop() against a minimal stub
    carrying only the attributes stop() touches."""
    class _Stub:
        def __init__(self):
            self._stop = threading.Event()
            self._pause = threading.Event()
            self._worker_threads = []
            self._url_queue = _queue.Queue()
            self._lock = threading.Lock()
            self.jobs = {}
            self._state = "rate_limited"
            self._rl_autostart = True              # a resume is pending
        def _stop_auto_retry(self):
            pass

    stub = _Stub()
    SiteRunner.stop(stub)
    assert stub._rl_autostart is False, (
        "stop() did not clear the pending rate-limit autostart flag — an "
        "operator stop during cooldown will be overridden by a stale resume"
    )


def test_stop_does_not_retry_timeout_aware_hook_after_internal_typeerror():
    """An exception raised inside teardown is not a signature mismatch."""
    class _Stub:
        def __init__(self):
            self._stop = threading.Event()
            self._pause = threading.Event()
            self._worker_threads = []
            self._url_queue = _queue.Queue()
            self._lock = threading.Lock()
            self.jobs = {}
            self._state = "running"
            self._rl_autostart = False
            self.auto_retry_calls = []

        def _stop_auto_retry(self, timeout=2.0):
            self.auto_retry_calls.append(timeout)
            raise TypeError("nested got unexpected keyword argument 'timeout'")

    stub = _Stub()
    SiteRunner.stop(stub)
    assert stub.auto_retry_calls == [0], (
        "stop retried a timeout-aware teardown hook after its body raised"
    )
