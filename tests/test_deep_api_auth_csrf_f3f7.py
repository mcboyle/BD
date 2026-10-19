"""deep-api-auth-csrf F5-F7 (O1789 cuts2 deep-api-auth-csrf-f3f7, PM RULING-pm-13-blocker-rows-0120Z).

F5: auth_throttle._cfg took NaN/inf for the throttle base/cap (max(0.0, nan) is 0.0: cooldown off); a
password had no length cap at create / set_password / login.
F6: the OIDC callback put the provider's raw ``error`` arg into the redirect query; oidc.discover cached
any JSON document (a list, a string) forever; the JWKS fetch never checked the HTTP status.
F7: a non-object JSON body or a non-string field was a 500 on /api/api_tokens, /api/shares and
/api/pair/redeem; create_token took ttl_hours=True as 1 h; /api/shares/access_log ?limit=abc was a 500 and
?limit=-1 returned every row; a non-numeric pair_port was a 500 on GET /api/pair.

Each refusal has a control beside it that shows the probe can say yes. Hermetic: bare Flask apps mount
one blueprint each, the IdP is an httpx.MockTransport, and the mutations are recorded stubs.
"""
from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from flask import Flask

import bulk_downloader.app as bd_app  # noqa: F401  -- settles every blueprint's routes first
from bulk_downloader import (api_tokens, app_api_tokens, app_auth, app_oidc, app_pair, app_shares,
                             auth_throttle, oidc, shares, ssrf_transport, user_accounts)

BD_GATE_SCOPE = "module"


def _client(bp):
    app = Flask(__name__)
    app.secret_key = "test-only"
    app.register_blueprint(bp)
    return app.test_client()


# ── F5: throttle config ─────────────────────────────────────────────────

@pytest.fixture
def no_store(monkeypatch):
    monkeypatch.setattr(auth_throttle, "_store_get", lambda key: None)


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
def test_f5_throttle_non_finite_env_is_default(no_store, monkeypatch, raw):
    monkeypatch.setenv("BD_AUTH_THROTTLE_BASE", raw)
    monkeypatch.setenv("BD_AUTH_THROTTLE_MAX", raw)
    _free, base, mx = auth_throttle._cfg()
    assert (base, mx) == (auth_throttle._DEFAULT_BASE, auth_throttle._DEFAULT_MAX), (
        f"F3F7_THROTTLE_NONFINITE: BD_AUTH_THROTTLE_BASE/MAX={raw} -> base={base} max={mx}")


def test_f5_throttle_non_finite_store_falls_through(monkeypatch):
    monkeypatch.setattr(auth_throttle, "_store_get",
                        lambda key: "nan" if key == "auth_throttle_base" else None)
    monkeypatch.setenv("BD_AUTH_THROTTLE_BASE", "3.5")
    assert auth_throttle._cfg()[1] == 3.5, "F3F7_THROTTLE_NONFINITE: store nan shadowed the env value"


def test_f5_throttle_finite_env_still_read(no_store, monkeypatch):
    monkeypatch.setenv("BD_AUTH_THROTTLE_BASE", "7")
    monkeypatch.setenv("BD_AUTH_THROTTLE_MAX", "60")
    assert auth_throttle._cfg()[1:] == (7.0, 60.0), "control: a finite value is no longer read"


# ── F5: password length cap ─────────────────────────────────────────────

LONG = "p" * (user_accounts.PASSWORD_MAX_LEN + 1) if hasattr(user_accounts, "PASSWORD_MAX_LEN") else "p" * 500_000
EDGE = "p" * getattr(user_accounts, "PASSWORD_MAX_LEN", 1024)


def test_f5_create_user_refuses_overlong_password(tmp_path):
    ok, msg = user_accounts.create_user("alice", LONG, role="admin", base_dir=tmp_path)
    assert not ok and "at most" in msg, f"F3F7_PW_TOO_LONG: create_user len={len(LONG)} -> {ok} {msg!r}"
    assert user_accounts.create_user("bob", EDGE, role="admin", base_dir=tmp_path)[0], (
        "control: a password at the cap is refused")
    assert user_accounts.verify_password("bob", EDGE, base_dir=tmp_path)


def test_f5_set_password_refuses_overlong_password(tmp_path):
    assert user_accounts.create_user("alice", "short-pass", role="admin", base_dir=tmp_path)[0]
    ok, msg = user_accounts.set_password("alice", LONG, base_dir=tmp_path)
    assert not ok and "at most" in msg, f"F3F7_PW_TOO_LONG: set_password -> {ok} {msg!r}"
    assert user_accounts.verify_password("alice", "short-pass", base_dir=tmp_path), "password changed anyway"
    assert user_accounts.set_password("alice", EDGE, base_dir=tmp_path)[0], "control: cap-length refused"


