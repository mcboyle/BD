"""Regression/acceptance tests for row914 -- SPA DOM-mutation settlement hooks.

Row 914 evidence: asynchronous client-side route transitions (pushState +
async component render) cause extractors to query DOM nodes before the new
view has finished mutating the DOM. ``BrowserMixin._install_spa_settlement_hooks``
installs a ``window.history.pushState``/``replaceState`` wrapper plus a
``MutationObserver`` so a caller can detect "a route transition just
happened" and then block on "the DOM has gone quiet since that transition"
(a settlement barrier) instead of guessing a fixed sleep.

Two tiers, matching the ``test_dom_recorder_asi.py`` convention:
  * **static** (stdlib only) -- the injected script text has the properties
    the barrier depends on (pushState/replaceState wrapped, MutationObserver
    installed, idempotent re-install guard) without needing a browser.
  * **behavioral** (Playwright; skipped cleanly when no browser is available)
    -- against a real SPA fixture (intercepted HTTP route, never a data URL,
    per this codebase's established fixture convention) prove: (1) a
    ``pushState`` route transition is detected, (2) the settlement barrier
    only reports settled once async DOM mutation from the new route has
    actually finished (not immediately on navigation), (3) racing extraction
    against a fast route flurry never observes a stale settlement mid-batch.
"""
import re

import pytest
from bulk_downloader.runner_browser import BrowserMixin

BD_GATE_SCOPE = "module"

try:  # behavioral tier is optional
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - environment without playwright
    sync_playwright = None


# ------------------------------------------------------------- static tier

def test_hooks_script_wraps_pushstate_and_replacestate():
    script = BrowserMixin._spa_settlement_script()
    assert "history.pushState" in script
    assert "history.replaceState" in script


def test_hooks_script_installs_mutation_observer():
    script = BrowserMixin._spa_settlement_script()
    assert "MutationObserver" in script
    assert re.search(r"\.observe\(", script), "observer must call .observe(...)"


def test_hooks_script_is_idempotent_against_double_install():
    """A re-install (e.g. add_init_script surviving + a manual evaluate on an
    already-loaded page, matching this codebase's affordance_learning.py
    pattern) must not wrap pushState twice -- a double wrap would double the
    nav counter per navigation and desync the settlement barrier."""
    script = BrowserMixin._spa_settlement_script()
    assert "__bd_spa_hooked" in script
    assert "if (window.__bd_spa_hooked) return" in script or \
        "if(window.__bd_spa_hooked)return" in script.replace(" ", "")


def test_hooks_script_has_no_cdn_or_network_io():
    script = BrowserMixin._spa_settlement_script()
    for host in ("unpkg", "jsdelivr", "cdnjs", "cdn.skypack", "//cdn"):
        assert host not in script
    for net in ("fetch(", "XMLHttpRequest", "import(", "importScripts("):
        assert net not in script


# --------------------------------------------------------- behavioral tier

_FIXTURE_URL = "http://spa-settlement-fixture.test/app"
# Simulates a client-side router: a nav() call does pushState immediately,
# then mutates the DOM only after an async delay (setTimeout), matching the
# row's evidence -- "extractors query DOM nodes before components finish
# rendering". The route's new content lands at t=+120ms after nav().
_FIXTURE_HTML = (
    "<body><div id='view'>home</div>"
    "<script>"
    "function nav(path, label){"
    "  history.pushState({}, '', path);"
    "  setTimeout(function(){"
    "    var v = document.getElementById('view');"
    "    v.textContent = label;"
    "    var extra = document.createElement('span');"
    "    extra.id = 'extra';"
    "    extra.textContent = 'loaded';"
    "    v.appendChild(extra);"
    "  }, 120);"
    "}"
    "</script></body>"
)


def _install_fixture_route(page):
    """Serve the fixture as an intercepted HTTP document, never a data URL."""
    page.route(
        _FIXTURE_URL,
        lambda route: route.fulfill(
            status=200, content_type="text/html", body=_FIXTURE_HTML),
    )


