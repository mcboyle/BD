"""a11y API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/a11y views moved onto a Flask Blueprint.
Endpoint labels gain a "a11y." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

a11y_bp = Blueprint("a11y", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@a11y_bp.route("/api/a11y/plain_language", methods=["POST"])
def api_a11y_plain():
    body = request.json or {}
    msg = body.get("message", "")
    try:
        from . import accessibility as _a11y
        return jsonify({"plain": _a11y.plain_language(msg),
                        "original": msg})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@a11y_bp.route("/api/a11y/contrast")
def api_a11y_contrast():
    """Compute WCAG contrast ratio. Query: ?fg=#fff&bg=#000."""
    try:
        from . import accessibility as _a11y
        return jsonify(_a11y.contrast_ratio(
            request.args.get("fg", "") or "",
            request.args.get("bg", "") or "",
        ))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@a11y_bp.route("/api/a11y/audit", methods=["POST"])
def api_a11y_audit():
    """Audit an HTML snippet for common ARIA issues. Body: {html}."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import accessibility as _a11y
        return jsonify(_a11y.aria_audit(body.get("html", "")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(a11y_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("a11y."))

