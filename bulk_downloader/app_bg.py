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
            _bg.ensure_default_tasks()
            _bg.start()
            api_bg_status._started = True
        return jsonify(_bg.status())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@bg_bp.route("/api/bg/enable/<name>", methods=["POST"])
def api_bg_enable(name):
    _check_csrf()
    body = request.json or {}
    # bool("false") is True: a string flag enabled the task it meant to stop.
    flag = body.get("enabled", True) if isinstance(body, dict) else None
    if isinstance(flag, str):
        flag = {"true": True, "1": True, "false": False, "0": False}.get(flag.strip().lower())
    elif not isinstance(flag, bool) and flag in (0, 1):
        flag = bool(flag)
    if not isinstance(flag, bool):
        return jsonify({"ok": False, "error": "body must be {\"enabled\": true|false}"}), 400
    try:
        from . import bg_scheduler as _bg
        ok = _bg.set_enabled(name, flag)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(bg_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("bg."))

