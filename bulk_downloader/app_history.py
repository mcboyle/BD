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

# O1789 hl-history-search F5: the largest page one request may ask for. An
# unbounded limit returned the whole table, and >= 2**63 crashed SQLite binding.
_HISTORY_LIMIT_MAX = 1000
_SQLITE_INT_MAX = 2 ** 63 - 1


def _prune_days(body):
    """F6: whole, non-negative days, or None. A bool, null, text, fraction,
    NaN/Infinity or negative value is refused instead of crashing (500) or
    being silently coerced (true -> 1, 3.9 -> 3, -5 -> a NULL cutoff)."""
    if not isinstance(body, dict):
        return None
    v = body.get("days", 90)
    if isinstance(v, str) and v.strip().isascii() and v.strip().isdigit():
        v = int(v.strip())
    elif isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= _SQLITE_INT_MAX:
        return None
    return v


@history_bp.route("/api/history/prune",methods=["POST"])
def api_prune():
    # r2 (cx158 F1): a missing, malformed or non-object body is refused before
    # anything is deleted -- never read as {} (which would prune 90 days).
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "body must be a JSON object"}), 400
    days = _prune_days(body)
    if days is None:
        return jsonify({"ok": False,
                        "error": "days must be a whole number >= 0"}), 400
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
    default_limit = 100 if cursor is not None or paginate else 200
    try:
        limit = int(request.args.get("limit", default_limit))
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be a positive integer"}), 400
    if limit < 1:
        return jsonify({"error": "limit must be a positive integer"}), 400
    limit = min(limit, _HISTORY_LIMIT_MAX)
    if cursor is not None or paginate:
        from .db import db_search_cursor
        # F7: a cursor is a history id handed out as next_cursor. Anything
        # else is refused (as library browse does) -- never a silent first page.
        after_id = None
        if cursor not in (None, ""):
            if not (cursor.isascii() and cursor.isdigit()) or int(cursor) > _SQLITE_INT_MAX:
                return jsonify({"error": "cursor_invalid"}), 400
            after_id = int(cursor)
        rows, next_cursor = db_search_cursor(
            site_id=site_id, status=status, query=query,
            after_id=after_id, limit=limit)
        return jsonify({"rows": rows, "next_cursor": next_cursor})
    return jsonify(db_search(site_id=site_id, status=status, query=query,
                             limit=limit))

def register_routes(app) -> int:
    app.register_blueprint(history_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("history."))

