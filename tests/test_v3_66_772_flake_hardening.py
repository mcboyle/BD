"""v3.66.772 -- flake hardening for the three low-pri parallel-load flakes.

#17 test_v3_66_13_phase2_p2_snapshot_replay::test_replay_tool_round_trips runs the
    deep_detect corpus replay; it flaked under parallel load (deep_detect state), so
    it is pinned to the serial lane (run first, no contention) -- the established fix
    (cf. FLAKE-729 @754c).
#18 test_v3_66_217_admission_f1a::test_retry_unchanged_when_in_window used
    time.time()+600, which flaked when it landed in the 23:59 window tail; now a
    fixed noon timestamp.
#19 test_session_keeper::test_get_takeover_lock_is_reentrant used the "wow"/0 key a
    keep-alive background thread can hold; now a unique key nothing else touches.

RED before the fixes: the replay file is absent from _PINNED_TOGETHER, and #18/#19
still carry the flaky inputs. GREEN after.

run_tests.py conventions: zero-arg test functions; repo root from __file__.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_replay_file_is_serial_pinned():
    """#17 must run in the serial lane so deep_detect has no parallel contention."""
    sys.path.insert(0, str(REPO))
    import run_tests as RT  # noqa: E402
    assert "test_v3_66_13_phase2_p2_snapshot_replay.py" in RT._PINNED_TOGETHER, (
        "the deep_detect replay file is not pinned to the serial lane; it flakes "
        "under parallel load")


def test_admission_window_test_uses_a_fixed_timestamp():
    """#18 must not reintroduce time.time() (the midnight-boundary flake)."""
    src = (REPO / "tests" / "test_v3_66_217_admission_f1a.py").read_text(encoding="utf-8")
    # the in-window unchanged test must key off a fixed datetime, not wall-clock
    fn = src.split("def test_retry_unchanged_when_in_window")[1].split("def ")[0]
    assert "= time.time()" not in fn, (
        "test_retry_unchanged_when_in_window assigns wall-clock time again -- it "
        "will flake near the 23:59 window boundary")
    assert "datetime(" in fn, "the test should build a fixed datetime timestamp"


def test_reentrant_lock_test_uses_a_unique_key():
    """#19 must not reintroduce the shared 'wow'/0 key a keep-alive thread can hold."""
    src = (REPO / "tests" / "test_session_keeper.py").read_text(encoding="utf-8")
    fn = src.split("def test_get_takeover_lock_is_reentrant")[1].split("def ")[0]
    assert 'get_takeover_lock("wow", 0)' not in fn, (
        "the reentrant test reuses the contended 'wow'/0 lock key")
    assert "__reentrant_pin__" in fn, "the reentrant test should use its own unique key"
