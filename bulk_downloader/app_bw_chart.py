"""bw_chart API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/bw_chart views moved onto a Flask Blueprint.
Endpoint labels gain a "bw_chart." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

bw_chart_bp = Blueprint("bw_chart", __name__)


@bw_chart_bp.route("/api/bw_chart/hourly")
def api_bw_chart_hourly():
    """Per-hour bandwidth + jobs + failure-rate for the last N hours.
    Default 168h (1 week). Used by the UI to render a single trend
    chart with three series (bytes, jobs, failure %)."""
    try:
        hours = int(request.args.get("hours", 168) or 168)
        from . import bw_chart as _bw
        return jsonify({
            "bandwidth": _bw.hourly_bandwidth(hours=hours),
            "jobs": _bw.hourly_jobs(hours=hours),
            "failure_rate": _bw.hourly_failure_rate(hours=hours),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@bw_chart_bp.route("/api/bw_chart/site_share")
def api_bw_chart_site_share():
    """Per-site bandwidth share (last N hours). Used to render a
    horizontal bar chart 'which sites consumed bandwidth this week'."""
    try:
        hours = int(request.args.get("hours", 168) or 168)
        from . import bw_chart as _bw
        return jsonify(_bw.site_share(hours=hours))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@bw_chart_bp.route("/api/bw_chart/summary")
def api_bw_chart_summary():
    """Headline numbers for last 24h (or N hours). Used by the
    dashboard summary card: total bytes, jobs, success rate."""
    try:
        hours = int(request.args.get("hours", 24) or 24)
        from . import bw_chart as _bw
        return jsonify(_bw.summary_card(hours=hours))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(bw_chart_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("bw_chart."))

