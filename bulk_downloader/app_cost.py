"""cost API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/cost views moved onto a Flask Blueprint.
Endpoint labels gain a "cost." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

cost_bp = Blueprint("cost", __name__)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@cost_bp.route("/api/cost/report")
def api_cost_report():
    s_cfg = _app_s_cfg()
    try:
        days = int(request.args.get("window_days", 30))
    except (TypeError, ValueError):
        days = 30
    try:
        from . import cost_economics as _ce
        return jsonify({
            "per_site": _ce.report_all(window_days=days, s_cfg=s_cfg),
            "total": _ce.total_monthly_cost(s_cfg=s_cfg),
        })
    except Exception as e:
        return jsonify({"per_site": [], "error": str(e)[:200]}), 500

@cost_bp.route("/api/cost/sunset_candidates")
def api_cost_sunset():
    s_cfg = _app_s_cfg()
    try:
        threshold = float(request.args.get("threshold_per_download", 10.0))
        window = int(request.args.get("window_days", 90))
        min_dls = int(request.args.get("min_downloads", 1))
    except (TypeError, ValueError):
        threshold, window, min_dls = 10.0, 90, 1
    try:
        from . import cost_economics as _ce
        return jsonify({"candidates": _ce.sunset_candidates(
            threshold_per_download=threshold,
            window_days=window,
            min_window_downloads=min_dls,
            s_cfg=s_cfg,
        )})
    except Exception as e:
        return jsonify({"candidates": [], "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(cost_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("cost."))

