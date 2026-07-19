"""eol API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/eol views moved onto a Flask Blueprint.
Endpoint labels gain a "eol." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

eol_bp = Blueprint("eol", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@eol_bp.route("/api/eol/migration_report")
def api_eol_report():
    s_cfg = _app_s_cfg()
    try:
        from . import eol_export as _eol
        return jsonify(_eol.migration_report(s_cfg=s_cfg))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@eol_bp.route("/api/eol/export", methods=["POST"])
def api_eol_export():
    s_cfg = _app_s_cfg()
    _check_csrf()
    body = request.json or {}
    dest = body.get("dest_dir", "")
    if not dest:
        return jsonify({"ok": False, "error": "dest_dir required"}), 400
    try:
        from . import eol_export as _eol
        return jsonify(_eol.full_export(
            dest,
            s_cfg=s_cfg,
            include_cookies=bool(body.get("include_cookies", False)),
            sign_secret=body.get("sign_secret") or None,
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(eol_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("eol."))

