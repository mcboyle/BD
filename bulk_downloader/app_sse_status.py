"""sse_status API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/sse_status views moved onto a Flask Blueprint.
Endpoint labels gain a "sse_status." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

sse_status_bp = Blueprint("sse_status", __name__)


@sse_status_bp.route("/api/sse_status")
def api_sse_status():
    """Diagnostic snapshot: how many SSE subscribers are connected,
    queue depths, etc. Useful for debugging 'my UI isn't updating'
    issues without spelunking through server logs."""
    try:
        from . import sse_broker as _sse
        return jsonify(_sse.get_broker().get_status())
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

def register_routes(app) -> int:
    app.register_blueprint(sse_status_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("sse_status."))

