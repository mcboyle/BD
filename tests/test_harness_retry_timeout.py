"""Harness guard -- the serial failure-retry must be TIMED and ISOLATED.

Observed live: a test file timed out at 900s in the parallel phase, then
_retry_failures_serial re-ran it IN-PROCESS via discover_and_run -- which has
no timeout -- so one pathologically slow test wedged the entire release-gate
run forever. The retry now goes through the same subprocess runner as the
parallel phase (_run_one_file_subprocess), which enforces the per-file wall
timeout (BD_TEST_FILE_TIMEOUT, default 900s) and full state isolation.

Runs the subprocess runner against a deliberately-sleeping test file with a
2-second timeout: it must return a TIMEOUT row in seconds, not hang.
"""
import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _load_run_tests(timeout_s):
    """Fresh-load the core with BD_TEST_FILE_TIMEOUT set for its constants."""
    os.environ["BD_TEST_FILE_TIMEOUT"] = str(timeout_s)
    spec = importlib.util.spec_from_file_location(
        f"run_tests_core_t{timeout_s}", _REPO / "run_tests_core.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_subprocess_runner_times_out_a_wedged_file():
    rt = _load_run_tests(2)
    assert rt._FILE_TIMEOUT_S == 2
    with tempfile.TemporaryDirectory() as td:
        sleepy = Path(td) / "test_wedge_sim.py"
        sleepy.write_text(
            "import time\n\ndef test_sleeps_forever():\n    time.sleep(30)\n")
        t0 = time.monotonic()
        fname, rows = rt._run_one_file_subprocess(sleepy)
        took = time.monotonic() - t0
    assert took < 15, f"runner did not enforce the timeout ({took:.1f}s)"
    assert fname == "test_wedge_sim.py"
    assert rows and rows[0][2] is False, rows
    assert "TIMEOUT" in (rows[0][1] or ""), rows


def test_serial_retry_uses_the_timed_subprocess_path():
    # Static guard: the retry must call _run_one_file_subprocess, never the
    # untimed in-process discover_and_run (the wedge regression).
    src = (_REPO / "run_tests_core.py").read_text(encoding="utf-8")
    start = src.index("def _retry_failures_serial")
    end = src.index("\ndef ", start + 10)
    body = src[start:end]
    assert "_run_one_file_subprocess(" in body, \
        "serial retry no longer uses the timed subprocess runner"
    assert "discover_and_run(tf)" not in body, \
        "serial retry regressed to the untimed in-process path"


def test_default_timeout_is_900():
    rt = _load_run_tests(900)
    assert rt._FILE_TIMEOUT_S == 900


if __name__ == "__main__":
    for k in [x for x in sorted(dict(globals())) if x.startswith("test_")]:
        try:
            globals()[k](); print(f"PASS  {k}")
        except AssertionError as e:
            print(f"FAIL  {k}: {e}")
        except Exception as e:
            print(f"ERROR {k}: {type(e).__name__}: {e}")
