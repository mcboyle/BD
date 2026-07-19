"""sites API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/sites views moved onto a Flask Blueprint.
Endpoint labels gain a "sites." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (CFG_FIELDS, DEFAULTS, RATE_LIMIT_WINDOW, _SITES_BULK_ACTIONS, _watch_stops, _watch_threads, runners, s_cfg, s_meta) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import os as _os
import json
import os
import re
import sys
import time
import uuid
from flask import Blueprint, Response, jsonify, request
from pathlib import Path
from .constants import SCREENSHOTS_DIR
from .runner import SiteRunner
from .runner import _ts
from datetime import datetime
from .db import db_search
from .db import queue_upsert

sites_bp = Blueprint("sites", __name__)

def _apply_login_template_by_id(*_a, **_k):
    """Delegate to app._apply_login_template_by_id at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_apply_login_template_by_id")(*_a, **_k)

def _apply_template_by_id(*_a, **_k):
    """Delegate to app._apply_template_by_id at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_apply_template_by_id")(*_a, **_k)

def _bd_cookie_dir(*_a, **_k):
    """Delegate to app._bd_cookie_dir at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_bd_cookie_dir")(*_a, **_k)

def _build_meta(*_a, **_k):
    """Delegate to app._build_meta at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_build_meta")(*_a, **_k)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _chk(*_a, **_k):
    """Delegate to app._chk at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_chk")(*_a, **_k)

def _create_site(*_a, **_k):
    """Delegate to app._create_site at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_create_site")(*_a, **_k)

def _do_action(*_a, **_k):
    """Delegate to app._do_action at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_do_action")(*_a, **_k)

def _m2_age_human(*_a, **_k):
    """Delegate to app._m2_age_human at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_m2_age_human")(*_a, **_k)

def _m2_auth_state(*_a, **_k):
    """Delegate to app._m2_auth_state at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_m2_auth_state")(*_a, **_k)

def _m2_avatar_color(*_a, **_k):
    """Delegate to app._m2_avatar_color at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_m2_avatar_color")(*_a, **_k)

def _m2_honeypot_suggestion(*_a, **_k):
    """Delegate to app._m2_honeypot_suggestion at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_m2_honeypot_suggestion")(*_a, **_k)

def _oi_dir_writable(*_a, **_k):
    """Delegate to app._oi_dir_writable at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_oi_dir_writable")(*_a, **_k)

def _rate_check(*_a, **_k):
    """Delegate to app._rate_check at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_rate_check")(*_a, **_k)

def _sanitize_display_name(*_a, **_k):
    """Delegate to app._sanitize_display_name at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_sanitize_display_name")(*_a, **_k)

def _save_sites_config(*_a, **_k):
    """Delegate to app._save_sites_config at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_save_sites_config")(*_a, **_k)

def _site_primary_url(*_a, **_k):
    """Delegate to app._site_primary_url at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_site_primary_url")(*_a, **_k)

def _start_session_keepers(*_a, **_k):
    """Delegate to app._start_session_keepers at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_start_session_keepers")(*_a, **_k)

def _start_watch_folder_threads(*_a, **_k):
    """Delegate to app._start_watch_folder_threads at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_start_watch_folder_threads")(*_a, **_k)

def _store_site_password_in_vault(*_a, **_k):
    """Delegate to app._store_site_password_in_vault at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_store_site_password_in_vault")(*_a, **_k)

def _teach_cors_response(*_a, **_k):
    """Delegate to app._teach_cors_response at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_teach_cors_response")(*_a, **_k)

def _validate_bulk_urls(*_a, **_k):
    """Delegate to app._validate_bulk_urls at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_validate_bulk_urls")(*_a, **_k)

def _validate_config_paths(*_a, **_k):
    """Delegate to app._validate_config_paths at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_validate_config_paths")(*_a, **_k)

def _vault_guard_for_password(*_a, **_k):
    """Delegate to app._vault_guard_for_password at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_vault_guard_for_password")(*_a, **_k)

def _app_CFG_FIELDS():
    """The live shared CFG_FIELDS from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "CFG_FIELDS")

def _app_DEFAULTS():
    """The live shared DEFAULTS from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "DEFAULTS")

def _app_RATE_LIMIT_WINDOW():
    """The live shared RATE_LIMIT_WINDOW from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "RATE_LIMIT_WINDOW")

def _app__SITES_BULK_ACTIONS():
    """The live shared _SITES_BULK_ACTIONS from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_SITES_BULK_ACTIONS")

def _app__watch_stops():
    """The live shared _watch_stops from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "_watch_stops")

def _app__watch_threads():
    """The live shared _watch_threads from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "_watch_threads")

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



def register_routes(app) -> int:
    app.register_blueprint(sites_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("sites."))


# ---- sub-slice route modules: importing them attaches their @sites_bp routes ----
from . import (
    app_sites_auth,
    app_sites_collection,
    app_sites_id_core,
    app_sites_integrations,
    app_sites_lifecycle,
    app_sites_queue,
    app_sites_teach,
)  # noqa: E402,F401
