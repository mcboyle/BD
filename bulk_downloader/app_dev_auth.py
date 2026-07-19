"""app_dev.auth -- 12 @dev_bp route handlers, sub-sliced from app_dev.py (Tier M, pure motion).

Handlers attach to the SHARED dev_bp (imported from .app_dev); the routing surface
(rule, methods, bare-name) is byte-identical -- test_route_map_invariant diffs EMPTY.
"""
from __future__ import annotations
from flask import Blueprint, jsonify, request
from .app_dev import (
    _app_app,
    _app_runners,
    _dev_mode_guard,
    dev_bp,
)


@dev_bp.route("/api/dev/auth_map")
def api_dev_auth_map():
    """Every route and the auth controls its view applies."""
    app = _app_app()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.auth_surface(app))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/csrf_check")
def api_dev_csrf_check():
    """Confirm every state-changing /api route enforces CSRF."""
    app = _app_app()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.csrf_coverage(app))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/cookie_jar")
def api_dev_cookie_jar():
    """U17/D-21 — per-cookie structure of each site's jar (read-only).
    Optional ?site=<id>. Cookie values are never emitted."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        site = request.args.get("site") or None
        return jsonify(_ds.cookie_jar_inspect(runners=runners,
                                              site_id=site))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/cookie_age")
def api_dev_cookie_age():
    """U17/D-22 — cookie expiry / age report (read-only).
    Optional ?site=<id>."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        site = request.args.get("site") or None
        return jsonify(_ds.cookie_age_report(runners=runners,
                                             site_id=site))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/auth_cookie_test")
def api_dev_auth_cookie_test():
    """U17/D-29 — run login._looks_authenticated on each site's jar
    (read-only). Optional ?site=<id>."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        site = request.args.get("site") or None
        return jsonify(_ds.auth_cookie_test(runners=runners,
                                            site_id=site))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/login_template_dryrun")
def api_dev_login_template_dryrun():
    """U18/D-24 — dry-run applying a login template to a site, without
    writing anything (read-only). Params: ?template=<id>&site=<id>."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        tpl = request.args.get("template") or ""
        site = request.args.get("site") or ""
        return jsonify(_ds.login_template_dry_run(
            tpl, site_id=site, runners=runners))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/credential_resolver")
def api_dev_credential_resolver():
    """U18/D-26 — per-site credential-reference resolution report
    (read-only; no secret decrypted). Optional ?site=<id>."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        site = request.args.get("site") or None
        return jsonify(_ds.credential_resolver(runners=runners,
                                               site_id=site))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/account_pool")
def api_dev_account_pool():
    """T5/D-17 — per-site account pool: count, active index, per-
    account cooldown. Credentials never emitted (read-only)."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.account_pool_inspect(runners=runners))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/csrf_token")
def api_dev_csrf_token():
    """T6/D-30 — inspect the CSRF token mechanism: HMAC double-submit
    scheme, key state, header/cookie names. If the caller has a
    session cookie, the derived token is included (read-only)."""
    app = _app_app()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        # the session cookie comes from the caller's own request — not
        # a query param — so the tool can never be used to derive a
        # token for someone else's session
        sess = request.cookies.get("bd_session") or None
        return jsonify(_ds.csrf_token_inspect(app, session_cookie=sess))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/login_flows")
def api_dev_login_flows():
    """T45/D-27 — read-only list of saved login flows. Optional
    ?site_id= filter."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        sid = request.args.get("site_id") or None
        return jsonify(_ds.login_flows_status(site_id=sid))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/login_flow_save", methods=["POST"])
def api_dev_login_flow_save():
    """T45/D-27 mutating — save a captured login-flow action sequence.
    Body: {site_id, name, actions: [...], description?}. CSRF-gated.

    The actual browser-side capture happens via the existing teach-
    mode overlay (learn.py TEACH_OVERLAY_JS); this route is the
    server-side save step."""
    guard = _dev_mode_guard()
    if guard: return guard
    body = request.json or {}
    site_id = body.get("site_id", "")
    name = body.get("name", "")
    actions = body.get("actions") or []
    description = body.get("description", "")
    if not isinstance(actions, list):
        return jsonify({"ok": False,
                        "error": "actions must be a list"}), 400
    try:
        from . import login_flow_recorder as _lfr
        return jsonify(_lfr.record_login_flow(
            site_id, name, actions, description=description))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/login_flow_delete", methods=["POST"])
def api_dev_login_flow_delete():
    """T45/D-27 mutating — delete a saved login flow.
    Body: {site_id, name}. CSRF-gated. Refuses to delete macros that
    don't carry the login_flow tag (defense against accidental delete
    of an unrelated macro that shares a name)."""
    guard = _dev_mode_guard()
    if guard: return guard
    body = request.json or {}
    site_id = body.get("site_id", "")
    name = body.get("name", "")
    try:
        from . import login_flow_recorder as _lfr
        return jsonify(_lfr.delete_login_flow(site_id, name))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
