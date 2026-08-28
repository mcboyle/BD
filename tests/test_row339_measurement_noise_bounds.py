"""Row 339: release-verifier walls remain measured, subordinate, and live."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


# Row 339. "module", not "repo-wide": the subject here is tools/verify_release.py
# and its per-file measurement walls, not a population spanning the tree.
# Eight sibling gates already carry "module" for exactly this reason.
BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "tools"))
import verify_release as VR  # noqa: E402


def _timeout_result(relpath: str, timeout: bool = False) -> dict:
    return {
        "file": relpath,
        "rc": 124 if timeout else 0,
        "total": None if timeout else 1,
        "passed": None if timeout else 1,
        "failed": None if timeout else 0,
        "skipped": None if timeout else 0,
        "harness": False,
        "summary_parsed": not timeout,
        "timeout": timeout,
    }


def test_verify_release_uses_the_loaded_measurement_for_each_standard_file():
    """Drive the real full-scope selector and observe both runtime walls."""
    root = tempfile.mkdtemp(prefix="vr_budget_selection_")
    original = VR._run_one
    observed: list[tuple[str, float]] = []
    try:
        os.makedirs(os.path.join(root, "tests"))
        for name in ("test_perf_lab.py", "test_standard.py"):
            with open(os.path.join(root, "tests", name), "w") as fh:
                fh.write("def test_placeholder():\n    pass\n")

        def record(_root, relpath, timeout):
            observed.append((relpath, timeout))
            return _timeout_result(relpath)

        VR._run_one = record
        aggregate = VR.run_tests_criteria(root, "full", "3.66.0")
    finally:
        VR._run_one = original
        shutil.rmtree(root, ignore_errors=True)

    assert aggregate["files"] == len(observed) == 2
    assert observed == [
        ("tests/test_perf_lab.py", 60),
        ("tests/test_standard.py", 149),
    ], f"runtime-selected verify_release walls were {observed!r}"


def test_verify_release_timeout_still_fires_for_a_genuinely_hung_file():
    """The standard-file selector withdraws a real sleeping child.

    The measured 149s value is compressed to 0.2s for this adversarial control;
    the test above independently pins the production value passed at this same
    runtime seam.  Removing the timeout or bypassing the selector makes this
    child sleep for 60s rather than manufacture an immediate green result.
    """
    root = tempfile.mkdtemp(prefix="vr_hung_file_")
    original_timeout = VR._STANDARD_TEST_FILE_TIMEOUT_S
    try:
        os.makedirs(os.path.join(root, "tests"))
        runner = os.path.join(root, "run_tests.py")
        target = os.path.join(root, "tests", "test_hung.py")
        with open(runner, "w") as fh:
            fh.write("import time\ntime.sleep(60)\n")
        with open(target, "w") as fh:
            fh.write("def test_never_reached():\n    pass\n")
        assert os.path.isfile(runner) and os.path.isfile(target)

        VR._STANDARD_TEST_FILE_TIMEOUT_S = 0.2
        started = time.monotonic()
        aggregate = VR.run_tests_criteria(root, "full", "3.66.0")
        elapsed = time.monotonic() - started
    finally:
        VR._STANDARD_TEST_FILE_TIMEOUT_S = original_timeout
        shutil.rmtree(root, ignore_errors=True)

    assert elapsed >= 0.15, f"hung child did not reach its wall: {elapsed:.3f}s"
    assert elapsed < 5, f"hung child escaped its 0.2s wall: {elapsed:.3f}s"
    assert aggregate["files"] == 1
    assert aggregate["timeouts"] == ["tests/test_hung.py"]
    assert aggregate["results"] == [
        _timeout_result("tests/test_hung.py", timeout=True)
    ]


def test_verify_release_timeout_transform_control_imports_without_judging_wall():
    """Mutation transform control: module import is deliberately no verdict."""
    assert VR.__name__ == "verify_release"
