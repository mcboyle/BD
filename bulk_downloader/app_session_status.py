"""session_status API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/session_status views moved onto a Flask Blueprint.
Endpoint labels gain a "session_status." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

session_status_bp = Blueprint("session_status", __name__)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@session_status_bp.route("/api/session_status", methods=["GET"])
def api_session_status():
    """Returns the state of every keep-alive thread. UI polls this for
    the green/amber/red status bar and countdown column.

    Shape: {"keepers": [{site_id, account_idx, state, last_login_ts,
                         last_heartbeat_ts, next_check_ts,
                         predicted_expiry_ts, consecutive_failures,
                         last_detail}, ...],
            "overall": "healthy" | "degraded" | "all_disconnected"}

    overall is a summary for the toolbar pill — green when all keepers
    are connected, amber when some are degraded, red when all are out.
    """
    s_cfg = _app_s_cfg()
    from . import session_keeper as _sk
    keepers = _sk.get_status()
    if not keepers:
        overall = "none"
    else:
        connected = sum(1 for k in keepers if k["state"] == "connected")
        total_active = sum(1 for k in keepers if k["state"] != "disabled")
        if total_active == 0:
            overall = "none"
        elif connected == total_active:
            overall = "healthy"
        elif connected == 0:
            overall = "all_disconnected"
        else:
            overall = "degraded"
    # Compute display values for each keeper
    import time as _t
    now = _t.time()
    for k in keepers:
        # session_seconds_remaining: time until predicted expiry
        pred = k.get("predicted_expiry_ts") or 0
        if pred and pred > now:
            k["session_seconds_remaining"] = int(pred - now)
        else:
            k["session_seconds_remaining"] = None
        # next_check_seconds: time until next scheduled check
        nxt = k.get("next_check_ts") or 0
        if nxt and nxt > now:
            k["next_check_seconds"] = int(nxt - now)
        else:
            k["next_check_seconds"] = 0
        # Site name for display (the UI doesn't have a sites map per-sid handy
        # in every render path).
        cfg = s_cfg.get(k["site_id"]) or {}
        k["site_name"] = cfg.get("name", k["site_id"])
    return jsonify({"ok": True, "keepers": keepers, "overall": overall})

def register_routes(app) -> int:
    app.register_blueprint(session_status_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("session_status."))

