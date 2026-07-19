"""analyzer API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/analyzer views moved verbatim onto a Flask
Blueprint. Endpoint labels gain a "analyzer." prefix; the (rule, methods,
bare-name) routing surface is byte-identical (test_route_map_invariant
diffs empty). App-level helpers are reached lazily at call time.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

analyzer_bp = Blueprint("analyzer", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@analyzer_bp.route("/api/analyzer/captures")
def api_analyzer_captures():
    """List capture artifacts available to the workbench. Read-only.

    BUG-3/7: enumerate RECURSIVELY (scan_captures) so onboarding/guided captures
    nested in subfolders appear in the picker; each row carries a ``rel_path``
    token the loader + draft builder resolve. list_captures was non-recursive,
    so subfolder captures showed as "No captures found".
    """
    try:
        from . import dom_analyzer as _da
        from . import db as _db  # Cut 1.2: durable capture index (lazy; edge re-frozen)
        # Cut 1.2: serve from the durable index. Reconcile-if-empty so the picker
        # never regresses to "No captures found" when captures exist on disk but
        # the index was never populated (first use / fresh DB): walk once, persist,
        # then read the index.
        rows = _db.db_captures_all(limit=1000)
        if not rows:
            walked = _da.scan_captures(limit=1000)
            if walked:
                _db.db_captures_upsert(walked)
                rows = _db.db_captures_all(limit=1000)
        return jsonify({"ok": True, "captures": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@analyzer_bp.route("/api/analyzer/load", methods=["POST"])
def api_analyzer_load():
    """Body: {capture}. Returns {ok, has_dom, residual_count, residual_kinds,
    tree, html, capture}. The gate fails closed: on a residual, tree/html are
    null and only counts-by-kind are returned."""
    _check_csrf()
    body = request.json or {}
    name = (body.get("capture") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "capture required"}), 400
    try:
        from . import dom_analyzer as _da
        res = _da.analyze_capture(name)
        if not res.get("ok") and res.get("error") == "unknown capture":
            return jsonify(res), 404
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@analyzer_bp.route("/api/analyzer/tree", methods=["POST"])
def api_analyzer_tree():
    """Body: {capture, max_depth?, max_children?}. Depth/breadth-limited tree
    for large DOMs; the gate still scans the full tree."""
    _check_csrf()
    body = request.json or {}
    name = (body.get("capture") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "capture required"}), 400
    try:
        from . import dom_analyzer as _da
        res = _da.tree_view(
            name,
            max_depth=int(body.get("max_depth") or 200),
            max_children=int(body.get("max_children") or 0))
        if not res.get("ok") and res.get("error") == "unknown capture":
            return jsonify(res), 404
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@analyzer_bp.route("/api/analyzer/test", methods=["POST"])
def api_analyzer_test():
    """Body: {capture, selectors:[...]}. Evaluates selectors against the
    capture's GATED (redacted) offline DOM — matches + counts. Distinct from
    /api/playground/test, which fetches a live URL; the workbench tests the
    captured DOM."""
    _check_csrf()
    body = request.json or {}
    name = (body.get("capture") or "").strip()
    sels = body.get("selectors") or []
    if not name or not sels:
        return jsonify({"ok": False, "error": "capture and selectors required"}), 400
    try:
        from . import dom_analyzer as _da
        res = _da.analyze_test(name, sels)
        if not res.get("ok") and res.get("error") == "unknown capture":
            return jsonify(res), 404
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@analyzer_bp.route("/api/analyzer/pin", methods=["POST"])
def api_analyzer_pin():
    """Body: {capture, selector, role, name?, host?}. Pins a REVIEW-ONLY
    template candidate into the drafts dir (status=draft_review_required,
    never enabled). Host derives from the capture when not supplied."""
    _check_csrf()
    body = request.json or {}
    selector = (body.get("selector") or "").strip()
    role = (body.get("role") or "").strip()
    cap_name = (body.get("capture") or "").strip()
    if not selector or not role:
        return jsonify({"ok": False, "error": "selector and role required"}), 400
    try:
        from . import dom_analyzer as _da
        from .template_manager import DRAFTS_DIR
        host = (body.get("host") or "").strip()
        if not host and cap_name:
            # Prefer the FILENAME/path derivation the picker uses (host comes from
            # the path naming, never the archive) — a redacted/scrubbed capture
            # carries no url in content, so archive-content derivation is empty.
            # Fall back to the archive url only for a flat capture that has one.
            host = _da.capture_host_from_name(cap_name)
            if not host:
                p = _da.resolve_capture(cap_name)
                if p is not None:
                    host = _da.capture_host(_da.load_capture(p))
        if not host:
            return jsonify({"ok": False, "error": "host required (capture had none)"}), 400
        res = _da.pin_candidate(selector, role, host=host, drafts_dir=DRAFTS_DIR,
                                name=(body.get("name") or "target"),
                                capture_name=cap_name or None)
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(analyzer_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("analyzer."))

