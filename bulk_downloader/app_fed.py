"""fed API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/fed views moved verbatim onto a Flask
Blueprint. Endpoint labels gain a "fed." prefix; the (rule, methods,
bare-name) routing surface is byte-identical (test_route_map_invariant
diffs empty). App-level helpers are reached lazily at call time.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

fed_bp = Blueprint("fed", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@fed_bp.route("/api/fed/announce", methods=["POST"])
def api_fed_announce():
    """A peer registers / refreshes. Requires HMAC signature in
    X-BD-Fed-Sig header when fed_token is configured."""
    body = request.json or {}
    try:
        from . import federation as _fed
        try:
            from .global_config import get_config
            fed_token = (get_config() or {}).get("fed_token") or ""
        except Exception:
            fed_token = ""
        if fed_token:
            sig = request.headers.get("X-BD-Fed-Sig", "")
            if not _fed.verify_request(request.get_data(),
                                       signature=sig, token=fed_token):
                return jsonify({"ok": False, "error": "bad signature"}), 401
        ok = _fed.register_peer(
            instance_id=body.get("instance_id", ""),
            base_url=body.get("base_url", ""),
            last_history_id=int(body.get("last_history_id", 0) or 0),
            version=body.get("version", ""),
            hostname=body.get("hostname", ""),
        )
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@fed_bp.route("/api/fed/peers")
def api_fed_peers():
    try:
        from . import federation as _fed
        # C7 11.2: include per-peer trust tier (already on the row) + replication
        # drift so the operator can see which peers are behind / blocked.
        return jsonify({"peers": _fed.active_peers(),
                        "drift": _fed.peer_drift()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@fed_bp.route("/api/fed/claim_url", methods=["POST"])
def api_fed_claim():
    _check_csrf()
    body = request.json or {}
    try:
        from . import federation as _fed
        url = body.get("url", "")
        inst = body.get("instance_id", "")
        if not url or not inst:
            return jsonify({"ok": False,
                            "error": "url and instance_id required"}), 400
        ok = _fed.claim_url(url, inst,
                           ttl_seconds=int(body.get("ttl_seconds", 1800)))
        existing = _fed.is_claimed(url) if not ok else None
        return jsonify({"ok": ok, "existing_claim": existing})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@fed_bp.route("/api/fed/sync_pull")
def api_fed_sync():
    try:
        from . import federation as _fed
        since_id = int(request.args.get("since_id", 0))
        return jsonify({"rows": _fed.history_since(
            since_id, limit=int(request.args.get("limit", 500)))})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@fed_bp.route("/api/fed/status")
def api_fed_status():
    try:
        from . import federation as _fed
        return jsonify(_fed.status())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

# ── v3.43.87 Phase 167: federation manual peer registration ───────────
@fed_bp.route("/api/fed/manual_register", methods=["POST"])
def api_fed_manual_register():
    """Operator-initiated peer add. Bypasses the announce flow's HMAC
    requirement — caller is already authenticated to BD's UI.
    Body: {instance_id, base_url, version?, hostname?}."""
    _check_csrf()
    body = request.json or {}
    iid = (body.get("instance_id") or "").strip()
    burl = (body.get("base_url") or "").strip()
    if not iid or not burl:
        return jsonify({"ok": False,
                        "error": "instance_id and base_url required"}), 400
    try:
        from . import federation as _fed
        ok = _fed.register_peer(
            instance_id=iid, base_url=burl,
            last_history_id=0,
            version=body.get("version", ""),
            hostname=body.get("hostname", ""))
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@fed_bp.route("/api/fed/set_trust", methods=["POST"])
def api_fed_set_trust():
    """Operator-set a peer's trust tier (C7 11.2). Body: {instance_id, tier}
    where tier in blocked|observed|trusted. A blocked peer is refused download
    coordination. Operator-initiated (already authenticated to BD's UI)."""
    _check_csrf()
    body = request.json or {}
    iid = (body.get("instance_id") or "").strip()
    tier = (body.get("tier") or "").strip()
    if not iid or not tier:
        return jsonify({"ok": False,
                        "error": "instance_id and tier required"}), 400
    try:
        from . import federation as _fed
        if tier not in _fed.TRUST_TIERS:
            return jsonify({"ok": False,
                            "error": f"tier must be one of {list(_fed.TRUST_TIERS)}"}), 400
        ok = _fed.set_peer_trust(iid, tier)
        if not ok:
            return jsonify({"ok": False, "error": "no such peer"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


# ── C7-11.2 (v3.66.681): template federation — signed push/pull + review ──
@fed_bp.route("/api/fed/templates_available")
def api_fed_templates_available():
    """Read-only: descriptors of templates this instance can share."""
    try:
        from . import federation as _fed
        return jsonify({"templates": _fed.list_shareable_templates()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@fed_bp.route("/api/fed/template_pull")
def api_fed_template_pull():
    """Read-only: return the signed, redacted bundle for one template (by
    host). A peer calls this to pull a template from us."""
    try:
        from . import federation as _fed
        host = (request.args.get("host") or "").strip()
        if not host:
            return jsonify({"error": "host required"}), 400
        bundle = _fed.build_template_bundle(host)
        if bundle is None:
            return jsonify({"error": "no enabled template for host"}), 404
        return jsonify({"bundle": bundle})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@fed_bp.route("/api/fed/template_push", methods=["POST"])
def api_fed_template_push():
    """Peer -> us: receive a template bundle and queue it for operator review.
    Requires the HMAC signature in X-BD-Fed-Sig when fed_token is configured
    (same peer-auth as /announce)."""
    body = request.json or {}
    try:
        from . import federation as _fed
        try:
            from .global_config import get_config
            fed_token = (get_config() or {}).get("fed_token") or ""
        except Exception:
            fed_token = ""
        if fed_token:
            sig = request.headers.get("X-BD-Fed-Sig", "")
            if not _fed.verify_request(request.get_data(),
                                       signature=sig, token=fed_token):
                return jsonify({"ok": False, "error": "bad signature"}), 401
        res = _fed.receive_template(
            from_instance=body.get("instance_id", ""),
            bundle=body.get("bundle") or {})
        code = 200 if res.get("ok") else 400
        return jsonify(res), code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@fed_bp.route("/api/fed/pending_templates")
def api_fed_pending_templates():
    """Read-only: peer templates awaiting operator review."""
    try:
        from . import federation as _fed
        return jsonify({"pending": _fed.list_pending_templates()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@fed_bp.route("/api/fed/pending_review", methods=["POST"])
def api_fed_pending_review():
    """Operator: approve or reject a pending peer template. Approve writes it
    non-destructively into the template store. Body: {id, action}."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import federation as _fed
        pid = body.get("id")
        action = (body.get("action") or "").strip()
        if pid is None or action not in ("approve", "reject"):
            return jsonify({"ok": False,
                            "error": "id and action (approve|reject) required"}), 400
        # v3.66.743 -- int() inside the try meant a client-typo'd id raised
        # ValueError and the blanket except answered 500 to a malformed
        # REQUEST. The server can name this problem; naming it is a 400.
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return jsonify({"ok": False,
                            "error": "invalid id (must be an integer)"}), 400
        res = _fed.review_pending_template(pid, action)
        return jsonify(res), (200 if res.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


def register_routes(app) -> int:
    app.register_blueprint(fed_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("fed."))

