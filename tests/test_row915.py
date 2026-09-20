"""Tests for Row 915: runner_browser integration, BrowserMixin normalization & seams.

Verifies:
1. Determinism and standardized profiles across headless runs.
2. Standard default state reporting (viewport, user-agent, locale, timezone).
3. Active callsites & Seams in BrowserMixin:
   - BrowserMixin._launch_args (line 80)
   - BrowserMixin._context_options (line 65)
   - BrowserMixin._launch_browser -> _launch_args call (line 306)
   - BrowserMixin._launch_browser -> _context_options call (line 352)
   - BrowserMixin._launch_browser -> open_persistent_context normal (line 375)
   - BrowserMixin._launch_browser -> open_persistent_context channel fallback (line 413)
   - BrowserMixin._launch_browser -> launch_browser normal (line 440)
   - BrowserMixin._launch_browser -> launch_browser channel fallback (line 448)
"""
from __future__ import annotations

import importlib
import sys
import types
from unittest import mock
import pytest

BD_GATE_SCOPE = "module"


def _rb():
    return importlib.import_module("bulk_downloader.runner_browser")


def _cloak():
    return importlib.import_module("bulk_downloader.cloak")


@pytest.fixture
def fake_pw_env(monkeypatch):
    cloak = _cloak()
    mock_context = mock.MagicMock()
    mock_browser = mock.MagicMock()
    mock_browser.new_context.return_value = mock_context
    mock_chromium = mock.MagicMock()
    mock_chromium.launch_persistent_context.return_value = mock_context
    mock_chromium.launch.return_value = mock_browser

    mock_pw = mock.MagicMock()
    mock_pw.chromium = mock_chromium

    mock_starter = mock.MagicMock()
    mock_starter.start.return_value = mock_pw

    sync_mod = types.ModuleType("playwright.sync_api")
    sync_mod.sync_playwright = lambda: mock_starter

    # monkeypatch restores sys.modules itself (no pop/del here: the v3.66.1034 leaker census)
    if sys.modules.get("playwright") is None:
        pkg = types.ModuleType("playwright")
        pkg.__path__ = []
        monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_mod)

    try:
        yield {
            "pw": mock_pw,
            "chromium": mock_chromium,
            "browser": mock_browser,
            "context": mock_context,
        }
    finally:
        cloak.reset_cache_for_tests()


def _make_mixin():
    rb = _rb()
    mixin = rb.BrowserMixin()
    mixin.config = {"browser_backend": "playwright"}
    mixin.site_id = "testsite_seams"
    mixin.log_event = lambda *a, **k: None
    mixin._record_channel_fallback = lambda *a, **k: None
    mixin._apply_persistent_cookie_file = lambda ctx: None
    mixin._install_stealth = lambda ctx: None
    return mixin


# ── 1. Consistency across headless runs & launch args ───────────────────────

def test_standard_launch_args_is_deterministic():
    rb = _rb()
    a = rb.standard_launch_args(headless=True)
    b = rb.standard_launch_args(headless=True)
    assert a == b
    assert a is not b  # fresh list each call


def test_standard_launch_args_includes_new_headless_mode():
    rb = _rb()
    args = rb.standard_launch_args(headless=True)
    assert "--headless=new" in args
    assert "--disable-blink-features=AutomationControlled" in args


def test_standard_launch_args_headed_omits_headless():
    rb = _rb()
    args = rb.standard_launch_args(headless=False)
    assert "--headless=new" not in args
    assert not any(a.startswith("--headless") for a in args)


# ── 2. Standard default state reporting & context options ───────────────────

def test_normalized_context_options_unconfigured_keeps_the_browsers_own_identity():
    """REFUTE E4: no fingerprint -> no UA/timezone/locale override (the browser's own values stand);
    only the headless viewport is standardized."""
    rb = _rb()
    opts = rb.normalized_context_options({}, headless=True)
    assert opts == {"accept_downloads": True, "viewport": rb.STANDARD_VIEWPORT}, opts
    assert "user_agent" not in opts and "timezone_id" not in opts and "locale" not in opts


def test_normalized_context_options_handles_partial_fingerprint():
    rb = _rb()
    opts = rb.normalized_context_options({"locale": "fr-FR"}, headless=True)
    assert opts["locale"] == "fr-FR"
    assert "user_agent" not in opts and "timezone_id" not in opts
    assert opts["viewport"] == rb.STANDARD_VIEWPORT
    full = rb.normalized_context_options(
        {"user_agent": "UA/1", "timezone": "Europe/Paris", "locale": "fr-FR", "viewport_w": 800, "viewport_h": 600},
        headless=True,
    )
    assert full == {"accept_downloads": True, "user_agent": "UA/1", "viewport": {"width": 800, "height": 600},
                    "timezone_id": "Europe/Paris", "locale": "fr-FR"}


