"""v3.66.203 — Phase 1 root flip (LEGACY_MIGRATION_PLAN).

`/` serves the D3 React SPA; the legacy shell moved to `/legacy`
FULLY FUNCTIONAL (the program-long escape hatch until Phase 4).
/m, /m/ops and /m2 are 302 shims to root; /m2 preserves deep links.

These pins are the flip's contract. The pre-flip contracts were
re-expressed (never dropped) in: test_v3_43_55_csrf_bootstrap.py
(legacy inline mint, now at /legacy), test_d3_u1_scaffold.py (vite
base + 503 not-built surface, now at /), test_d3_u9_opt_in.py (shim
targets), test_d3_u8_polish.py (e2e targets root),
test_fresh_install_gui_smoke.py (SPA root).
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
os.environ.setdefault("BD_HOME", tempfile.mkdtemp(prefix="bd_rootflip_"))

_DIST = _REPO_ROOT / "frontend" / "dist" / "index.html"


def _fresh_client():
    from bulk_downloader.app import app as flask_app
    return flask_app.test_client()


def test_root_serves_spa():
    """GET / returns the built SPA index (200, html, #root div).
    If dist is absent (pristine sandbox), the actionable 503
    not-built surface answers instead — same contract /m2 had."""
    c = _fresh_client()
    r = c.get("/")
    if not _DIST.is_file():
        assert r.status_code == 503
        assert r.headers.get("X-BD-M2-Status") == "not-built"
        return
    assert r.status_code == 200
    assert "html" in (r.headers.get("Content-Type") or "")
    assert b'<div id="root">' in r.data, "SPA root div missing from /"


def test_root_warms_session_cookie():
    """The _bootstrap_session hook covers the SPA shell: a fresh
    GET / sets bd_session so the first /api/csrf finds a valid
    session and the first POST never races a Set-Cookie."""
    c = _fresh_client()
    r = c.get("/")
    set_cookies = [v for k, v in r.headers.items() if k.lower() == "set-cookie"]
    assert any("bd_session=" in v for v in set_cookies), \
        "fresh GET / did not warm the session cookie"


def test_spa_client_route_falls_back_to_index():
    """An unrouted non-reserved path (a React Router client route)
    returns the SPA index so the router can claim it."""
    if not _DIST.is_file():
        return  # 503 surface covered in test_root_serves_spa
    c = _fresh_client()
    for path in ("/queue", "/settings", "/sites/3"):
        r = c.get(path)
        assert r.status_code == 200, f"{path}: {r.status_code}"
        assert b'<div id="root">' in r.data, f"{path} did not get SPA HTML"


def test_reserved_namespaces_404_not_spa_html():
    """An unrouted path under a reserved infra namespace must be a
    real 404 — never SPA HTML masquerading as success."""
    c = _fresh_client()
    for path in ("/api/definitely_not_a_route_zz",
                 "/cockpit/definitely_not_a_page_zz",
                 "/legacy/definitely_not_a_thing_zz",
                 "/m2zz_is_not_reserved_but_this_is/../"):
        r = c.get("/api/definitely_not_a_route_zz")
        assert r.status_code == 404
    r = c.get("/cockpit/definitely_not_a_page_zz")
    assert r.status_code == 404


def test_missing_asset_is_404_not_spa_html():
    """An asset-looking path (file extension) not present in dist is a
    404 — a stale hashed bundle name must fail loudly, not return HTML."""
    c = _fresh_client()
    r = c.get("/assets/definitely-not-a-real-bundle-zz.js")
    assert r.status_code == 404


def test_real_asset_served_from_root():
    """The built bundle's own asset URLs (emitted root-relative by
    vite base "/") resolve through the catch-all."""
    if not _DIST.is_file():
        return
    idx = _DIST.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'(?:src|href)="(/assets/[^"]+)"', idx)
    assert m, "built index.html has no root-relative asset refs (vite base wrong?)"
    c = _fresh_client()
    r = c.get(m.group(1))
    assert r.status_code == 200, f"asset {m.group(1)} not served from root"


def test_legacy_route_removed():
    """Phase 4 (v3.66.334): the legacy shell AND its /legacy route were
    removed outright (dev-only tool, no external bookmarks to preserve).
    "legacy" stays a reserved prefix so /legacy + /legacy/ resolve to a
    clean 404 rather than falling through to the SPA catch-all."""
    for path in ("/legacy", "/legacy/"):
        c = _fresh_client()
        r = c.get(path)
        assert r.status_code == 404, \
            f"{path}: expected 404 (route removed), got {r.status_code}"


def test_m2_shim_preserves_deep_links_and_query():
    c = _fresh_client()
    r = c.get("/m2/sites/3?tab=runs")
    assert r.status_code == 302
    assert r.headers.get("Location") == "/sites/3?tab=runs"
    r = c.get("/m2")
    assert r.status_code == 302
    assert r.headers.get("Location") == "/"


def test_mobile_shims_redirect_to_root():
    c = _fresh_client()
    for path in ("/m", "/m/", "/m/ops", "/m/ops/"):
        r = c.get(path)
        assert r.status_code == 302, f"{path}: {r.status_code}"
        assert r.headers.get("Location") == "/", \
            f"{path} -> {r.headers.get('Location')!r}, expected /"


def test_api_routes_not_shadowed_by_catch_all():
    """Werkzeug gives <path:> rules lowest priority — explicit rules
    must keep winning. /api/health is the canary."""
    c = _fresh_client()
    r = c.get("/api/health")
    assert r.status_code in (200, 503)  # 503 only if db not ok
    body = r.get_json() or {}
    assert "version" in body, "/api/health shadowed by the SPA catch-all?"


def test_frontend_re_rooted_in_source():
    """vite base and router basename are both "/" — the single
    coupling between build output and Flask mount."""
    vite_cfg = (_REPO_ROOT / "frontend" / "vite.config.ts"
                ).read_text(encoding="utf-8")
    assert re.search(r'base:\s*["\']/["\']', vite_cfg)
    main_tsx = (_REPO_ROOT / "frontend" / "src" / "main.tsx"
                ).read_text(encoding="utf-8")
    assert 'basename="/"' in main_tsx


def test_bootstrap_hook_covers_spa_root():
    """_bootstrap_session warms the cookie for the SPA shell at /. The
    legacy shell was removed in P4 (v3.66.334), so / is the only shell
    and the hook must no longer reference /legacy."""
    src = (_REPO_ROOT / "bulk_downloader" / "app.py").read_text(encoding="utf-8")
    pos = src.find("def _bootstrap_session")
    assert pos > 0
    body = src[pos:pos + 2000]
    assert '"/"' in body, "hook must gate on / (the SPA shell)"
    assert '"/legacy"' not in body, \
        "legacy shell removed in P4 — hook must not reference /legacy"


# ── v3.66.204: method-semantics parity (the 203 stash-suite regression) ──
# The root catch-all accepts GET on every path, which (at 203) made a
# GET on a POST-only route fall into serve_spa_root's reserved-prefix
# 404 instead of Werkzeug's native 405 — 8 on-stash test failures, all
# one class. 204 restores full pre-flip method semantics.


def test_get_on_post_only_routes_is_405_with_allow():
    """The exact 8-failure class from the 203 on-stash suite: GET on a
    POST-only route answers 405 + Allow (pre-flip Werkzeug semantics),
    never the reserved-prefix 404 and never SPA HTML. Paths mirror the
    suites that pinned it (d3_u3/u5/u10/u11, t2, t9, v3_66_8)."""
    c = _fresh_client()
    for path in ("/api/dev/vision_test",
                 "/api/dev/fixture_site/start",
                 "/api/queue/v2/add_url",
                 "/api/queue/v2/cancel",
                 "/api/queue/v2/bulk_cancel",
                 "/api/sites/v2/bulk",
                 "/api/sites/whatever/jobs/reorder",
                 "/api/dashboard/v2/resolve"):
        r = c.get(path)
        assert r.status_code == 405, f"GET {path}: {r.status_code} (want 405)"
        allow = r.headers.get("Allow") or ""
        assert "POST" in allow, f"GET {path}: Allow={allow!r} missing POST"
        assert "GET" not in allow, \
            f"GET {path}: Allow={allow!r} must not advertise GET"


def test_wrong_nonget_method_on_real_endpoint_keeps_405_clean_allow():
    """DELETE on a POST-only route: native 405 preserved, Allow lists
    only the explicit methods (+OPTIONS) — not the catch-all's GET."""
    c = _fresh_client()
    r = c.delete("/api/queue/v2/add_url")
    assert r.status_code == 405
    allow = r.headers.get("Allow") or ""
    assert "POST" in allow and "GET" not in allow, f"Allow={allow!r}"


def test_nonget_to_unknown_reserved_or_asset_path_is_404():
    """Pre-flip parity: a non-GET to a path with NO explicit rule in a
    reserved namespace (or asset-shaped) is a plain 404 — the 405 the
    catch-all's path-match manufactured at 203 is converted back."""
    c = _fresh_client()
    assert c.post("/api/definitely_not_a_route_zz").status_code == 404
    assert c.put("/api/definitely_not_a_route_zz").status_code == 404
    assert c.post("/assets/definitely-not-real-zz.js").status_code == 404


def test_nonget_to_spa_page_path_is_405_allow_get():
    """A non-GET to a genuine SPA page path answers 405 with Allow: GET
    — exactly what the /m2 mount answered pre-flip (POST /m2/queue)."""
    c = _fresh_client()
    r = c.post("/queue")
    assert r.status_code == 405, f"POST /queue: {r.status_code}"
    allow = r.headers.get("Allow") or ""
    assert "GET" in allow, f"Allow={allow!r} missing GET"
