"""ytdlp_archive API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/ytdlp_archive views moved onto a Flask Blueprint.
Endpoint labels gain a "ytdlp_archive." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_YTDLP_ARCH_AVAILABLE, _ytdlp_archive_module, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

ytdlp_archive_bp = Blueprint("ytdlp_archive", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app__YTDLP_ARCH_AVAILABLE():
    """The live shared _YTDLP_ARCH_AVAILABLE from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_YTDLP_ARCH_AVAILABLE")

def _app__ytdlp_archive_module():
    """The live shared _ytdlp_archive_module from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_ytdlp_archive_module")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@ytdlp_archive_bp.route("/api/ytdlp_archive/stats", methods=["GET"])
def api_ytdlp_archive_stats():
    """Stats for the archive file configured on any site. The UI uses
    the first site that has a path configured."""
    _YTDLP_ARCH_AVAILABLE = _app__YTDLP_ARCH_AVAILABLE()
    _ytdlp_archive_module = _app__ytdlp_archive_module()
    s_cfg = _app_s_cfg()
    if not _YTDLP_ARCH_AVAILABLE:
        return jsonify({"ok": False, "error": "yt_dlp_archive module unavailable"})
    archive_path = ""
    for sid, cfg in s_cfg.items():
        raw_path = cfg.get("ytdlp_archive_path") if isinstance(cfg, dict) else None
        if isinstance(raw_path, str) and raw_path.strip():
            archive_path = raw_path.strip()
            break
    if not archive_path:
        return jsonify({"ok": True, "configured": False,
                        "stats": {"total": 0, "by_extractor": {}}})
    return jsonify({
        "ok": True,
        "configured": True,
        "archive_path": archive_path,
        "stats": _ytdlp_archive_module.stats_for_archive(archive_path),
    })


@ytdlp_archive_bp.route("/api/ytdlp_archive/derive", methods=["POST"])
def api_ytdlp_archive_derive():
    """Test URL → (extractor, id) derivation. Used by the UI to show
    the user what BD would write to the archive for a given URL."""
    _YTDLP_ARCH_AVAILABLE = _app__YTDLP_ARCH_AVAILABLE()
    _ytdlp_archive_module = _app__ytdlp_archive_module()
    _check_csrf()
    if not _YTDLP_ARCH_AVAILABLE:
        return jsonify({"ok": False, "error": "yt_dlp_archive module unavailable"})
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    elif not isinstance(body, dict):
        return jsonify({"ok": False, "error": "request body must be a JSON object"}), 400
    raw_url = body.get("url")
    url = raw_url.strip() if isinstance(raw_url, str) else ""
    if not url:
        return jsonify({"ok": False, "error": "missing 'url'"})
    derived = _ytdlp_archive_module.derive_id(url)
    if derived is None:
        return jsonify({"ok": True, "url": url, "derived": None})
    extractor, vid = derived
    return jsonify({
        "ok": True,
        "url": url,
        "derived": {"extractor": extractor, "video_id": vid},
        "archive_line": f"{extractor} {vid}",
    })

def register_routes(app) -> int:
    app.register_blueprint(ytdlp_archive_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("ytdlp_archive."))

