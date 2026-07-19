"""daily_budget API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/daily_budget views moved onto a Flask Blueprint.
Endpoint labels gain a "daily_budget." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

daily_budget_bp = Blueprint("daily_budget", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@daily_budget_bp.route("/api/daily_budget/status")
def api_daily_budget_status():
    """Snapshot of every site's today usage + budget status."""
    s_cfg = _app_s_cfg()
    try:
        from . import daily_budget as _db_budget
        return jsonify({
            "ymd": _db_budget._today_ymd(),
            "sites": _db_budget.status_all(s_cfg),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@daily_budget_bp.route("/api/daily_budget/reset/<sid>", methods=["POST"])
def api_daily_budget_reset(sid):
    """Zero today's counter for one site. Used in emergencies or for
    testing — doesn't change the daily cap, just resets usage."""
    _check_csrf()
    try:
        from . import daily_budget as _db_budget
        return jsonify({"ok": _db_budget.reset_today(sid), "site_id": sid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@daily_budget_bp.route("/api/daily_budget/history/<sid>")
def api_daily_budget_history(sid):
    """Daily usage history for charting. Query: ?days=30 (max 365)."""
    try:
        from . import daily_budget as _db_budget
        days = min(365, max(1, int(request.args.get("days", 30))))
        return jsonify({"site_id": sid,
                        "history": _db_budget.history(sid, days=days)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(daily_budget_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("daily_budget."))

