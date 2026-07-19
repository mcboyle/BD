"""Route + promote-gate contract tests for Cut 625 / C7 11.1a.

Locks the auth blueprint surface + the login flow, and the reviewer-role promote
gate (default-no-op when multi-user is off). The identity engine itself is covered
by test_user_accounts; these keep the wiring honest. The routes + gate did not
exist on pristine 624, so these are the RED->GREEN contract for the wiring.

Sandbox-runner conventions: zero-arg, tempfile.mkdtemp, no monkeypatch; chdir into
a tmp cwd (the stores + config are cwd-relative) and restore in try/finally.
"""
from __future__ import annotations

import json
import os
import tempfile


# ── blueprint surface (isolated app; no full boot / CSRF spine) ──────────

def _iso_app():
    from flask import Flask
    from bulk_downloader import app_auth as M
    app = Flask(__name__)
    n = M.register_routes(app)
    return app, n


def test_register_routes_reports_five():
    # 11.1b added 3 admin routes (set-role / set-password / delete) -> 8 total.
    app, n = _iso_app()
    assert n == 8


def test_routes_registered_with_expected_methods():
    app, _ = _iso_app()
    seen = {}
    for r in app.url_map.iter_rules():
        if r.endpoint.startswith("auth."):
            seen.setdefault(r.rule, set()).update(
                m for m in r.methods if m not in ("HEAD", "OPTIONS"))
    assert seen.get("/api/auth/login") == {"POST"}
    assert seen.get("/api/auth/logout") == {"POST"}
    assert seen.get("/api/auth/whoami") == {"GET"}
    assert seen.get("/api/auth/users") == {"GET", "POST"}


def test_login_flow_bootstrap_then_whoami():
    app, _ = _iso_app()
    cwd = os.getcwd()
    os.chdir(tempfile.mkdtemp(prefix="bdc7auth_"))
    try:
        c = app.test_client()
        # bootstrap (count==0) creates the first admin without prior auth
        r1 = c.post("/api/auth/users",
                    json={"username": "matt", "password": "pw12345", "role": "admin"})
        assert r1.status_code == 200 and r1.get_json()["user"]["role"] == "admin"
        # login sets the bd_user cookie
        r2 = c.post("/api/auth/login", json={"username": "matt", "password": "pw12345"})
        assert r2.status_code == 200
        assert "bd_user" in r2.headers.get("Set-Cookie", "")
        # whoami resolves the cookie
        who = c.get("/api/auth/whoami").get_json()
        assert who["user"]["username"] == "matt" and who["user"]["role"] == "admin"
        # wrong password -> 401
        assert c.post("/api/auth/login",
                      json={"username": "matt", "password": "nope"}).status_code == 401
        # a second create now requires admin -> without the cookie a fresh client 403s
        c2 = app.test_client()
        assert c2.post("/api/auth/users",
                       json={"username": "x", "password": "pw12345"}).status_code == 403
    finally:
        os.chdir(cwd)


# ── promote gate (full app; default-no-op vs enforced) ──────────────────

def _promote(client, file="x.template-draft.json"):
    return client.post("/api/template_manager/promote", json={"file": file})


def test_promote_gate_is_noop_when_multi_user_off():
    cwd = os.getcwd()
    os.chdir(tempfile.mkdtemp(prefix="bdc7off_"))
    try:
        # no multi_user config -> gate off -> promote reaches the normal path
        # (400 draft-not-found), NOT a 403 on auth grounds
        from bulk_downloader.app import app
        r = _promote(app.test_client())
        assert r.status_code != 403
    finally:
        os.chdir(cwd)


def test_promote_gate_enforces_reviewer_when_multi_user_on():
    cwd = os.getcwd()
    os.chdir(tempfile.mkdtemp(prefix="bdc7on_"))
    try:
        json.dump({"multi_user": {"enabled": True}}, open("app_config.json", "w"))
        from bulk_downloader import user_accounts as UA
        UA.create_user("op", "pw12345", role="operator")
        UA.create_user("rev", "pw12345", role="reviewer")
        from bulk_downloader.app import app
        c = app.test_client()
        # operator cannot promote -> 403
        c.set_cookie("bd_user", UA.issue_session("op"))
        assert _promote(c).status_code == 403
        # reviewer passes the gate (normal 400 draft-not-found, not 403)
        c.set_cookie("bd_user", UA.issue_session("rev"))
        assert _promote(c).status_code != 403
        # no identity -> 403
        c.delete_cookie("bd_user")
        assert _promote(c).status_code == 403
    finally:
        os.chdir(cwd)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    p = f = 0
    for fn in fns:
        try:
            fn(); p += 1; print(f"  [PASS] {fn.__name__}")
        except Exception as e:
            f += 1; print(f"  [FAIL] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{p} passed / {f} failed")
    raise SystemExit(1 if f else 0)
