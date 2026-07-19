"""RED-first contract for C7 11.1b -- user-admin routes + wiring.

11.1a (625/626) shipped the 5 base auth routes (login/logout/whoami/users
create+list) + the typed auth.ts client. 11.1b adds the three ADMIN operations
that had engines in user_accounts (set_role / set_password / delete_user) but no
routes, plus the SPA components/wiring that consume them.

The routes are SENSITIVE + mutating (role / password / delete match the
gui_parity _SENSITIVE gate), so per the 626 parity footgun they must be real and
SPA-wired in the SAME cut (the operator_facing_unwired==0 invariant is enforced
by test_parity_method_aware; this file locks the backend surface).

Routes added:
  POST   /api/auth/users/<username>/role      {role}         -> set_role  (admin)
  POST   /api/auth/users/<username>/password  {password}     -> set_pw    (admin)
  DELETE /api/auth/users/<username>                          -> delete    (admin)

Pre-fix: register_routes reports 5 and the admin routes 404 -> RED.

Sandbox-runner conventions: zero-arg, tempfile.mkdtemp, chdir + restore in
try/finally (stores are cwd-relative), no monkeypatch.
"""
from __future__ import annotations

import os
import tempfile


def _iso_app():
    from flask import Flask
    from bulk_downloader import app_auth as M
    app = Flask(__name__)
    n = M.register_routes(app)
    return app, n


def test_register_routes_reports_eight():
    app, n = _iso_app()
    assert n == 8, f"expected 8 auth routes after 11.1b, got {n}"


def test_admin_routes_registered_with_expected_methods():
    app, _ = _iso_app()
    seen = {}
    for r in app.url_map.iter_rules():
        if r.endpoint.startswith("auth."):
            seen.setdefault(r.rule, set()).update(
                m for m in r.methods if m not in ("HEAD", "OPTIONS"))
    assert seen.get("/api/auth/users/<username>/role") == {"POST"}
    assert seen.get("/api/auth/users/<username>/password") == {"POST"}
    assert seen.get("/api/auth/users/<username>") == {"DELETE"}


def _bootstrap_admin(c):
    r = c.post("/api/auth/users",
               json={"username": "admin1", "password": "pw123456", "role": "admin"})
    assert r.status_code == 200
    c.post("/api/auth/login", json={"username": "admin1", "password": "pw123456"})


def test_set_role_admin_gated_and_effective():
    app, _ = _iso_app()
    cwd = os.getcwd()
    os.chdir(tempfile.mkdtemp(prefix="bd111b_"))
    try:
        c = app.test_client()
        _bootstrap_admin(c)
        # create a second (operator) user
        c.post("/api/auth/users",
               json={"username": "bob", "password": "pw123456", "role": "operator"})
        # promote bob -> admin
        r = c.post("/api/auth/users/bob/role", json={"role": "admin"})
        assert r.status_code == 200 and r.get_json()["ok"] is True
        # verify via list
        users = {u["username"]: u for u in c.get("/api/auth/users").get_json()["users"]}
        assert users["bob"]["role"] == "admin"
        # unauth client (no cookie) is 403
        c2 = app.test_client()
        assert c2.post("/api/auth/users/bob/role",
                       json={"role": "operator"}).status_code == 403
    finally:
        os.chdir(cwd)


def test_set_password_admin_gated_and_effective():
    app, _ = _iso_app()
    cwd = os.getcwd()
    os.chdir(tempfile.mkdtemp(prefix="bd111b_"))
    try:
        c = app.test_client()
        _bootstrap_admin(c)
        c.post("/api/auth/users",
               json={"username": "carol", "password": "oldpw123", "role": "operator"})
        r = c.post("/api/auth/users/carol/password", json={"password": "newpw456"})
        assert r.status_code == 200 and r.get_json()["ok"] is True
        # carol can log in with the new password on a fresh client
        c3 = app.test_client()
        assert c3.post("/api/auth/login",
                       json={"username": "carol", "password": "newpw456"}).status_code == 200
        # empty password rejected
        assert c.post("/api/auth/users/carol/password",
                      json={"password": ""}).status_code == 400
    finally:
        os.chdir(cwd)


def test_delete_user_admin_gated_and_effective():
    app, _ = _iso_app()
    cwd = os.getcwd()
    os.chdir(tempfile.mkdtemp(prefix="bd111b_"))
    try:
        c = app.test_client()
        _bootstrap_admin(c)
        c.post("/api/auth/users",
               json={"username": "dave", "password": "pw123456", "role": "operator"})
        r = c.delete("/api/auth/users/dave")
        assert r.status_code == 200 and r.get_json()["ok"] is True
        users = {u["username"] for u in c.get("/api/auth/users").get_json()["users"]}
        assert "dave" not in users
        # deleting a nonexistent user -> 404
        assert c.delete("/api/auth/users/ghost").status_code == 404
        # unauth client 403
        c2 = app.test_client()
        assert c2.delete("/api/auth/users/admin1").status_code == 403
    finally:
        os.chdir(cwd)
