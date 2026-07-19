"""cluster_rate API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/cluster_rate views moved onto a Flask Blueprint.
Endpoint labels gain a "cluster_rate." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

cluster_rate_bp = Blueprint("cluster_rate", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@cluster_rate_bp.route("/api/cluster_rate/load")
def api_cluster_load():
    try:
        from . import cluster_rate as _cr
        return jsonify(_cr.current_load(
            site_id=request.args.get("site_id")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@cluster_rate_bp.route("/api/cluster_rate/acquire", methods=["POST"])
def api_cluster_acquire():
    _check_csrf()
    body = request.json or {}
    sid = body.get("site_id")
    if not sid:
        return jsonify({"ok": False, "error": "site_id required"}), 400
    try:
        from . import cluster_rate as _cr
        return jsonify(_cr.acquire_lease(
            sid,
            max_concurrent=int(body.get("max_concurrent", 1)),
            lease_seconds=int(body.get("lease_seconds", 300))))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@cluster_rate_bp.route("/api/cluster_rate/release", methods=["POST"])
def api_cluster_release():
    _check_csrf()
    body = request.json or {}
    if not body.get("lease_id"):
        return jsonify({"ok": False, "error": "lease_id required"}), 400
    try:
        from . import cluster_rate as _cr
        return jsonify({"ok": _cr.release_lease(int(body["lease_id"]))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(cluster_rate_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("cluster_rate."))

