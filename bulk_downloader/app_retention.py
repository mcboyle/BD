"""retention API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/retention views moved onto a Flask Blueprint.
Endpoint labels gain a "retention." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

retention_bp = Blueprint("retention", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@retention_bp.route("/api/retention/preview/<sid>")
def api_retention_preview(sid):
    """Show what retention would delete for one site (dry-run inspection)."""
    s_cfg = _app_s_cfg()
    cfg = (s_cfg or {}).get(sid)
    if not cfg:
        return jsonify({"error": f"no such site: {sid}"}), 404
    try:
        from . import retention as _rt
        candidates = _rt.find_candidates(sid, cfg)
        # Summarize sizes
        total_bytes = sum(c.get("file_size", 0) for c in candidates)
        return jsonify({
            "site_id": sid,
            "candidate_count": len(candidates),
            "total_bytes": total_bytes,
            "candidates": candidates[:200],  # cap for huge libraries
            "retention_days": cfg.get("retention_days", 0),
            "retention_max_gb": cfg.get("retention_max_gb", 0),
            "retention_keep_tagged_with":
                cfg.get("retention_keep_tagged_with", []),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@retention_bp.route("/api/retention/apply", methods=["POST"])
def api_retention_apply():
    """Apply retention. Body: {dry_run: bool, confirm_ids?: [int],
    site_id?: str}. Default dry_run=True; pass false to actually delete.

    Preview-verbatim (F4.2): when ``confirm_ids`` is supplied, deletion is
    restricted to the intersection of those ids with the freshly computed
    candidates — apply can never delete more than the preceding preview
    disclosed. ``site_id`` scopes the run to one site (the per-site SPA
    destructive flow). Omitting confirm_ids preserves the legacy unbound
    all-sites sweep.
    """
    s_cfg = _app_s_cfg()
    _check_csrf()
    body = request.json or {}
    dry_run = bool(body.get("dry_run", True))
    confirm_ids = body.get("confirm_ids")
    if confirm_ids is not None and not isinstance(confirm_ids, list):
        return jsonify({"ok": False,
                        "error": "confirm_ids must be a list of ids"}), 400
    site_id = body.get("site_id")
    try:
        from . import retention as _rt
        return jsonify(_rt.apply_retention(
            s_cfg, dry_run=dry_run,
            confirm_ids=confirm_ids, site_id=site_id))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@retention_bp.route("/api/retention/audit")
def api_retention_audit():
    """Recent retention deletion audit log. ?dry_run=1 for dry-run rows
    only, ?dry_run=0 for actual deletions only."""
    try:
        from . import retention as _rt
        dry = request.args.get("dry_run")
        if dry == "1":
            dry_only = True
        elif dry == "0":
            dry_only = False
        else:
            dry_only = None
        return jsonify({"audit": _rt.audit_log(
            limit=int(request.args.get("limit", 100) or 100),
            dry_run_only=dry_only)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@retention_bp.route("/api/retention/mark_excluded/<int:hid>", methods=["POST"])
def api_retention_exclude(hid):
    """Toggle 'do not delete' on a history row. Body: {excluded: bool}."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import retention as _rt
        return jsonify({"ok": _rt.mark_excluded(
            hid, bool(body.get("excluded", True)))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(retention_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("retention."))

