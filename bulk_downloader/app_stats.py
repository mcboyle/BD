"""stats API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/stats views moved onto a Flask Blueprint.
Endpoint labels gain a "stats." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from .db import db_conn
from .db import db_stats

stats_bp = Blueprint("stats", __name__)


@stats_bp.route("/api/stats")
def api_stats(): return jsonify(db_stats(request.args.get("site_id") or None))

@stats_bp.route("/api/stats/bandwidth")
def api_stats_bandwidth():
    """Phase 8.3: 1-hour rolling bandwidth chart data. Returns up to 3600
    points of (timestamp, bytes_in_that_second). Frontend converts to MB/s
    and renders a line chart."""
    from .runner import get_bandwidth_history
    seconds=max(60,min(3600,int(request.args.get("seconds",3600))))
    points=get_bandwidth_history(seconds=seconds)
    return jsonify({"points":points,"window_seconds":seconds})

@stats_bp.route("/api/stats/timeline")
def api_stats_timeline():
    """Per-day buckets of done/failed counts and bytes downloaded for the last
    N days. If site_id is given, scopes to that site; otherwise aggregates
    across all sites."""
    sid=request.args.get("site_id") or None
    days=max(1,min(365,int(request.args.get("days",30))))
    # Audit 2026-05: use parameterized date offset rather than f-string
    # interpolation. The int+clamp already neutralizes the injection vector,
    # but ? placeholders match the policy applied elsewhere in db.py.
    where_clauses=["ts >= date('now', ?)"]
    args=[f"-{days} days"]
    if sid: where_clauses.append("site_id=?"); args.append(sid)
    where="WHERE "+" AND ".join(where_clauses)
    sql=f"""SELECT date(ts) AS day,
                   SUM(CASE WHEN status='done'   THEN 1 ELSE 0 END) AS done,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                   SUM(CASE WHEN status='needs_review' THEN 1 ELSE 0 END) AS review,
                   COALESCE(SUM(file_size),0) AS bytes
              FROM history {where}
             GROUP BY day ORDER BY day"""
    with db_conn() as cx:
        rows=[dict(r) for r in cx.execute(sql,args)]
    return jsonify({"days":days,"site_id":sid,"buckets":rows})

def register_routes(app) -> int:
    app.register_blueprint(stats_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("stats."))

