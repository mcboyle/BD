"""Row mainred-A-eventloop: a leaked running event loop is attributed, not inherited.

THE DEFECT THIS PINS. At base 2294a21c the main-red gate carried 22 reds across
three files and every one of them passed under a single-node-id probe
(bd-persist/harness-work/HB-F51/MAINRED-CLASSIFY.md, cluster A). The cause was
an unstopped sync-Playwright session leaving its dispatcher loop in the thread's
running-loop slot; the tests scheduled behind it failed on a condition none of
them created.

WHAT A PASSING RUN OF THIS FILE PROVES, and what it deliberately does not:
  - the predicate the guard reads is the SAME one Playwright and `asyncio.run`
    read, demonstrated against a REAL `sync_playwright().start()` rather than a
    synthetic slot poke (`test_the_real_leak_shape_...`);
  - a leak is charged to the test that CAUSED it and the next test still runs
    clean (`test_a_leaked_loop_fails_the_leaker_...`);
  - a legitimately OWNED loop -- the module-scoped `with sync_playwright()` that
    `tests/test_row363_affordance_learning.py` uses -- is NOT reported
    (`test_an_outer_scoped_owner_is_not_a_leak`, and the nested-run twin). This
    is the control that a level-based check fails: measured on the cut band
    before this shape, a level check fired 59 times across 12 files, 50 of them
    inside that one blameless module.
It does NOT prove the suite is leak-free; that is what the in-band gate says.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _event_loop_guard  # noqa: E402

pytest_plugins = ["pytester"]

_GUARD_DIR = str(Path(_event_loop_guard.__file__).resolve().parent)

_NESTED_CONFTEST = """
import sys
sys.path.insert(0, %r)
import _event_loop_guard
# THE SAME fixture object the real suite installs, not a copy of it.
_bd_no_leaked_event_loop = _event_loop_guard.autouse_fixture
""" % _GUARD_DIR


def test_negative_control_an_unchanged_slot_reports_nothing():
    """The probe can say NO. Without this, every YES below is worthless."""
    assert _event_loop_guard.running_loop() is None
    assert _event_loop_guard.leak_message("t::a", None, None) is None


def test_an_outer_scoped_owner_is_not_a_leak():
    """A loop that was ALREADY running is somebody else's to stop.

    This is the case a level-based check gets wrong, and it is not hypothetical:
    tests/test_row363_affordance_learning.py owns one per module.
    """
    owner = asyncio.new_event_loop()
    try:
        assert _event_loop_guard.leak_message("t::a", owner, owner) is None
        # And an owner that tears down early is equally not this test's doing.
        assert _event_loop_guard.leak_message("t::a", owner, None) is None
    finally:
        owner.close()


def test_the_probe_can_say_yes_and_names_the_test_that_did_it():
    """Positive control, paired with the two negative ones above."""
    appeared = asyncio.new_event_loop()
    inherited = asyncio.new_event_loop()
    try:
        fresh = _event_loop_guard.leak_message("t::culprit", None, appeared)
        assert fresh is not None and "t::culprit" in fresh
        swapped = _event_loop_guard.leak_message("t::culprit", inherited, appeared)
        assert swapped is not None and "t::culprit" in swapped
        assert "replaced the running event loop it inherited" in swapped
    finally:
        appeared.close()
        inherited.close()


def test_the_real_leak_shape_is_what_the_guard_reads():
    """Drive a REAL sync-Playwright session, not a synthetic slot poke.

    FLEET_RULE 46: a browser test fails closed here -- no skip when the browser
    is missing; the runtime this repo pins ships playwright + chromium.
    """
    from playwright.sync_api import sync_playwright

    assert _event_loop_guard.running_loop() is None
    pw = sync_playwright().start()
    try:
        # THE MECHANISM, measured: a live sync session puts its dispatcher loop
        # in this thread's running-loop slot, which is why `asyncio.run` and a
        # second `sync_playwright()` both refuse while it is open.
        leaked = _event_loop_guard.running_loop()
        assert leaked is not None
        assert leaked.is_running() is True
        assert _event_loop_guard.leak_message("t::x", None, leaked) is not None
        with pytest.raises(RuntimeError, match="cannot be called from a running event loop"):
            asyncio.run(asyncio.sleep(0))
    finally:
        pw.stop()
    # ... and a session that IS stopped leaves nothing behind. This is the half
    # that makes the assertion above a defect report rather than a description.
    assert _event_loop_guard.running_loop() is None


def test_a_leaked_loop_fails_the_leaker_and_spares_the_next_test(pytester):
    """End to end, in a nested run: attribution AND restored isolation."""
    pytester.makeconftest(_NESTED_CONFTEST)
    pytester.makepyfile(test_leak="""
        import asyncio

        def test_leaks_a_running_loop():
            loop = asyncio.new_event_loop()
            asyncio.events._set_running_loop(loop)   # never cleared: the defect

        def test_runs_after_the_leak():
            # Would have died with "asyncio.run() cannot be called from a
            # running event loop" if the guard had not restored the slot.
            asyncio.run(asyncio.sleep(0))
    """)
    result = pytester.runpytest_subprocess("-p", "no:randomly", "-q")
    # The leaker's BODY passes and its TEARDOWN errors, because the guard is a
    # teardown-side fixture (see _event_loop_guard.autouse_fixture for why). An
    # ERROR is as red as a FAILURE and carries the same nodeid, which is the
    # property this row needs.
    result.assert_outcomes(passed=2, errors=1)
    assert result.ret != 0, "a leak must make the run non-zero"
    text = result.stdout.str()
    assert "test_leaks_a_running_loop" in text
    assert "left a running event loop" in text
    assert "sync_playwright().start()" in text


def test_negative_control_a_module_scoped_owner_runs_all_green(pytester):
    """The shape test_row363_affordance_learning.py uses must stay silent.

    A module-scoped `with sync_playwright()` means EVERY test in the file runs
    with a loop installed. Under a level-based check every one of them is a
    false positive; under the delta this run is green.
    """
    pytester.makeconftest(_NESTED_CONFTEST)
    pytester.makepyfile(test_owned="""
        import asyncio
        import pytest

        @pytest.fixture(scope="module", autouse=True)
        def owned_loop():
            loop = asyncio.new_event_loop()
            asyncio.events._set_running_loop(loop)
            yield
            asyncio.events._set_running_loop(None)
            loop.close()

        def test_one():
            assert asyncio.get_running_loop() is not None

        def test_two():
            assert asyncio.get_running_loop() is not None
    """)
    result = pytester.runpytest_subprocess("-p", "no:randomly", "-q")
    result.assert_outcomes(passed=2, errors=0, failed=0)


def test_negative_control_a_clean_nested_run_is_all_green(pytester):
    """The nested harness itself does not manufacture failures."""
    pytester.makeconftest(_NESTED_CONFTEST)
    pytester.makepyfile(test_clean="""
        import asyncio

        def test_one():
            asyncio.run(asyncio.sleep(0))

        def test_two():
            assert True
    """)
    result = pytester.runpytest_subprocess("-p", "no:randomly", "-q")
    result.assert_outcomes(passed=2, failed=0)
