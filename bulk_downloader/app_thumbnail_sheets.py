"""thumbnail_sheets API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/thumbnail_sheets views moved onto a Flask Blueprint.
Endpoint labels gain a "thumbnail_sheets." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from .db import db_conn

thumbnail_sheets_bp = Blueprint("thumbnail_sheets", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@thumbnail_sheets_bp.route("/api/thumbnail_sheets/contact_sheet/<int:hid>", methods=["POST"])
def api_thumbsheet_contact_by_hid(hid):
    """Generate a 4x4 contact sheet for one history row. Body may
    override {rows, cols, width}. Returns path to written PNG."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import thumbnail_sheets as _ts
        with db_conn() as cx:
            row = cx.execute(
                "SELECT filename FROM history WHERE id = ?", (hid,)
            ).fetchone()
        if not row or not row[0]:
            return jsonify({"ok": False, "error": "no file for hid"}), 404
        return jsonify(_ts.contact_sheet(
            row[0],
            rows=int(body.get("rows", 4) or 4),
            cols=int(body.get("cols", 4) or 4),
            tile_width=int(body.get("tile_width", 320) or 320),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@thumbnail_sheets_bp.route("/api/thumbnail_sheets/single/<int:hid>", methods=["POST"])
def api_thumbsheet_single_by_hid(hid):
    """Generate one thumbnail at a given percentage of the video."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import thumbnail_sheets as _ts
        with db_conn() as cx:
            row = cx.execute(
                "SELECT filename FROM history WHERE id = ?", (hid,)
            ).fetchone()
        if not row or not row[0]:
            return jsonify({"ok": False, "error": "no file for hid"}), 404
        return jsonify(_ts.single_thumb(
            row[0],
            at_pct=float(body.get("at_pct", 50.0) or 50.0),
            size=body.get("size", "640x360"),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(thumbnail_sheets_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("thumbnail_sheets."))

