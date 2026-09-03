"""status API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/status views moved onto a Flask Blueprint.
Endpoint labels gain a "status." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners, s_cfg, s_meta) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import time
from flask import Blueprint, jsonify, request


def _runners_generation(mapping):
    """A stable (sid, runner) list; locked when `mapping` is the live registry.

    Row 634: walking ``app_state.runners`` bare raises ``RuntimeError:
    dictionary changed size during iteration`` the instant a site create or
    delete lands mid-walk, AFTER the loop body has already acted on a prefix of
    the fleet.  Imported lazily (importlib, per call) for the same reason the
    other shared-state accessors here are: no new static import edge.
    """
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"),
                   "runners_generation")(mapping)


status_bp = Blueprint("status", __name__)

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


@status_bp.route("/api/status")
def api_status():
    """Phase 4.6: light=1 omits jobs/url_order. Frontend uses light mode
    when not viewing the Queue tab — saves MBs of payload at 10k+ URLs."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    s_meta = _app_s_meta()
    light = request.args.get("light") == "1"
    out={}
    import shutil as _shutil
    # Phase 14.8: cache disk-free per directory for 5 seconds. shutil.disk_usage
    # is fast but we'd still hit it once per site per poll (every 1.5s) which
    # is wasteful when users tend to share download dirs across sites.
    if not hasattr(api_status, "_disk_cache"):
        api_status._disk_cache = {}
    now = time.time()
    for sid, runner in _runners_generation(runners):
        st=runner.get_status(light=light); st["name"]=s_meta[sid].get("name",sid); st["config"]=s_meta[sid]
        # Compute disk-free for the download directory (cached)
        dl_dir = (s_cfg.get(sid) or {}).get("download_dir") or ""
        if dl_dir:
            cache = api_status._disk_cache.get(dl_dir)
            if cache and (now - cache[0]) < 5:
                st["disk_free_gb"] = cache[1]
            else:
                try:
                    free_bytes = _shutil.disk_usage(dl_dir).free
                    free_gb = round(free_bytes / (1024**3), 2)
                    st["disk_free_gb"] = free_gb
                    api_status._disk_cache[dl_dir] = (now, free_gb)
                except Exception:
                    pass  # path doesn't exist yet — silently skip
        out[sid]=st
    return jsonify(out)

def register_routes(app) -> int:
    app.register_blueprint(status_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("status."))

