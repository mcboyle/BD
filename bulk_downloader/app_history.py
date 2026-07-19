"""history API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/history views moved onto a Flask Blueprint.
Endpoint labels gain a "history." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from .db import db_prune
from .db import db_search
from .db import db_vacuum

history_bp = Blueprint("history", __name__)


@history_bp.route("/api/history/prune",methods=["POST"])
def api_prune():
    days=int((request.json or {}).get("days",90))
    deleted=db_prune(days)
    return jsonify({"ok":True,"deleted":deleted,"days":days})
@history_bp.route("/api/history/vacuum",methods=["POST"])
def api_vacuum():
    try: db_vacuum(); return jsonify({"ok":True})
    except Exception as e: return jsonify({"error":str(e)}),500
@history_bp.route("/api/history")
def api_history():
    site_id = request.args.get("site_id") or None
    status = request.args.get("status") or None
    query = request.args.get("q") or None
    # F4.4 (v3.66.219): opt-in cursor pagination. When `cursor` is present
    # (or paginate=1), return the {rows, next_cursor} envelope backed by
    # db_search_cursor; otherwise the legacy BARE ARRAY contract is unchanged.
    cursor = request.args.get("cursor")
    paginate = (request.args.get("paginate") or "0") == "1"
    if cursor is not None or paginate:
        from .db import db_search_cursor
        try:
            after_id = int(cursor) if cursor not in (None, "") else None
        except (TypeError, ValueError):
            after_id = None
        limit = int(request.args.get("limit", 100))
        rows, next_cursor = db_search_cursor(
            site_id=site_id, status=status, query=query,
            after_id=after_id, limit=limit)
        return jsonify({"rows": rows, "next_cursor": next_cursor})
    return jsonify(db_search(site_id=site_id, status=status, query=query,
                             limit=int(request.args.get("limit", 200))))

def register_routes(app) -> int:
    app.register_blueprint(history_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("history."))

