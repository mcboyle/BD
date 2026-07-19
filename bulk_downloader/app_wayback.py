"""wayback API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/wayback views moved onto a Flask Blueprint.
Endpoint labels gain a "wayback." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

wayback_bp = Blueprint("wayback", __name__)


@wayback_bp.route("/api/wayback/snapshots")
def api_wayback_snapshots():
    url = (request.args.get("url") or "").strip()
    if not url:
        return jsonify({"snapshots": []})
    try:
        from . import wayback_cdx as _wb
        return jsonify({"snapshots": _wb.find_snapshots(url, limit=50)})
    except Exception as e:
        return jsonify({"snapshots": [], "error": str(e)[:200]}), 500

@wayback_bp.route("/api/wayback/availability")
def api_wayback_avail():
    url = (request.args.get("url") or "").strip()
    if not url:
        return jsonify({"available": False})
    try:
        from . import wayback_cdx as _wb
        return jsonify(_wb.availability(url))
    except Exception as e:
        return jsonify({"available": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(wayback_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("wayback."))

