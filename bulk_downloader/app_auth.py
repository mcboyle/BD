"""auth / identity API -- Cut 625 / C7 sub-wave 11.1a. A thin Flask blueprint
over the ``user_accounts`` engine (multi-user identity + roles). Distinct from
``app_accounts`` (which serves site-credential health under /api/accounts/*) --
this is USER identity, under /api/auth/*, on its own ``auth`` blueprint.

Issues a parallel, signed ``bd_user`` session cookie; it does NOT touch the
existing ``bd_session`` pairing cookie or app.py's auth hot path, so no existing
auth behaviour changes.

Routes:
  POST /api/auth/login    -- {username, password} -> set bd_user cookie, {ok, user}
  POST /api/auth/logout   -- clear bd_user cookie
  GET  /api/auth/whoami   -- current user from bd_user cookie (or null) + multi_user flag
  POST /api/auth/users    -- create a user (bootstrap first account when empty, else admin-gated)
  GET  /api/auth/users    -- list users (admin-gated)
  POST /api/auth/users/<username>/role      -- {role} set a user's role (admin, 11.1b)
  POST /api/auth/users/<username>/password  -- {password} reset a user's password (admin, 11.1b)
  DELETE /api/auth/users/<username>         -- delete a user (admin, 11.1b)

CSRF: /api/auth/login is the pre-session entry point and is naturally exempt
(app._check_csrf skips when no bd_session cookie is present); the app's global
Origin check already refuses cross-origin state-changing /api/ POSTs, so the
management routes are protected cross-origin, with the admin-role check as the
authorization gate.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

auth_bp = Blueprint("auth", __name__)

_COOKIE = "bd_user"


def _caller(base_dir=None):
    """Resolve the calling user's {username, role} from the bd_user cookie."""
    from . import user_accounts as _ua
    return _ua.current_user_from_cookie(request.cookies.get(_COOKIE, ""), base_dir)


@auth_bp.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    from . import user_accounts as _ua
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or not password:
        return jsonify({"ok": False, "error": "missing username or password"}), 400
    if not _ua.verify_password(username, password):
        return jsonify({"ok": False, "error": "invalid credentials"}), 401
    token = _ua.issue_session(username)
    resp = jsonify({"ok": True, "user": _ua.get_user(username)})
    resp.set_cookie(_COOKIE, token, httponly=True, samesite="Lax", max_age=12 * 3600)
    return resp


@auth_bp.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    resp = jsonify({"ok": True})
    resp.delete_cookie(_COOKIE)
    return resp


@auth_bp.route("/api/auth/whoami", methods=["GET"])
def api_auth_whoami():
    from . import user_accounts as _ua
    return jsonify({"ok": True, "user": _caller(),
                    "multi_user": _ua.multi_user_enabled()})


@auth_bp.route("/api/auth/users", methods=["POST"])
def api_auth_users_create():
    from . import user_accounts as _ua
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    role = body.get("role") or "operator"
    # bootstrap: the very first account can be created without auth (there is no
    # admin yet); after that, only an admin may create users.
    if _ua.count() > 0:
        who = _caller()
        if not (who and who.get("role") == "admin"):
            return jsonify({"ok": False, "error": "admin role required"}), 403
    ok, msg = _ua.create_user(username, password, role=role)
    return (jsonify({"ok": True, "user": _ua.get_user(username)}), 200) if ok \
        else (jsonify({"ok": False, "error": msg}), 400)


@auth_bp.route("/api/auth/users", methods=["GET"])
def api_auth_users_list():
    from . import user_accounts as _ua
    who = _caller()
    if not (who and who.get("role") == "admin"):
        return jsonify({"ok": False, "error": "admin role required"}), 403
    return jsonify({"ok": True, "users": _ua.list_users()})


def _require_admin():
    """Return None if the caller is an admin, else a (json, status) 403 tuple."""
    who = _caller()
    if not (who and who.get("role") == "admin"):
        return jsonify({"ok": False, "error": "admin role required"}), 403
    return None


@auth_bp.route("/api/auth/users/<username>/role", methods=["POST"])
def api_auth_user_set_role(username):
    # C7 11.1b: admin-only role change. Sensitive + mutating -> SPA-wired.
    from . import user_accounts as _ua
    denied = _require_admin()
    if denied is not None:
        return denied
    body = request.get_json(silent=True) or {}
    role = (body.get("role") or "").strip()
    ok, msg = _ua.set_role(username, role)
    if ok:
        return jsonify({"ok": True, "user": _ua.get_user(username)})
    # "no such user" -> 404; validation (bad role / empty) -> 400
    status = 404 if msg == "no such user" else 400
    return jsonify({"ok": False, "error": msg}), status


@auth_bp.route("/api/auth/users/<username>/password", methods=["POST"])
def api_auth_user_set_password(username):
    # C7 11.1b: admin-only password reset for another user.
    from . import user_accounts as _ua
    denied = _require_admin()
    if denied is not None:
        return denied
    body = request.get_json(silent=True) or {}
    new_password = body.get("password") or ""
    ok, msg = _ua.set_password(username, new_password)
    if ok:
        return jsonify({"ok": True})
    status = 404 if msg == "no such user" else 400
    return jsonify({"ok": False, "error": msg}), status


@auth_bp.route("/api/auth/users/<username>", methods=["DELETE"])
def api_auth_user_delete(username):
    # C7 11.1b: admin-only user deletion.
    from . import user_accounts as _ua
    denied = _require_admin()
    if denied is not None:
        return denied
    if _ua.delete_user(username):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "no such user"}), 404


def register_routes(app) -> int:
    app.register_blueprint(auth_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("auth."))
