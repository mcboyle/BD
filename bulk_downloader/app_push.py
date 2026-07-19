"""push API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/push views moved verbatim onto a Flask
Blueprint. Endpoint labels gain a "push." prefix; the (rule, methods,
bare-name) routing surface is byte-identical (test_route_map_invariant
diffs empty). App-level helpers are reached lazily at call time.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

push_bp = Blueprint("push", __name__)


@push_bp.route("/api/push/info")
def api_push_info():
    """Returns the server's VAPID public key + whether push is available
    (i.e. cryptography + pywebpush installed). UI hides the subscribe
    button if available=False."""
    try:
        from . import push as _push
        return jsonify({"available": _push.is_available(),
                        "public_key": _push.vapid_public_key()})
    except Exception as e:
        return jsonify({"available": False, "public_key": "",
                        "error": str(e)[:120]})

@push_bp.route("/api/push/subscribe",methods=["POST"])
def api_push_subscribe():
    """Register a PushSubscription from the browser. Body is the
    .toJSON() form: {endpoint, keys:{p256dh, auth}}."""
    from . import push as _push
    sub = request.json or {}
    ua = request.headers.get("User-Agent", "")[:200]
    ok = _push.add_subscription(sub, ua)
    return jsonify({"ok": ok})

@push_bp.route("/api/push/unsubscribe",methods=["POST"])
def api_push_unsubscribe():
    from . import push as _push
    endpoint = (request.json or {}).get("endpoint")
    ok = _push.remove_subscription(endpoint)
    return jsonify({"ok": ok})

@push_bp.route("/api/push/test",methods=["POST"])
def api_push_test():
    """Send a test push to all subscribers. Useful for verifying setup."""
    from . import push as _push
    result = _push.send_push("Bulk Downloader",
                             "Test notification — push is working ✓",
                             url="/", tag="test", throttle_seconds=0)
    return jsonify({"ok": result.get("sent", 0) > 0, **result})

@push_bp.route("/api/push/subscriptions")
def api_push_list():
    from . import push as _push
    return jsonify({"subscriptions": _push.list_subscriptions()})

def register_routes(app) -> int:
    app.register_blueprint(push_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("push."))

