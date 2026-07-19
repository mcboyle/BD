"""auth_health API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/auth_health views moved onto a Flask Blueprint.
Endpoint labels gain a "auth_health." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

auth_health_bp = Blueprint("auth_health", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@auth_health_bp.route("/api/auth_health/status")
def api_auth_health_status():
    """Snapshot of every site's last-known auth health."""
    try:
        from . import cookie_health as _ch
        return jsonify({"sites": _ch.status_all()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@auth_health_bp.route("/api/auth_health/check/<sid>", methods=["POST"])
def api_auth_health_check_one(sid):
    """Force a check of one site's cookies right now. Returns the
    classification + persisted state."""
    s_cfg = _app_s_cfg()
    _check_csrf()
    if sid not in s_cfg:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    try:
        from . import cookie_health as _ch
        return jsonify({"ok": True, **_ch.check_site(sid, s_cfg[sid])})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@auth_health_bp.route("/api/auth_health/check_all", methods=["POST"])
def api_auth_health_check_all():
    """Force a check of every site. Slow (one HTTP request per site).
    Optional ?only_if_stale=1 skips sites checked recently."""
    s_cfg = _app_s_cfg()
    _check_csrf()
    try:
        from . import cookie_health as _ch
        only_if_stale = (request.args.get("only_if_stale") or "0") == "1"
        return jsonify({"ok": True,
                        **_ch.check_all_sites(s_cfg,
                                                only_if_stale=only_if_stale)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(auth_health_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("auth_health."))

