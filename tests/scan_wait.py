"""Wait for a library scan to CONVERGE -- or fail saying that it did not.

WHY THIS EXISTS. Eight copies of this loop lived across three test files:

    for _ in range(30):
        if not lib.scan_status().get("running"):
            break
        time.sleep(0.05)

It has two exits and the code after it cannot tell them apart: the scan
finished, or the budget -- thirty polls of 50ms, so 1.5 SECONDS -- ran out
while the scan was still going. The caller asserts on the snapshot either way.

MEASURED on the operator's box 2026-08-07 (capture at 48707ad, v3.66.932),
under a parallel lane wide enough to reach gw38: `test_scanner_idempotency`
failed with `assert 4 == 5`, one file short of a scan that had not finished.
In a quiet container the same scan takes 0.098s over two polls -- fifteen times
the headroom -- which is why the defect is invisible everywhere except the
machine that gates the release.

That is CLAUDE.md section 0 in a test harness: the OK branch swallows the state
it cannot see. Unknown is a third state, and it fails.

THREE THINGS THIS GETS RIGHT THAT THE HAND-ROLLED LOOP DID NOT. Each was
measured against bulk_downloader/library.py, not reasoned about.

1. IT WAITS ON `finished_at`, NOT ON `running`. `to_dict` computes
   `"running": self.finished_at is None and not self.cancelled`, so
   `scan_cancel()` flips `running` to False while the worker thread is still
   walking and still mutating the counters. Measured: `seen` climbed 70 -> 190
   with `running == False`, and reached 4000 by the time `finished_at` was set.
   `finished_at` is assigned in a `finally:` on every exit path, so it is the
   only field that means the worker is gone.

2. IT CHECKS THAT THE SCAN STARTED, AND THAT THE ONE THAT FINISHED IS THE ONE
   IT STARTED. `scan_start` RETURNS `{"ok": False, ...}` rather than raising --
   for an unusable root and for "scan already running" -- and leaves
   `_scan_state` untouched, so the previous scan's terminal counters stay in
   place and a wait returns instantly on them. Measured:

       finished small scan: added=3 seen=3
       scan_start(bad root) -> {'ok': False, 'error': 'no valid roots provided'}
       status after refusal: running=False added=3 seen=3   <- stale, and final

   That is what turns a truncated wait into a CASCADE: the first wait exhausts,
   the second `scan_start` is refused because the first scan is still running,
   the second wait polls the FIRST scan, and the assertion is made about a scan
   that never ran. So `start_and_wait` calls `scan_start` itself -- a wait-only
   helper leaves the discarded return exactly where it was -- and pins
   `started_at` across the wait.

3. IT REFUSES TO START ON TOP OF A LIVE WORKER. `scan_start` refuses only while
   `finished_at is None and not cancelled` (library.py), so after
   `scan_cancel()` a NEW scan is ACCEPTED while the cancelled worker is still
   alive -- and that worker's `_mut` writes into the NEW ScanState. `started_at`
   alone cannot catch that, because the new state legitimately carries the new
   stamp while a foreign thread corrupts its counters. This is a PRODUCT defect,
   recorded rather than fixed here; no test in the suite calls `scan_cancel`,
   but it is reachable from the library scan route. The helper refuses instead
   of racing.

WHAT IT DELIBERATELY DOES NOT ASSERT. `errors == 0` is not evidence of a clean
run: `_scan_worker`'s outer `try:` has no `except`, only a `finally:` that sets
`finished_at`, so a worker that dies on an uncaught exception leaves
`errors == 0` and `error_samples == []` with partial counts. Measured, a
crashed worker and a clean one are indistinguishable by any exposed field. An
errors assertion therefore buys less than it appears to, and would break a
future test that deliberately induces record failures -- so errors are always
DUMPED in the failure message, and `require_clean=True` is there for the caller
that wants the assertion anyway.
"""
from __future__ import annotations

import time
from typing import Any, Iterable

# Generous on purpose. The budget exists to bound a HANG, not to measure
# performance: a scan that needs eleven seconds on a loaded box is slow, not
# broken, and failing it would be a gate firing on identity rather than content
# -- which CLAUDE.md section 0 counts as a soundness bug equal to a false clean.
# A caller that genuinely wants to assert on timing should assert on `elapsed_s`
# in the returned status.
DEFAULT_TIMEOUT_S = 30.0
_POLL_S = 0.02

