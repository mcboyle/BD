"""doctor API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/doctor views moved onto a Flask Blueprint.
Endpoint labels gain a "doctor." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from .db import db_conn

doctor_bp = Blueprint("doctor", __name__)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@doctor_bp.route("/api/doctor")
def api_doctor():
    """Full diagnostic pass: environment, optional deps, cookie age."""
    s_cfg = _app_s_cfg()
    from . import doctor as _doctor
    try:
        report = _doctor.run_diagnostics(sites_config=s_cfg)
        return jsonify({"ok": True, "report": report})
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 500


@doctor_bp.route("/api/doctor/diagnose", methods=["POST"])
def api_doctor_diagnose():
    """Pattern-match a failure error string. Body: {error: '...'} or
    {hid: <history_id>} to look the error up from a history row.
    Returns {matched, cause, suggestion, confidence}."""
    from . import doctor as _doctor
    body = request.get_json(silent=True) or {}
    error_text = body.get("error", "")
    # Allow passing a history id instead of raw text
    if not error_text and body.get("hid"):
        try:
            with db_conn() as cx:
                row = cx.execute(
                    "SELECT message FROM history WHERE id=?",
                    (int(body["hid"]),)).fetchone()
            if row:
                error_text = row["message"] or ""
        except Exception:
            pass
    result = _doctor.diagnose_failure(error_text)
    return jsonify({"ok": True, "diagnosis": result})

def register_routes(app) -> int:
    app.register_blueprint(doctor_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("doctor."))

