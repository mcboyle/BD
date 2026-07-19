"""gallerydl_status API -- C6 (8.4), mirrors app_ytdlp_status.

GET /api/gallerydl_status -> {installed, version, age_days, stale} for the
installed gallery-dl. ``age_days`` is always None and ``stale`` always False
(gallery-dl uses semver, which carries no release date). Cached 1h internally.
"""
from __future__ import annotations

from flask import Blueprint, jsonify

gallerydl_status_bp = Blueprint("gallerydl_status", __name__)


@gallerydl_status_bp.route("/api/gallerydl_status")
def api_gallerydl_status():
    try:
        from . import gallerydl_updater
        return jsonify(gallerydl_updater.status_dict())
    except Exception as e:
        return jsonify({"installed": False, "version": None,
                        "age_days": None, "stale": False,
                        "error": str(e)[:200]})


def register_routes(app) -> int:
    app.register_blueprint(gallerydl_status_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("gallerydl_status."))
