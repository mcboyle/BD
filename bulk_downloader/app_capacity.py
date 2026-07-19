"""capacity API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/capacity views moved onto a Flask Blueprint.
Endpoint labels gain a "capacity." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

capacity_bp = Blueprint("capacity", __name__)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@capacity_bp.route("/api/capacity")
def api_capacity():
    """Return capacity forecasts: disk runway, queue ETA, bottleneck hint.
    Optional ?site_id=<sid> scopes the queue forecast to one site.
    Optional ?dl_dir=<path> overrides the disk path (default: first
    configured site's download_dir)."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    try:
        from . import capacity as _cap
        sid = request.args.get("site_id")
        dl_dir = request.args.get("dl_dir") or ""
        if not dl_dir:
            # Pick any configured download_dir; they're usually shared
            for _sid, cfg in (s_cfg or {}).items():
                dl_dir = (cfg or {}).get("download_dir") or ""
                if dl_dir:
                    break
        scoped_runners = {sid: runners[sid]} if (sid and sid in runners) else runners
        return jsonify(_cap.capacity_report(scoped_runners, dl_dir))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(capacity_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("capacity."))

