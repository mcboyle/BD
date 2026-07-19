"""T55 — live-test harness per-check hard timeout.

Bug (pre-v3.63.2):
  `harness.run_all()` called each check synchronously inside a try
  block. A check that wedged (e.g. L3 on a Chromium build without
  MV3 service worker support) blocked the entire run forever — no
  timeout, no progress, no exit code. Confirmed twice on the v3.63.1
  fresh-machine capture: the harness hung on L3 indefinitely.

Fix (T55):
  - Add `harness._run_with_timeout(fn, ctx, timeout_s)` that runs
    the check in a daemon thread and waits up to `timeout_s` for
    it to finish. Returns one of:
      ("done", (level, detail))    — fn returned normally
      ("exc", "<repr>")            — fn raised
      ("timeout", "<elapsed>s")    — fn did not return in time
  - `run_all()` accepts a `per_check_timeout_s` kwarg (default 60s
    via `harness.DEFAULT_PER_CHECK_TIMEOUT_S`) and routes each
    check through the helper. Timeouts record FAIL+TIMEOUT and
    continue with the next check.
  - `live_tests/run.py` exposes `--per-check-timeout`.

Wedged thread is intentionally LEAKED rather than killed: Python
has no safe way to terminate a thread holding native resources
(Playwright, ffmpeg). The thread is a daemon, so it dies with the
process at run-end. Letting the harness continue is strictly
better than wedging the whole run.

Test strategy:
  - `_run_with_timeout` returns ("done", ...) for a fast function.
  - `_run_with_timeout` returns ("exc", ...) for a raising function.
  - `_run_with_timeout` returns ("timeout", ...) when the function
    sleeps past the limit — uses a short timeout (0.3s vs 2s sleep)
    so the test itself stays fast.
  - End-to-end: register a wedging check, run `run_all()` with a
    short timeout, assert the resulting SUMMARY.txt records FAIL
    with TIMEOUT and the run completed (didn't hang).
  - CLI exposes `--per-check-timeout`.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from live_tests import harness  # noqa: E402
# Importing `live_tests.checks` is what registers the real L1..L35
# checks via @live_test decorators. We need that population BEFORE
# any test in this file snapshots/restores `harness._REGISTRY` —
# otherwise the snapshot is empty, the restore clears the registry
# for the rest of the run, and subsequent test files that rely on
# the registry being populated (test_u32..u43, test_t46) fail
# spuriously because Python's import cache means a later
# `import live_tests.checks` is a no-op and the decorators don't
# re-fire.
from live_tests import checks  # noqa: E402, F401


# T56-style guard: snapshot the real registry at module import,
# restore it around EVERY test in this file via an autouse fixture.
# The runner's autouse discovery requires a non-underscore name
# (line ~393 of run_tests.py: `not name.startswith("_")`).
_REGISTRY_SNAPSHOT = list(harness._REGISTRY)


@pytest.fixture(autouse=True)
def autouse_restore_live_registry():
    """Restore the registry to its real-checks state after every
    test in this file, regardless of what the test did to it.
    This prevents the T55 wedger/quick fakes from leaking into
    other test files in the same batch."""
    yield
    harness._REGISTRY[:] = _REGISTRY_SNAPSHOT


# ── Module surface ──────────────────────────────────────────────────

def test_default_timeout_constant_exists_and_is_reasonable():
    """The default must be at least 30s (slowest legitimate check
    has headroom) and at most 600s (a wedge mustn't drag forever)."""
    t = harness.DEFAULT_PER_CHECK_TIMEOUT_S
    assert isinstance(t, (int, float))
    assert 30.0 <= t <= 600.0, (
        f"default per-check timeout {t}s out of sane range")


def test_run_with_timeout_is_module_level_callable():
    assert callable(getattr(harness, "_run_with_timeout", None)), (
        "_run_with_timeout helper missing — T55 unit not wired")


# ── _run_with_timeout behaviour ─────────────────────────────────────

def test_run_with_timeout_returns_done_on_fast_function():
    def quick(ctx):
        return harness.PASS, "fast"

    outcome, payload = harness._run_with_timeout(quick, None, 2.0)
    assert outcome == "done"
    assert payload == (harness.PASS, "fast")


def test_run_with_timeout_returns_exc_on_raise():
    def boom(ctx):
        raise RuntimeError("kaboom")

    outcome, payload = harness._run_with_timeout(boom, None, 2.0)
    assert outcome == "exc"
    assert "RuntimeError" in payload
    assert "kaboom" in payload


def test_run_with_timeout_returns_timeout_when_slow():
    """The whole point of T55. A check that sleeps past the limit
    must NOT block — the helper returns ('timeout', ...) and the
    caller carries on."""
    def slow(ctx):
        time.sleep(2.0)
        return harness.PASS, "never reached"

    t0 = time.time()
    outcome, payload = harness._run_with_timeout(slow, None, 0.3)
    elapsed = time.time() - t0
    assert outcome == "timeout"
    assert "s" in payload  # elapsed-time format
    # The helper must return roughly at the timeout, not after the
    # sleep completes. 1.5s is generous slack for thread scheduling.
    assert elapsed < 1.5, (
        f"_run_with_timeout took {elapsed:.2f}s with a 0.3s "
        "timeout — it must not wait for the slow function")


# ── End-to-end through run_all ──────────────────────────────────────

def test_run_all_records_timeout_and_continues(tmp_path):
    """Register a wedging check and a quick one; run_all with a
    short per-check timeout must record FAIL for the wedge and
    PASS for the quick one, then exit."""
    # The autouse_restore_live_registry fixture restores the real
    # registry after this test, so we can mutate freely here.
    harness._REGISTRY.clear()

    @harness.live_test("T55W", "t55-wedger")
    def _wedger(ctx):
        time.sleep(5.0)  # well past the timeout we'll set
        return harness.PASS, "would-have-passed"

    @harness.live_test("T55Q", "t55-quick")
    def _quick(ctx):
        return harness.PASS, "ok"

    rdir = tmp_path / "results"
    t0 = time.time()
    code = harness.run_all(
        "http://127.0.0.1:1",  # dead URL, fine — _wedger ignores it
        str(tmp_path),
        only=["T55W", "T55Q"],
        results_dir=rdir,
        per_check_timeout_s=0.3,
    )
    elapsed = time.time() - t0

    # Wedger should TIMEOUT (FAIL) → exit 1.
    assert code == 1, f"expected exit 1 from a TIMEOUT, got {code}"
    # Must NOT have waited for the full 5s sleep. The bound used to
    # be 3.0s but flaked on Windows hosts running ~60 parallel test
    # workers (3.22s observed at v3.63.6); 4.5s still proves the
    # cutoff worked (the wedger sleeps 5.0s) while tolerating
    # scheduler pressure on heavily-loaded hosts.
    assert elapsed < 4.5, (
        f"run_all took {elapsed:.2f}s — the wedger should have "
        "been cut at 0.3s, not blocked the run")

    summary = (rdir / "SUMMARY.txt").read_text(encoding="utf-8")
    assert "T55W" in summary
    assert "T55Q" in summary
    assert "TIMEOUT" in summary, (
        f"wedger result should mention TIMEOUT; got:\n{summary}")
    # The quick check should have run AFTER the wedger and
    # passed normally.
    assert "PASS  T55Q" in summary


# ── CLI wiring ──────────────────────────────────────────────────────

def test_cli_help_documents_per_check_timeout():
    cp = subprocess.run(
        [sys.executable, "-m", "live_tests.run", "--help"],
        cwd=str(_REPO_ROOT),
        capture_output=True, text=True, timeout=15,
    )
    assert cp.returncode == 0
    assert "--per-check-timeout" in cp.stdout, (
        "CLI must surface the new flag")
    assert "TIMEOUT" in cp.stdout or "timeout" in cp.stdout.lower()
