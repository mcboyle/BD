"""saved_searches API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/saved_searches views moved verbatim onto a Flask
Blueprint. Endpoint labels gain a "saved_searches." prefix; the (rule, methods,
bare-name) routing surface is byte-identical (test_route_map_invariant
diffs empty). App-level helpers are reached lazily at call time.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

saved_searches_bp = Blueprint("saved_searches", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@saved_searches_bp.route("/api/saved_searches", methods=["GET"])
def api_saved_searches_list():
    try:
        from . import saved_searches as _ss
        return jsonify({"searches": _ss.list_all()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@saved_searches_bp.route("/api/saved_searches", methods=["POST"])
def api_saved_searches_add():
    _check_csrf()
    body = request.json or {}
    try:
        from . import saved_searches as _ss
        sid = _ss.add(
            name=body.get("name", "") or "",
            query=body.get("query", "") or "",
            site_id=body.get("site_id", "") or "",
            status=body.get("status", "") or "",
            schedule=body.get("schedule", "manual") or "manual",
            notify_via=body.get("notify_via", "") or "",
            action=body.get("action", "notify") or "notify",
            daily_cap=body.get("daily_cap", _ss.DEFAULT_DAILY_CAP),
        )
        if sid is None:
            return jsonify({"ok": False, "error": "add failed (name/query required, or duplicate name)"}), 400
        return jsonify({"ok": True, "id": sid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@saved_searches_bp.route("/api/saved_searches/<int:search_id>", methods=["DELETE"])
def api_saved_searches_remove(search_id):
    _check_csrf()
    try:
        from . import saved_searches as _ss
        ok = _ss.remove(search_id=search_id)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@saved_searches_bp.route("/api/saved_searches/<int:search_id>", methods=["PATCH"])
def api_saved_searches_update(search_id):
    """F3.1: partial update of a saved search. Body carries any subset of
    the editable fields (name, query, site_id, status, schedule, notify_via,
    enabled, action, daily_cap); saved_searches.update() validates and drops
    out-of-range fields (notably an unknown `action` is dropped, never coerced,
    so a bad PATCH can't silently flip a rule's lane). Returns {ok} where ok is
    False when no row matched or the body carried no accepted change."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import saved_searches as _ss
        ok = _ss.update(search_id, **body)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@saved_searches_bp.route("/api/saved_searches/<int:search_id>/run", methods=["POST"])
def api_saved_searches_run(search_id):
    _check_csrf()
    try:
        from . import saved_searches as _ss
        return jsonify(_ss.run_one(search_id))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@saved_searches_bp.route("/api/saved_searches/digest")
def api_saved_searches_digest():
    try:
        hours = int(request.args.get("hours_back", 168))
    except (TypeError, ValueError):
        hours = 168
    try:
        from . import saved_searches as _ss
        return jsonify(_ss.digest(hours_back=hours))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(saved_searches_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("saved_searches."))

