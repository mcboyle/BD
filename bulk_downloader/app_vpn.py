"""vpn API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/vpn views moved onto a Flask Blueprint.
Endpoint labels gain a "vpn." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

vpn_bp = Blueprint("vpn", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@vpn_bp.route("/api/vpn/stats")
def api_vpn_stats():
    try:
        from . import vpn_stats as _vs
        return jsonify({"report": _vs.profile_report(
            since_hours=int(request.args.get("since_hours", 168)))})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@vpn_bp.route("/api/vpn/best_for/<sid>")
def api_vpn_best(sid):
    try:
        from . import vpn_stats as _vs
        return jsonify(_vs.best_profile_for(sid) or {"vpn_profile": None})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@vpn_bp.route("/api/vpn/blacklist")
def api_vpn_blacklist():
    try:
        from . import vpn_stats as _vs
        return jsonify({"blacklist": _vs.current_blacklist()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@vpn_bp.route("/api/vpn/auto_blacklist", methods=["POST"])
def api_vpn_auto_bl():
    _check_csrf()
    try:
        from . import vpn_stats as _vs
        return jsonify({"new_blacklisted": _vs.auto_blacklist_check()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(vpn_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("vpn."))

