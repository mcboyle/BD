"""palette API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/palette views moved onto a Flask Blueprint.
Endpoint labels gain a "palette." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

palette_bp = Blueprint("palette", __name__)


@palette_bp.route("/api/palette/commands")
def api_palette_commands():
    try:
        from . import command_palette as _cp
        return jsonify({
            "commands": _cp.list_commands(
                category=request.args.get("category", ""),
                q=request.args.get("q", "")),
            "categories": _cp.categories(),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(palette_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("palette."))

