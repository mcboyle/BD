"""v3.66.758 -- MOD-1 Architecture A, cut A-4: wire the driver.

The A-1/2/3 pipe (screencast SSE + input route + TakeoverViewer) is shipped but
carries no live frames. A-4 connects it: start_captcha_solve_session opens the
solve browser HEADLESS + screencast-enabled when config captcha_takeover_mode ==
"remote", routing CDP screencast frames -> the per-session takeover channel and
draining queued operator input -> CDP Input, all on the ManualLoginSession's
owning thread. "visible" (default) keeps today's behavior byte-for-byte.

RED-first on pristine v3.66.757 (config/helpers/method all ABSENT). The live
frame-flow itself needs a real solve session (A-0 proved the CDP mechanics
in-sandbox); these anchors pin the DETERMINISTIC contract that gates it.
"""
from __future__ import annotations

import pytest


# ── config: the mode gate, default preserves today's visible behavior ────────

def test_captcha_takeover_mode_declared_default_visible():
    from bulk_downloader import global_config as gc
    spec = gc.GLOBAL_CONFIG_SCHEMA
    assert "captcha_takeover_mode" in spec, "captcha_takeover_mode not declared"
    entry = spec["captcha_takeover_mode"]
    assert entry.get("safe_default") == "visible", (
        "default must be 'visible' -- A-4 must not change the fallback behavior")


# ── mode resolution: config/store -> visible|remote ──────────────────────────

def test_resolve_takeover_mode():
    from bulk_downloader import runner_auth as ra
    assert ra._resolve_takeover_mode({}) == "visible"           # default
    assert ra._resolve_takeover_mode({"captcha_takeover_mode": "remote"}) == "remote"
    # anything unrecognized falls back to visible (fail-safe to the human path)
    assert ra._resolve_takeover_mode({"captcha_takeover_mode": "bogus"}) == "visible"


# ── headless threads into the launch kwargs (pure builder, no real launch) ───

def test_manual_launch_kwargs_threads_headless():
    from bulk_downloader.login_impl import manual as m
    vis = m._manual_launch_kwargs({}, headless=False)
    rem = m._manual_launch_kwargs({}, headless=True)
    assert vis["headless"] is False, "visible mode must stay headless=False"
    assert rem["headless"] is True, "remote mode must launch headless=True"
    # the anti-automation / autofill args must survive either way
    assert any("AutomationControlled" in a for a in rem.get("args", []))


# ── the session exposes a screencast bridge (class-level, no instantiation) ──

def test_manual_session_has_start_screencast():
    from bulk_downloader.login_impl.manual import ManualLoginSession
    assert hasattr(ManualLoginSession, "start_screencast"), (
        "ManualLoginSession must expose start_screencast(sid) so the driver can "
        "screencast the solve browser over the takeover channel")
    assert hasattr(ManualLoginSession, "__init__")


# ── the input pump: the worker thread drains queued CDP input in bounded batches

def test_takeover_channel_drain_inputs():
    from bulk_downloader import takeover
    sid = "a4-drain"
    takeover.close_channel(sid)
    takeover.open_channel(sid)
    try:
        for i in range(5):
            assert takeover.enqueue_input(sid, {"type": "mouseMoved", "x": i, "y": i}) == "ok"
        first = takeover.drain_inputs(sid, max_n=3)
        assert len(first) == 3, "drain must be bounded by max_n"
        rest = takeover.drain_inputs(sid, max_n=10)
        assert len(rest) == 2, "drain returns the remainder"
        assert takeover.drain_inputs(sid, max_n=10) == [], "empty when drained"
    finally:
        takeover.close_channel(sid)
