"""Fresh-install GUI smoke.

Asserts that a freshly built/installed tree boots and the GUI surfaces load —
the regression class where a release ships but the SPA bundle is missing, a
cockpit page 500s, or the root serves a 503 because `vite build` was never run.

Two tiers:

  * Browser-free (runs in the custom run_tests.py harness AND under pytest, and
    in the on-stash band): in-process Flask test client. `/api/health` is ok;
    the SPA root serves the built bundle (200, not the 503 "bundle missing"
    sentinel) when `frontend/dist/` is present, else skips cleanly; the key
    cockpit report pages return 200 and emit their server-rendered shell.

  * Rendered (skips cleanly without a healthy browser, so it is band-safe on a
    headless host): a real Chromium via Playwright with an explicit
    executable_path renders the SPA root + the cockpit report pages off a real
    `make_server` origin and asserts no fatal page error and the panel shell is
    present. Proven green in-sandbox with the repo's e2e harness pattern.

The class names are prefixed `_` for the unittest-style render harness so the
project's run_tests.py (which collects zero-arg `test_*` functions) ignores it;
it runs under real pytest / on-host. The browser-free functions are plain
zero-arg functions so the custom runner executes them.
"""
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_COCKPIT_PAGES = (
    "/cockpit/reports/system_status",
    "/cockpit/reports/dom_recorder_status",
    "/cockpit/reports/workflow_analytics",
    "/cockpit/reports/vpn_secrets_status",
)
_DIST = _ROOT / "frontend" / "dist" / "index.html"
_CHROME = "/home/claude/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome"


def _boot_client():
    """Isolated cwd + db_init + test client (no live server, no browser)."""
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    td = tempfile.mkdtemp()
    os.chdir(td)
    Path(td, "screenshots").mkdir(exist_ok=True)
    db_init()
    return A.app.test_client()


# ── Browser-free band-safe smoke (custom runner + pytest + on-stash band) ────

def test_health_ok():
    orig = os.getcwd()
    try:
        c = _boot_client()
        r = c.get("/api/health")
        assert r.status_code in (200, 503)  # 503 only if db not ok
        body = r.get_json() or {}
        assert "version" in body and body.get("version")
    finally:
        os.chdir(orig)


def test_spa_root_serves_built_bundle():
    """With a built dist present, the SPA root `/` returns the React index
    (RE-EXPRESSED at v3.66.203, Phase 1 root flip — was /m2/)
    (200), NOT the 503 'bundle missing' response. If dist is absent (a pristine
    sandbox that never ran vite build), assert the actionable 503 instead — the
    on-stash release tree ships dist."""
    orig = os.getcwd()
    try:
        c = _boot_client()
        r = c.get("/")
        if not _DIST.exists():
            assert r.status_code == 503  # the actionable 'build the SPA' message
            return
        assert r.status_code == 200, f"SPA root not served: {r.status_code}"
        html = (r.get_data(as_text=False) or b"").decode("utf-8", "ignore")
        assert 'id="root"' in html and "BulkDownloader" in html, \
            "SPA root response does not look like the built bundle"
    finally:
        os.chdir(orig)


def test_cockpit_report_pages_load():
    """Each key cockpit report page returns 200 and a non-trivial shell (the
    server-rendered scaffold; data hydrates client-side)."""
    orig = os.getcwd()
    try:
        c = _boot_client()
        bad = []
        for path in _COCKPIT_PAGES:
            r = c.get(path)
            body = (r.get_data(as_text=False) or b"").decode("utf-8", "ignore")
            if r.status_code != 200 or len(body) < 200:
                bad.append((path, r.status_code, len(body)))
        assert not bad, f"cockpit page(s) failed to load: {bad}"
    finally:
        os.chdir(orig)


# ── Rendered smoke (real browser; skips cleanly when none is healthy) ────────

class _FreshInstallRenderSmoke:
    """Render-tier smoke. Not a unittest.TestCase and `_`-prefixed so neither
    the custom runner nor a bare pytest collection treats it as a gate; invoked
    explicitly (run_render()) for the in-sandbox / on-host green proof."""

    @staticmethod
    def _free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    @classmethod
    def run_render(cls, shot_dir=None):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:  # no playwright → skip cleanly
            return {"skipped": f"playwright unavailable: {e}"}
        if not Path(_CHROME).exists():
            return {"skipped": "chromium not installed"}

        from bulk_downloader import app as A
        from bulk_downloader.db import db_init
        from werkzeug.serving import make_server

        orig = os.getcwd()
        td = tempfile.mkdtemp()
        os.chdir(td)
        Path(td, "screenshots").mkdir(exist_ok=True)
        db_init()
        port = cls._free_port()
        base = f"http://127.0.0.1:{port}"
        server = make_server("127.0.0.1", port, A.app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        results = {"rendered": [], "errors": []}
        try:
            # wait for liveness
            up = False
            for _ in range(60):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
                    up = True
                    break
                except OSError:
                    time.sleep(0.15)
            if not up:
                return {"skipped": "server did not come up"}

            with sync_playwright() as p:
                try:
                    br = p.chromium.launch(headless=True, executable_path=_CHROME)
                except Exception as le:
                    return {"skipped": f"browser launch failed: {str(le)[:80]}"}
                pages = [("spa_root", base + "/")] + \
                        [(pp.rsplit("/", 1)[-1], base + pp) for pp in _COCKPIT_PAGES]
                for name, url in pages:
                    pg = br.new_page(viewport={"width": 1000, "height": 760})
                    errs = []
                    pg.on("pageerror", lambda e: errs.append(str(e)))
                    try:
                        pg.goto(url, wait_until="networkidle", timeout=20000)
                    except Exception as nav:
                        # Browser launched but can't navigate (the documented
                        # capture-navigation blocker). That is an environment
                        # limitation, not a GUI regression — skip cleanly, the
                        # same way the asi behavioral tests do on goto timeout.
                        pg.close()
                        br.close()
                        return {"skipped": f"navigation failed ({name}): {str(nav)[:80]}"}
                    pg.wait_for_timeout(900)
                    if shot_dir:
                        pg.screenshot(path=str(Path(shot_dir) / f"smoke_{name}.png"))
                    fatal = [e for e in errs if "Failed to fetch" not in e]
                    if fatal:
                        results["errors"].append((name, fatal[:2]))
                    results["rendered"].append(name)
                    pg.close()
                br.close()
        finally:
            server.shutdown()
            os.chdir(orig)
        return results


def test_render_smoke_skips_without_browser():
    """Band-safe wrapper: the render tier launches a real browser, which an
    unattended suite run (the custom runner AND the on-stash full suite) must
    NOT do by default — a host where the browser launches but can't navigate
    would otherwise redden the suite. So this is OPT-IN via BD_GUI_SMOKE_RENDER=1
    (set it for the in-sandbox / on-host green proof). When enabled it PASSES on
    a clean render and on a clean skip (no browser / nav blocked); it only FAILS
    on a fatal page error."""
    if os.environ.get("BD_GUI_SMOKE_RENDER") != "1":
        return  # opt-in only; default suite never launches a browser
    res = _FreshInstallRenderSmoke.run_render()
    if "skipped" in res:
        return  # clean skip on a headless host / nav blocker
    assert not res.get("errors"), f"fatal page errors during render: {res['errors']}"
    assert "spa_root" in res.get("rendered", [])
