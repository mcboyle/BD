"""Outgoing webhooks API -- extracted from app.py at v3.66.405 (Phase 4 cut 1).

Pure code MOTION: the 5 ``/api/webhooks`` views moved verbatim into a blueprint.
Endpoint *labels* gain a ``webhooks.`` prefix (Flask blueprint convention); the
(rule, methods, bare-name) routing surface is byte-identical, so
``test_route_map_invariant`` diffs empty. ``_check_csrf`` is reached lazily from
``bulk_downloader.app`` at call time (the established ``import_module`` convention
used by ``app_widgets_api``) to avoid an import cycle and to keep the single
CSRF implementation in app.py.
"""
from __future__ import annotations

from flask import Blueprint, request, jsonify

webhooks_bp = Blueprint("webhooks", __name__)


def _check_csrf():
    """Delegate to app._check_csrf at call time (avoids an import cycle)."""
    import importlib
    return importlib.import_module("bulk_downloader.app")._check_csrf()


@webhooks_bp.route("/api/webhooks", methods=["GET"])
def api_webhooks_list():
    try:
        from . import webhooks as _wh
        return jsonify({"subscriptions": _wh.list_subscriptions()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@webhooks_bp.route("/api/webhooks", methods=["POST"])
def api_webhooks_add():
    _check_csrf()
    body = request.json or {}
    try:
        from . import webhooks as _wh
        sid = _wh.add_subscription(
            url=body.get("url", ""),
            events=body.get("events", []) or [],
            secret=body.get("secret", "") or "",
        )
        if sid is None:
            return jsonify({"ok": False,
                            "error": "url and a non-empty events list are required, and the "
                                     "url must be a valid http(s) endpoint that is not an "
                                     "internal SSRF target (cloud-metadata/link-local/CGNAT/"
                                     "multicast/unspecified)"}), 400
        return jsonify({"ok": True, "id": sid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@webhooks_bp.route("/api/webhooks/<int:wid>", methods=["DELETE"])
def api_webhooks_remove(wid):
    _check_csrf()
    try:
        from . import webhooks as _wh
        return jsonify({"ok": _wh.remove_subscription(wid)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@webhooks_bp.route("/api/webhooks/stats")
def api_webhooks_stats():
    try:
        from . import webhooks as _wh
        return jsonify(_wh.stats())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@webhooks_bp.route("/api/webhooks/drain", methods=["POST"])
def api_webhooks_drain():
    """Force a drain pass (mainly for tests)."""
    _check_csrf()
    try:
        from . import webhooks as _wh
        return jsonify(_wh._drain_once())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


def register_routes(app) -> int:
    app.register_blueprint(webhooks_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("webhooks."))
