"""integrations API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/integrations views moved onto a Flask Blueprint.
Endpoint labels gain a "integrations." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_app_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

integrations_bp = Blueprint("integrations", __name__)

def _app__app_cfg():
    """The live shared _app_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "_app_cfg")


@integrations_bp.route("/api/integrations/health", methods=["GET"])
def api_integrations_health():
    """Cut 7: read-only, FAIL-OPEN, SANITIZED integration health rollup.

    Always 200/ok:true. Each integration reports only booleans / counts /
    latency — never an endpoint URL, token, or key. A failing sub-probe is
    reported as that integration being unhealthy, never as a failed call."""
    _app_cfg = _app__app_cfg()
    integrations = {}

    # AI assist — sanitized to the rolling health counters.
    try:
        from . import aiassist
        h = aiassist.get_health() or {}
        integrations["ai"] = {
            "ok": bool(h.get("last_ok")),
            "calls": int(h.get("call_count") or 0),
            "failures": int(h.get("fail_count") or 0),
            "latency_ms": h.get("latency_ms"),
        }
    except Exception:
        integrations["ai"] = {"ok": False, "calls": 0, "failures": 0,
                              "latency_ms": None}

    # Media integrations — configured-boolean only (presence of the enable flag
    # in the global config). Never the URL or credential.
    try:
        cfg = _app_cfg or {}
    except Exception:
        cfg = {}
    for name in ("plex", "jellyfin", "stash", "ha"):
        integrations[name] = {"configured": bool(cfg.get(f"{name}_enabled"))}

    return jsonify({"ok": True, "integrations": integrations})

def register_routes(app) -> int:
    app.register_blueprint(integrations_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("integrations."))

