"""tags API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/tags views moved verbatim onto a Flask
Blueprint. Endpoint labels gain a "tags." prefix; the (rule, methods,
bare-name) routing surface is byte-identical (test_route_map_invariant
diffs empty). App-level helpers are reached lazily at call time.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

tags_bp = Blueprint("tags", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@tags_bp.route("/api/tags/all")
def api_tags_all():
    """List all distinct tags with use counts."""
    try:
        from . import tags as _tags
        return jsonify({
            "tags": _tags.all_tags(with_counts=True,
                min_count=int(request.args.get("min_count", 1) or 1)),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@tags_bp.route("/api/tags/for/<int:hid>")
def api_tags_for(hid):
    """List tags assigned to one history row."""
    try:
        from . import tags as _tags
        return jsonify({"history_id": hid,
                        "tags": _tags.tags_for(hid)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@tags_bp.route("/api/tags/for_many", methods=["POST"])
def api_tags_for_many():
    """Batch tag lookup. Body {history_ids: [...]}. Returns
    {tags: {hid: [tags]}}. Used by the history-table render to
    populate tag pills in one round-trip."""
    _check_csrf()
    body = request.json or {}
    ids = body.get("history_ids") or []
    if not ids:
        return jsonify({"tags": {}})
    try:
        from . import tags as _tags
        result = _tags.tags_for_many(ids)
        # JSON keys are strings; the frontend reads dict by str(hid)
        return jsonify({"tags": {str(k): v for k, v in result.items()}})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@tags_bp.route("/api/tags/add", methods=["POST"])
def api_tags_add():
    """Bulk add: body {history_ids: [...], tag: '...'}."""
    _check_csrf()
    body = request.json or {}
    tag = body.get("tag", "")
    history_ids = body.get("history_ids") or []
    if not tag or not history_ids:
        return jsonify({"ok": False,
                        "error": "tag and history_ids required"}), 400
    try:
        from . import tags as _tags
        return jsonify(_tags.add_tag(history_ids, tag))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@tags_bp.route("/api/tags/remove", methods=["POST"])
def api_tags_remove():
    """Bulk remove: body {history_ids: [...], tag: '...'}."""
    _check_csrf()
    body = request.json or {}
    tag = body.get("tag", "")
    history_ids = body.get("history_ids") or []
    if not tag or not history_ids:
        return jsonify({"ok": False,
                        "error": "tag and history_ids required"}), 400
    try:
        from . import tags as _tags
        return jsonify(_tags.remove_tag(history_ids, tag))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@tags_bp.route("/api/tags/rows/<tag>")
def api_tags_rows(tag):
    """Find all rows tagged X. ?site_id=Y to filter; ?limit=N."""
    try:
        from . import tags as _tags
        return jsonify({"rows": _tags.rows_with_tag(
            tag,
            limit=int(request.args.get("limit", 100) or 100),
            site_id=request.args.get("site_id") or None,
        )})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@tags_bp.route("/api/tags/rename", methods=["POST"])
def api_tags_rename():
    """Bulk rename: body {old, new}. Merges if new already exists
    on some rows."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import tags as _tags
        return jsonify(_tags.rename_tag(body.get("old", ""),
                                        body.get("new", "")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@tags_bp.route("/api/tags/delete", methods=["POST"])
def api_tags_delete():
    """Remove tag from every row. Body {tag}."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import tags as _tags
        return jsonify(_tags.untag_all(body.get("tag", "")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@tags_bp.route("/api/tags/suggest/<int:hid>")
def api_tags_suggest(hid):
    """Run tag_inference on a row's filename — returns suggested tags
    not already applied."""
    try:
        from . import tags as _tags
        return jsonify({"history_id": hid,
                        "suggested": _tags.suggest_tags(hid)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(tags_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("tags."))

