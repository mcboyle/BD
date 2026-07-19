"""marketplace API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/marketplace views moved onto a Flask Blueprint.
Endpoint labels gain a "marketplace." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

marketplace_bp = Blueprint("marketplace", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@marketplace_bp.route("/api/marketplace/export/<sid>", methods=["POST"])
def api_marketplace_export(sid):
    """Export site `sid` as a portable bundle. Body may include:
      {name, description, author, include_learned, sign_with}.
    Returns the bundle JSON."""
    s_cfg = _app_s_cfg()
    _check_csrf()
    if sid not in s_cfg:
        return jsonify({"error": "site not found"}), 404
    body = request.json or {}
    try:
        from . import marketplace as _mp
        bundle = _mp.export_template(
            sid, s_cfg[sid],
            name=body.get("name"),
            description=body.get("description", ""),
            author=body.get("author", ""),
            include_learned=bool(body.get("include_learned", True)),
            sign_with=body.get("sign_with") or None,
        )
        return jsonify({"ok": True, "bundle": bundle,
                        "suggested_filename": _mp.bundle_to_filename(bundle)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@marketplace_bp.route("/api/marketplace/import", methods=["POST"])
def api_marketplace_import():
    """Import a bundle. Body: {bundle: <obj>, target_site_id: <str>,
    verify_with: <secret>}. Returns the validated config + warnings."""
    _check_csrf()
    body = request.json or {}
    if not body.get("bundle"):
        return jsonify({"ok": False, "error": "bundle required"}), 400
    try:
        from . import marketplace as _mp
        r = _mp.import_template(
            body["bundle"],
            target_site_id=body.get("target_site_id"),
            verify_with=body.get("verify_with") or None,
        )
        return jsonify(r)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@marketplace_bp.route("/api/marketplace/import/preview", methods=["POST"])
def api_marketplace_import_preview():
    """Read-only preview mirror of /api/marketplace/import (Cut 3).

    Validates + verifies the bundle, classifies the target as new/changed
    against the configured sites, and returns a REDACTED config preview plus
    the omitted-secret keys — without emitting a config to persist. Body:
    {bundle, target_site_id?, verify_with?}. No CSRF gate: read-only.
    """
    s_cfg = _app_s_cfg()
    body = request.json or {}
    if not body.get("bundle"):
        return jsonify({"ok": False, "error": "bundle required"}), 400
    try:
        from . import marketplace as _mp
        r = _mp.preview_import_template(
            body["bundle"],
            target_site_id=body.get("target_site_id"),
            verify_with=body.get("verify_with") or None,
            existing_site_ids=set(s_cfg or {}),
        )
        return jsonify(r)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(marketplace_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("marketplace."))

