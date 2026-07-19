"""dev API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/dev views moved onto a Flask Blueprint.
Endpoint labels gain a "dev." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_app_cfg, app, runners, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

dev_bp = Blueprint("dev", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _dev_mode_guard(*_a, **_k):
    """Delegate to app._dev_mode_guard at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_dev_mode_guard")(*_a, **_k)

def _app__app_cfg():
    """The live shared _app_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "_app_cfg")

def _app_app():
    """The live shared app from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "app")

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")



def register_routes(app) -> int:
    app.register_blueprint(dev_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("dev."))


# ---- sub-slice route modules: importing them attaches their @dev_bp routes ----
from . import (
    app_dev_ai,
    app_dev_auth,
    app_dev_config,
    app_dev_db,
    app_dev_extract,
    app_dev_lint,
    app_dev_maint,
    app_dev_net,
    app_dev_obs,
    app_dev_runtime,
    app_dev_testci,
)  # noqa: E402,F401
