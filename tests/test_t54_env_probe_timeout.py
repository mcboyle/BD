"""T54 regression — tests/_env.py systemd probe robustness.

Bug (pre-v3.63.2):
  `_has_systemd_bus()` and `_has_bulkdownloader_unit()` used a hard
  2-second `subprocess.run(..., timeout=2)`. Under `--workers=4`
  parallel test load on a real deployment, that timeout fired
  for a transient stall, caching `HAS_BULKDOWNLOADER_UNIT=False`
  even when the unit WAS installed. The downstream effect was
  5 failing tests in `test_u41_systemd_live_tests.py` — they
  asserted the "no unit" branch when the unit really existed.

Fix (T54):
  - Bump the timeout to 8 seconds (constant
    `_SYSTEMD_PROBE_TIMEOUT_S`).
  - Wrap subprocess.run in `_run_with_retry`: on
    `TimeoutExpired`, retry once. Return None only if both
    attempts time out.
  - Return semantics unchanged for the True/False answer: still
    False on any failure mode, True on rc==0. The only difference
    is forgiveness for a single transient stall.

Test strategy:
  - Force `subprocess.run` to raise `TimeoutExpired` once, then
    succeed → assert the probe still returns True (retry worked).
  - Force `subprocess.run` to raise `TimeoutExpired` twice → assert
    the probe returns False (gave up after retry).
  - Verify the timeout constant is at least 8s (so a transient
    load spike is forgiven).
  - Source-level guard: no `timeout=2` left in the file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


# Import _env via file-relative path (same as the other tests in
# this suite use, since `tests/` is not a package).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env as env  # noqa: E402


# ── Constant + module surface ──────────────────────────────────────

def test_probe_timeout_constant_is_forgiving():
    """The constant must be at least 5s — small enough that a
    permanently-broken host doesn't stall the suite, large enough
    to survive a transient load spike under --workers=4."""
    assert isinstance(env._SYSTEMD_PROBE_TIMEOUT_S, (int, float))
    assert env._SYSTEMD_PROBE_TIMEOUT_S >= 5.0, (
        f"timeout must be >= 5s; got {env._SYSTEMD_PROBE_TIMEOUT_S}")


def test_run_with_retry_is_module_level_callable():
    assert callable(getattr(env, "_run_with_retry", None)), (
        "_run_with_retry helper missing — T54 unit not wired")


# ── _run_with_retry behaviour ──────────────────────────────────────

class _FakeCompletedProcess:
    """Stand-in for subprocess.CompletedProcess so tests don't
    actually shell out."""
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_with_retry_returns_on_first_success(monkeypatch):
    """No retry needed — first call succeeds."""
    calls = {"n": 0}

    def fake_run(cmd, capture_output=False, timeout=None):
        calls["n"] += 1
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(env.subprocess, "run", fake_run)
    result = env._run_with_retry(["systemctl", "any"])
    assert result is not None
    assert result.returncode == 0
    assert calls["n"] == 1


def test_run_with_retry_retries_after_one_timeout(monkeypatch):
    """The whole point of T54: a single TimeoutExpired must not
    flip the answer to False. After one timeout, retry once and
    accept that result."""
    calls = {"n": 0}

    def fake_run(cmd, capture_output=False, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(env.subprocess, "run", fake_run)
    result = env._run_with_retry(["systemctl", "any"])
    assert result is not None, (
        "must retry after a single timeout and accept the second "
        "result — this is the whole bug T54 fixes")
    assert result.returncode == 0
    assert calls["n"] == 2


def test_run_with_retry_gives_up_after_two_timeouts(monkeypatch):
    """Two timeouts in a row → genuine systemctl unavailability;
    None is the right answer."""
    calls = {"n": 0}

    def fake_run(cmd, capture_output=False, timeout=None):
        calls["n"] += 1
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(env.subprocess, "run", fake_run)
    result = env._run_with_retry(["systemctl", "any"])
    assert result is None, (
        "after two timeouts, the helper must return None — "
        "real degradation, not a transient stall")
    assert calls["n"] == 2


def test_run_with_retry_returns_none_on_other_exceptions(monkeypatch):
    """Non-Timeout exceptions are NOT retried — they indicate
    permanent unavailability (no executable, permission denied,
    etc.). Return None immediately."""
    calls = {"n": 0}

    def fake_run(cmd, capture_output=False, timeout=None):
        calls["n"] += 1
        raise FileNotFoundError("systemctl not on PATH")

    monkeypatch.setattr(env.subprocess, "run", fake_run)
    result = env._run_with_retry(["systemctl", "any"])
    assert result is None
    assert calls["n"] == 1, (
        "non-Timeout exceptions must not be retried")


# ── Source-level guard (no stale 2-second timeout) ─────────────────

def test_source_has_no_2_second_subprocess_timeout():
    """Belt-and-braces: a future edit can't silently reintroduce
    the 2-second timeout that motivated T54."""
    src = (Path(__file__).resolve().parent
           / "_env.py").read_text(encoding="utf-8")
    # Look for the literal `timeout=2` pattern that was the bug.
    # Tolerate `timeout=2.0`/`timeout=20` etc. by checking for the
    # exact form.
    forbidden = ["timeout=2)", "timeout=2,"]
    for needle in forbidden:
        assert needle not in src, (
            f"tests/_env.py contains {needle!r} — the 2-second "
            "subprocess timeout was the T54 bug; use "
            "_SYSTEMD_PROBE_TIMEOUT_S instead")


# ── Cached constants still respond correctly ───────────────────────

def test_cached_constants_are_booleans():
    """HAS_SYSTEMD_BUS / HAS_BULKDOWNLOADER_UNIT / HAS_OLLAMA_LOCAL
    are evaluated at import time and exposed as bools. Tests across
    the suite branch on these — they must remain bool-typed."""
    assert isinstance(env.HAS_SYSTEMD_BUS, bool)
    assert isinstance(env.HAS_BULKDOWNLOADER_UNIT, bool)
    assert isinstance(env.HAS_OLLAMA_LOCAL, bool)


def test_has_bulkdownloader_unit_implies_has_systemd_bus():
    """A degenerate combination — unit installed but no bus — is
    impossible by construction (the unit probe short-circuits on
    HAS_SYSTEMD_BUS=False). If this ever fires, _env.py was
    reordered in a way that breaks the invariant."""
    if env.HAS_BULKDOWNLOADER_UNIT:
        assert env.HAS_SYSTEMD_BUS, (
            "HAS_BULKDOWNLOADER_UNIT=True but HAS_SYSTEMD_BUS=False "
            "— the probe ordering is broken")
