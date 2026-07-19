"""thumbs API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/thumbs views moved onto a Flask Blueprint.
Endpoint labels gain a "thumbs." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

thumbs_bp = Blueprint("thumbs", __name__)

import os as _os


def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


def _confine_path(raw):
    """Confine an operator-supplied media path to a configured download_dir.

    v3.66.754c (operator decision: CONFINE, not remove). The /api/thumbs/* routes hand
    body['path'] to ffmpeg; without this an arbitrary readable file reaches a subprocess.
    Reuses the /api/thumbnails/serve/ pattern: realpath (symlink + `..` safety) then
    startswith against the set of every configured site download_dir.

    Returns (real_path, None) if the path resolves UNDER some configured root, else
    (None, error_string). An empty root set is a CLOSED door, not an open one: with no
    media roots configured nothing is confinable, so everything is rejected -- fail shut.
    """
    import importlib
    A = importlib.import_module("bulk_downloader.app")
    s_cfg = getattr(A, "s_cfg", {}) or {}
    roots = []
    for _sid, _cfg in s_cfg.items():
        if isinstance(_cfg, dict):
            _dd = (_cfg.get("download_dir") or "").strip()
            if _dd:
                roots.append(_os.path.realpath(_dd))
    if not roots:
        return None, "no media root configured; refusing an unconfined path"
    real = _os.path.realpath(raw)
    for r in roots:
        if real == r or real.startswith(r + _os.sep):
            return real, None
    return None, "path is not under any configured download_dir"


@thumbs_bp.route("/api/thumbs/single", methods=["POST"])
def api_thumb_single():
    _check_csrf()
    body = request.json or {}
    if not body.get("path"):
        return jsonify({"ok": False, "error": "path required"}), 400
    safe, err = _confine_path(body["path"])
    if err:
        return jsonify({"ok": False, "error": err}), 403
    try:
        from . import thumbnail_sheets as _th
        return jsonify(_th.single_thumb(
            safe,
            at_pct=float(body.get("at_pct", 50)),
            size=body.get("size", "640x360")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@thumbs_bp.route("/api/thumbs/contact_sheet", methods=["POST"])
def api_thumb_contact():
    _check_csrf()
    body = request.json or {}
    if not body.get("path"):
        return jsonify({"ok": False, "error": "path required"}), 400
    safe, err = _confine_path(body["path"])
    if err:
        return jsonify({"ok": False, "error": err}), 403
    try:
        from . import thumbnail_sheets as _th
        return jsonify(_th.contact_sheet(
            safe,
            rows=int(body.get("rows", 4)),
            cols=int(body.get("cols", 4)),
            tile_width=int(body.get("tile_width", 320))))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@thumbs_bp.route("/api/thumbs/sprite_sheet", methods=["POST"])
def api_thumb_sprite():
    _check_csrf()
    body = request.json or {}
    if not body.get("path"):
        return jsonify({"ok": False, "error": "path required"}), 400
    safe, err = _confine_path(body["path"])
    if err:
        return jsonify({"ok": False, "error": err}), 403
    try:
        from . import thumbnail_sheets as _th
        return jsonify(_th.sprite_sheet(
            safe,
            count=int(body.get("count", 100)),
            tile_width=int(body.get("tile_width", 200))))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(thumbs_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("thumbs."))

