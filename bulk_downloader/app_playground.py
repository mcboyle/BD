"""playground API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/playground views moved onto a Flask Blueprint.
Endpoint labels gain a "playground." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

playground_bp = Blueprint("playground", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@playground_bp.route("/api/playground/test", methods=["POST"])
def api_playground_test():
    """Body: {url, selectors: [...], cookies: {...}, use_playwright: bool}
    Returns {fetch_ok, status, selectors: [{selector, count, sample, ...}]}.
    Operator scratchpad — nothing persisted."""
    _check_csrf()
    body = request.json or {}
    url = (body.get("url") or "").strip()
    sels = body.get("selectors") or []
    if not url or not sels:
        return jsonify({"error": "url and selectors required"}), 400
    try:
        from . import selector_playground as _sp
        return jsonify(_sp.playground(
            url, sels,
            cookies=body.get("cookies"),
            use_playwright=bool(body.get("use_playwright", False)),
        ))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@playground_bp.route("/api/playground/status")
def api_playground_status():
    try:
        from . import selector_playground as _sp
        return jsonify(_sp.is_available())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(playground_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("playground."))

