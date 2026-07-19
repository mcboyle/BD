"""selector_drift API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/selector_drift views moved onto a Flask Blueprint.
Endpoint labels gain a "selector_drift." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

selector_drift_bp = Blueprint("selector_drift", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@selector_drift_bp.route("/api/selector_drift/status")
def api_selector_drift_status():
    """Snapshot of every site's drift state. Stale-flagged first."""
    try:
        from . import selector_drift as _sd
        return jsonify({"sites": _sd.status_all()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@selector_drift_bp.route("/api/selector_drift/status/<sid>")
def api_selector_drift_status_one(sid):
    """One site's drift state."""
    try:
        from . import selector_drift as _sd
        return jsonify(_sd.status_for(sid))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@selector_drift_bp.route("/api/selector_drift/reset/<sid>", methods=["POST"])
def api_selector_drift_reset(sid):
    """Reset drift state for one site — used after a successful
    re-teach to clear the stale flag."""
    _check_csrf()
    try:
        from . import selector_drift as _sd
        return jsonify({"ok": _sd.reset(sid)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(selector_drift_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("selector_drift."))

