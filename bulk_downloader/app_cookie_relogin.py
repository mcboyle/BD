"""cookie_relogin API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/cookie_relogin views moved onto a Flask Blueprint.
Endpoint labels gain a "cookie_relogin." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

cookie_relogin_bp = Blueprint("cookie_relogin", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@cookie_relogin_bp.route("/api/cookie_relogin/check", methods=["POST"])
def api_cookie_relogin_check():
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    _check_csrf()
    body = request.json or {}
    try:
        from . import cookie_relogin as _cr
        return jsonify(_cr.check_and_schedule(
            s_cfg=s_cfg, runners=runners,
            relogin_threshold=int(body.get("threshold", 50))))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@cookie_relogin_bp.route("/api/cookie_relogin/history")
def api_cookie_relogin_history():
    try:
        from . import cookie_relogin as _cr
        return jsonify({"runs": _cr.report_recent(
            limit=int(request.args.get("limit", 50)))})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(cookie_relogin_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("cookie_relogin."))

