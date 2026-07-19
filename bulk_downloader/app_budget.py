"""budget API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/budget views moved onto a Flask Blueprint.
Endpoint labels gain a "budget." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

budget_bp = Blueprint("budget", __name__)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@budget_bp.route("/api/budget/<sid>")
def api_budget(sid):
    s_cfg = _app_s_cfg()
    if sid not in s_cfg:
        return jsonify({"error": "site not found"}), 404
    try:
        from . import policy_gates as _pg
        return jsonify({
            "budget": _pg.budget_state(s_cfg[sid]),
            "quiet": _pg.in_quiet_hours(s_cfg[sid]),
            "next_transition": _pg.next_window_transition(s_cfg[sid]),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(budget_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("budget."))

