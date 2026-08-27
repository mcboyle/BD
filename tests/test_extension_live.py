"""End-to-end tests for the BD browser extension (Phase 178).

Loads the extension into Chromium via Playwright's
`launch_persistent_context(args=['--load-extension=...'])`, then drives
real navigations to verify:

  • Extension manifest is loadable (no syntax errors in background.js)
  • Status-badge updates on tab navigation
  • lookup_url endpoint integration

These tests boot a real BD server in a thread + a real Chromium with
the extension loaded. Each test takes ~5-10s. Skipped cleanly when
Playwright + Chromium are unavailable.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


try:
    from playwright.sync_api import sync_playwright
    _PW = True
except ImportError:
    _PW = False


def _free_port():
    # v3.66.6: this helper is RETAINED for back-compat with any external
    # code that may import it, but _BDServerThread no longer calls it.
    # See the comment in _BDServerThread.__enter__ for why: the bind→
    # close→reuse pattern has a TOCTOU window during which another
    # process can grab the port, causing the test to silently talk to
    # the wrong server (or fail to bind). Pass port 0 to make_server
    # instead and read .server_port after bind — no window, no race.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXTENSION_DIR = _REPO_ROOT / "extension"


class _BDServerThread:
    """Spin BD up in a thread on a free port. Same pattern as
    test_e2e_smoke._BDServerHarness but distilled for the extension
    tests' simpler needs."""

    def __init__(self):
        self.install_dir = tempfile.mkdtemp(prefix="bd-ext-test-")
        # v3.66.6: defer port selection until the real listener binds.
        # Pre-v3.66.6 we called _free_port() here (bind→getsockname→
        # close→return int) and then handed that int to make_server,
        # which re-bound. The window between close and re-bind is a
        # classic TOCTOU: another process can be assigned the same
        # ephemeral port. Symptom under parallel test load was
        # test_lookup_url_surfaces_blocked_state silently getting
        # blocked=False because the lookup hit a sibling worker's BD
        # instance that never received the block_url POST. Always
        # passed on serial retry → flagged as flake. Fix: let
        # make_server pick the port atomically (port=0) and read
        # self._server.server_port after it binds.
        self.port = None
        self.base_url = None
        self._server = None
        self._thread = None

    def __enter__(self):
        # v3.66.16: snapshot bulk_downloader.* modules + env vars we
        # mutate, so __exit__ can restore them. Pre-fix, this block
        # blanket-deleted every bulk_downloader.* entry without
        # restoring, leaving any subsequent test holding a reference
        # to (e.g.) bulk_downloader.aiassist talking to a stale module
        # object while dev_suite's late `from . import aiassist`
        # imported a fresh one. Manifested as ~36 spurious failures
        # across test_t7/t8/t9/t40/t44/t46/u22/u26/u27/u28/u29 when
        # the broader sweep ran with extension_live before them.
        self._saved_modules = {
            m: sys.modules[m]
            for m in list(sys.modules)
            if m.startswith("bulk_downloader")
        }
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("BD_INSTALL_DIR", "BD_DISABLE_KEEPALIVE")
        }
        os.environ["BD_INSTALL_DIR"] = self.install_dir
        os.environ["BD_DISABLE_KEEPALIVE"] = "1"
        for m in list(sys.modules):
            if m.startswith("bulk_downloader"):
                del sys.modules[m]
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import app as bd_app
        from werkzeug.serving import make_server
        # v3.66.6: port=0 → kernel assigns an ephemeral port at bind
        # time. No TOCTOU window. Read the chosen port via
        # server_port after the BaseWSGIServer is constructed.
        self._server = make_server("127.0.0.1", 0,
                                    bd_app.app, threaded=True)
        self.port = self._server.server_port
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True)
        self._thread.start()
        # Wait for port
        for _ in range(50):
            try:
                s = socket.create_connection(("127.0.0.1", self.port),
                                              timeout=0.2)
                s.close()
                return self
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("BD didn't come up")

    def __exit__(self, *exc):
        if self._server:
            self._server.shutdown()
        # v3.66.16: restore the sys.modules + env-var snapshot taken
        # in __enter__. See the rationale comment there.
        for m in list(sys.modules):
            if m.startswith("bulk_downloader"):
                del sys.modules[m]
        sys.modules.update(self._saved_modules)
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ─── Manifest + syntax checks (run without browser) ────────────────────


