"""csrf API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/csrf views moved onto a Flask Blueprint.
Endpoint labels gain a "csrf." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (SESSION_IDLE_TTL) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

csrf_bp = Blueprint("csrf", __name__)

def _csrf_token_for(*_a, **_k):
    """Delegate to app._csrf_token_for at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_csrf_token_for")(*_a, **_k)

def _session_create(*_a, **_k):
    """Delegate to app._session_create at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_session_create")(*_a, **_k)

def _session_valid(*_a, **_k):
    """Delegate to app._session_valid at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_session_valid")(*_a, **_k)

def _app_SESSION_IDLE_TTL():
    """The live shared SESSION_IDLE_TTL from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "SESSION_IDLE_TTL")


@csrf_bp.route("/api/csrf")
def api_csrf():
    """Return the CSRF token derived from the caller's session cookie.
    Used by the JS UI to seed the X-CSRF-Token header on first load.

    P0.1 (v3.66.202, LEGACY_MIGRATION_PLAN Phase 0): if no valid session
    exists, mint one HERE and set the cookie on this response — the
    app-level bootstrap. Previously sessions were minted only on GET /
    (the legacy serve_index inline mint + the path-gated
    _bootstrap_session hook), so a client that never loaded the legacy
    shell (SPA deep-link, post-root-flip /m2, headless API consumer
    relying on cookie auth) got {"ok": false} here, a null token, and a
    403 on every CSRF-protected POST. The minted session is the same
    anonymous source="csrf_bootstrap" session any cookie-less GET /
    already receives — identical exposure, no new privilege. Legacy
    bootstrap paths are unchanged (this is additive); the double-submit
    CSRF design is unchanged (token is still HMAC(session) with the
    process-local key)."""
    SESSION_IDLE_TTL = _app_SESSION_IDLE_TTL()
    sess = request.cookies.get("bd_session", "")
    if not sess or not _session_valid(sess):
        sess = _session_create(source="csrf_bootstrap")
        secure = request.scheme == "https"
        resp = jsonify({"ok": True, "csrf_token": _csrf_token_for(sess),
                        "minted": True})
        # Same cookie attributes as serve_index / _bootstrap_session.
        resp.set_cookie("bd_session", sess,
                        max_age=SESSION_IDLE_TTL, httponly=True,
                        samesite="Lax", secure=secure)
        return resp
    return jsonify({"ok": True, "csrf_token": _csrf_token_for(sess)})

def register_routes(app) -> int:
    app.register_blueprint(csrf_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("csrf."))

