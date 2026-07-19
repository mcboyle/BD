"""cookie_quality API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/cookie_quality views moved onto a Flask Blueprint.
Endpoint labels gain a "cookie_quality." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

cookie_quality_bp = Blueprint("cookie_quality", __name__)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@cookie_quality_bp.route("/api/cookie_quality/<sid>")
def api_cookie_quality(sid):
    s_cfg = _app_s_cfg()
    try:
        from . import cookie_quality as _cq
        return jsonify(_cq.score(sid, s_cfg_entry=(s_cfg or {}).get(sid, {})))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
@cookie_quality_bp.route("/api/cookie_quality")
def api_cookie_quality_all():
    s_cfg = _app_s_cfg()
    try:
        from . import cookie_quality as _cq
        return jsonify({"rows": _cq.report_all(s_cfg)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(cookie_quality_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("cookie_quality."))

