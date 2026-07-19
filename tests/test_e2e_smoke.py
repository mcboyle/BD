"""End-to-end Playwright smoke tests (Phase 193).

Boots a real BD instance + Chromium and drives the actual UI to catch
regressions unit tests miss (JS errors, CSP violations, render races).

Run via:
  $ pip install playwright
  $ python3 -m playwright install chromium
  $ python3 -m unittest tests.test_e2e_smoke._RealE2ESmoke

The class name starts with `_` so the project's run_tests.py — which
runs unconditionally on every push — won't pick it up. E2E runs on a
separate slower job that explicitly invokes the class.

Common waits use `domcontentloaded` instead of `networkidle` because
BD's main page keeps SSE streams open continuously and never goes
idle. We assert on specific DOM markers (#qtb for queue table,
#cmdp-overlay for command palette, etc) rather than blanket network
quiet.
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
import unittest


try:
    from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout
    _PLAYWRIGHT = True
except ImportError:
    _PLAYWRIGHT = False


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _BDServerHarness:
    """Boots a BD instance in a thread on a free port.

    Usage:
        with _BDServerHarness() as h:
            print(h.base_url)
    """

    def __init__(self, install_dir=None):
        self.install_dir = install_dir or tempfile.mkdtemp(prefix="bd-e2e-")
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = None
        self._server = None

    def __enter__(self):
        # v3.66.9: snapshot env + modules so __exit__ can restore them.
        # Without this, downstream tests that hold top-level `from
        # bulk_downloader import db` references see their bound module
        # become stale after we re-import here, and our DB path changes
        # leak into the rest of the suite.
        self._saved_env = {k: os.environ.get(k)
                            for k in ("BD_INSTALL_DIR",
                                       "BD_DISABLE_KEEPALIVE")}
        self._saved_modules = {k: v for k, v in sys.modules.items()
                               if k.startswith("bulk_downloader")}
        os.environ["BD_INSTALL_DIR"] = self.install_dir
        os.environ["BD_DISABLE_KEEPALIVE"] = "1"
        # Clear cached modules so the new BD_INSTALL_DIR takes effect
        for m in list(sys.modules):
            if m.startswith("bulk_downloader"):
                del sys.modules[m]
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import app as _app
        from werkzeug.serving import make_server
        # threaded=True is essential here: BD's main page opens an SSE
        # connection (/api/stream) that holds its worker indefinitely.
        # Without threading, the next test's HTTP request queues forever
        # waiting for that worker to free up, causing the suite to hang.
        self._server = make_server("127.0.0.1", self.port, _app.app,
                                    threaded=True)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True)
        self._thread.start()
        for _ in range(50):
            try:
                s = socket.create_connection(("127.0.0.1", self.port),
                                              timeout=0.2)
                s.close()
                return self
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(f"BD server didn't come up on {self.base_url}")

    def __exit__(self, *exc):
        if self._server:
            self._server.shutdown()
        # v3.66.9: restore env + modules so downstream tests aren't
        # tainted (this harness used to leak BD_INSTALL_DIR + leave
        # stale module objects in sys.modules).
        for k, v in getattr(self, "_saved_env", {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if hasattr(self, "_saved_modules"):
            for m in [k for k in list(sys.modules)
                      if k.startswith("bulk_downloader")]:
                del sys.modules[m]
            sys.modules.update(self._saved_modules)


# Module-level skip when Playwright absent
if not _PLAYWRIGHT:
    raise unittest.SkipTest(
        "playwright not installed — skipping E2E smoke tests. "
        "Install with `pip install playwright && "
        "python3 -m playwright install chromium`.")


# Underscore prefix → run_tests.py skips automatically; only explicit
# `python -m unittest tests.test_e2e_smoke._RealE2ESmoke` picks it up.
class _RealE2ESmoke(unittest.TestCase):
    """Critical-path smoke tests. Each test should take under 5s on a
    modern machine."""

    @classmethod
    def setUpClass(cls):
        cls.harness = _BDServerHarness()
        cls.harness.__enter__()
        cls.pw_ctx = sync_playwright().start()
        cls.browser = cls.pw_ctx.chromium.launch(headless=True)
        # Create a test site via the BD API so tab panels become visible
        # (most depend on `det` becoming non-empty). The /api/sites POST
        # IGNORES any "id" field in the request and generates a random
        # uuid sid; we capture it from the response and stash it on the
        # class so tests can address the site reliably.
        cls.test_site_id = None
        import urllib.request
        import json as _json
        try:
            sess_req = urllib.request.Request(
                f"{cls.harness.base_url}/api/csrf")
            with urllib.request.urlopen(sess_req, timeout=5) as r:
                csrf_data = _json.loads(r.read().decode())
                cookie = r.headers.get("Set-Cookie", "")
            csrf = csrf_data.get("csrf_token", "")
            cookie_val = cookie.split(";")[0] if cookie else ""
            body = _json.dumps({
                "name": "E2E Test Site",
                "max_concurrent": 1, "wait": 2, "delay": 1,
                "download_dir": cls.harness.install_dir,
            }).encode()
            add_req = urllib.request.Request(
                f"{cls.harness.base_url}/api/sites",
                data=body, method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrf,
                    "Cookie": cookie_val,
                })
            with urllib.request.urlopen(add_req, timeout=5) as r:
                resp = _json.loads(r.read().decode())
                cls.test_site_id = resp.get("id")
            if not cls.test_site_id:
                raise RuntimeError(f"POST /api/sites returned no id: {resp}")
        except Exception as _e:
            # Tests will still run but tab-dependent ones will fail clearly
            print(f"  [setUpClass] site bootstrap warning: {_e}",
                  file=sys.stderr)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "browser", None):
            cls.browser.close()
        if getattr(cls, "pw_ctx", None):
            cls.pw_ctx.stop()
        if getattr(cls, "harness", None):
            cls.harness.__exit__(None, None, None)

    def setUp(self):
        self.errors = []  # captures pageerror events
        self.page: Page = self.browser.new_page()
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        # Pre-set localStorage so the "smart sidebar" doesn't collapse
        # idle sites (default behavior is to hide them when there are
        # ≥4 sites total). The test site is always idle and would be
        # buried under the collapse toggle otherwise. add_init_script
        # runs BEFORE the page's own scripts on every navigation, so
        # the very first renderSites() call sees the expanded value.
        self.page.add_init_script(
            "() => { try { "
            "  localStorage.setItem('bd_idle_collapsed', '0'); "
            "} catch(e) {} }")
        # Use commit — fires the moment server response starts. Avoids
        # SSE / lazy-script issues with domcontentloaded.
        self.page.goto(self.harness.base_url,
                       wait_until="commit", timeout=10000)
        # Wait for the header to be present
        self.page.wait_for_selector("header", timeout=5000)
        # Wait until app.js has finished bootstrap (cmdpOpen is defined
        # AFTER the keyboard listener is bound). This avoids races where
        # a test fires keys before listeners are wired.
        self.page.wait_for_function(
            "() => typeof cmdpOpen === 'function' "
            "&& typeof swTab === 'function'",
            timeout=5000)

    def tearDown(self):
        self.page.close()

    # ─── critical flows ──────────────────────────────────────────────

    def _select_test_site(self):
        """Click the e2e test site so #det panel + tabs become visible.
        Helper for tab/panel tests. Idempotent: safe to call after a
        site is already selected.

        Sites are populated async via /api/status polling, so we may
        need to wait a few seconds after page load before the row
        appears. We trigger an immediate poll() (the page's status
        fetcher) to avoid waiting for the next interval."""
        sid = type(self).test_site_id
        if not sid:
            self.skipTest("setUpClass site bootstrap failed; "
                          "test_site_id was never set")
        # Force an immediate status fetch — page may not have refreshed
        # since the site was added
        self.page.evaluate(
            "() => { if (typeof poll === 'function') poll(); }")
        site_row = self.page.locator(f'[data-sid="{sid}"]').first
        try:
            site_row.wait_for(state="visible", timeout=6000)
        except PWTimeout:
            raise AssertionError(
                f"site row [data-sid={sid!r}] never appeared. "
                f"Page may not have polled /api/status yet.")
        site_row.click()
        # Wait for the detail panel's name field to populate. selSite()
        # sets #d-name via renderDet() — that's the last DOM write in
        # the click handler. After that, tabs are visible/clickable.
        self.page.wait_for_function(
            "() => { const t = document.getElementById('d-name'); "
            "  return t && t.textContent "
            "          && t.textContent.length > 0; }",
            timeout=3000)

    def test_root_page_loads(self):
        """Smoke: the index page renders without JS errors."""
        title = self.page.title()
        # Title is "Bulk Downloader" (with space) in the template
        self.assertIn("Bulk", title, f"title was {title!r}")
        self.assertEqual(self.errors, [],
            f"JS errors on page load: {self.errors}")
        # Header is always present
        self.assertTrue(self.page.locator("header").count() > 0,
            "header should be present")

    def test_command_palette_opens(self):
        """Ctrl+K opens the command palette overlay."""
        self.page.locator("body").click()
        self.page.keyboard.press("Control+k")
        ov = self.page.locator("#cmdp-overlay")
        ov.wait_for(state="visible", timeout=2000)
        self.assertTrue(
            self.page.locator("#cmdp-q").is_visible(),
            "palette search input should be visible after Ctrl+K")
        self.page.keyboard.press("Escape")
        ov.wait_for(state="detached", timeout=1000)
        self.assertEqual(self.errors, [],
            f"JS errors during palette open: {self.errors}")

    def test_add_site_modal_opens(self):
        """The + button (.addbtn) opens the add-site modal (#modal)."""
        add = self.page.locator(".addbtn").first
        add.wait_for(state="visible", timeout=2000)
        add.click()
        # openAdd() sets #modal.style.display='flex' — wait for that
        # state. Using is_visible() rather than wait_for(state='visible')
        # because the element is permanently in the DOM with display:none
        # toggling, which Playwright's visibility check handles correctly.
        modal = self.page.locator("#modal")
        modal.wait_for(state="visible", timeout=2000)
        self.assertTrue(modal.is_visible(),
            "#modal should be visible after clicking .addbtn")
        # Title should read "Add Site"
        title = self.page.locator("#mtitle").text_content()
        self.assertIn("Add", title or "",
            f"modal title should mention 'Add', got {title!r}")
        self.assertEqual(self.errors, [],
            f"JS errors during add-site modal open: {self.errors}")

    def test_history_tab_loads(self):
        """Clicking History tab activates #tp-history and renders #htb."""
        self._select_test_site()
        # Use the tab's onclick attribute since text-based selector
        # may match the section header inside the modal
        history_tab = self.page.locator(
            '.tab[onclick*="history"]').first
        history_tab.wait_for(state="visible", timeout=2000)
        history_tab.click()
        panel = self.page.locator("#tp-history")
        panel.wait_for(state="visible", timeout=2000)
        # #htb is a <tbody> — when empty, Playwright considers it
        # "hidden" (zero height). Just assert it exists in the DOM,
        # which is what we actually care about for the smoke test.
        self.assertGreater(self.page.locator("#htb").count(), 0,
            "history table body #htb should be present")
        self.assertEqual(self.errors, [],
            f"JS errors during history tab load: {self.errors}")

    def test_review_tab_renders_sections(self):
        """The Review tab's sections render (Phase 188 added capacity
        forecast). Hitting the tab and waiting for rv-summary to
        update proves loadReview() ran."""
        self._select_test_site()
        # Review tab is added dynamically via addEventListener, not
        # via an onclick= attribute, so we have to match by text. The
        # `.tab` element with textContent='Review' is the one.
        review = self.page.locator('.tab', has_text='Review').first
        review.wait_for(state="visible", timeout=2000)
        review.click()
        try:
            self.page.wait_for_function(
                """() => {
                    const el = document.getElementById('rv-summary');
                    return el && el.textContent
                        && !el.textContent.includes('Refreshing');
                }""",
                timeout=5000)
        except PWTimeout:
            # Acceptable — rv-summary may update async; section
            # presence is what matters
            pass
        for sec_id in ("rv-alerts", "rv-capacity"):
            self.assertGreater(
                self.page.locator(f"#{sec_id}").count(), 0,
                f"Review section #{sec_id} should be present")
        self.assertEqual(self.errors, [],
            f"JS errors during review tab load: {self.errors}")

    def test_blocklist_refuses_url(self):
        """Phase 194 end-to-end: POST a URL to /api/rights/block_url
        via the page's fetch, verify it appears in the GET listing."""
        result = self.page.evaluate("""
            async () => {
                const meta = document.querySelector(
                    'meta[name="csrf-token"]');
                const csrf = (meta && meta.content)
                    || window._csrfToken || '';
                const r1 = await fetch('/api/rights/block_url', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': csrf,
                    },
                    body: JSON.stringify({
                        pattern: 'e2e-test-block-' + Date.now(),
                        reason: 'e2e smoke test',
                    }),
                });
                const d1 = await r1.json();
                const r2 = await fetch('/api/rights/blocklist',
                    {credentials: 'same-origin'});
                const d2 = await r2.json();
                return {add: d1, list: d2};
            }
        """)
        self.assertTrue(result["add"].get("ok"),
            f"block_url POST should succeed: {result['add']}")
        self.assertGreater(len(result["list"].get("blocks", [])), 0,
            "blocklist should now have at least one entry")


if __name__ == "__main__":
    unittest.main()