def test_normalized_context_options_non_numeric_viewport_fallback():
    rb = _rb()
    opts = rb.normalized_context_options(
        {"viewport_w": "invalid", "viewport_h": 900}, headless=True
    )
    assert opts["viewport"] == rb.STANDARD_VIEWPORT


def test_normalized_context_options_headed_tracks_window():
    rb = _rb()
    opts = rb.normalized_context_options({}, headless=False)
    assert opts["no_viewport"] is True
    assert "viewport" not in opts


# ── 3. Active BrowserMixin Callsite Integration & Seams ─────────────────────

def test_browser_mixin_launch_args_wired_to_standard_args():
    """Guards standard_launch_args@bulk_downloader/runner_browser.py:80."""
    rb = _rb()
    mixin = rb.BrowserMixin()
    mixin.config = {}
    args = mixin._launch_args(headless=True)
    assert args is not None
    assert "--headless=new" in args
    assert "--disable-blink-features=AutomationControlled" in args


def test_browser_mixin_context_options_wired_to_normalized_options():
    """Guards normalized_context_options@bulk_downloader/runner_browser.py:65."""
    rb = _rb()
    mixin = rb.BrowserMixin()
    mixin.config = {}
    opts = mixin._context_options(headless=True)
    assert opts is not None
    assert "viewport" in opts
    assert opts["viewport"] == rb.STANDARD_VIEWPORT
    assert "user_agent" not in opts and "timezone_id" not in opts and "locale" not in opts  # E4: unconfigured = browser's own


def test_browser_mixin_launch_browser_persistent_normal_seams(fake_pw_env):
    """Guards:
    - _launch_args@bulk_downloader/runner_browser.py:306
    - _context_options@bulk_downloader/runner_browser.py:352
    - open_persistent_context@bulk_downloader/runner_browser.py:375
    """
    mixin = _make_mixin()
    _, ctx, used_pw, backend = mixin._launch_browser(headless=True, use_persistent=True)
    assert ctx is fake_pw_env["context"]
    assert backend == "playwright"

    call_args = fake_pw_env["chromium"].launch_persistent_context.call_args.kwargs
    args = call_args.get("args")
    assert args is not None, "launch_persistent_context received None args (_launch_args seam failed)"
    assert "--headless=new" in args
    assert "--disable-blink-features=AutomationControlled" in args

    assert call_args.get("user_agent") is None  # E4: no fingerprint -> the browser's own UA
    assert call_args.get("viewport") == _rb().STANDARD_VIEWPORT


def test_browser_mixin_launch_browser_persistent_channel_fallback(fake_pw_env):
    """Guards open_persistent_context@bulk_downloader/runner_browser.py:413."""
    mixin = _make_mixin()
    mixin.config["use_real_chrome"] = True
    fake_pw_env["chromium"].launch_persistent_context.side_effect = [
        Exception("Chrome channel unavailable"),
        fake_pw_env["context"],
    ]
    _, ctx, used_pw, backend = mixin._launch_browser(headless=True, use_persistent=True)
    assert ctx is fake_pw_env["context"]
    assert fake_pw_env["chromium"].launch_persistent_context.call_count == 2


def test_browser_mixin_launch_browser_non_persistent_normal(fake_pw_env):
    """Guards launch_browser@bulk_downloader/runner_browser.py:440."""
    mixin = _make_mixin()
    browser, _, used_pw, backend = mixin._launch_browser(headless=True, use_persistent=False)
    assert browser is fake_pw_env["browser"]
    assert backend == "playwright"


def test_browser_mixin_launch_browser_non_persistent_channel_fallback(fake_pw_env):
    """Guards launch_browser@bulk_downloader/runner_browser.py:448."""
    mixin = _make_mixin()
    mixin.config["use_real_chrome"] = True
    fake_pw_env["chromium"].launch.side_effect = [
        Exception("Chrome channel unavailable"),
        fake_pw_env["browser"],
    ]
    browser, _, used_pw, backend = mixin._launch_browser(headless=True, use_persistent=False)
    assert browser is fake_pw_env["browser"]
    assert fake_pw_env["chromium"].launch.call_count == 2


def test_browser_mixin_launch_browser_headed_seam_checks_launch_args(fake_pw_env):
    """Guards _launch_args@bulk_downloader/runner_browser.py:306."""
    mixin = _make_mixin()
    mixin._launch_browser(headless=False, use_persistent=True)
    call_args = fake_pw_env["chromium"].launch_persistent_context.call_args.kwargs
    args = call_args.get("args") or []
    assert "--window-size=1366,800" in args, (
        "launch_persistent_context missing --window-size (_launch_args seam not wired)"
    )
    assert "--password-store=basic" in args
