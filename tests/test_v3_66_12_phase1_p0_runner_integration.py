"""v3.66.12 — Phase 1, P0: runner integration with deep_detect_live.

The runner's `_try_deep_detect_fallback` now calls `deep_detect_live()`
instead of the offline `deep_detect()`. This unlocks four features
the v3.66.10 release shipped but the runner never reached:

  • probe_parallelism (concurrent HEAD probes)
  • manifest-follow (HLS variant resolution)
  • URL canonicalization (dedup signed-URL pairs)
  • signed-URL detection (skip pre-expired URLs)

The change is gated on per-site `runner_use_live_dd` (default True);
setting it to False reverts to the v3.66.11 offline behaviour.

These tests cover:

  1. Live-mode is the default. With no config override, the live
     path is taken and the deep_detect_live function is invoked.
  2. Off-switch works. With `runner_use_live_dd=False`, the offline
     analyzer is invoked instead.
  3. Cookies and headers are threaded through. The runner's
     page.context.cookies() become the httpx.Client cookies; UA and
     Referer become probe_headers.
  4. Exceptions in deep_detect_live don't escape — the runner falls
     through to a None return like the pre-v3.66.12 behaviour.
  5. Budget truncation is respected — the runner sets
     max_total_probe_time=8.0 so a slow probe-storm can't hang the
     runner indefinitely.
  6. Parallelism is requested — probe_parallelism=4 is passed
     through to deep_detect_live.

Fixtures + helpers are intentionally minimal so the tests are easy
to read at a glance. We don't spin up a real Playwright browser;
the `_FakePage` and `_FakeCtx` stand-ins are enough for the
runner's actual usage of `page.content()` / `page.context.cookies()`.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

# ── Page / context stubs ──────────────────────────────────────────────


class _FakeCtx:
    """Minimal Playwright BrowserContext stand-in."""

    def __init__(self, cookies=None):
        self._cookies = cookies or []

    def cookies(self):
        # Playwright returns a list of dicts; runner code expects the
        # `name` and `value` keys.
        return list(self._cookies)


class _FakePage:
    """Minimal Playwright Page stand-in. _try_deep_detect_fallback
    reads `.content()`, `.url`, `.context.cookies()`."""

    def __init__(self, html, url="https://example.test/p/1", cookies=None):
        self._html = html
        self.url = url
        self.context = _FakeCtx(cookies=cookies)

    def content(self):
        return self._html


def _build_runner():
    """Construct a SiteRunner with minimal config.

    The class's __init__ does a lot of setup (queue, threads), so we
    sidestep it by allocating without __init__ and stamping the
    attributes the deep-detect path actually touches.
    """
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner.__new__(SiteRunner)
    r.site_id = "test-site"
    r.config = {
        "deep_detect_fallback": True,
        # Live mode is the v3.66.12 default; we set it explicitly
        # here so the test intent is obvious even after future
        # default changes.
        "runner_use_live_dd": True,
    }
    return r


# ── P0 tests ──────────────────────────────────────────────────────────


def test_P0_live_mode_is_default_path():
    """With `runner_use_live_dd` unset, the live path is taken."""
    r = _build_runner()
    del r.config["runner_use_live_dd"]  # exercise the default
    page = _FakePage("<html><body>" + "x" * 500 + "</body></html>")

    captured = {}

    def fake_dd_live(html, **kwargs):
        captured["called"] = "live"
        captured["kwargs"] = kwargs
        return {"download_candidates": [], "blockers": {}}

    def fake_dd_offline(html, **kwargs):
        captured["called"] = "offline"
        return {"download_candidates": [], "blockers": {}}

    from bulk_downloader import deep_detect
    with patch.object(deep_detect, "deep_detect_live", fake_dd_live), \
            patch.object(deep_detect, "deep_detect", fake_dd_offline):
        r._try_deep_detect_fallback(page, "https://x.test/", {})

    assert captured.get("called") == "live", (
        f"runner should default to live mode; got {captured.get('called')!r}")


def test_P0_off_switch_falls_back_to_offline():
    """`runner_use_live_dd=False` reverts to the v3.66.11 offline path."""
    r = _build_runner()
    r.config["runner_use_live_dd"] = False
    page = _FakePage("<html><body>" + "x" * 500 + "</body></html>")

    captured = {}

    def fake_dd_live(html, **kwargs):
        captured["called"] = "live"
        return {"download_candidates": [], "blockers": {}}

    def fake_dd_offline(html, **kwargs):
        captured["called"] = "offline"
        return {"download_candidates": [], "blockers": {}}

    from bulk_downloader import deep_detect
    with patch.object(deep_detect, "deep_detect_live", fake_dd_live), \
            patch.object(deep_detect, "deep_detect", fake_dd_offline):
        r._try_deep_detect_fallback(page, "https://x.test/", {})

    assert captured.get("called") == "offline", (
        f"off-switch should use offline analyzer; "
        f"got {captured.get('called')!r}")


def test_P0_passes_recommended_live_params():
    """The runner should pass through the parameters the roadmap
    spec'd: probe_parallelism=4, max_total_probe_time=8.0,
    follow_manifests=True, poll_async_workflows=False,
    sniff_attachments=True."""
    r = _build_runner()
    page = _FakePage("<html><body>" + "x" * 500 + "</body></html>")

    captured = {}

    def fake_dd_live(html, **kwargs):
        captured.update(kwargs)
        return {"download_candidates": [], "blockers": {}}

    from bulk_downloader import deep_detect
    with patch.object(deep_detect, "deep_detect_live", fake_dd_live):
        r._try_deep_detect_fallback(page, "https://x.test/", {})

    assert captured.get("probe_parallelism") == 4
    assert captured.get("max_total_probe_time") == 8.0
    assert captured.get("follow_manifests") is True
    assert captured.get("poll_async_workflows") is False
    assert captured.get("sniff_attachments") is True


def test_P0_passes_cookies_through_to_httpx_client():
    """page.context.cookies() should land on the httpx.Client used
    for probes, so authenticated CDN URLs return 200 not 401/403."""
    r = _build_runner()
    page = _FakePage(
        "<html><body>" + "x" * 500 + "</body></html>",
        cookies=[
            {"name": "session", "value": "abc123", "domain": ".x.test"},
            {"name": "csrf", "value": "tok-xyz", "domain": ".x.test"},
            {"name": "no_value"},  # malformed; runner must skip
        ],
    )

    captured = {}

    def fake_dd_live(html, **kwargs):
        # The runner passes `http=` to deep_detect_live; inspect its
        # cookies attribute to confirm what was put on the client.
        client = kwargs.get("http")
        # httpx.Client exposes .cookies as a Cookies instance with
        # dict-like access.
        captured["cookies"] = {
            k: v for k, v in client.cookies.items()
        } if client is not None else None
        return {"download_candidates": [], "blockers": {}}

    from bulk_downloader import deep_detect
    with patch.object(deep_detect, "deep_detect_live", fake_dd_live):
        r._try_deep_detect_fallback(page, "https://x.test/", {})

    cookies = captured.get("cookies") or {}
    assert cookies.get("session") == "abc123"
    assert cookies.get("csrf") == "tok-xyz"
    # Malformed cookie (no `value` key) must be skipped, not crash
    assert "no_value" not in cookies


def test_P0_passes_ua_and_referer_in_probe_headers():
    """probe_headers should include UA + Referer so probes look
    like browser requests (CDNs commonly 403 the default UA)."""
    r = _build_runner()
    r.config["user_agent"] = "TestBot/1.0"
    page = _FakePage(
        "<html><body>" + "x" * 500 + "</body></html>",
        url="https://x.test/page/1",
    )

    captured = {}

    def fake_dd_live(html, **kwargs):
        captured["probe_headers"] = kwargs.get("probe_headers")
        captured["base_url"] = kwargs.get("base_url")
        return {"download_candidates": [], "blockers": {}}

    from bulk_downloader import deep_detect
    with patch.object(deep_detect, "deep_detect_live", fake_dd_live):
        r._try_deep_detect_fallback(page, "https://x.test/p/1", {})

    headers = captured.get("probe_headers") or {}
    assert headers.get("User-Agent") == "TestBot/1.0"
    # Referer should be the page URL (not the originating URL arg),
    # so paywalled CDNs can validate the request origin.
    assert headers.get("Referer") == "https://x.test/page/1"
    assert headers.get("Accept") == "*/*"


def test_P0_exception_in_live_mode_returns_none_not_raise():
    """If deep_detect_live raises, the runner must swallow the error
    and return None (the existing fallback contract); the caller
    falls through to no-button-found handling."""
    r = _build_runner()
    page = _FakePage("<html><body>" + "x" * 500 + "</body></html>")

    def fake_dd_live(html, **kwargs):
        raise RuntimeError("simulated network meltdown")

    from bulk_downloader import deep_detect
    with patch.object(deep_detect, "deep_detect_live", fake_dd_live):
        result = r._try_deep_detect_fallback(page, "https://x.test/", {})

    assert result is None, (
        "live-mode exception must not escape; "
        f"got {result!r}")


def test_P0_gate_still_works_in_live_mode():
    """Without `deep_detect_fallback` or mode=deep, the runner
    returns None before invoking either analyzer — even in live
    mode the gate is honored."""
    r = _build_runner()
    del r.config["deep_detect_fallback"]  # ungate
    page = _FakePage("<html><body>" + "x" * 500 + "</body></html>")

    captured = {}

    def fake_dd_live(html, **kwargs):
        captured["called"] = True
        return {"download_candidates": [], "blockers": {}}

    from bulk_downloader import deep_detect
    with patch.object(deep_detect, "deep_detect_live", fake_dd_live):
        result = r._try_deep_detect_fallback(page, "https://x.test/", {})

    assert result is None
    assert "called" not in captured, "gate should prevent the call"


def test_P0_short_html_skips_dd_in_live_mode():
    """The < 200-char HTML guard is honored even in live mode."""
    r = _build_runner()
    page = _FakePage("<html></html>")  # too short

    captured = {}

    def fake_dd_live(html, **kwargs):
        captured["called"] = True
        return {"download_candidates": [], "blockers": {}}

    from bulk_downloader import deep_detect
    with patch.object(deep_detect, "deep_detect_live", fake_dd_live):
        result = r._try_deep_detect_fallback(page, "https://x.test/", {})

    assert result is None
    assert "called" not in captured, (
        "live-mode shouldn't probe sub-threshold HTML")
