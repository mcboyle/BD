"""ytdlp_update API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/ytdlp_update views moved onto a Flask Blueprint.
Endpoint labels gain a "ytdlp_update." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

ytdlp_update_bp = Blueprint("ytdlp_update", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@ytdlp_update_bp.route("/api/ytdlp_update", methods=["POST"])
def api_ytdlp_update():
    """Manually trigger yt-dlp pip upgrade. Operator-initiated only —
    not called from any automatic path. Force=True bypasses the
    'is it stale?' check; force=False respects the 30-day threshold.
    24h rate limit applies regardless of force."""
    _check_csrf()
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    elif not isinstance(body, dict):
        return jsonify({"ok": False, "error": "request body must be a JSON object"}), 400
    raw_force = body.get("force", False)
    if isinstance(raw_force, str):
        force = raw_force.strip().lower() not in ("", "0", "false", "no", "off")
    else:
        force = bool(raw_force)
    try:
        from . import ytdlp_updater
        ran, msg = ytdlp_updater.maybe_update(force=force)
        return jsonify({"ok": True, "ran": ran, "message": msg,
                        "status": ytdlp_updater.status_dict()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(ytdlp_update_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("ytdlp_update."))

