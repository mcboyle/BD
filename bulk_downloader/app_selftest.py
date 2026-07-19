"""selftest API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/selftest views moved onto a Flask Blueprint.
Endpoint labels gain a "selftest." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_SITES_CFG_PATH, _STARTUP_SELFTEST, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import os

from flask import Blueprint, jsonify, request
from .constants import DB_PATH as _DB_PATH
from . import selftest as _selftest

selftest_bp = Blueprint("selftest", __name__)


def _capture_store_root_for_selftest():
    """Resolved capture store root (Cut 1.3) for the selftest disk check, so a
    relocated store's disk is checked. Falls back to PROJECT_ROOT."""
    try:
        from . import dom_analyzer as _da
        return _da._capture_store_root()
    except Exception:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _app__SITES_CFG_PATH():
    """The live shared _SITES_CFG_PATH from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_SITES_CFG_PATH")

def _app__STARTUP_SELFTEST():
    """The live shared _STARTUP_SELFTEST from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_STARTUP_SELFTEST")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@selftest_bp.route("/api/selftest")
def api_selftest():
    """Return self-test results. Two modes:
       - ?startup=1  → the snapshot from app boot (cached, no re-run)
       - default     → fresh run with current state
    The startup snapshot is what the UI banner uses; on-demand rerun
    is what the user clicks to verify fixes."""
    _SITES_CFG_PATH = _app__SITES_CFG_PATH()
    _STARTUP_SELFTEST = _app__STARTUP_SELFTEST()
    s_cfg = _app_s_cfg()
    if request.args.get("startup"):
        return jsonify(_STARTUP_SELFTEST)
    dd = []
    for _s in s_cfg.values():
        _d = _s.get("download_dir")
        if _d: dd.append(_d)
    report = _selftest.run_all(
        sites_config_path=_SITES_CFG_PATH,
        db_path=_DB_PATH,
        cookies_dir="cookies",
        download_dirs=dd,
        captures_root=str(_capture_store_root_for_selftest()),
    )
    return jsonify(report)

def register_routes(app) -> int:
    app.register_blueprint(selftest_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("selftest."))

