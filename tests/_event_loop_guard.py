"""Catch a test that leaves a RUNNING EVENT LOOP installed in its thread.

MAIN-RED, 2026-09-21. The gate at 2294a21c carried 22 reds across three files,
and every one of them PASSED WHEN RUN ALONE (the classification and the per-id
probes are in bd-persist/harness-work/HB-F51/MAINRED-CLASSIFY.md, cluster A).
Two symptom shapes appeared:

    playwright._impl._errors.Error: It looks like you are using Playwright Sync
        API inside the asyncio loop.
    RuntimeError: asyncio.run() cannot be called from a running event loop

They are ONE fault with ONE predicate. Playwright's sync entry point
(playwright/sync_api/_context_manager.py, `PlaywrightContextManager.__enter__`)
reads `asyncio.get_running_loop()` and refuses if that loop `is_running()`;
`asyncio.run` refuses when `asyncio.events._get_running_loop()` is not None.
Both read the SAME per-thread slot. Sync Playwright pumps its dispatcher loop on
a greenlet, and greenlets share the thread, so a session that is started and
never stopped leaves its loop in that slot after the test that started it has
finished. Every later test in that xdist worker then reads a running loop it
never created. 21 of the 22 reds named a test that did nothing wrong.

THE PREDICATE IS A DELTA, NOT A LEVEL, and that distinction is the whole design.
"A loop is running during this test" is NOT a defect: `tests/test_row363_
affordance_learning.py` holds one correctly for its whole module through
`@pytest.fixture(scope="module")` + `with sync_playwright() as pw:`, and a
`setup_method` may own one for a class. Measured 2026-09-21 on the cut band at
tree 85bbcf2e's predecessor: a level check fired 59 times across 12 files, 50 of
them inside that one blameless module. What IS a defect is a loop that APPEARS
during a test and is still there when it ends -- nobody outside the test owns it,
so nobody is going to stop it. So this compares the slot before the test body
with the slot after it:

    before None,  after a loop      -> LEAK, fail the test that did it
    before loop L, after loop L     -> an outer-scoped owner; say nothing
    before loop L, after loop M     -> LEAK, the owner's loop was replaced
    before loop L, after None       -> the owner tore down early; not our business

WHY A RUNTIME DETECTOR AND NOT A GREP GATE. Whether a leak is visible is
SCHEDULE-DEPENDENT: `--dist loadfile` decides which file follows which, so the
same tree is green or red depending on worker assignment. That is why the reds
did not reproduce under a single-node-id probe, and why a static audit of
`sync_playwright().start()` call sites cannot answer the question -- a start site
with no visible `.stop()` may be stopped by its caller, and one that looks paired
may still skip the stop on an exception path.

WHAT IT DOES ON A HIT. It FAILS THE TEST THAT CAUSED IT, by name, and then
restores the slot to what that test inherited. The restore is not a suppression:
the culprit is already red and no path here turns a red green. It exists so the
NEXT test is judged on its own behaviour instead of inheriting the fault, which
is the difference between a run that reports one defect and a run that reports
twenty-two. FLEET_RULE 46's "fail closed, never skip" is the same principle: a
broken precondition must be loud and attributable, never silent.

WHAT THIS CANNOT SEE, per CLAUDE.md section 1's rule that an instrument
publishes its blind spots:
  - it reads only the CALLING thread's slot. A loop left running in a helper
    thread is invisible here, and so is the harm it does there.
  - it cannot see a leak from a MODULE- or SESSION-scoped finaliser, because
    those run outside any test's window; the delta for every test in such a
    module is zero and the leak surfaces only in the next module.
  - it fires on the LEAK, not on the harm. A leaked loop that no later test
    touches is still reported; that is deliberate, because whether it is touched
    is the scheduler's choice and not a property of the tree.
"""
from __future__ import annotations

import asyncio

import pytest


def running_loop():
    """The loop asyncio believes is running in THIS thread, or None.

    Deliberately the same read Playwright's own guard performs, so the detector
    can never disagree with the error it exists to attribute.
    """
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def restore(loop):
    """Put the slot back to `loop` (None for "nothing running").

    `asyncio.events._set_running_loop` is private and there is no public way to
    undo another test's leak. The alternative is to let one unstopped session
    decide the verdict of every test scheduled behind it.
    """
    asyncio.events._set_running_loop(loop)


def leak_message(nodeid, before, after):
    """Message for a test that changed the running-loop slot, else None."""
    if after is before:
        return None
    if after is None:
        # An outer-scoped owner finished early. Not this test's doing.
        return None
    if before is None:
        return (
            "%s left a running event loop installed in this thread (%r) that "
            "was not there when it started. The usual cause is "
            "`sync_playwright().start()` without a matching `.stop()` on every "
            "path: the dispatcher loop stays in the thread's running-loop slot "
            "and poisons every later test in this xdist worker -- sync "
            "Playwright and asyncio.run both refuse to start while it is set. "
            "Stop the session in a `finally`, or use "
            "`with sync_playwright() as pw:`." % (nodeid, after))
    return (
        "%s replaced the running event loop it inherited (%r) with a different "
        "one (%r) and left it installed. Whoever owns the first loop will not "
        "stop the second, so it poisons every later test in this xdist worker."
        % (nodeid, before, after))


# The fixture lives HERE, not in conftest, so the regression test can install
# the REAL one in a nested pytest run instead of a hand-copied lookalike. A
# guard proved only against a copy of itself is not proved at all.
@pytest.fixture(autouse=True)
def autouse_fixture(request):
    """Fail the test that leaks a running event loop, by name.

    Teardown-side rather than a `pytest_runtest_call` wrapper so a leak from a
    function-scoped fixture's own teardown is caught too: those finalisers run
    inside this fixture's window and a call-phase wrapper has already returned.
    """
    before = running_loop()
    yield
    after = running_loop()
    message = leak_message(request.node.nodeid, before, after)
    if message is not None:
        restore(before)
        pytest.fail(message, pytrace=False)
