"""The crashing unit. Its LAST test hard-exits.

LoadScopeScheduling.tests_finished treats a unit with fewer than TWO pending
tests as finished (loadscope.py: `if self._pending_of(assigned_unit) >= 2`).
So once this file is down to its final test, the SESSION declares itself
finished and triggershutdown() latches every node via WorkerController.shutdown()
-- while that final test is still executing. node.shutting_down is
`self._down or self._shutdown_sent`, set-once and never cleared.

Then this test dies. worker_errordown re-queues the unit's remaining work and
clones a replacement, but every node -- including the clone, which gets latched
the same way -- is refused work by _reschedule's first guard:

    if node.shutting_down:
        return

so the re-queued work can never be dispatched, tests_finished stays False
(has_pending is True), _active_nodes is non-empty, and DSession.loop_once spins
on queue.get(timeout=2) forever.
"""
import os
import time


def test_filler_one():
    time.sleep(0.1)


def test_the_last_one_dies():
    # By now the other file is done and this unit has exactly 1 pending test,
    # so tests_finished has already gone True and latched everybody.
    time.sleep(3.0)
    os._exit(1)          # what pytest-timeout's thread method does at --timeout
