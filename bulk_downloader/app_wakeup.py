"""wakeup API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/wakeup views moved onto a Flask Blueprint.
Endpoint labels gain a "wakeup." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

wakeup_bp = Blueprint("wakeup", __name__)


@wakeup_bp.route("/api/wakeup/should_wake")
def api_wakeup_should():
    try:
        from . import smart_wakeup as _sw
        from .global_config import get_config
        gc = get_config() or {}
        return jsonify(_sw.should_wake_now(
            quiet_hours=gc.get("quiet_hours") or [],
            wakeup_threshold=int(gc.get("wakeup_threshold", 50)),
            cool_down_seconds=int(gc.get("wakeup_cool_down_seconds", 14400))))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@wakeup_bp.route("/api/wakeup/history")
def api_wakeup_history():
    try:
        from . import smart_wakeup as _sw
        return jsonify({"decisions": _sw.recent_decisions(
            limit=int(request.args.get("limit", 50)))})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(wakeup_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("wakeup."))

