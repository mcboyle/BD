"""recommendations API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/recommendations views moved onto a Flask Blueprint.
Endpoint labels gain a "recommendations." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

recommendations_bp = Blueprint("recommendations", __name__)


@recommendations_bp.route("/api/recommendations/similar")
def api_recs_similar():
    anchor = (request.args.get("anchor") or "").strip()
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    try:
        from . import recommendations as _r
        return jsonify({"results": _r.similar_to(anchor, limit=limit)})
    except Exception as e:
        return jsonify({"results": [], "error": str(e)[:200]}), 500

@recommendations_bp.route("/api/recommendations/recent")
def api_recs_recent():
    try:
        days = int(request.args.get("days", 7))
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        days, limit = 7, 50
    try:
        from . import recommendations as _r
        site_id = request.args.get("site_id") or None
        return jsonify({"results": _r.recent_additions(
            days=days, limit=limit, site_id=site_id)})
    except Exception as e:
        return jsonify({"results": [], "error": str(e)[:200]}), 500

@recommendations_bp.route("/api/recommendations/top")
def api_recs_top():
    """Combined top tags + top performers for dashboard."""
    try:
        days = int(request.args.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    try:
        from . import recommendations as _r
        return jsonify({
            "tags": _r.top_tags(days=days),
            "performers": _r.top_performers(days=days),
        })
    except Exception as e:
        return jsonify({"tags": [], "performers": [], "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(recommendations_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("recommendations."))