def test_f5_login_overlong_password_is_400_before_hash(monkeypatch):
    hashed = []
    monkeypatch.setattr(user_accounts, "verify_password", lambda *a, **k: hashed.append(1) or False)
    c = _client(app_auth.auth_bp)
    r = c.post("/api/auth/login", json={"username": "alice", "password": LONG})
    assert r.status_code == 400 and not hashed, (
        f"F3F7_PW_TOO_LONG: login len={len(LONG)} -> {r.status_code}, verify_password called={bool(hashed)}")
    r = c.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
    assert r.status_code == 401 and hashed, "control: an ordinary wrong password no longer reaches verify"


def test_f5_routes_overlong_password_400(monkeypatch, tmp_path):
    monkeypatch.setattr(user_accounts, "_store_path",
                        lambda base_dir=None: str(tmp_path / user_accounts.ACCOUNTS_FILE))
    c = _client(app_auth.auth_bp)
    r = c.post("/api/auth/users", json={"username": "alice", "password": LONG})
    assert r.status_code == 400 and "at most" in r.get_json()["error"], (
        f"F3F7_PW_TOO_LONG: bootstrap create -> {r.status_code} {r.get_data(as_text=True)[:120]}")
    r = c.post("/api/auth/users", json={"username": "alice", "password": "fine-pass"})
    assert r.status_code == 200, "control: the bootstrap create is refused"


# ── F6: OIDC ────────────────────────────────────────────────────────────

def test_f6_callback_error_is_url_encoded():
    c = _client(app_oidc.oidc_bp)
    r = c.get("/api/auth/oidc/callback", query_string={"error": "x&sso_ok=1#frag"})
    loc = r.headers.get("Location", "")
    q = parse_qs(urlsplit(loc).query)
    assert r.status_code == 302 and q == {"sso_error": ["x&sso_ok=1#frag"]} and "#" not in loc, (
        f"F3F7_SSO_ERROR_RAW: Location={loc!r}")
    r = c.get("/api/auth/oidc/callback", query_string={"error": "access_denied"})
    assert r.headers["Location"].endswith("/?sso_error=access_denied"), "control: a plain error changed"


def _idp(monkeypatch, status, payload):
    def handler(request):
        return httpx.Response(status, json=payload)
    monkeypatch.setattr(ssrf_transport, "guarded_transport", lambda *a, **k: httpx.MockTransport(handler))
    monkeypatch.setattr(oidc, "_DISCO_CACHE", {})


@pytest.mark.parametrize("doc", [[1, 2], "a string", 7])
def test_f6_discover_refuses_and_never_caches_non_object(monkeypatch, doc):
    _idp(monkeypatch, 200, doc)
    try:
        got = oidc.discover("https://idp.invalid")
    except ValueError:
        got = None
    assert got is None and oidc._DISCO_CACHE == {}, (
        f"F3F7_DISCO_CACHED_NON_DICT: discover returned {got!r} cache={oidc._DISCO_CACHE!r}")


def test_f6_discover_caches_object(monkeypatch):
    _idp(monkeypatch, 200, {"issuer": "https://idp.invalid"})
    assert oidc.discover("https://idp.invalid") == {"issuer": "https://idp.invalid"}
    assert "https://idp.invalid" in oidc._DISCO_CACHE, "control: a dict document is not cached"


def test_f6_jwks_http_error_raises_status(monkeypatch):
    _idp(monkeypatch, 500, {"keys": []})
    monkeypatch.setattr(oidc, "oidc_config", lambda: {"issuer": "https://idp.invalid", "client_id": "c"})
    try:
        oidc.verify_id_token("x.y.z", nonce="n", disco={"jwks_uri": "https://idp.invalid/jwks"})
    except httpx.HTTPStatusError:
        return
    except Exception as e:
        raise AssertionError(f"F3F7_JWKS_NO_STATUS_CHECK: JWKS 500 parsed as keys -> {type(e).__name__}") from e
    raise AssertionError("F3F7_JWKS_NO_STATUS_CHECK: JWKS 500 accepted")


# ── F7: malformed JSON / types ──────────────────────────────────────────

@pytest.fixture
def minted(monkeypatch):
    seen = []
    monkeypatch.setattr(api_tokens, "create_token", lambda **kw: seen.append(kw) or {"ok": True})
    return seen