_REPORT_FIELDS = ("running", "cancelled", "never_run", "started_at",
                  "finished_at", "current_root", "roots", "seen", "added",
                  "updated", "missing_marked", "errors", "error_samples",
                  "elapsed_s")


def _describe(st: dict) -> str:
    """Every field the caller might have been about to assert on.

    Not a fixed-width slice of anything: `test_source_windows_do_not_shift`
    ratchets those, and a helper that added one would cost the next cut a
    baseline bump for a debug string.
    """
    return "\n".join(f"    {k} = {st[k]!r}" for k in _REPORT_FIELDS if k in st)


def _converged(st: dict) -> bool:
    """The worker thread has left. Deliberately NOT `not st["running"]`."""
    return st.get("finished_at") is not None


def wait_for_scan(lib: Any, *, timeout: float = DEFAULT_TIMEOUT_S,
                  started_at: float | None = None,
                  require_clean: bool = False) -> dict:
    """Block until the library scan's worker has finished. Return its status.

    Raises AssertionError -- naming the whole scan state -- if the budget
    expires with the worker still running, if no scan was ever started, or if
    `started_at` is given and the converged status belongs to another run.

    `time.monotonic` rather than `time.time`: the box runs NTP, and a step
    adjustment mid-wait must not turn a converging scan into a failure or a
    hung one into a pass.
    """
    deadline = time.monotonic() + timeout
    while True:
        st = lib.scan_status()
        if st.get("never_run"):
            raise AssertionError(
                "wait_for_scan: no scan has ever been started in this process "
                "-- scan_status() reports never_run. scan_start returns "
                "{'ok': False} instead of raising when every root is "
                "rejected; use start_and_wait, which checks that.")
        if _converged(st):
            if started_at is not None and st.get("started_at") != started_at:
                raise AssertionError(
                    f"wait_for_scan: the converged status belongs to a "
                    f"DIFFERENT run (started_at {st.get('started_at')!r}, "
                    f"expected {started_at!r}) -- the scan being waited on was "
                    f"never started, and these counters are an earlier "
                    f"scan's:\n{_describe(st)}")
            if require_clean and st.get("errors"):
                raise AssertionError(
                    f"wait_for_scan: the scan finished with {st['errors']} "
                    f"error(s):\n{_describe(st)}")
            return st
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"wait_for_scan: the scan did NOT converge within {timeout}s "
                f"-- finished_at is still unset, so the worker thread is still "
                f"walking. Asserting on the counters below would be asserting "
                f"on a mid-scan snapshot:\n{_describe(st)}")
        time.sleep(_POLL_S)


def start_and_wait(lib: Any, roots: Iterable[str], *,
                   timeout: float = DEFAULT_TIMEOUT_S,
                   require_clean: bool = False) -> dict:
    """`lib.scan_start(roots)` + `wait_for_scan`, with BOTH ends checked.

    Prefer this over `wait_for_scan` at every call site that starts a scan: the
    wait-only form cannot see a refused start, and a refused start is the
    failure that cascades.
    """
    prior = lib.scan_status()
    if not prior.get("never_run") and not _converged(prior):
        raise AssertionError(
            "start_and_wait: a previous scan's worker has not finished "
            "(finished_at is unset), so starting another one now is unsafe. "
            "scan_start only refuses while `finished_at is None and not "
            "cancelled`, so after scan_cancel() it ACCEPTS a new scan while "
            "the cancelled worker is still walking -- and that worker's "
            "counter writes land in the NEW ScanState. Wait for the previous "
            f"scan first:\n{_describe(prior)}")

    started = lib.scan_start(list(roots))
    if not (isinstance(started, dict) and started.get("ok")):
        raise AssertionError(
            f"start_and_wait: scan_start refused the request and returned "
            f"{started!r}. It returns a dict rather than raising, so an "
            f"unchecked call leaves the PREVIOUS scan's terminal state in "
            f"place and every assertion that follows measures the wrong run.")

    mine = lib.scan_status().get("started_at")
    return wait_for_scan(lib, timeout=timeout, started_at=mine,
                         require_clean=require_clean)
