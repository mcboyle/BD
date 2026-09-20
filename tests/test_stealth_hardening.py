"""Acceptance tests for Row 915: client automation attribute normalization & stealth hardening.

Register Row 915:
SCOPE: pass standardized launch flags and initialize default prototype properties in
bulk_downloader/cloak.py to ensure navigator.webdriver returns undefined or standard default;
0 site logins touched (Rule 21).
ACCEPTANCE: tests/test_stealth_hardening.py verifying:
(1) navigator.webdriver reports standard default state,
(2) plugins and languages lists match real browser profile,
(3) CDP permissions unaltered.
"""
from __future__ import annotations

import importlib
import sys
import types
from unittest import mock
import pytest

BD_GATE_SCOPE = "module"


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


# ── Acceptance (1): navigator.webdriver and standardized launch flags ───────

def test_cloak_open_persistent_context_passes_standard_launch_flags(fake_pw_env):
    cloak = _cloak()
    cloak.open_persistent_context(
        user_data_dir="/tmp/test_ud",
        headless=True,
        config={"browser_backend": "playwright"},
    )
    call_kwargs = fake_pw_env["chromium"].launch_persistent_context.call_args.kwargs
    args = call_kwargs.get("args", [])
    assert "--disable-blink-features=AutomationControlled" in args, (
        f"Missing standard stealth flag AutomationControlled in launch args: {args}"
    )
    assert "--headless=new" in args, (
        f"Missing --headless=new in headless launch args: {args}"
    )


def test_cloak_launch_browser_passes_standard_launch_flags(fake_pw_env):
    cloak = _cloak()
    cloak.launch_browser(
        headless=True,
        config={"browser_backend": "playwright"},
    )
    call_kwargs = fake_pw_env["chromium"].launch.call_args.kwargs
    args = call_kwargs.get("args", [])
    assert "--disable-blink-features=AutomationControlled" in args, (
        f"Missing standard stealth flag AutomationControlled in launch args: {args}"
    )
    assert "--headless=new" in args, (
        f"Missing --headless=new in headless launch args: {args}"
    )


def test_cloak_initializes_prototype_normalization_script(fake_pw_env):
    cloak = _cloak()
    cloak.open_persistent_context(
        user_data_dir="/tmp/test_ud",
        headless=True,
        config={"browser_backend": "playwright"},
    )
    ctx = fake_pw_env["context"]
    assert ctx.add_init_script.called, (
        "Persistent context launch did not initialize prototype normalization script"
    )
    script = ctx.add_init_script.call_args[0][0]
    assert "webdriver" in script, "Initialization script missing webdriver normalization"


_PROBE_JS = """() => ({
  webdriver: navigator.webdriver,
  webdriverOwn: Object.getOwnPropertyNames(navigator).includes('webdriver'),
  webdriverGetterNative: (() => { const d = Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver');
                                  return !!(d && d.get && d.get.toString().includes('[native code]')); })(),
  plugins: navigator.plugins.length,
  pluginNames: Array.from(navigator.plugins).map(p => p.name),
  pluginsIsPluginArray: navigator.plugins instanceof PluginArray,
  languages: Array.from(navigator.languages || []),
  permissionsNative: navigator.permissions.query.toString().includes('[native code]'),
})"""


def _probe_real_chromium(*, with_script: bool) -> dict:
    """Launch the venv's real Chromium with standard_launch_flags(headless=True) and, when asked, the
    normalization init script; return the navigator surface measured on about:blank. The
    with_script=False run is the CONTROL: it shows what the flags alone give (REFUTE E1-E3 were
    measured this way -- a substring test cannot see any of them)."""
    from playwright.sync_api import sync_playwright
    cloak = _cloak()
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True, args=cloak.standard_launch_flags(headless=True))
        context = browser.new_context(**cloak.normalized_context_options(None, headless=True))
        if with_script:
            context.add_init_script(cloak.DEFAULT_NORMALIZATION_SCRIPT)
        page = context.new_page()
        page.goto("about:blank")
        probe = page.evaluate(_PROBE_JS)
        browser.close()
        return probe
    finally:
        pw.stop()


