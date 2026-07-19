"""rate_limit API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/rate_limit views moved onto a Flask Blueprint.
Endpoint labels gain a "rate_limit." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

rate_limit_bp = Blueprint("rate_limit", __name__)


@rate_limit_bp.route("/api/rate_limit/status")
def api_rate_limit_status():
    """Live snapshot of the rate limiter: global caps, per-domain
    overrides, and current in-flight counts per active domain. Used
    by the settings UI to show what's happening right now."""
    try:
        from bulk_downloader import rate_limit as _rl
        return jsonify(_rl.get_limiter().get_status())
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

def register_routes(app) -> int:
    app.register_blueprint(rate_limit_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("rate_limit."))

