"""supervisor API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/supervisor views moved onto a Flask Blueprint.
Endpoint labels gain a "supervisor." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_SUPERVISOR_AVAILABLE, _supervisor_mod) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

supervisor_bp = Blueprint("supervisor", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app__SUPERVISOR_AVAILABLE():
    """The live shared _SUPERVISOR_AVAILABLE from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_SUPERVISOR_AVAILABLE")

def _app__supervisor_mod():
    """The live shared _supervisor_mod from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_supervisor_mod")


@supervisor_bp.route("/api/supervisor/status", methods=["GET"])
def api_supervisor_status():
    """Token-bucket bandwidth supervisor stats."""
    _SUPERVISOR_AVAILABLE = _app__SUPERVISOR_AVAILABLE()
    _supervisor_mod = _app__supervisor_mod()
    if not _SUPERVISOR_AVAILABLE:
        return jsonify({"ok": False, "error": "supervisor unavailable"})
    return jsonify({"ok": True, "stats": _supervisor_mod.stats()})


@supervisor_bp.route("/api/supervisor/configure", methods=["POST"])
def api_supervisor_configure():
    """Hot-reload supervisor rate limits."""
    _SUPERVISOR_AVAILABLE = _app__SUPERVISOR_AVAILABLE()
    _supervisor_mod = _app__supervisor_mod()
    _check_csrf()
    if not _SUPERVISOR_AVAILABLE:
        return jsonify({"ok": False, "error": "supervisor unavailable"})
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled", False))
    global_bps = int(body.get("global_bps", 0) or 0)
    per_site_bps = body.get("per_site_bps") or {}
    if not isinstance(per_site_bps, dict):
        return jsonify({"ok": False, "error": "per_site_bps must be a dict"})
    # Sanitize
    sanitized_per_site = {
        str(k): int(v) for k, v in per_site_bps.items()
        if v is not None
    }
    _supervisor_mod.configure(
        enabled=enabled, global_bps=global_bps,
        per_site_bps=sanitized_per_site,
    )
    return jsonify({"ok": True, "stats": _supervisor_mod.stats()})

def register_routes(app) -> int:
    app.register_blueprint(supervisor_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("supervisor."))

