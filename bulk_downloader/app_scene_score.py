"""scene_score API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/scene_score views moved onto a Flask Blueprint.
Endpoint labels gain a "scene_score." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

scene_score_bp = Blueprint("scene_score", __name__)


@scene_score_bp.route("/api/scene_score/top")
def api_scene_score_top():
    """Highest-scored files in the library (or one site).
    ?site_id=X to filter; ?limit=N for top-N; default 20."""
    try:
        from . import scene_scoring as _ss
        return jsonify({"scenes": _ss.top_scenes(
            site_id=request.args.get("site_id") or None,
            limit=int(request.args.get("limit", 20) or 20),
            scan_limit=int(request.args.get("scan_limit", 500) or 500),
        )})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@scene_score_bp.route("/api/scene_score/bottom")
def api_scene_score_bottom():
    """Lowest-scored files — candidates for review or re-download."""
    try:
        from . import scene_scoring as _ss
        return jsonify({"scenes": _ss.bottom_scenes(
            site_id=request.args.get("site_id") or None,
            limit=int(request.args.get("limit", 20) or 20),
            scan_limit=int(request.args.get("scan_limit", 500) or 500),
        )})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@scene_score_bp.route("/api/scene_score/distribution")
def api_scene_score_dist():
    """Score distribution histogram. Used by dashboard summary card."""
    try:
        from . import scene_scoring as _ss
        return jsonify(_ss.distribution(
            site_id=request.args.get("site_id") or None,
            scan_limit=int(request.args.get("scan_limit", 1000) or 1000),
        ))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(scene_score_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("scene_score."))

