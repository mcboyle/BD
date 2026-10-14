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
# /api/auth/login back-off (auth_throttle, off unless BD_AUTH_THROTTLE is on).
# Every attempt is counted under two labels, keyed on the casefolded username
# whether or not it exists (same path and same PBKDF2 cost, so no username oracle):
#   pair  (username, client address): full escalating back-off. A success clears
#         only this label, so one account's login never resets another account's
#         counter, and a failure flood from address X never refuses the user on Y.
#   user  (username, every address): a soft ceiling for guesses spread over many
#         addresses; its cooldown is capped at BD_AUTH_THROTTLE_BASE seconds, so it
#         slows the real user but never locks them out. Only the failures of the
#         last BD_AUTH_THROTTLE_MAX seconds count, and a success clears it.
# An attempt is counted on both labels BEFORE the PBKDF2 verify, in the same lock
# hold as the check (auth_throttle.reserve), and each label has one verify in flight
# at most: a burst of concurrent guesses gets no more 401s than the same guesses
# sent one by one. A success then drops both labels; a failure or an exception in
# the verify (or in reserve) leaves the attempt counted. reserve is inside the try
# and the release in its finally frees only this request's own attempt (owner): no
# label outlives its request, and a refused request never frees another's.
def _login_labels(username):
    who = username.casefold()
    return (("user_login", "pair", who, request.remote_addr or ""),
            ("user_login", "user", who))


def _too_many(retry):
    resp = jsonify({"ok": False,
                    "error": f"too many attempts; try again in {retry:.0f}s"})
    resp.headers["Retry-After"] = str(int(retry) + 1)
    return resp, 429


def _json_object():
    """The request's JSON body if it is an object, else None (400)."""
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


def _bad(error):
    return jsonify({"ok": False, "error": error}), 400


def _str_field(body, key, default=""):
    """``body[key]`` if absent/None (-> default) or a string, else None."""
    v = body.get(key)
    if v is None:
        return default
    return v if isinstance(v, str) else None


def _store_unreadable():
    from . import user_accounts as _ua
    return jsonify({"ok": False, "error": _ua._UNREADABLE}), 503


def _caller(base_dir=None):
    """Resolve the calling user's {username, role} from the bd_user cookie."""
    from . import user_accounts as _ua
    return _ua.current_user_from_cookie(request.cookies.get(_COOKIE, ""), base_dir)


@auth_bp.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    from . import user_accounts as _ua
    from . import auth_throttle as _at
    body = _json_object()
    if body is None:
        return _bad("body must be a JSON object")
    username, password = _str_field(body, "username"), _str_field(body, "password")
    if username is None or password is None:
        return _bad("username and password must be strings")
    username = username.strip()
    if not username or not password:
        return jsonify({"ok": False, "error": "missing username or password"}), 400
    # Checked and counted before the PBKDF2 verify: a throttled guess costs no hash.
    labels = _login_labels(username)
    per_user = labels[1]
    me = object()  # this request's mark on the labels it holds
    ok = False
    try:
        allowed, retry = _at.reserve(labels, soft=(per_user,), owner=me)
        if not allowed:
            return _too_many(retry)
        ok = _ua.verify_password(username, password)
    finally:
        _at.release(labels, success=ok, owner=me)
    if not ok:
        return jsonify({"ok": False, "error": "invalid credentials"}), 401
    token = _ua.issue_session(username)
    resp = jsonify({"ok": True, "user": _ua.get_user(username)})
    resp.set_cookie(_COOKIE, token, httponly=True, samesite="Lax", max_age=12 * 3600)
    return resp


@auth_bp.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    # Revoke server-side, not just in this browser: a copy of the old cookie
    # must stop working. Per user (a new session stamp), so every device of
    # this user is logged out; per-token revocation would need a denylist.
    from . import user_accounts as _ua
    who = _caller()
    if who and not _ua.revoke_sessions(who["username"]):
        resp = jsonify({"ok": False, "error": "could not revoke the session"})
        resp.delete_cookie(_COOKIE)
        return resp, 500
    resp = jsonify({"ok": True})
    resp.delete_cookie(_COOKIE)
    return resp


@auth_bp.route("/api/auth/whoami", methods=["GET"])
def api_auth_whoami():
    from . import user_accounts as _ua
    # bootstrap: no account exists yet, so the SPA offers "create the first admin".
    # Keyed on the account count, never on the multi_user flag (svam-fix F2).
    try:
        bootstrap = _ua.count() == 0
    except _ua.AccountsStoreError:
        return _store_unreadable()
    return jsonify({"ok": True, "user": _caller(),
                    "multi_user": _ua.multi_user_enabled(),
                    "bootstrap": bootstrap})


@auth_bp.route("/api/auth/users", methods=["POST"])
def api_auth_users_create():
    from . import user_accounts as _ua
    body = _json_object()
    if body is None:
        return _bad("body must be a JSON object")
    username, password = _str_field(body, "username"), _str_field(body, "password")
    role = _str_field(body, "role", "operator")
    if username is None or password is None or role is None:
        return _bad("username, password and role must be strings")
    username, role = username.strip(), role or "operator"
    # bootstrap: the very first account can be created without auth (there is no
    # admin yet); after that, only an admin may create users.
    try:
        bootstrap = _ua.count() == 0
    except _ua.AccountsStoreError:
        return _store_unreadable()
    if not bootstrap:
        who = _caller()
        if not (who and who.get("role") == "admin"):
            return jsonify({"ok": False, "error": "admin role required"}), 403
        ok, msg = _ua.create_user(username, password, role=role)
    else:
        # The bootstrap account IS the initial admin: any other role would leave no
        # admin to create the rest (svam-fix F1). only_if_empty re-checks the
        # count under the store lock: a concurrent bootstrap that won is a 403.
        ok, msg = _ua.create_user(username, password, role="admin", only_if_empty=True)
        if msg == _ua.NOT_EMPTY:
            return jsonify({"ok": False, "error": "admin role required"}), 403
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
    body = _json_object()
    if body is None:
        return _bad("body must be a JSON object")
    role = _str_field(body, "role")
    if role is None:
        return _bad("role must be a string")
    ok, msg = _ua.set_role(username, role.strip(), keep_last_admin=True)
    if ok:
        return jsonify({"ok": True, "user": _ua.get_user(username)})
    # "no such user" -> 404; last admin -> 409; validation (bad role / empty) -> 400
    status = {"no such user": 404, _ua.LAST_ADMIN: 409}.get(msg, 400)
    return jsonify({"ok": False, "error": msg}), status


@auth_bp.route("/api/auth/users/<username>/password", methods=["POST"])
def api_auth_user_set_password(username):
    # C7 11.1b: admin-only password reset for another user.
    from . import user_accounts as _ua
    denied = _require_admin()
    if denied is not None:
        return denied
    body = _json_object()
    if body is None:
        return _bad("body must be a JSON object")
    new_password = _str_field(body, "password")
    if new_password is None:
        return _bad("password must be a string")
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
    ok, msg = _ua.delete_user_checked(username, keep_last_admin=True)
    if ok:
        return jsonify({"ok": True})
    status = {"no such user": 404, _ua.LAST_ADMIN: 409}.get(msg, 400)
    return jsonify({"ok": False, "error": msg}), status


def register_routes(app) -> int:
    app.register_blueprint(auth_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("auth."))
