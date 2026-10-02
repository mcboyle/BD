"""v3.43.52: session keeper Playwright collision fix.

Bug: the session keeper runs in a daemon thread with a live
sync_playwright() context (its persistent Chromium for
heartbeats). When heartbeat detected a session expiry and called
the do_login_callback, the callback invoked do_login() which
attempted to start ANOTHER sync_playwright. Sync Playwright
rejects nested contexts with the error "It looks like you are
using Playwright Sync API inside the asyncio loop. Please use
the Async API instead."

The same conflict applied to:
  - The new wizard's verify step (verify_login_after_wizard
    spawns sync_playwright while keeper still has its own live)
  - Manual takeover starts (start_manual_login launches
    open_manual_login_browser while keeper still holds a profile)

The fix: tear down the keeper's Playwright context before any
sync_playwright-using flow on the same site. The keeper detects
its torn-down state on the next heartbeat and relaunches.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest import mock

# [SAST 3:13pm 13 may] removed unused: import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _bd_runner_src():
    """v3.66.403: runner.py decomposed into runner_*.py mixins; aggregate the
    package so moved SiteRunner method bodies stay visible to source checks."""
    from pathlib import Path as _P
    from bulk_downloader import runner as _R
    _pd = _P(_R.__file__).parent
    return "\n".join(q.read_text(encoding="utf-8")
                     for q in [_pd / "runner.py"] + sorted(_pd.glob("runner_*.py")))


def _start_manual_login_src():
    src = _bd_runner_src()
    methods = [node for node in ast.walk(ast.parse(src))
               if isinstance(node, ast.FunctionDef)
               and node.name == "start_manual_login"]
    assert len(methods) == 1, "expected one start_manual_login method"
    body = ast.get_source_segment(src, methods[0])
    assert body is not None
    return body


# ── pause_site_keepers helper ────────────────────────────────────


def test_pause_site_keepers_exists():
    """The new helper must be exported from session_keeper."""
    from bulk_downloader import session_keeper as sk
    assert hasattr(sk, "pause_site_keepers")
    assert callable(sk.pause_site_keepers)


def test_pause_site_keepers_returns_zero_when_no_keepers():
    """Pausing a site with no keepers is a no-op, returns 0."""
    from bulk_downloader import session_keeper as sk
    # Use a unique sid so the test doesn't fight a real keeper
    result = sk.pause_site_keepers("nonexistent_sid_for_test_xyz")
    assert result == 0


def test_pause_site_keepers_tears_down_matching_keepers():
    """Pausing site X tears down ALL keepers with site_id=X
    (regardless of account_idx), leaving other sites untouched."""
    from bulk_downloader import session_keeper as sk

    # Mock keeper objects
    keeper_a0 = mock.MagicMock()
    keeper_a1 = mock.MagicMock()
    keeper_b0 = mock.MagicMock()
    # Snapshot and restore the module-level _keepers dict
    saved = dict(sk._keepers)
    try:
        sk._keepers.clear()
        sk._keepers[("siteA", 0)] = keeper_a0
        sk._keepers[("siteA", 1)] = keeper_a1
        sk._keepers[("siteB", 0)] = keeper_b0
        n = sk.pause_site_keepers("siteA")
        assert n == 2
        # siteA keepers torn down
        keeper_a0._teardown_browser.assert_called_once()
        keeper_a1._teardown_browser.assert_called_once()
        # siteB keeper untouched
        keeper_b0._teardown_browser.assert_not_called()
    finally:
        sk._keepers.clear()
        sk._keepers.update(saved)


def test_pause_site_keepers_swallows_teardown_errors():
    """A teardown that raises mustn't stop the others from running."""
    from bulk_downloader import session_keeper as sk

    bad_keeper = mock.MagicMock()
    bad_keeper._teardown_browser.side_effect = RuntimeError("boom")
    good_keeper = mock.MagicMock()
    saved = dict(sk._keepers)
    try:
        sk._keepers.clear()
        sk._keepers[("siteA", 0)] = bad_keeper
        sk._keepers[("siteA", 1)] = good_keeper
        # Should not raise
        sk.pause_site_keepers("siteA")
        # Both were attempted
        bad_keeper._teardown_browser.assert_called_once()
        good_keeper._teardown_browser.assert_called_once()
    finally:
        sk._keepers.clear()
        sk._keepers.update(saved)


# ── _auto_relogin tears down before callback ─────────────────────


