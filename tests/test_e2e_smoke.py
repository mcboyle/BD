"""End-to-end Playwright smoke tests (Phase 193).

Boots a real BD instance + Chromium and drives the actual UI to catch
regressions unit tests miss (JS errors, CSP violations, render races).

Run directly via:
  $ pip install playwright
  $ python3 -m playwright install chromium
  $ python3 -m unittest tests.test_e2e_smoke._RealE2ESmoke

The canonical pytest suite also collects this class and keeps the file in
the serial lane.  The class name starts with `_` only for compatibility
with the legacy project runner, whose narrower discovery contract predates
the canonical pytest entrypoint.

Common waits use `domcontentloaded` instead of `networkidle` because
BD's main page keeps SSE streams open continuously and never goes
idle. We assert on observable React roles, labels, and rendered shell
markers rather than blanket network quiet or legacy window globals.
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
    from playwright.sync_api import sync_playwright, Page
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


# The canonical pytest suite collects this unittest class.  Its underscore
# prefix only preserves compatibility with the legacy project runner.
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
                # Empty means "use the configured default".  Pointing this
                # at the isolated install root conflicts with the fresh
                # install's independently seeded download-path allowlist.
                "download_dir": "",
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
        # Web-first readiness for the current React shell. The legacy shell
        # exposed cmdpOpen/swTab globals; the module-bundled SPA intentionally
        # does not. Wait for its observable root/header contract instead.
        root = self.page.locator("#root")
        root.wait_for(state="visible", timeout=5000)
        header = self.page.locator("header")
        header.wait_for(state="visible", timeout=5000)
        self.page.wait_for_function(
            "() => document.readyState === 'complete' "
            "&& document.querySelector('#root')?.children.length > 0",
            timeout=5000)
        self.assertIn("BulkDL", header.inner_text())

    def tearDown(self):
        self.page.close()

    # ─── critical flows ──────────────────────────────────────────────

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
        """Ctrl+K opens the current cmdk dialog and focuses its search."""
        self.page.locator("body").click()
        self.page.keyboard.press("Control+k")
        dialog = self.page.get_by_role("dialog")
        dialog.wait_for(state="visible", timeout=2000)
        search = dialog.get_by_placeholder(
            "Type a command or search sites…")
        search.wait_for(state="visible", timeout=2000)
        self.assertTrue(search.is_visible(),
            "palette search input should be visible after Ctrl+K")
        self.page.keyboard.press("Escape")
        dialog.wait_for(state="hidden", timeout=2000)
        self.assertEqual(self.errors, [],
            f"JS errors during palette open: {self.errors}")

    def test_escape_closes_palette_with_no_settle_delay(self):
        """Escape must close the palette even pressed the instant it opens.

        Radix's DismissableLayer (react-dismissable-layer 1.1.11) compares an
        `index` captured at RENDER time against `layers.size` read at EVENT
        time. Inside that settle window the guard is false and the handler
        RETURNS -- the keypress is DISCARDED, not queued -- so the dialog
        stays data-state="open" indefinitely and no timeout can rescue it.
        That is the v3.66.902 capture failure, which reproduced at 1/10 on the
        box and 4/65 in a container, always with data-state="open".

        Firing Escape from a MutationObserver the moment the dialog node is
        inserted lands inside the window deterministically: 10/10 swallowed on
        pristine source, vs 5/5 closed once the press is delayed 64ms. So this
        test is RED for the defect rather than flaky against it.
        """
        self.page.locator("body").click()
        # Arm BEFORE opening: fire Escape the instant the dialog node appears,
        # which is the tightest gap reachable and the one Radix loses.
        # Dispatch on the cmdk INPUT, not on the dialog div. A real Escape goes
        # to the focused element, which is that input, so targeting the div
        # would leave useKeyboardShortcut's allowInInput flag unconstrained --
        # a mutant flipping it to false escaped this test until the target was
        # fixed. isTextInput() only sees an <input>/<textarea>/contenteditable.
        self.page.evaluate("""
            () => {
              window.__bdEscapeFired = 0;
              window.__bdEscapeTarget = '';
              const obs = new MutationObserver(() => {
                if (window.__bdEscapeFired) return;
                const d = document.querySelector('[role="dialog"]');
                if (!d) return;
                const input = d.querySelector('[cmdk-input]');
                if (!input) return;
                window.__bdEscapeFired = 1;
                window.__bdEscapeTarget = input.tagName;
                input.dispatchEvent(new KeyboardEvent('keydown', {
                  key: 'Escape', code: 'Escape', keyCode: 27,
                  bubbles: true, cancelable: true,
                }));
              });
              obs.observe(document.body, {childList: true, subtree: true});
            }
        """)
        self.page.keyboard.press("Control+k")
        # Do NOT wait for visible: once the defect is fixed the dialog closes
        # in ~14ms and may never be observed visible. But "hidden" passes
        # trivially if the palette never OPENED, so the denominator needs its
        # own guard -- the observer only fires when a [role=dialog] node was
        # actually inserted, so this proves both that it opened and that
        # Escape was dispatched into the settle window.
        self.page.wait_for_function(
            "() => window.__bdEscapeFired === 1", timeout=5000)
        self.assertEqual(
            self.page.evaluate("() => window.__bdEscapeTarget"), "INPUT",
            "Escape must be dispatched at the focused cmdk input, else the "
            "allowInInput flag is unconstrained and a mutant removing it escapes")
        dialog = self.page.get_by_role("dialog")
        dialog.wait_for(state="hidden", timeout=2000)
        self.assertEqual(self.errors, [],
            f"JS errors during palette escape: {self.errors}")

    def test_add_site_modal_opens(self):
        """The Sites route's Add button opens the current add-site dialog."""
        self.page.goto(f"{self.harness.base_url}/sites",
                       wait_until="commit", timeout=10000)
        add = self.page.get_by_role(
            "button", name="Add site", exact=True).first
        add.wait_for(state="visible", timeout=2000)
        add.click()
        dialog = self.page.get_by_role("dialog")
        dialog.wait_for(state="visible", timeout=2000)
        title = dialog.get_by_role(
            "heading", name="Add site", exact=True)
        title.wait_for(state="visible", timeout=2000)
        self.assertTrue(dialog.is_visible(),
            "Add site dialog should be visible after clicking Add")
        self.assertEqual(self.errors, [],
            f"JS errors during add-site modal open: {self.errors}")

    def test_history_tab_loads(self):
        """The History route renders its default History & Search tab."""
        self.page.goto(f"{self.harness.base_url}/history",
                       wait_until="commit", timeout=10000)
        heading = self.page.get_by_role(
            "heading", name="History · Logs · Search", exact=True)
        heading.wait_for(state="visible", timeout=3000)
        history_tab = self.page.get_by_role(
            "tab", name="History & Search", exact=True)
        history_tab.wait_for(state="visible", timeout=2000)
        self.assertEqual(history_tab.get_attribute("aria-selected"), "true")
        search = self.page.get_by_placeholder(
            "Search history (FTS) — leave empty for recent")
        search.wait_for(state="visible", timeout=3000)
        self.assertEqual(self.errors, [],
            f"JS errors during history tab load: {self.errors}")

    def test_needs_review_route_renders(self):
        """The current global Needs review route renders its queue state."""
        self.page.goto(f"{self.harness.base_url}/needs-review",
                       wait_until="commit", timeout=10000)
        heading = self.page.get_by_role(
            "heading", name="Needs review", exact=True)
        heading.wait_for(state="visible", timeout=3000)
        # The harness DB has no queued jobs, so the stable empty-state marker
        # proves the route's query completed and its main content rendered.
        empty = self.page.get_by_text("No review items", exact=True)
        empty.wait_for(state="visible", timeout=3000)
        self.assertEqual(self.errors, [],
            f"JS errors during needs-review load: {self.errors}")

    def test_blocklist_refuses_url(self):
        """Phase 194 end-to-end: POST a URL to /api/rights/block_url
        via the page's fetch, verify it appears in the GET listing."""
        result = self.page.evaluate("""
            async () => {
                const csrfResponse = await fetch('/api/csrf', {
                    credentials: 'same-origin',
                });
                const csrfData = await csrfResponse.json();
                const csrf = csrfData.csrf_token
                    || csrfData.csrf || csrfData.token || '';
                const pattern = 'e2e-test-block-' + Date.now();
                const r1 = await fetch('/api/rights/block_url', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': csrf,
                    },
                    body: JSON.stringify({
                        pattern,
                        reason: 'e2e smoke test',
                    }),
                });
                const d1 = await r1.json();
                const r2 = await fetch('/api/rights/blocklist',
                    {credentials: 'same-origin'});
                const d2 = await r2.json();
                return {
                    csrfStatus: csrfResponse.status,
                    addStatus: r1.status,
                    pattern,
                    add: d1,
                    list: d2,
                };
            }
        """)
        self.assertEqual(result["csrfStatus"], 200)
        self.assertEqual(result["addStatus"], 200)
        self.assertTrue(result["add"].get("ok"),
            f"block_url POST should succeed: {result['add']}")
        self.assertTrue(
            any(block.get("pattern") == result["pattern"]
                for block in result["list"].get("blocks", [])),
            "blocklist should contain the pattern added by this test")


if __name__ == "__main__":
    unittest.main()
