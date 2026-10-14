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


def _is_master() -> bool:
    """The request carries the master token (Bearer / X-BD-Token matching
    app._accepted_tokens) or a logged-in operator session. False when auth
    is unconfigured: there is no master token then."""
    import importlib
    a = importlib.import_module("bulk_downloader.app")
    toks = a._accepted_tokens()
    if not toks:
        return False
    auth = request.headers.get("Authorization", "")
    sent = [auth[7:].strip()] if auth.startswith("Bearer ") else []
    sent.append(request.headers.get("X-BD-Token", "").strip())
    if any(x and a._token_eq(x, t) for x in sent for t in toks):
        return True
    sess = request.cookies.get("bd_session", "")
    return bool(sess) and a._session_authenticated(sess)


def _fed_write_refusal():
    """fed-auth r1 (F2): when a fed_token is configured, a state-changing fed
    route needs the fed HMAC (X-BD-Fed-Sig over the raw body) or the master
    token. Before this, only announce/template_push checked the HMAC, so
    manual_register / set_trust / claim_url reached the same peer-registry
    writes without it. No fed_token keeps today's behaviour (auth OFF stays
    open by design; auth ON is gated by app._check_token). Returns a 401
    response, or None to proceed."""
    from . import federation as _fed
    try:
        from .global_config import get_config
        fed_token = (get_config() or {}).get("fed_token") or ""
    except Exception:
        fed_token = ""
    if not fed_token:
        return None
    # fed-replay r1: the signature binds method, path, X-BD-Fed-Ts and
    # X-BD-Fed-Nonce (no replay, no cross-route reuse). The legacy body-only
    # signature passes only with fed_legacy_sig on, only on announce /
    # template_push, and is logged; the fed-auth write routes never take it.
    body = request.get_data()
    if request.headers.get("X-BD-Fed-Ts") or request.headers.get("X-BD-Fed-Nonce"):
        ok, reason = _fed.verify_signed_request(request.method, request.path, body,
                                                request.headers.get, fed_token)
    elif request.endpoint in ("fed.api_fed_manual_register", "fed.api_fed_set_trust",
                              "fed.api_fed_claim") or not _fed.legacy_sig_enabled():
        ok, reason = False, "legacy signature refused"
    else:
        ok = _fed.verify_request(body, signature=request.headers.get("X-BD-Fed-Sig", ""),
                                 token=fed_token)
        reason = "bad signature"
        if ok:
            _fed.log_legacy_acceptance(request.path, request.remote_addr or "")
    if ok:
        return None
    if _is_master():
        return None
    return jsonify({"ok": False, "error": "bad signature", "reason": reason,
                    "required": "X-BD-Fed-Sig or master token"}), 401


def _int_field(value):
    """fed-input-lows F10/F11: a numeric field from a request -> int, or None
    when int() refuses it ("x", "1.5", 4301 digits, inf). The routes answer
    None with a 400; before this the int() ran inside the blanket except and
    a malformed request came back 500 with the exception text."""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


@fed_bp.route("/api/fed/announce", methods=["POST"])
def api_fed_announce():
    """A peer registers / refreshes. Requires HMAC signature in
    X-BD-Fed-Sig header (or the master token) when fed_token is configured."""
    body = request.json or {}
    try:
        from . import federation as _fed
        refused = _fed_write_refusal()
        if refused:
            return refused
        last_history_id = _int_field(body.get("last_history_id", 0) or 0)
        if last_history_id is None:
            return jsonify({"ok": False,
                            "error": "last_history_id must be an integer"}), 400
        ok = _fed.register_peer(
            instance_id=body.get("instance_id", ""),
            base_url=body.get("base_url", ""),
            last_history_id=last_history_id,
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
    refused = _fed_write_refusal()
    if refused:
        return refused
    body = request.json or {}
    try:
        from . import federation as _fed
        url = body.get("url", "")
        inst = body.get("instance_id", "")
        if not url or not inst:
            return jsonify({"ok": False,
                            "error": "url and instance_id required"}), 400
        ttl = _int_field(body.get("ttl_seconds", 1800))
        if ttl is None:
            return jsonify({"ok": False,
                            "error": "ttl_seconds must be an integer"}), 400
        # claim_url clamps ttl to 1 s .. federation.MAX_CLAIM_TTL_S
        ok = _fed.claim_url(url, inst, ttl_seconds=ttl)
        existing = _fed.is_claimed(url) if not ok else None
        return jsonify({"ok": ok, "existing_claim": existing})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@fed_bp.route("/api/fed/sync_pull")
def api_fed_sync():
    try:
        from . import federation as _fed
        since_id = _int_field(request.args.get("since_id", 0))
        limit = _int_field(request.args.get("limit", 500))
        if since_id is None or limit is None:
            return jsonify({"error": "since_id and limit must be integers"}), 400
        # history_since clamps limit to 1 .. federation.MAX_SYNC_PULL_ROWS
        return jsonify({"rows": _fed.history_since(since_id, limit=limit)})
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
    refused = _fed_write_refusal()
    if refused:
        return refused
    body = request.json or {}
    iid = (body.get("instance_id") or "").strip()
    burl = (body.get("base_url") or "").strip()
    if not iid or not burl:
        return jsonify({"ok": False,
                        "error": "instance_id and base_url required"}), 400
    try:
        from . import federation as _fed
        # fed-trust F3 / fed-input-lows: the operator may re-point an
        # existing peer to a new base_url; a peer's own announce may not.
        ok = _fed.register_peer(
            instance_id=iid, base_url=burl,
            last_history_id=0,
            version=body.get("version", ""),
            hostname=body.get("hostname", ""),
            operator=True)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@fed_bp.route("/api/fed/set_trust", methods=["POST"])
def api_fed_set_trust():
    """Operator-set a peer's trust tier (C7 11.2). Body: {instance_id, tier}
    where tier in blocked|observed|trusted. A blocked peer is refused download
    coordination. Operator-initiated (already authenticated to BD's UI)."""
    _check_csrf()
    refused = _fed_write_refusal()
    if refused:
        return refused
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
        refused = _fed_write_refusal()
        if refused:
            return refused
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

