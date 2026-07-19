"""subtitles API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/subtitles views moved onto a Flask Blueprint.
Endpoint labels gain a "subtitles." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from .db import db_conn

subtitles_bp = Blueprint("subtitles", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@subtitles_bp.route("/api/subtitles/status")
def api_subtitles_status():
    try:
        from . import subtitles as _sub
        return jsonify(_sub.status_dict())
    except Exception as e:
        return jsonify({"available": False, "error": str(e)[:200]}), 500
@subtitles_bp.route("/api/subtitles/fetch/<int:hid>", methods=["POST"])
def api_subtitles_fetch(hid):
    """Download subtitles for one history row's file. Body may include
    {languages: ['en', 'es']}; otherwise reads the site's
    `subtitle_languages` config or defaults to ['en']."""
    s_cfg = _app_s_cfg()
    _check_csrf()
    body = request.json or {}
    try:
        from . import subtitles as _sub
        if not _sub.is_available():
            return jsonify({
                "ok": False,
                "error": ("subliminal not installed — "
                          "pip install subliminal"),
                "import_error": _sub.import_error(),
            }), 503
        with db_conn() as cx:
            row = cx.execute(
                "SELECT site_id, filename FROM history WHERE id = ?",
                (hid,)).fetchone()
        if not row:
            return jsonify({"ok": False,
                            "error": f"no history row {hid}"}), 404
        filename = row["filename"] or ""
        if not filename:
            return jsonify({"ok": False,
                            "error": "row has no filename"}), 400
        # Languages: body > site config > default
        langs = body.get("languages") or []
        if not langs:
            site_cfg = (s_cfg or {}).get(row["site_id"]) or {}
            langs = (site_cfg.get("subtitle_languages")
                     or ["en"])
        result = _sub.download_for_file(filename, languages=langs)
        return jsonify({"ok": True, **(result or {})})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(subtitles_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("subtitles."))

