"""hourly_stats API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/hourly_stats views moved onto a Flask Blueprint.
Endpoint labels gain a "hourly_stats." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

hourly_stats_bp = Blueprint("hourly_stats", __name__)


@hourly_stats_bp.route("/api/hourly_stats")
def api_hourly_stats():
    """Phase 74 (v3.43.16): time-of-day success rate analytics. Returns
    a 24-element list (one per hour) with success rate and sample size.
    Args: site_id (optional), since_days (default 30, max 365)."""
    from .db import db_hourly_success_rate
    site_id = request.args.get("site_id") or None
    since = int(request.args.get("since_days", 30))
    since = max(1, min(since, 365))
    rows = db_hourly_success_rate(site_id=site_id, since_days=since)
    # Heuristic: detect "bad hours" (rate < 50% with sample >= 5)
    bad_hours = [r["hour"] for r in rows
                 if r["rate"] is not None and r["rate"] < 0.5 and r["total"] >= 5]
    return jsonify({"rows": rows, "since_days": since,
                    "site_id": site_id, "bad_hours": bad_hours})

def register_routes(app) -> int:
    app.register_blueprint(hourly_stats_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("hourly_stats."))

