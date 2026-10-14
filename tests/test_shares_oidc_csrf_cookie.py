"""raw-editors-dev-auth F2 + F3 (shares-oidc-fix r1).

F2: app_shares.py (create, revoke) and app_dev_db.py (wal_checkpoint, sql) called ``_check_csrf()`` and dropped
its return value. ``_check_csrf`` RETURNS a 403 response to refuse; it never aborts. So the route-level call did
nothing, and the routes were protected only by the app's before_request hook. Each row here mounts the blueprint
on a bare Flask app with no hook (a route moved off the hook) and sends a cross-origin state-changing request.
The route itself must refuse it, with the cross-origin body, before the mutation runs.

F3: the OIDC callback set the ``bd_user`` session cookie without the Secure flag. Over https it must carry Secure;
over plain http it must not (the same rule as app_pair's ``bd_session``: secure=(request.scheme == "https")).

Hermetic: no hook, no IdP, no DB. The mutation and IdP calls are recorded stubs.
"""
import pytest
from flask import Flask

import bulk_downloader.app as bd_app  # settles every blueprint's routes before a bare app mounts one
import bulk_downloader.app_dev_db  # noqa: F401  -- attaches the wal_checkpoint / sql handlers to dev_bp
from bulk_downloader import app_dev, app_oidc, app_shares, dev_suite, oidc, shares, user_accounts

BD_GATE_SCOPE = "module"

EVIL = "http://evil.invalid"
REFUSED = "cross-origin request refused"


@pytest.fixture
def calls(monkeypatch):
    """Record every mutation the four routes can reach; the dev-surface guard is passed (it is not under test)."""
    seen = []
    monkeypatch.setattr(shares, "scope_error", lambda scopes: None)
    monkeypatch.setattr(shares, "create_token", lambda **kw: seen.append("create_token") or {"ok": True})
    monkeypatch.setattr(shares, "revoke_token", lambda tid: seen.append("revoke_token") or True)
    monkeypatch.setattr(dev_suite, "wal_checkpoint", lambda mode: seen.append("wal_checkpoint") or {"ok": True})
    monkeypatch.setattr(dev_suite, "sql_console", lambda q, lim: seen.append("sql_console") or {"ok": True})
    monkeypatch.setattr(bd_app, "_dev_mode_guard", lambda: None)
    return seen


def _client(bp):
    app = Flask(__name__)
    app.secret_key = "test-only"
    app.register_blueprint(bp)
    return app.test_client()


ROUTES = [
    ("shares-create", app_shares.shares_bp, "post", "/api/shares", {"scopes": ["all_stats"]}, "create_token"),
    ("shares-revoke", app_shares.shares_bp, "delete", "/api/shares/tok1", None, "revoke_token"),
    ("dev-wal-checkpoint", app_dev.dev_bp, "post", "/api/dev/wal_checkpoint", {"mode": "PASSIVE"}, "wal_checkpoint"),
    ("dev-sql", app_dev.dev_bp, "post", "/api/dev/sql", {"query": "SELECT 1"}, "sql_console"),
]


@pytest.mark.parametrize("name,bp,verb,url,body,mutation", ROUTES, ids=[r[0] for r in ROUTES])
def test_f2_route_refuses_its_own_csrf_failure(calls, name, bp, verb, url, body, mutation):
    r = getattr(_client(bp), verb)(url, json=body, headers={"Origin": EVIL})
    assert calls == [], (f"F2-CSRF-DISCARDED {name}: {mutation} ran although the route's _check_csrf() refused "
                         f"(status={r.status_code} body={r.get_data(as_text=True)[:160]!r})")
    assert r.status_code == 403 and r.get_json().get("error") == REFUSED, \
        f"F2-WRONG-REFUSAL {name}: status={r.status_code} body={r.get_data(as_text=True)[:160]!r}"


@pytest.mark.parametrize("name,bp,verb,url,body,mutation", ROUTES, ids=[r[0] for r in ROUTES])
def test_f2_control_same_origin_reaches_the_mutation(calls, name, bp, verb, url, body, mutation):
    r = getattr(_client(bp), verb)(url, json=body)
    assert r.status_code == 200 and calls == [mutation], \
        f"F2-CONTROL {name}: a same-origin call must pass: status={r.status_code} calls={calls}"


@pytest.fixture
def sso(monkeypatch):
    monkeypatch.setattr(oidc, "exchange_code", lambda code: {"id_token": "idt"})
    monkeypatch.setattr(oidc, "verify_id_token", lambda tok, nonce=None: {"sub": "s"})
    monkeypatch.setattr(oidc, "provision_user", lambda claims: "alice")
    monkeypatch.setattr(user_accounts, "issue_session", lambda user: "sess-token")

    def login(base_url):
        c = _client(app_oidc.oidc_bp)
        with c.session_transaction(base_url=base_url) as s:
            s["oidc_state"], s["oidc_nonce"] = "st", "nn"
        r = c.get("/api/auth/oidc/callback?code=c&state=st", base_url=base_url)
        assert r.status_code == 302, f"F3-RIG callback did not complete: {r.status_code} {r.get_data(as_text=True)[:160]!r}"
        cookies = [h for h in r.headers.getlist("Set-Cookie") if h.startswith("bd_user=")]
        assert len(cookies) == 1, f"F3-RIG expected one bd_user Set-Cookie, got {r.headers.getlist('Set-Cookie')}"
        return [p.strip().lower() for p in cookies[0].split(";")]
    return login


def test_f3_bd_user_cookie_secure_over_https(sso):
    attrs = sso("https://localhost")
    assert "secure" in attrs, f"F3-COOKIE-NOT-SECURE bd_user over https: {attrs}"
    assert "httponly" in attrs and "samesite=lax" in attrs, f"F3-FLAGS-LOST {attrs}"


def test_f3_control_plain_http_not_secure(sso):
    attrs = sso("http://localhost")
    assert "secure" not in attrs, f"F3-CONTROL a Secure cookie over plain http is never sent back: {attrs}"
    assert "httponly" in attrs and "samesite=lax" in attrs, f"F3-FLAGS-LOST {attrs}"
