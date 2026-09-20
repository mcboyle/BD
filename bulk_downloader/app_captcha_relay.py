"""v3.43.60: Flask routes for the captcha relay.

Five endpoints:

  GET   /api/captcha/pending                    All pending+solving items
  GET   /api/captcha/pending/<url>              Single item lookup
  POST  /api/captcha/start_solve                Body: {url}
                                                Triggers manual takeover for the URL
  POST  /api/captcha/resolved                   Body: {url}
                                                Marks resolved + signals worker to retry
  POST  /api/captcha/dismiss                    Body: {url}
                                                User gives up on this URL

The dashboard polls /api/captcha/pending every 5s when the captcha panel
is open. POST endpoints expect a JSON body with a `url` key.
"""
from __future__ import annotations

import sys
from urllib.parse import unquote

try:
    from flask import Blueprint, request, jsonify, Response, stream_with_context
except ImportError:  # pragma: no cover
    Blueprint = None
    request = None
    jsonify = None
    Response = None
    stream_with_context = None


captcha_bp = Blueprint("captcha_relay_api", __name__) if Blueprint else None


def _err(msg: str, status: int = 400):
    return jsonify({"ok": False, "error": msg}), status


# ─── Routes ─────────────────────────────────────────────────────────

@captcha_bp.route("/api/captcha/pending", methods=["GET"]) if captcha_bp else (lambda f: f)
def captcha_pending():
    from . import captcha_relay
    include_resolved = request.args.get("include_resolved") in ("1", "true", "yes")
    return jsonify({
        "ok": True,
        "pending": captcha_relay.list_pending(include_resolved=include_resolved),
        "challenge_types": list(captcha_relay.CHALLENGE_TYPES),
    })


@captcha_bp.route("/api/captcha/pending/<path:url>", methods=["GET"]) if captcha_bp else (lambda f: f)
def captcha_pending_one(url):
    from . import captcha_relay
    # Flask URL-decodes once; some clients double-encode.
    if "%2F" in url:
        url = unquote(url)
    p = captcha_relay.get_pending(url)
    if p is None:
        return _err("not found", 404)
    return jsonify({"ok": True, "pending": p})


def _server_side_egress_ip(url: str) -> str:
    """The egress identity a clearance is scoped by, derived on the server:
    the carrier the URL's pending site is bound to (egress_identity), never a
    value from the request body -- a caller could otherwise read, write or
    invalidate another session's cached clearance by naming its IP. Call it
    BEFORE the pending record is resolved/dismissed."""
    from . import captcha_relay
    from .egress_identity import UNKNOWN_EGRESS, egress_ip_for_site
    pending = captcha_relay.get_pending(url) or {}
    site_id = pending.get("site_id")
    if not site_id:
        return UNKNOWN_EGRESS
    return egress_ip_for_site(site_id)


@captcha_bp.route("/api/captcha/start_solve", methods=["POST"]) if captcha_bp else (lambda f: f)
def captcha_start_solve():
    import time
    from urllib.parse import urlparse
    from . import captcha_relay

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return _err("body.url required")

    domain = urlparse(url).netloc
    egress_ip = _server_side_egress_ip(url)      # never data["egress_ip"]

    try:
        from .login_impl.token_manager import get_default_cache
        cache = get_default_cache()
        if not data.get("bypass_cache"):
            cached_clearance = cache.get_clearance(egress_ip, domain)
            if cached_clearance is not None:
                session_id = f"cached-{int(time.time())}"
                captcha_relay.mark_resolved(url)
                return jsonify({
                    "ok": True,
                    "cached": True,
                    "session": {
                        "session_id": session_id,
                        "url": url,
                        "status": "resolved",
                        "clearance": cached_clearance,
                    },
                })
    except Exception:
        pass

    try:
        info = captcha_relay.start_solve(url)
    except RuntimeError as e:
        return _err(str(e), 409)
    except Exception as e:
        sys.stderr.write(f"[captcha-api] start_solve raised: {e}\n")
        return _err(f"unexpected error: {e}", 500)
    return jsonify({"ok": True, "session": info})


