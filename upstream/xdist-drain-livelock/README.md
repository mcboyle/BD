# pytest-xdist: a worker that hard-exits during the drain livelocks the master

**STATUS: NOT FILED.** Prepared for upstream, held locally by operator decision
on 2026-08-14. Nothing here has been posted publicly.

Versions: pytest 9.1.1 / pytest-xdist 3.8.0 / execnet 2.1.2 / CPython 3.12.3 / Linux.

## Symptom

With `--dist loadfile`, a worker that dies via `os._exit()` while the session is
draining leaves the master spinning forever:

    [gw5] node down: Not properly terminated

and then nothing. No timeout, no diagnostic, no exit. The master is not blocked
on a lock or a dead pipe -- it is awake, polling its event queue every 2s:

    Thread (idle): "MainThread"
        wait (threading.py:359)
        get (queue.py:180)              # block=True, timeout=2
        loop_once (xdist/dsession.py:154)
        pytest_runtestloop (xdist/dsession.py:138)

`os._exit()` is not exotic: **pytest-timeout's `--timeout-method=thread` calls it
from its timer thread** (pytest_timeout.py:542) whenever a test exceeds the
timeout. Any suite combining `--dist loadfile` with `--timeout-method=thread`
can hit this, and the resulting hang has no built-in bound.

## Reproducer

    PYTHONPATH=. pytest . -n 2 --dist loadfile -p no:randomly \
        -o python_files="repro_*.py"

Measured 5/5 hangs. Control -- identical tree with `os._exit(1)` replaced by
`pass` -- passes 5/5 in ~4s. Does NOT reproduce at `-n 4`, with
`--max-worker-restart=0`, or with `--dist load`; it needs loadfile and this
worker/file ratio.

Add `-p bd_diag` for a plugin that traces the scheduler and, after 20s with no
progress, dumps the deadlock state (no debugger needed).

## Mechanism

`LoadScopeScheduling.tests_finished` treats a unit with fewer than TWO pending
tests as finished:

    for assigned_unit in self.assigned_work.values():
        if self._pending_of(assigned_unit) >= 2:      # >= 2, not > 0
            return False
    return True

So once every node is down to its last test, the session declares itself
finished and `triggershutdown()` latches every worker via
`WorkerController.shutdown()` -- **while those last tests are still running**.
The latch is `shutting_down = self._down or self._shutdown_sent`: set once,
never cleared.

If a worker then dies on its final test, `worker_errordown` re-queues its work
and clones a replacement, but `_reschedule` refuses every latched node:

    # xdist/scheduler/loadscope.py:319
    # Do not add more work to a node shutting down
    if node.shutting_down:
        return

The re-queued units therefore cannot be dispatched. `has_pending` stays True, so
`tests_finished` stays False, so `triggershutdown()` -- reachable only AFTER
`queue.get()` returns an event -- is never called again. And the clone keeps
`_active_nodes` non-empty, disarming `loop_once`'s only other exit:

    if not self._active_nodes:
        self.triggershutdown()
        raise RuntimeError("Unexpectedly no active workers available")

Both exits are closed and no further event can arrive. Observed state from the
reproducer's watchdog:

    DSession.shuttingdown    = False
    DSession.session_finished= False
    _active_nodes            = ['gw2']
    sched.tests_finished     = False
    sched.has_pending        = True
    sched.workqueue          = 1 unit: ['test_filler_2.py']
    node gw2  latch=False units=['test_aaa_crasher.py'] pending=1

and from a real 48-worker run wedged for 11.6 hours, read live with gdb:

    _active_nodes            = ['gw48']
    sched.workqueue          = 8 units, undispatched
    sched.tests_finished     = False
    sched.has_pending        = True
    assigned_work            = {gw48: {one unit: 97 tests, 0 pending}}

## Not fully explained

In the reproducer the clone holds an **unexecuted** assignment (`pending=1`);
in the production wedge the clone had **completed** its unit (`pending=0`) with
8 units still queued. Same opening -- work re-queued after the drain has begun,
with no node left able to receive it -- but the endings differ, and this writeup
does not claim they are one path.

## Suggested directions (not a tested patch)

1. `loop_once` has no way out that does not depend on an event arriving. A
   liveness check -- all active nodes idle, work pending, nothing in flight --
   could raise or shut down rather than spin silently forever.
2. `worker_errordown` resets the session's `shuttingdown` to False but cannot
   clear the per-node latch, so the session un-drains while its workers do not.
   Either the reset should be accompanied by re-arming eligible nodes, or
   re-queued work should be recognised as requiring a fresh node.
3. The `>= 2` heuristic in `tests_finished` declares completion while tests are
   still running, which is what makes the latch land at the wrong moment.

## A note on observability, which cost this investigation a night

`-q` sets `verbose == -1`, and `DSession.report_line` (dsession.py:78) is guarded
on `verbose >= 0`. Under `-q` the entire recovery narration -- "replacing crashed
worker gwN", "maximum crashed workers reached" -- is silently dropped, while
`pytest_testnodedown` writes "node down" unguarded. The user is shown the symptom
and denied the response. Measured on this reproducer: `-q` -> 0 replace lines,
no flag -> 8, `-v` -> 8.

Separately, a master that never exits never flushes stdout. Across 657 captured
runs: 15/15 wedged logs ended MID-LINE at a 4KB boundary; 642/642 completed logs
ended cleanly. The stranded tail is exactly where the recovery narration lives.

## Why the files are named `repro_*.py`

They are deliberately OUTSIDE pytest's default `test_*.py` discovery pattern,
because one of them calls `os._exit(1)` and this directory lives inside a larger
repository whose gates enumerate the tree. `-o python_files="repro_*.py"` makes
them collectable on purpose and only on purpose. Rename them to `test_*.py` if
you drop them into a scratch directory of their own.
