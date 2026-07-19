"""scheduled_exports API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/scheduled_exports views moved onto a Flask Blueprint.
Endpoint labels gain a "scheduled_exports." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

scheduled_exports_bp = Blueprint("scheduled_exports", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@scheduled_exports_bp.route("/api/scheduled_exports/list")
def api_sched_exports_list():
    """List all scheduled exports + their last-run status."""
    try:
        from . import scheduled_exports as _se
        return jsonify({"schedules": _se.list_schedules()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@scheduled_exports_bp.route("/api/scheduled_exports/add", methods=["POST"])
def api_sched_exports_add():
    """Register a new scheduled export. Body: {label, format,
    destination, cadence_hours, filter_dict?, retention_count?,
    source_saved_search?}. CAP-3: source_saved_search links the export to a
    saved search whose LIVE criteria drive the export (overrides filter_dict)."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import scheduled_exports as _se
        _src = body.get("source_saved_search")
        rid = _se.add_schedule(
            label=body.get("label", ""),
            format=body.get("format", "csv"),
            destination=body.get("destination", ""),
            cadence_hours=int(body.get("cadence_hours", 24) or 24),
            filter_dict=body.get("filter_dict") or None,
            retention_count=int(body.get("retention_count", 10) or 10),
            source_saved_search=int(_src) if _src else None,
        )
        if rid is None:
            return jsonify({"ok": False,
                            "error": "invalid params (format must be one of "
                                     "eol/csv/json/ndjson/m3u; destination "
                                     "must be writable; cadence_hours > 0)"}), 400
        return jsonify({"ok": True, "id": rid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@scheduled_exports_bp.route("/api/scheduled_exports/remove/<int:sid>", methods=["POST"])
def api_sched_exports_remove(sid):
    """Delete a scheduled export."""
    _check_csrf()
    try:
        from . import scheduled_exports as _se
        return jsonify({"ok": bool(_se.remove_schedule(sid))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@scheduled_exports_bp.route("/api/scheduled_exports/run_now", methods=["POST"])
def api_sched_exports_run_now():
    """Force-run all due exports immediately (bypasses scheduler)."""
    s_cfg = _app_s_cfg()
    _check_csrf()
    try:
        from . import scheduled_exports as _se
        return jsonify(_se.run_due_exports(s_cfg=s_cfg))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(scheduled_exports_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("scheduled_exports."))

