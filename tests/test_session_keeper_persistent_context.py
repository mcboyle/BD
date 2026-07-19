"""v3.64.2: persistent Chromium context for session_keeper.

The session keeper now uses `launch_persistent_context(user_data_dir=
profiles/<sid>/keepalive_<idx>)` instead of `launch()` +
`new_context()`. This means:

  1. The browser's cookie jar SURVIVES across heartbeats and across
     browser restarts (every 24h, on crash, on takeover release).
     Previously the ephemeral context lost any cookies the site set
     between heartbeats — the keeper would have to re-inject from DB
     on every launch, and anything rotated during the gap was gone.

  2. The profile directory layout matches what the module-level
     docstring has always claimed: profiles/<sid>/keepalive_<idx>.
     Distinct from worker profiles (profiles/<sid>/worker_*) to avoid
     contention with concurrent downloads.

  3. DB cookies are still seeded into the context on the FIRST launch
     only — when the profile dir is empty. After that the persistent
     profile is the source of truth and cookies flow OUT via
     `_persist_cookies()` after every successful navigate.

These tests don't require a real Chromium binary — they mock the
Playwright `sync_playwright` import and inspect the call shape. The
real-browser tests in `test_keepalive_browser.py` cover the
heartbeat path end-to-end.
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch


@contextmanager
def _isolated_cwd():
    """Run inside a temp dir so the profile dir doesn't leak."""
    orig = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        try:
            yield Path(td)
        finally:
            os.chdir(orig)


def _build_mock_playwright():
    """Stand-in for `playwright.sync_api`. Returns (mock_pw_module,
    mock_ctx) so the test can inspect the launch_persistent_context
    call."""
    mock_ctx = MagicMock(name="persistent_context")
    mock_ctx.pages = []  # no pre-existing pages → keeper calls new_page()
    mock_ctx.new_page.return_value = MagicMock(name="page")
    mock_ctx.new_page.return_value.is_closed.return_value = False

    mock_chromium = MagicMock(name="chromium")
    mock_chromium.launch_persistent_context.return_value = mock_ctx

    mock_pw_instance = MagicMock(name="pw_instance")
    mock_pw_instance.chromium = mock_chromium

    mock_sync_playwright = MagicMock(name="sync_playwright_callable")
    mock_sync_playwright.return_value.start.return_value = mock_pw_instance

    # Build a fake module so `from playwright.sync_api import
    # sync_playwright` resolves.
    fake_sync_api = MagicMock(name="playwright.sync_api")
    fake_sync_api.sync_playwright = mock_sync_playwright

    return fake_sync_api, mock_ctx, mock_chromium


def _install_fake_playwright(fake_sync_api):
    """Patch sys.modules so the keeper's `from playwright.sync_api
    import sync_playwright` resolves to the mock. Returns a cleanup
    callable."""
    saved_pw = sys.modules.get("playwright")
    saved_sync = sys.modules.get("playwright.sync_api")
    sys.modules["playwright"] = MagicMock(name="playwright")
    sys.modules["playwright.sync_api"] = fake_sync_api

    def _restore():
        if saved_pw is None:
            sys.modules.pop("playwright", None)
        else:
            sys.modules["playwright"] = saved_pw
        if saved_sync is None:
            sys.modules.pop("playwright.sync_api", None)
        else:
            sys.modules["playwright.sync_api"] = saved_sync
    return _restore


def test_launch_uses_persistent_context_not_ephemeral():
    """The load-bearing assertion: `launch_persistent_context` is
    called, NOT `launch` + `new_context`. A future refactor that
    reverts to the ephemeral pattern (the v3.43.16 → v3.64.1 bug)
    fails this test."""
    from bulk_downloader.session_keeper import SessionKeeper
    fake_sync_api, mock_ctx, mock_chromium = _build_mock_playwright()
    restore = _install_fake_playwright(fake_sync_api)
    try:
        with _isolated_cwd():
            keeper = SessionKeeper(
                "testsite", 0,
                {"login_url": "http://localhost", "password": "p"},
                lambda *_: (False, "test"))
            ok = keeper._launch_browser()
            assert ok, "launch returned False under mocked playwright"

            # The critical assertion — persistent, not ephemeral.
            assert mock_chromium.launch_persistent_context.called, (
                "Expected launch_persistent_context to be called; "
                "instead got launch() + new_context() — the v3.64.1 "
                "ephemeral bug has reappeared")
            assert not mock_chromium.launch.called, (
                "launch() must NOT be called when the persistent "
                "path is in effect — would create an ephemeral "
                "context whose cookies don't survive restarts")

            # Profile dir path matches the documented layout.
            call_kwargs = mock_chromium.launch_persistent_context.call_args.kwargs
            user_data_dir = call_kwargs.get("user_data_dir")
            assert user_data_dir is not None
            assert "testsite" in user_data_dir
            assert "keepalive_0" in user_data_dir
            # Headless stays true — these are background sessions.
            assert call_kwargs.get("headless") is True
    finally:
        restore()