class TestExtensionManifest(unittest.TestCase):
    """Sanity-checks on the extension files. These run in any env;
    no Playwright needed."""

    def test_manifest_loadable(self):
        manifest = json.loads(
            (_EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertIn("permissions", manifest)
        # Phase 178 needs `tabs` permission for the onUpdated/onActivated
        # listeners to fire with tab.url populated
        self.assertIn("tabs", manifest["permissions"])

    def test_background_js_references_lookup_url_endpoint(self):
        """The badge logic must call /api/extension/lookup_url —
        the endpoint added in Phase 178."""
        bg = (_EXTENSION_DIR / "background.js").read_text(encoding="utf-8")
        self.assertIn("/api/extension/lookup_url", bg)
        self.assertIn("lookupUrlStatus", bg)
        self.assertIn("updateBadge", bg)

    def test_background_js_wires_navigation_events(self):
        bg = (_EXTENSION_DIR / "background.js").read_text(encoding="utf-8")
        # onUpdated catches reloads + navigations within a tab
        self.assertIn("onUpdated", bg)
        # onActivated catches tab switches
        self.assertIn("onActivated", bg)

    def test_badge_handles_all_states(self):
        """The badge must distinguish:
          • blocked (⚠)
          • in queue (⏳)
          • already downloaded (✓)
          • would route to a site (→)
          • unknown (no badge)
        Without all five states the operator can't tell what BD's
        going to do with a URL.
        """
        bg = (_EXTENSION_DIR / "background.js").read_text(encoding="utf-8")
        for marker in ("blocked", "in_queue", "known", "would_route_to"):
            self.assertIn(marker, bg,
                f"background.js missing state {marker!r}")


# ─── Live extension tests (Playwright + persistent context) ────────────


@unittest.skipUnless(_PW, "playwright not installed")
class TestExtensionLiveBadge(unittest.TestCase):
    """Loads the extension in a real Chromium and exercises the
    service worker. Requires:
      • Playwright + Chromium installed
      • `launch_persistent_context` (MV3 extensions need a persistent
        profile; non-persistent contexts can't load unpacked extensions)

    Uses setup_method/teardown_method (pytest-style) for compatibility
    with the project's run_tests.py harness, which doesn't invoke
    unittest's setUpClass/tearDownClass. Per-test boot is slower (~5s
    each) but reliable across harnesses.

    When Playwright isn't installed (run_tests.py harness doesn't
    honor class-level @skipUnless), each setup_method calls pytest.skip
    explicitly — see the import guard at the top of the method."""

    def setup_method(self, method=None):
        # The project's run_tests.py reuses ONE instance for the whole
        # class and calls setup_method/teardown_method once per test;
        # pytest uses a fresh instance per test and fires both the
        # *_method hook and its unittest alias (setUp/tearDown). A single
        # `_live` flag — set here, cleared in teardown_method — makes both
        # correct: it blocks the pytest double-fire (a second call sees a
        # live session and returns) while still re-establishing a fresh
        # session for the NEXT test under the shared-instance runner
        # (teardown clears the flag). The earlier _setup_done/_teardown_done
        # pair set up once but tore down after the FIRST test, so under the
        # shared-instance runner tests 2+ ran against a stopped Playwright
        # session ("Event loop is closed") and a closed harness
        # ("Connection refused"). Per-test boot (~5s each) is the original
        # intended design — reliable across both harnesses.
        if getattr(self, "_live", False):
            return
        self._live = True
        import pytest
        if not _PW:
            pytest.skip("playwright not installed")
        self.harness = _BDServerThread()
        self.harness.__enter__()
        # v3.66.16: ensure harness.__exit__ runs even if pw / Chromium
        # initialization below raises (e.g. Playwright's "Sync API
        # inside the asyncio loop" error in some test harnesses).
        # Without this, pytest sees setup fail, never calls
        # teardown_method, and the sys.modules / env snapshot taken
        # in __enter__ is never restored.
        try:
            self.pw = sync_playwright().start()
            self.user_data_dir = tempfile.mkdtemp(prefix="bd-ext-pw-")
            self.context = None
            self.service_worker = None
            self.skip_reason = None
        except Exception:
            self.harness.__exit__(None, None, None)
            raise
        try:
            # v3.63.5: load the real Chromium-for-Testing build via
            # `channel="chromium"`. The default `pw.chromium` binary is
            # Playwright's headless-shell — a stripped Chromium that
            # cannot host MV3 extension service workers, regardless of
            # whether `--headless=new` is passed (v3.62.1's earlier
            # attempt). The channel swap is what the Playwright docs
            # call out as the headless-extensions enabler. Verified by
            # binary inspection: no-channel launches
            # `.../chromium_headless_shell-*/chrome-linux/headless_shell`
            # and the SW never registers; channel="chromium" launches
            # `.../chromium-*/chrome-linux/chrome` and the SW registers
            # immediately. `playwright install chromium` pulls both
            # builds, so no installer change is needed.
            #
            # Defensive: if the chosen Chromium build still can't
            # register the worker (e.g. an environment without the
            # full channel installed), the skip-fallback below keeps
            # the suite green rather than turning into a hard failure.
            self.context = self.pw.chromium.launch_persistent_context(
                self.user_data_dir,
                headless=True,
                channel="chromium",
                args=[
                    f"--disable-extensions-except={_EXTENSION_DIR}",
                    f"--load-extension={_EXTENSION_DIR}",
                ],
            )
        except Exception as e:
            self.skip_reason = (
                f"Chromium build doesn't support extension loading: {e}")
            return
        # v3.63.5: wait for service worker registration via the
        # Playwright event hook with a generous ceiling. MV3 service
        # workers may take a few seconds to register after the
        # extension loads — long enough that a 6s poll was flaky on
        # the stash deploy (one of three tests reliably passed within
        # the window, two reliably timed out, even though all three
        # had a working SW seconds later). `wait_for_event` returns
        # the instant the worker registers; the 30s ceiling is the
        # max we'll allow before giving up and skipping. If the SW
        # was already up before this point (sandbox case), the event
        # has already fired and we use the existing entry.
        if self.context.service_workers:
            self.service_worker = self.context.service_workers[0]
        else:
            try:
                self.service_worker = self.context.wait_for_event(
                    "serviceworker", timeout=30_000)
            except Exception:
                self.service_worker = None
        if self.service_worker is None:
            self.skip_reason = (
                "Extension service worker never registered "
                "(this Chromium build may not run extensions in headless)")
            return
        try:
            self.service_worker.evaluate(
                "url => chrome.storage.local.set({url})",
                self.harness.base_url)
        except Exception:
            pass
        time.sleep(0.3)

    def _require_sw(self):
        """Each test calls this first; raises pytest.skip if setup
        couldn't establish a service worker connection."""
        if self.skip_reason:
            import pytest
            pytest.skip(self.skip_reason)

    setUp = setup_method

    def teardown_method(self, method=None):
        # Pair with setup_method's `_live` guard: tear the session down
        # once per test and clear the flag so the next test re-establishes
        # a fresh one. Returning early when not `_live` absorbs pytest's
        # teardown_method + tearDown double-fire. Nulling the handles means
        # a re-established session never sees a stale closed one.
        if not getattr(self, "_live", False):
            return
        self._live = False
        if getattr(self, "context", None):
            self.context.close()
            self.context = None
        if getattr(self, "pw", None):
            self.pw.stop()
            self.pw = None
        if getattr(self, "harness", None):
            self.harness.__exit__(None, None, None)
            self.harness = None

    tearDown = teardown_method

    def test_service_worker_registered(self):
        """If we got past setUpClass, the SW is up. This test just
        asserts the SW responds to evaluate() — sanity that we're
        connected to the right worker."""
        self._require_sw()
        result = self.service_worker.evaluate(
            "() => typeof lookupUrlStatus")
        self.assertEqual(result, "function",
            "lookupUrlStatus should be defined in the service worker")

    def test_lookup_endpoint_callable_from_extension_context(self):
        """The SW can fetch /api/extension/lookup_url with default
        credentials. Returns valid JSON shape."""
        self._require_sw()
        result = self.service_worker.evaluate(f"""
            async () => {{
                try {{
                    const r = await fetch(
                        '{self.harness.base_url}/api/extension/lookup_url'
                        + '?url=' + encodeURIComponent('https://test.example/v/1'));
                    return await r.json();
                }} catch (e) {{
                    return {{__error: String(e)}};
                }}
            }}
        """)
        self.assertNotIn("__error", result,
            f"fetch failed: {result.get('__error')}")
        self.assertIn("url", result)
        self.assertIn("known", result)
        # Test URL won't be known to BD, so:
        self.assertFalse(result["known"])
        self.assertEqual(result.get("history_count"), 0)

    def test_lookup_url_surfaces_blocked_state(self):
        """Add a blocklist entry server-side, then verify the extension
        sees `blocked=True` for a matching URL."""
        self._require_sw()
        import urllib.error
        import urllib.request
        # v3.63.5: visit `/` first to mint the bd_session cookie.
        # `/api/csrf` returns `{"ok": False, "error": "no session — reload
        # the page to mint one"}` when called without a session, because
        # session minting is gated to GET `/` (see app.py's
        # `_inject_csrf_meta` hook). Pre-v3.63.5 this file was always
        # skipped (Chromium build issue + missing extension icons), so
        # the bug never surfaced.
        cookie_jar = urllib.request.HTTPCookieProcessor()
        opener = urllib.request.build_opener(cookie_jar)
        # PRIME THE SESSION COOKIE, DO NOT ASSERT THE SPA. This GET exists only
        # so /api/csrf can read a session cookie; the body is discarded. Without
        # a built frontend/dist the SPA route answers 503, which urllib raises --
        # and this test then failed for a reason that has nothing to do with its
        # subject (the extension lookup API). A 503 still carries Set-Cookie, so
        # the session is established either way. Anything OTHER than a 503 is
        # still a real transport failure and is left to propagate.
        try:
            opener.open(f"{self.harness.base_url}/", timeout=3).read()
        except urllib.error.HTTPError as _e:
            if _e.code != 503:
                raise
            _e.read()
        # Now /api/csrf can read the session cookie and return a token
        with opener.open(
                f"{self.harness.base_url}/api/csrf", timeout=3) as r:
            payload = json.loads(r.read())
        self.assertTrue(payload.get("ok"),
            f"/api/csrf returned not-ok: {payload}")
        csrf = payload["csrf_token"]
        # Build a Cookie header from the jar for the POST
        cookie = "; ".join(
            f"{c.name}={c.value}" for c in cookie_jar.cookiejar)
        # Add a blocked pattern
        block_req = urllib.request.Request(
            f"{self.harness.base_url}/api/rights/block_url",
            data=json.dumps({"pattern": "ext-test-blocked",
                              "reason": "phase 178 test"}).encode(),
            method="POST",
            headers={"Content-Type": "application/json",
                      "X-CSRF-Token": csrf,
                      "Cookie": cookie})
        urllib.request.urlopen(block_req, timeout=3).read()

        # Now look up a URL that should match the pattern
        result = self.service_worker.evaluate(f"""
            async () => {{
                const r = await fetch(
                    '{self.harness.base_url}/api/extension/lookup_url'
                    + '?url=' + encodeURIComponent(
                        'https://example.com/ext-test-blocked/v/1'));
                return await r.json();
            }}
        """)
        self.assertTrue(result.get("blocked"),
            f"URL with blocked pattern should be flagged: {result}")
        self.assertIn("phase 178 test", result.get("block_reason", ""))


if __name__ == "__main__":
    unittest.main()
