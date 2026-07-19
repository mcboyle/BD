"""gallerydl_update API -- C6 (8.4), mirrors app_ytdlp_update.

POST /api/gallerydl_update -> operator-initiated `pip install --upgrade
gallery-dl`. Since gallery-dl versions are semver (no local staleness signal),
the update is force-only: `force=True` runs it, `force=False` is a no-op. A 24h
rate limit applies regardless. Operator-initiated only -- no automatic path.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

gallerydl_update_bp = Blueprint("gallerydl_update", __name__)


def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@gallerydl_update_bp.route("/api/gallerydl_update", methods=["POST"])
def api_gallerydl_update():
    _check_csrf()
    try:
        from . import gallerydl_updater
        force = bool((request.json or {}).get("force", False))
        ran, msg = gallerydl_updater.maybe_update(force=force)
        return jsonify({"ok": True, "ran": ran, "message": msg,
                        "status": gallerydl_updater.status_dict()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


def register_routes(app) -> int:
    app.register_blueprint(gallerydl_update_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("gallerydl_update."))
