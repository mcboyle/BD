"""cleanup API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/cleanup views moved onto a Flask Blueprint.
Endpoint labels gain a "cleanup." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

cleanup_bp = Blueprint("cleanup", __name__)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@cleanup_bp.route("/api/cleanup/summary")
def api_cleanup_summary():
    s_cfg = _app_s_cfg()
    try:
        from . import cleanup_helpers as _ch
        return jsonify(_ch.summary(s_cfg=s_cfg))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(cleanup_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("cleanup."))

