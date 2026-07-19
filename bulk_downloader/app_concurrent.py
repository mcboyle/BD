"""concurrent API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/concurrent views moved onto a Flask Blueprint.
Endpoint labels gain a "concurrent." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners, s_cfg, s_meta) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

concurrent_bp = Blueprint("concurrent", __name__)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")

def _app_s_meta():
    """The live shared s_meta from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_meta")


@concurrent_bp.route("/api/concurrent/<sid>",methods=["POST"])
def api_conc(sid):
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    s_meta = _app_s_meta()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    n=max(1,min(20,int((request.json or {}).get("n",2))))
    s_cfg[sid]["max_concurrent"]=n; s_meta[sid]["max_concurrent"]=n
    runners[sid].update_config(s_cfg[sid]); return jsonify({"ok":True,"max_concurrent":n})

def register_routes(app) -> int:
    app.register_blueprint(concurrent_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("concurrent."))

