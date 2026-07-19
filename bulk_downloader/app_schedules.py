"""schedules API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/schedules views moved onto a Flask Blueprint.
Endpoint labels gain a "schedules." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_capture_enqueue) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

schedules_bp = Blueprint("schedules", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app__capture_enqueue():
    """The live shared _capture_enqueue from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_capture_enqueue")


@schedules_bp.route("/api/schedules", methods=["GET"])
def api_schedules_list():
    """List all recurring-capture schedules + their last/next-run state."""
    try:
        from . import capture_schedules as _cs
        return jsonify({"ok": True, "schedules": _cs.list_schedules()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@schedules_bp.route("/api/schedules", methods=["POST"])
def api_schedules_add():
    """Register a recurring-capture schedule. Body: {site_id,
    cadence_hours, label?, urls?}. Validated + CSRF-gated; idempotent on
    (site_id, cadence_hours) to absorb a double-submit."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import capture_schedules as _cs
        rid = _cs.add_schedule(
            site_id=str(body.get("site_id", "")),
            cadence_hours=body.get("cadence_hours", 0),
            label=str(body.get("label", "")),
            urls=body.get("urls") or None,
        )
        if rid is None:
            return jsonify({"ok": False,
                            "error": "invalid params (site_id required; "
                                     "cadence_hours must be > 0)"}), 400
        return jsonify({"ok": True, "id": rid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@schedules_bp.route("/api/schedules/<int:sid>/remove", methods=["POST"])
def api_schedules_remove(sid):
    """Delete a recurring-capture schedule."""
    _check_csrf()
    try:
        from . import capture_schedules as _cs
        return jsonify({"ok": bool(_cs.remove_schedule(sid))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@schedules_bp.route("/api/schedules/<int:sid>/run_now", methods=["POST"])
def api_schedules_run_now(sid):
    """Force-run one schedule immediately (bypasses the cadence guard)."""
    _capture_enqueue = _app__capture_enqueue()
    _check_csrf()
    try:
        from . import capture_schedules as _cs
        return jsonify(_cs.run_one(sid, enqueue_fn=_capture_enqueue))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@schedules_bp.route("/api/schedules/export.ics", methods=["GET"])
def api_schedules_export_ics():
    """F7: iCalendar (.ics) export of all recurring-capture schedules --
    one recurring VEVENT per schedule. Content-Disposition makes the
    browser save the file; the feed can also be subscribed to."""
    try:
        from . import capture_schedules as _cs
        body = _cs.schedules_to_ics(_cs.list_schedules())
        resp = Response(body, mimetype="text/calendar; charset=utf-8")
        resp.headers["Content-Disposition"] = (
            'attachment; filename="bulkdownloader-schedules.ics"')
        return resp
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


def register_routes(app) -> int:
    app.register_blueprint(schedules_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("schedules."))