@pytest.mark.parametrize("body", [[1], "str", {"scope": 5}, {"scope": "read", "label": 5}])
def test_f7_api_tokens_create_bad_body_400(minted, body):
    r = _client(app_api_tokens.api_tokens_bp).post("/api/api_tokens", json=body)
    assert r.status_code == 400 and not minted, f"F3F7_BAD_BODY_500: /api/api_tokens {body!r} -> {r.status_code}"


def test_f7_api_tokens_create_control(minted):
    r = _client(app_api_tokens.api_tokens_bp).post("/api/api_tokens", json={"scope": "read", "label": "l"})
    assert r.status_code == 200 and minted, "control: a good mint is refused"


def test_f7_create_token_rejects_bool_ttl(clean_workdir):
    res = api_tokens.create_token(scope="read", ttl_hours=True)
    assert res["ok"] is False, f"F3F7_TTL_BOOL: ttl_hours=True -> {res.get('expires_at')} (now {time.time()})"
    assert api_tokens.create_token(scope="read", ttl_hours=1)["ok"], "control: ttl_hours=1 refused"


@pytest.fixture
def share_calls(monkeypatch):
    seen = []
    monkeypatch.setattr(shares, "scope_error", lambda scopes: None)
    monkeypatch.setattr(shares, "create_token", lambda **kw: seen.append(kw) or {"ok": True})
    monkeypatch.setattr(shares, "recent_access", lambda *, limit=100: seen.append(limit) or [])
    return seen


@pytest.mark.parametrize("body", [[1], "str", {"scopes": ["all_stats"], "label": 5},
                                  {"scopes": ["all_stats"], "ip_whitelist": ["1.2.3.4"]}])
def test_f7_shares_create_bad_body_400(share_calls, body):
    r = _client(app_shares.shares_bp).post("/api/shares", json=body)
    assert r.status_code == 400 and not share_calls, f"F3F7_BAD_BODY_500: /api/shares {body!r} -> {r.status_code}"


def test_f7_shares_create_control(share_calls):
    r = _client(app_shares.shares_bp).post("/api/shares", json={"scopes": ["all_stats"], "label": "l"})
    assert r.status_code == 200 and share_calls, "control: a good share is refused"


@pytest.mark.parametrize("limit", ["abc", "-1", "0", "1.5", "\u0661", "1" * 5000])
def test_f7_access_log_bad_limit_400(share_calls, limit):
    r = _client(app_shares.shares_bp).get("/api/shares/access_log", query_string={"limit": limit})
    assert r.status_code == 400 and not share_calls, (
        f"F3F7_ACCESS_LIMIT: ?limit={limit[:12]} -> {r.status_code}, recent_access({share_calls})")


def test_f7_access_log_limit_clamped_and_control(share_calls):
    c = _client(app_shares.shares_bp)
    assert c.get("/api/shares/access_log", query_string={"limit": "5"}).status_code == 200
    assert c.get("/api/shares/access_log").status_code == 200
    assert c.get("/api/shares/access_log", query_string={"limit": "999999"}).status_code == 200
    assert share_calls == [5, 100, app_shares.ACCESS_LOG_MAX], f"F3F7_ACCESS_LIMIT: limits={share_calls}"


@pytest.mark.parametrize("body", [[1], "str", {"token": 5}])
def test_f7_pair_redeem_bad_body_400(body):
    r = _client(app_pair.pair_bp).post("/api/pair/redeem", json=body)
    assert r.status_code == 400, f"F3F7_BAD_BODY_500: /api/pair/redeem {body!r} -> {r.status_code}"


def test_f7_pair_redeem_control():
    r = _client(app_pair.pair_bp).post("/api/pair/redeem", json={"token": "never-minted"})
    assert r.status_code == 404, "control: an unknown token no longer reaches the store"


@pytest.mark.parametrize("raw,port", [("abc", 5555), ("70000", 5555), ("0", 5555), ("8080", 8080), (8081, 8081)])
def test_f7_pair_port_non_numeric_falls_back(monkeypatch, raw, port):
    monkeypatch.setattr(app_pair, "_app__app_cfg", lambda: {"pair_port": raw, "pair_lan_ip": "192.0.2.1"})
    r = _client(app_pair.pair_bp).get("/api/pair")
    assert r.status_code == 200 and r.get_json()["port"] == port, (
        f"F3F7_PAIR_PORT: pair_port={raw!r} -> {r.status_code} {r.get_data(as_text=True)[:80]}")
