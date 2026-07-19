"""gamification API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/gamification views moved onto a Flask Blueprint.
Endpoint labels gain a "gamification." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

gamification_bp = Blueprint("gamification", __name__)


@gamification_bp.route("/api/gamification/report")
def api_gamification():
    try:
        from . import gamification as _g
        return jsonify(_g.report())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(gamification_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("gamification."))

