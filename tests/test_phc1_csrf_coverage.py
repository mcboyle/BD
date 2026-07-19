"""PHC-1 (B1 + B3): the global CSRF/origin guard must cover every state-changing
route, including the cockpit JSON endpoints.

RED-first on pristine v3.66.530:
  * `_check_csrf` gates only paths under `/api/`, so the 28 `/cockpit/api/`
    write routes escape the guard -> the coverage invariant + the behavioral
    cross-origin refusal are RED.
After the cut (gate extended to `("/api/", "/cockpit/api/")`) both pass.

These are pins: a future write route that lands off the guarded prefixes, or a
widened exempt set, trips the band instead of shipping CSRF-unguarded.
"""
import importlib.util
import os

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _load_auditor():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    path = os.path.join(root, "tools", "audit_write_route_guard.py")
    spec = importlib.util.spec_from_file_location("audit_write_route_guard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- B1 invariant: every write route is under a guarded prefix --------------
def test_every_write_route_is_guard_covered():
    aud = _load_auditor()
    write_routes, escapes = aud.collect_write_routes()
    assert write_routes, "expected to enumerate some write routes"
    assert escapes == [], (
        "state-changing routes escaping the CSRF/origin guard prefix gate "
        "(must be under /api/ or /cockpit/api/): "
        + ", ".join(f"{','.join(m)} {r}" for r, m in escapes))


# --- B1 behavioral: a cross-origin cockpit write is refused -----------------
def test_cockpit_write_refuses_cross_origin():
    from bulk_downloader.app import app
    c = app.test_client()
    # Cross-origin POST to a representative /cockpit/api/ write route. The
    # same-origin Origin guard inside _check_csrf must refuse it (403). On
    # pristine 530 the path-prefix gate returns early for /cockpit/, so the
    # request is NOT refused -> this assertion is RED there.
    r = c.post("/cockpit/api/ui_prefs",
               headers={"Origin": "http://evil.example", "Host": "localhost"},
               json={})
    assert r.status_code == 403, (
        f"cross-origin /cockpit/api/ write must be refused (403); got {r.status_code}")
    body = r.get_data(as_text=True).lower()
    assert "cross-origin" in body, "expected the cross-origin refusal message"


# --- B1 behavioral: a same-origin cockpit write is NOT blocked by origin -----
def test_cockpit_write_same_origin_passes_origin_check():
    from bulk_downloader.app import app
    c = app.test_client()
    # Same-origin (Origin host == Host) must NOT be refused by the origin check.
    # It may still fail CSRF-token validation if a session cookie is present,
    # but with no session cookie _check_csrf returns None (no 403). This proves
    # the gate extension did not break same-origin cockpit use.
    r = c.post("/cockpit/api/ui_prefs",
               headers={"Origin": "http://localhost", "Host": "localhost"},
               json={})
    assert r.status_code != 403 or "cross-origin" not in r.get_data(as_text=True).lower(), (
        "same-origin cockpit write must not be refused by the cross-origin guard")


# --- B3: AI write/stream routes are all under /api/ (guard-covered) ----------
def test_ai_write_routes_are_api_prefixed():
    from bulk_downloader.app import app
    ai_writes = []
    for rule in app.url_map.iter_rules():
        methods = set(rule.methods or set()) & WRITE_METHODS
        if not methods:
            continue
        ep = (rule.endpoint or "")
        if "ai" in ep.lower() or "/ai/" in rule.rule:
            ai_writes.append(rule.rule)
    assert ai_writes, "expected to find AI write routes"
    bad = [r for r in ai_writes if not (r.startswith("/api/") or r.startswith("/cockpit/api/"))]
    assert bad == [], f"AI write routes must be guard-covered (/api/): {bad}"