def _launch():
    # FLEET_RULE 46: a browser test fails closed -- no skip when the browser
    # is missing; the runtime this repo pins ships playwright + chromium.
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.chromium.launch(
        headless=True, timeout=20000,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    return p, browser


def test_route_transition_is_detected_on_spa_fixture():
    """Acceptance (1): a pushState route transition increments the nav
    counter the hooks expose."""
    p, browser = _launch()
    try:
        page = browser.new_page()
        _install_fixture_route(page)
        mixin = BrowserMixin()
        mixin._install_spa_settlement_hooks(page)
        page.goto(_FIXTURE_URL, wait_until="load")
        before = page.evaluate("window.__bd_spa_nav_count")
        page.evaluate("nav('/detail', 'detail-view')")
        after = page.evaluate("window.__bd_spa_nav_count")
        page.close()
    finally:
        browser.close(); p.stop()
    assert after == before + 1, (
        f"pushState route transition not detected: nav_count {before} -> {after}"
    )


def test_settlement_barrier_waits_for_async_mutation_not_just_navigation():
    """Acceptance (2): the barrier must not report settled at the instant of
    navigation -- it must wait for the DOM mutation that follows the async
    render to actually happen and quiesce."""
    p, browser = _launch()
    try:
        page = browser.new_page()
        _install_fixture_route(page)
        mixin = BrowserMixin()
        mixin._install_spa_settlement_hooks(page)
        page.goto(_FIXTURE_URL, wait_until="load")
        page.evaluate("nav('/detail', 'detail-view')")
        # Immediately after nav(), the async render (120ms) has not run yet --
        # the barrier must NOT already claim settlement.
        settled_immediately = page.evaluate("window.__bd_spa_settled === true")
        settled = mixin._wait_for_spa_settlement(page, timeout_ms=3000)
        text = page.evaluate("document.getElementById('view').textContent")
        extra_present = page.evaluate("!!document.getElementById('extra')")
        page.close()
    finally:
        browser.close(); p.stop()
    assert not settled_immediately, (
        "barrier reported settled before the async DOM mutation ran -- "
        "this is exactly the race the row exists to close"
    )
    assert settled, "settlement barrier timed out waiting for the async render"
    assert "detail-view" in text, f"DOM not actually updated when settled: {text!r}"
    assert extra_present, "settlement reported before the appended node landed"


def test_no_stale_settlement_across_a_fast_route_flurry():
    """Acceptance (3): zero race conditions during extraction -- firing a
    second navigation before the first has settled must not let the barrier
    report settled against the FIRST (stale) navigation's nav count."""
    p, browser = _launch()
    try:
        page = browser.new_page()
        _install_fixture_route(page)
        mixin = BrowserMixin()
        mixin._install_spa_settlement_hooks(page)
        page.goto(_FIXTURE_URL, wait_until="load")
        page.evaluate("nav('/a', 'view-a')")
        nav_count_after_first = page.evaluate("window.__bd_spa_nav_count")
        # Fire a second nav before the first's async render (120ms) completes.
        page.wait_for_timeout(30)
        page.evaluate("nav('/b', 'view-b')")
        settled = mixin._wait_for_spa_settlement(page, timeout_ms=3000)
        settled_nav = page.evaluate("window.__bd_spa_settled_nav")
        text = page.evaluate("document.getElementById('view').textContent")
        page.close()
    finally:
        browser.close(); p.stop()
    assert settled, "settlement barrier timed out during the route flurry"
    assert settled_nav > nav_count_after_first, (
        "settlement reported against the stale first navigation's nav count, "
        "not the latest one -- extraction could read the first route's DOM"
    )
    assert "view-b" in text, f"expected the LATEST route's DOM, got {text!r}"


# ---- fixer (O928) controls: correctness REFUTE E1 / E2 ----

class _Runner(BrowserMixin):
    config = {"browser_asset_filter": False}

def test_never_quiet_dom_settles_within_the_bound_not_the_full_timeout():
    """E2: a spinner/ticker mutating every 30 ms used to reset the quiet timer
    forever, forcing the caller's full timeout; the barrier now settles at
    most maxSettleMs (1.5 s) after the latest transition."""
    import time
    p, browser = _launch()
    try:
        page = browser.new_page()
        _install_fixture_route(page)
        r = _Runner()
        assert r._install_spa_settlement_hooks(page) is True
        page.goto(_FIXTURE_URL, wait_until="load")
        page.evaluate("""() => {
            const tick = document.createElement('div'); tick.id = 'tick'; document.body.appendChild(tick);
            let n = 0;
            window.__bd_ticker = setInterval(() => { tick.textContent = String(++n); }, 30);
            history.pushState({}, '', '/route-with-spinner');
        }""")
        t0 = time.perf_counter()
        assert r._wait_for_spa_settlement(page, timeout_ms=10000) is True
        elapsed = time.perf_counter() - t0
        assert elapsed < 4.0, f"settled after {elapsed:.2f}s (bound is 1.5 s + quiet)"
        assert page.evaluate("window.__bd_spa_settled_nav === window.__bd_spa_nav_count")
        # the ticker keeps mutating; the settled route stays settled
        page.wait_for_timeout(200)
        assert page.evaluate("window.__bd_spa_settled") is True
        page.evaluate("clearInterval(window.__bd_ticker)")
    finally:
        browser.close(); p.stop()


def test_settle_after_navigation_is_the_workers_post_goto_hook():
    """E1: the extractor path calls _settle_after_navigation after goto; it
    arms the hooks and waits; config spa_settlement=False disables it."""
    calls = []

    class Page:
        def add_init_script(self, script):
            calls.append("init")

        def evaluate(self, script):
            calls.append("evaluate"); return True

        def wait_for_function(self, expr, timeout=None):
            calls.append(("wait", timeout))

    r = _Runner()
    assert r._settle_after_navigation(Page(), timeout_ms=1234) is True
    assert calls == ["init", "evaluate", ("wait", 1234)]
    r.config = {"spa_settlement": False}
    calls.clear()
    assert r._settle_after_navigation(Page()) is None and calls == []
    # the worker wires it right after the page load (runner.py)
    import ast
    import inspect
    from bulk_downloader import runner as _runner
    import textwrap
    fn = ast.unparse(ast.parse(textwrap.dedent(
        inspect.getsource(_runner.SiteRunner._process_one))))
    assert "page.goto(url, wait_until='domcontentloaded', timeout=30000)" in fn
    assert "self._settle_after_navigation(page)" in fn


def test_observer_attaches_when_installed_through_add_init_script():
    """Found while fixing E2: through add_init_script the hook runs at
    document start (no documentElement yet); observe() threw and the barrier
    silently degraded to navigation-only. The observer must be live after a
    real navigation, and a mutation after a route change must delay settlement."""
    p, browser = _launch()
    try:
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _install_fixture_route(page)
        r = _Runner()
        r._install_spa_settlement_hooks(page)
        page.goto(_FIXTURE_URL, wait_until="load")
        assert errors == [], errors
        assert page.evaluate("!!window.__bd_spa_observer") is True
        page.evaluate("""() => {
            history.pushState({}, '', '/late-mount');
            setTimeout(() => { const d = document.createElement('div'); d.id = 'mounted'; document.body.appendChild(d); }, 100);
        }""")
        assert r._wait_for_spa_settlement(page, timeout_ms=5000) is True
        assert page.evaluate("!!document.getElementById('mounted')") is True   # settled only after the late mount
    finally:
        browser.close(); p.stop()


def test_a_claimed_worker_url_is_processed_and_the_claim_slot_is_released():
    """Self-mutation seam (runner.py _process_worker_url -> _process_one):
    a claimed URL is handed to _process_one exactly once and reported
    PROCESSED; a URL whose claim did not succeed is never processed. The
    worker's current-url/generation slot is released either way."""
    import threading
    from bulk_downloader.runner import SiteRunner

    processed, updates = [], []
    r = SiteRunner.__new__(SiteRunner)
    r._worker_heartbeats_lock = threading.Lock()
    r._worker_url_generations, r._worker_current_urls = {7: 3}, {7: "http://a.invalid/x"}
    r._claim_worker_item = lambda idx, url, gen: ("claimed", 3)
    r._update_job = lambda url, status, msg, **kw: updates.append((url, status))
    r._process_one = lambda browser, url, persistent_ctx=None: processed.append((browser, url, persistent_ctx))

    out = r._process_worker_url(7, "browser", "http://a.invalid/x", persistent_ctx="ctx", run_generation=3)
    assert out == SiteRunner._WORKER_CLAIM_PROCESSED
    assert processed == [("browser", "http://a.invalid/x", "ctx")]
    assert updates == [("http://a.invalid/x", "running")]
    assert r._worker_current_urls == {} and r._worker_url_generations == {}

    r._claim_worker_item = lambda idx, url, gen: ("stale", 3)
    assert r._process_worker_url(7, "browser", "http://a.invalid/y") == "stale"
    assert len(processed) == 1                       # not processed without a claim
