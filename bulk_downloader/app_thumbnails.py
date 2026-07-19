"""thumbnails API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/thumbnails views moved onto a Flask Blueprint.
Endpoint labels gain a "thumbnails." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_THUMB_AVAILABLE, _thumb_mod, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import os
from flask import Blueprint, jsonify, request

thumbnails_bp = Blueprint("thumbnails", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app__THUMB_AVAILABLE():
    """The live shared _THUMB_AVAILABLE from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_THUMB_AVAILABLE")

def _app__thumb_mod():
    """The live shared _thumb_mod from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_thumb_mod")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@thumbnails_bp.route("/api/thumbnails/status", methods=["GET"])
def api_thumbnails_status():
    """ffmpeg availability + worker stats."""
    _THUMB_AVAILABLE = _app__THUMB_AVAILABLE()
    _thumb_mod = _app__thumb_mod()
    if not _THUMB_AVAILABLE:
        return jsonify({"ok": False, "error": "thumbnail module unavailable"})
    worker_stats: dict = {}
    try:
        worker = _thumb_mod.get_default_worker()
        worker_stats = worker.stats()
    except Exception:
        pass
    return jsonify({
        "ok": True,
        "ffmpeg_available": _thumb_mod.is_ffmpeg_available(),
        "ffprobe_available": _thumb_mod.is_ffprobe_available(),
        "available": _thumb_mod.is_available(),
        "worker": worker_stats,
    })


@thumbnails_bp.route("/api/thumbnails/regenerate", methods=["POST"])
def api_thumbnails_regenerate():
    """Queue a thumbnail (re)generation for a specific file."""
    _THUMB_AVAILABLE = _app__THUMB_AVAILABLE()
    _thumb_mod = _app__thumb_mod()
    _check_csrf()
    if not _THUMB_AVAILABLE:
        return jsonify({"ok": False, "error": "thumbnail module unavailable"})
    body = request.get_json(silent=True) or {}
    source_path = (body.get("source_path") or "").strip()
    if not source_path:
        return jsonify({"ok": False, "error": "missing 'source_path'"})
    cfg = {
        "mode": body.get("mode", "single"),
        "output_dir_mode": body.get("output_dir_mode", "sidecar"),
        "download_dir": body.get("download_dir", ""),
        "sheet_rows": int(body.get("sheet_rows", 3) or 3),
        "sheet_cols": int(body.get("sheet_cols", 3) or 3),
        "skip_existing": False,  # explicit re-gen
    }
    try:
        worker = _thumb_mod.get_default_worker()
        worker.submit(source_path, config=cfg)
    except Exception as e:
        return jsonify({"ok": False, "error": f"submit_failed:{e}"})
    return jsonify({"ok": True, "source_path": source_path,
                     "queued": True})


@thumbnails_bp.route("/api/thumbnails/serve/<site_id>/<path:filename>", methods=["GET"])
def api_thumbnails_serve(site_id, filename):
    """Serve a thumbnail file. Used by the UI to display thumbs inline.

    Resolves the path against the site's download_dir + thumbnail_dir_mode.
    Returns 404 if the file isn't there or paths look unsafe."""
    _THUMB_AVAILABLE = _app__THUMB_AVAILABLE()
    _thumb_mod = _app__thumb_mod()
    s_cfg = _app_s_cfg()
    if not _THUMB_AVAILABLE:
        return ("thumbnail module unavailable", 503)
    cfg = s_cfg.get(site_id)
    if not cfg:
        return ("unknown site", 404)
    dl_dir = (cfg.get("download_dir") or "").strip()
    if not dl_dir:
        return ("download_dir not set", 404)
    # Build the candidate thumb path
    dir_mode = cfg.get("thumbnail_dir_mode", "sidecar")
    # Compute against the *video* path the UI passed in, e.g.
    # "Brazzers/scene_2024.mp4". The thumb path for it:
    candidate_video = os.path.join(dl_dir, filename)
    thumb_path = _thumb_mod.resolve_output_path(
        candidate_video, mode=dir_mode, download_dir=dl_dir,
    )
    if not thumb_path or not os.path.isfile(thumb_path):
        return ("thumbnail not found", 404)
    # Safety check: the resolved path must be UNDER dl_dir (no
    # `..` traversal). Use realpath for symlink safety.
    real_dl = os.path.realpath(dl_dir)
    real_thumb = os.path.realpath(thumb_path)
    if not real_thumb.startswith(real_dl + os.sep) and real_thumb != real_dl:
        return ("path outside download_dir", 403)
    from flask import send_file
    return send_file(real_thumb, mimetype="image/jpeg")

def register_routes(app) -> int:
    app.register_blueprint(thumbnails_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("thumbnails."))