@captcha_bp.route("/api/captcha/resolved", methods=["POST"]) if captcha_bp else (lambda f: f)
def captcha_resolved():
    from urllib.parse import urlparse
    from . import captcha_relay

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return _err("body.url required")
    egress_ip = _server_side_egress_ip(url)      # from the pending record, before it is resolved
    ok = captcha_relay.mark_resolved(url)
    if not ok:
        return _err("url not pending", 404)

    clearance = data.get("clearance") or data.get("cookies") or data.get("token")
    if clearance:
        try:
            from .login_impl.token_manager import get_default_cache

            domain = urlparse(url).netloc
            ttl = float(data.get("ttl_seconds") or 7200.0)
            get_default_cache().set_clearance(egress_ip, domain, clearance, ttl_seconds=ttl)
        except Exception:
            pass

    return jsonify({"ok": True, "url": url})


@captcha_bp.route("/api/captcha/dismiss", methods=["POST"]) if captcha_bp else (lambda f: f)
def captcha_dismiss():
    from urllib.parse import urlparse
    from . import captcha_relay

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return _err("body.url required")
    egress_ip = _server_side_egress_ip(url)      # from the pending record, before it is dismissed
    ok = captcha_relay.mark_dismissed(url)
    if not ok:
        return _err("url not pending", 404)

    try:
        from .login_impl.token_manager import get_default_cache

        domain = urlparse(url).netloc
        get_default_cache().invalidate_clearance(egress_ip, domain)
    except Exception:
        pass

    return jsonify({"ok": True, "url": url})


# ─── MOD-1 remote takeover (A-1 screencast SSE + A-2 input) ──────────
# Under the /cockpit/api/ guarded prefix (before_request auth + CSRF apply).
# Calls captcha_relay wrappers ONLY -> the blueprint gains no new import edge.

@captcha_bp.route("/cockpit/api/takeover/<sid>/screencast", methods=["GET"]) if captcha_bp else (lambda f: f)
def takeover_screencast(sid):
    from . import captcha_relay
    gen = captcha_relay.takeover_screencast(sid)
    if gen is None:
        # Unknown sid or not in `solving`: no stream is opened (sid-binding).
        return _err("no active takeover for session", 404)
    resp = Response(stream_with_context(gen), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache, no-transform"
    resp.headers["X-Accel-Buffering"] = "no"
    # v3.66.794 (F0.4): Connection is a hop-by-hop header (PEP 3333) -- waitress
    # 500s the stream if the app sets it. Server-owned; keep-alive is default.
    return resp


@captcha_bp.route("/cockpit/api/takeover/<sid>/input", methods=["POST"]) if captcha_bp else (lambda f: f)
def takeover_input(sid):
    from . import captcha_relay
    event = request.get_json(silent=True) or {}
    result = captcha_relay.submit_takeover_input(sid, event)
    if result == "ok":
        return jsonify({"ok": True}), 200
    if result == "unknown":
        return _err("no such takeover session", 404)
    if result == "gone":
        return _err("takeover session already ended", 410)
    if result == "rate":
        return _err("input rate exceeded", 429)
    # 'invalid' | 'closed'
    return _err("input rejected", 400)


# ─── Registration ───────────────────────────────────────────────────

def register_routes(app) -> int:
    if captcha_bp is None:
        sys.stderr.write("[captcha-api] Flask not installed; routes not registered\n")
        return 0
    try:
        app.register_blueprint(captcha_bp)
    except (ValueError, AssertionError) as e:
        sys.stderr.write(f"[captcha-api] blueprint already registered: {e}\n")
        return 0
    n = sum(1 for r in app.url_map.iter_rules() if r.rule.startswith("/api/captcha"))
    sys.stderr.write(f"[captcha-api] registered {n} captcha routes\n")
    return n


__all__ = ["register_routes", "captcha_bp"]