def test_real_chromium_webdriver_is_false_and_not_an_own_property():
    """Acceptance (1): navigator.webdriver === false with NO own property, exactly the real-Chrome
    default; the script must not add an own "webdriver" property on top of the flags (REFUTE E3)."""
    control = _probe_real_chromium(with_script=False)
    probe = _probe_real_chromium(with_script=True)
    assert control["webdriver"] is False and control["webdriverOwn"] is False, control
    assert probe["webdriver"] is False, probe
    assert probe["webdriverOwn"] is False, probe
    # the flags already give the real default, so the script must leave the NATIVE prototype getter in place
    assert control["webdriverGetterNative"] is True and probe["webdriverGetterNative"] is True, (control, probe)


def test_real_chromium_plugins_match_the_real_chrome_profile():
    """Acceptance (2): headless Chromium reports 0 plugins (control); with the script it reports the
    5 PDF plugins real Chrome ships, as a PluginArray, and languages are populated (REFUTE E1)."""
    control = _probe_real_chromium(with_script=False)
    probe = _probe_real_chromium(with_script=True)
    assert control["plugins"] == 0, control
    assert probe["plugins"] == 5, probe
    assert probe["pluginNames"][:2] == ["PDF Viewer", "Chrome PDF Viewer"], probe
    assert probe["pluginsIsPluginArray"] is True, probe
    assert probe["languages"] and probe["languages"][0].startswith("en"), probe


def test_real_chromium_permissions_query_stays_native():
    """Acceptance (3): CDP permissions unaltered -- navigator.permissions.query is still the native
    function after the script (its toString() carries "[native code]"), as at the control (REFUTE E2)."""
    control = _probe_real_chromium(with_script=False)
    probe = _probe_real_chromium(with_script=True)
    assert control["permissionsNative"] is True, control
    assert probe["permissionsNative"] is True, probe


# ── Production Seams in cloak.py (Selfmut Coverage) ──────────────────────────

def test_cloak_persistent_context_seam_normal(fake_pw_env):
    """Guards open_persistent_context@bulk_downloader/cloak.py:741."""
    cloak = _cloak()
    with cloak.persistent_context(
        user_data_dir="/tmp/test_pc",
        config={"browser_backend": "playwright"},
    ) as (ctx, backend):
        assert ctx is fake_pw_env["context"]
        assert backend == "playwright"


def test_cloak_persistent_context_seam_channel_fallback(fake_pw_env):
    """Guards open_persistent_context@bulk_downloader/cloak.py:749 (channel retry)."""
    cloak = _cloak()
    fake_pw_env["chromium"].launch_persistent_context.side_effect = [
        Exception("Channel chrome unavailable"),
        fake_pw_env["context"],
    ]
    with cloak.persistent_context(
        user_data_dir="/tmp/test_pc_fb",
        config={"browser_backend": "playwright"},
        channel="chrome",
        channel_fallback=True,
    ) as (ctx, backend):
        assert ctx is fake_pw_env["context"]


def test_cloak_cloaked_page_seam_and_launch_browser(fake_pw_env):
    """Guards launch_browser@bulk_downloader/cloak.py:804 & cloaked_page definition."""
    cloak = _cloak()
    with cloak.cloaked_page(
        config={"browser_backend": "playwright"},
        headless=True,
    ) as page:
        assert page is not None
        # seam cloaked_page@cloak.py: the normalization script is installed on the fresh context
        ctx = fake_pw_env["context"]
        assert ctx.add_init_script.called and "webdriver" in ctx.add_init_script.call_args[0][0]


def test_cloak_backend_persistent_context_installs_the_normalization_script(monkeypatch, fake_pw_env):
    """Seam open_persistent_context(cloak)@cloak.py: the cloakbrowser branch installs the script too."""
    from unittest import mock
    cloak = _cloak()
    cloak_ctx = mock.MagicMock()
    lpc = mock.MagicMock(return_value=cloak_ctx)
    monkeypatch.setattr(cloak, "_CLOAK_LPC", lpc)
    monkeypatch.setattr(cloak, "_AVAILABLE", True, raising=False)
    monkeypatch.setattr(cloak, "resolve_backend", lambda config=None, **kw: cloak.CLOAKBROWSER)
    ctx, pw, backend = cloak.open_persistent_context(
        user_data_dir="/tmp/bd-915-cloak-profile", headless=True, config={"browser_backend": "cloakbrowser"}
    )
    assert backend == cloak.CLOAKBROWSER and ctx is cloak_ctx and lpc.called
    assert cloak_ctx.add_init_script.called, "cloakbrowser persistent context did not get the normalization script"
    assert "webdriver" in cloak_ctx.add_init_script.call_args[0][0]