def test_auto_relogin_tears_down_before_callback():
    """Critical fix: the keeper must release its sync_playwright
    context before invoking do_login_callback, otherwise nested
    sync_playwright() crashes."""
    src = (_REPO_ROOT / "bulk_downloader" / "session_keeper.py").read_text(encoding="utf-8")
    pos = src.find("def _auto_relogin")
    assert pos > 0
    body = src[pos:pos + 2000]
    # _teardown_browser must be called before the callback
    teardown_pos = body.find("self._teardown_browser()")
    callback_pos = body.find("self.do_login_callback(")
    assert teardown_pos > 0, "no teardown before callback"
    assert callback_pos > 0, "no callback invocation"
    assert teardown_pos < callback_pos, (
        "teardown must precede callback (otherwise sync_playwright "
        "conflict)")


# ── verify_login_after_wizard pauses keepers ────────────────────


def test_verify_pauses_site_keepers():
    """The wizard's verify step uses sync_playwright on the same
    profile dir the keeper has — must pause keepers first."""
    src = _bd_runner_src()
    pos = src.find("def verify_login_after_wizard")
    assert pos > 0
    body = src[pos:pos + 3000]
    # Must call pause_site_keepers BEFORE verify_login_replay
    pause_pos = body.find("pause_site_keepers")
    verify_pos = body.find("verify_login_replay(")
    assert pause_pos > 0, "verify doesn't pause keepers"
    assert verify_pos > 0, "verify call missing"
    assert pause_pos < verify_pos, "pause must come before verify call"


# ── start_manual_login pauses keepers ────────────────────────────


def _manual_window_carrier():
    """A bare AuthMixin carrier: enough state for _open_manual_window to
    run its keeper-pause + browser-open prologue without a full runner."""
    from bulk_downloader.runner_auth import AuthMixin

    class _Carrier(AuthMixin):
        pass

    c = _Carrier()
    c.site_id = "keeper_collision_site"
    c.config = {"manual_use_persistent_profile": False}
    return c


def _run_open_manual_window(pause_side_effect=None):
    """Drive the manual-takeover browser open with keeper pause and the
    browser launch both mocked; return (result, ordered call log)."""
    calls = []

    def _pause(site_id):
        calls.append(("pause", site_id))
        if pause_side_effect is not None:
            raise pause_side_effect
        return 1

    def _open(cfg, manual_profile_dir=None):
        calls.append(("open", manual_profile_dir))
        return None   # "no handle" -> returns before the poller thread

    c = _manual_window_carrier()
    with mock.patch("bulk_downloader.session_keeper.pause_site_keepers",
                    side_effect=_pause), \
         mock.patch("bulk_downloader.login.open_manual_login_browser",
                    side_effect=_open):
        result = c._open_manual_window("https://example.test/login")
    return result, calls


def test_start_manual_login_routes_through_open_manual_window():
    """fx-manual-cancel-noop moved the browser open (and the keeper pause
    that guards it) out of start_manual_login into _open_manual_window;
    start_manual_login must still reach it."""
    body = _start_manual_login_src()
    assert "self._open_manual_window(" in body


def test_manual_login_pauses_site_keepers():
    """Starting a manual takeover also conflicts with keeper
    Playwright contexts on the same profile: the site's keepers are
    paused before the manual browser opens."""
    result, calls = _run_open_manual_window()
    assert result == (False, "Browser open returned no handle")
    assert [k for k, _ in calls] == ["pause", "open"], calls
    assert calls[0] == ("pause", "keeper_collision_site")


# ── Defensive: pause errors don't crash the flow ─────────────────


def test_verify_continues_when_keeper_pause_raises():
    """If pause_site_keepers throws (e.g. import failure), the
    verify still runs. We log the error but don't abort."""
    src = _bd_runner_src()
    pos = src.find("def verify_login_after_wizard")
    body = src[pos:pos + 3000]
    # try/except around the pause call
    assert "try:" in body
    # The actual verify_login_replay call must be outside that try
    # (or in a separate try) — i.e. not gated on pause success
    assert "continuing anyway" in body


def test_manual_login_continues_when_keeper_pause_raises(capsys):
    """A keeper-pause failure is logged and the browser still opens."""
    result, calls = _run_open_manual_window(
        pause_side_effect=RuntimeError("keeper import broke"))
    assert [k for k, _ in calls] == ["pause", "open"], calls
    assert result == (False, "Browser open returned no handle")
    err = capsys.readouterr().err
    assert "keeper pause failed" in err and "continuing anyway" in err