def test_profile_dir_is_created_under_cwd():
    """Profile dir is created under cwd at profiles/<sid>/keepalive_<idx>.
    This separation from worker profiles (profiles/<sid>/worker_N) is
    load-bearing — concurrent downloads must not contend with the
    keep-alive's browser for the same on-disk profile."""
    from bulk_downloader.session_keeper import SessionKeeper
    fake_sync_api, _mock_ctx, _mock_chromium = _build_mock_playwright()
    restore = _install_fake_playwright(fake_sync_api)
    try:
        with _isolated_cwd() as td:
            keeper = SessionKeeper(
                "mysite", 3,
                {"login_url": "http://localhost"},
                lambda *_: (False, "test"))
            keeper._launch_browser()
            expected = td / "profiles" / "mysite" / "keepalive_3"
            assert expected.is_dir(), (
                f"Expected profile dir {expected} to exist; "
                f"keeper either used the wrong layout or never "
                f"created it")
    finally:
        restore()


def test_db_cookies_seeded_only_on_first_launch():
    """First launch (empty profile dir) → DB cookies are seeded into
    the context. Second launch (non-empty profile dir) → DB cookies
    are NOT re-injected. This is the whole point of the persistent
    context — re-injecting on every launch would wipe out any
    rotation the site did between heartbeats.

    The behavior is gated on whether the profile dir already has
    contents; we simulate the post-first-launch state by pre-creating
    a file in the profile dir."""
    from bulk_downloader.session_keeper import SessionKeeper

    fake_sync_api, mock_ctx, _mock_chromium = _build_mock_playwright()
    restore = _install_fake_playwright(fake_sync_api)
    try:
        with _isolated_cwd() as td:
            # Pre-create profile dir with some content — simulates
            # "we've launched here before, the persistent profile
            # owns the cookie state now"
            profile = td / "profiles" / "sitex" / "keepalive_0"
            profile.mkdir(parents=True)
            (profile / "Cookies").write_text("simulated browser state")
            # Pre-create a DB cookies file so _load_cookies_as_playwright
            # would return non-empty if called.
            cookies_dir = td / "cookies"
            cookies_dir.mkdir()
            (cookies_dir / "sitex.json").write_text(
                '[{"name": "s", "value": "v", "domain": ".x.com", "path": "/"}]')

            keeper = SessionKeeper(
                "sitex", 0,
                {"login_url": "http://x.com", "success_url": "http://x.com"},
                lambda *_: (False, "test"))
            ok = keeper._launch_browser()
            assert ok

            # The key assertion: add_cookies was NOT called because the
            # profile already existed. If it WAS called, the persistent
            # context is being overwritten with stale DB state on every
            # launch — the bug this fix exists to close.
            assert not mock_ctx.add_cookies.called, (
                "add_cookies was called on a non-empty profile dir — "
                "the keeper is overwriting persistent cookies with "
                "stale DB state, which is the exact bug v3.64.2 "
                "fixed. The persistent profile is the source of "
                "truth after the first launch.")
    finally:
        restore()


def test_db_cookies_seeded_when_profile_is_fresh():
    """The complement of the previous test: when there's no existing
    profile (first launch ever, or the dir was deleted), DB cookies
    SHOULD seed the new context. Otherwise the keeper has to log in
    from scratch on every fresh-install boot — losing the runner's
    last-known-good session state."""
    from bulk_downloader.session_keeper import SessionKeeper

    fake_sync_api, mock_ctx, _mock_chromium = _build_mock_playwright()
    restore = _install_fake_playwright(fake_sync_api)
    try:
        with _isolated_cwd() as td:
            # DO NOT pre-create the profile dir. DB cookies file
            # exists with one cookie.
            cookies_dir = td / "cookies"
            cookies_dir.mkdir()
            (cookies_dir / "freshsite.json").write_text(
                '[{"name": "s", "value": "v", '
                '"domain": ".fresh.com", "path": "/"}]')

            keeper = SessionKeeper(
                "freshsite", 0,
                {"login_url": "http://fresh.com",
                 "success_url": "http://fresh.com"},
                lambda *_: (False, "test"))
            ok = keeper._launch_browser()
            assert ok

            # add_cookies WAS called on the fresh profile, seeding
            # the cookie loaded from cookies/freshsite.json.
            assert mock_ctx.add_cookies.called, (
                "Expected add_cookies on a fresh profile so the "
                "keeper inherits the runner's last login state. "
                "Got no call — first-launch seed is broken.")
    finally:
        restore()
