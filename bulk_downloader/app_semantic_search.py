"""semantic-search API -- Cut 624 / C2. A thin Flask blueprint over the
``semantic_search`` engine (embeddings + brute-force vector index over the
Phase-1 capture metadata index and the template corpus). Mirrors the
``app_backup`` blueprint pattern; endpoint labels gain a ``semantic.`` prefix.

Routes:
  GET  /api/semantic/status   -- enabled flag, indexed count, dims, sqlite-vec
  POST /api/semantic/search   -- {query, k?} -> best-matching prior captures/templates
  POST /api/semantic/reindex  -- rebuild the index from the live corpus
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

semantic_bp = Blueprint("semantic", __name__)


def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@semantic_bp.route("/api/semantic/status", methods=["GET"])
def api_semantic_status():
    try:
        from . import semantic_search as _ss
        return jsonify({"ok": True, **_ss.status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@semantic_bp.route("/api/semantic/search", methods=["POST"])
def api_semantic_search():
    _check_csrf()
    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"ok": False, "error": "missing 'query'"}), 400
    try:
        k = int(body.get("k", 10) or 10)
    except (TypeError, ValueError):
        k = 10
    k = max(1, min(50, k))
    try:
        from . import semantic_search as _ss
        return jsonify(_ss.search(query, k=k))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@semantic_bp.route("/api/semantic/reindex", methods=["POST"])
def api_semantic_reindex():
    _check_csrf()
    try:
        from . import semantic_search as _ss
        return jsonify(_ss.reindex())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


def register_routes(app) -> int:
    app.register_blueprint(semantic_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("semantic."))
