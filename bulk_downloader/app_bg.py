"""bg API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/bg views moved onto a Flask Blueprint.
Endpoint labels gain a "bg." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

bg_bp = Blueprint("bg", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@bg_bp.route("/api/bg/status")
def api_bg_status():
    try:
        from . import bg_scheduler as _bg
        # Lazy first-start: register canonical tasks + spin up thread.
        if not getattr(api_bg_status, "_started", False):
            _bg.register_default_tasks()
            _bg.start()
            api_bg_status._started = True
        return jsonify(_bg.status())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@bg_bp.route("/api/bg/enable/<name>", methods=["POST"])
def api_bg_enable(name):
    _check_csrf()
    body = request.json or {}
    try:
        from . import bg_scheduler as _bg
        ok = _bg.set_enabled(name, bool(body.get("enabled", True)))
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(bg_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("bg."))

