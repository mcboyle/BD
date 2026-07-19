"""session_history API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/session_history views moved onto a Flask Blueprint.
Endpoint labels gain a "session_history." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

session_history_bp = Blueprint("session_history", __name__)


@session_history_bp.route("/api/session_history", methods=["GET"])
def api_session_history():
    """Recent session_history events for the UI's real-time log.
    Query: ?site_id=...&account_idx=...&limit=100"""
    from . import db as _db
    sid = request.args.get("site_id") or None
    acc_str = request.args.get("account_idx")
    acc = int(acc_str) if acc_str and acc_str.isdigit() else None
    limit = min(500, int(request.args.get("limit", "100")))
    return jsonify({"ok": True,
                    "events": _db.session_event_recent(sid, acc, limit)})

def register_routes(app) -> int:
    app.register_blueprint(session_history_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("session_history."))

