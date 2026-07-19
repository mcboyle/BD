"""v3.66.681 (B2/P6): OIDC / SSO login blueprint.

Routes (all under the existing auth namespace family /api/auth/oidc/*):
  GET /api/auth/oidc/status   -- {enabled} so the SPA can show the SSO button
  GET /api/auth/oidc/login    -- 302 to the provider (state+nonce in session)
  GET /api/auth/oidc/callback -- handle the redirect: exchange code, verify the
                                 id_token, provision the user, set bd_user cookie

The login/callback endpoints are OAuth redirect GETs (not JSON POSTs), so CSRF
does not apply; state is bound to the Flask session to defend against forgery.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request, redirect, session

oidc_bp = Blueprint("oidc", __name__)

_COOKIE = "bd_user"


@oidc_bp.route("/api/auth/oidc/status")
def api_oidc_status():
    """Read-only: whether SSO is configured + enabled."""
    try:
        from . import oidc as _o
        return jsonify({"enabled": _o.is_enabled()})
    except Exception as e:
        return jsonify({"enabled": False, "error": str(e)[:200]}), 500


@oidc_bp.route("/api/auth/oidc/login")
def api_oidc_login():
    """Begin the OIDC flow: stash state+nonce in the session, 302 to provider."""
    from . import oidc as _o
    if not _o.is_enabled():
        return jsonify({"ok": False, "error": "OIDC not configured"}), 400
    state = _o.new_state()
    nonce = _o.new_state()
    session["oidc_state"] = state
    session["oidc_nonce"] = nonce
    try:
        url = _o.build_authorize_url(state=state, nonce=nonce)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 502
    return redirect(url, code=302)


@oidc_bp.route("/api/auth/oidc/callback")
def api_oidc_callback():
    """Handle the provider redirect: verify state, exchange code, verify the
    id_token, provision the user, and issue the bd_user session cookie."""
    from . import oidc as _o
    from . import user_accounts as _ua
    if request.args.get("error"):
        return redirect("/?sso_error=" + request.args.get("error", "error"), code=302)
    code = request.args.get("code", "")
    state = request.args.get("state", "")
    if not code or not state or state != session.get("oidc_state"):
        return jsonify({"ok": False, "error": "invalid state"}), 400
    nonce = session.get("oidc_nonce")
    try:
        tokens = _o.exchange_code(code)
        claims = _o.verify_id_token(tokens.get("id_token", ""), nonce=nonce)
        username = _o.provision_user(claims)
    except Exception:
        return redirect("/?sso_error=exchange", code=302)
    token = _ua.issue_session(username)
    resp = redirect("/", code=302)
    resp.set_cookie(_COOKIE, token, httponly=True, samesite="Lax", max_age=12 * 3600)
    session.pop("oidc_state", None)
    session.pop("oidc_nonce", None)
    return resp


def register_routes(app) -> int:
    app.register_blueprint(oidc_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("oidc."))
