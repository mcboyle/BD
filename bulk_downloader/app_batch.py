"""batch API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/batch views moved onto a Flask Blueprint.
Endpoint labels gain a "batch." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

batch_bp = Blueprint("batch", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@batch_bp.route("/api/batch/retry", methods=["POST"])
def api_batch_retry():
    _check_csrf()
    body = request.json or {}
    try:
        from . import batch_ops as _bo
        return jsonify(_bo.bulk_retry(
            body.get("filter", {}),
            dry_run=bool(body.get("dry_run", True)),
            reset_to_status=body.get("reset_to_status", "pending"),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@batch_bp.route("/api/batch/delete", methods=["POST"])
def api_batch_delete():
    _check_csrf()
    body = request.json or {}
    try:
        from . import batch_ops as _bo
        return jsonify(_bo.bulk_delete(
            body.get("filter", {}),
            dry_run=bool(body.get("dry_run", True)),
            delete_files=bool(body.get("delete_files", False)),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@batch_bp.route("/api/batch/move", methods=["POST"])
def api_batch_move():
    _check_csrf()
    body = request.json or {}
    target = body.get("target_dir", "") or ""
    if not target:
        return jsonify({"ok": False, "error": "target_dir required"}), 400
    try:
        from . import batch_ops as _bo
        return jsonify(_bo.bulk_move(
            body.get("filter", {}),
            target_dir=target,
            dry_run=bool(body.get("dry_run", True)),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@batch_bp.route("/api/batch/dedup_scan", methods=["POST"])
def api_batch_dedup():
    _check_csrf()
    body = request.json or {}
    try:
        from . import batch_ops as _bo
        return jsonify(_bo.bulk_dedup_scan(
            site_id=body.get("site_id"),
            min_file_size_mb=int(body.get("min_file_size_mb", 50)),
            limit=int(body.get("limit", 5000)),
        ))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(batch_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("batch."))

