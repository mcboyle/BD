"""aac-fu1: auth/CSRF follow-ups A1-A3 to aac-f8-dcm-c2 (security advisory).

A1  a header credential (Authorization: Bearer / X-BD-API-Token) exempts a
    request from the cookie CSRF check only when it was VERIFIED -- the master
    token by constant-time compare, a bdapi_ token by _check_token's
    verify_token. Header PRESENCE no longer exempts.
A2  a cross-site GET /api/pair (a page in a browser on the box) is refused, so
    it cannot mint codes and evict the operator's pending one.
A3  DECIDED OPEN: a GET to /api/dev/* or /api/pair carrying neither Fetch
    Metadata nor Origin is not refused by the CSRF hook, cookie or not -- it
    cannot be told from curl/bdctl, and the handler guards keep their own
    diagnostics. Residual: a browser without Fetch Metadata navigating blind
    (SameSite=Lax rides a top-level GET) can still mint /api/pair codes.
"""
import sqlite3

import pytest

from bulk_downloader import api_tokens, app_state, db, global_config, shares
from bulk_downloader import app as app_mod
from bulk_downloader.app import app

BD_GATE_SCOPE = "module"  # O906.1 gate marker (precollect guard)

_MASTER = "aac-fu1-master-token"
_PROBE = "/api/zzz_aac_fu1_probe"
_DEV_HINT = b"dev console requests must be same-origin"
_CSRF = b"csrf token"


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """A private config store, sqlite DB, no auth token, and snapshots of the
    in-process session / pairing stores (same isolation as the aac-f8 tests)."""
    monkeypatch.setattr(global_config, "_CONFIG_FILE", tmp_path / "app_config.json")
    monkeypatch.setattr(global_config, "_cached", None)
    monkeypatch.setattr(global_config, "_cached_mtime", 0.0)
    for name in ("BD_AUTH_TOKEN", "BD_TOKEN", "BD_DEV_MODE_DISABLE"):
        monkeypatch.delenv(name, raising=False)
    for key in ("auth_token", "pair_lan_ip", "pair_port"):
        monkeypatch.delitem(app_mod._app_cfg, key, raising=False)
    database = tmp_path / "aac_fu1.db"
    real_db_conn = db.db_conn

    def db_conn(*args, **kwargs):
        if args or kwargs:
            return real_db_conn(*args, **kwargs)
        cx = sqlite3.connect(database)
        cx.row_factory = sqlite3.Row
        return cx

    monkeypatch.setattr(db, "db_conn", db_conn)
    if hasattr(api_tokens._signing_secret, "_fb"):
        monkeypatch.delattr(api_tokens._signing_secret, "_fb")
    if hasattr(shares._signing_secret, "_fb"):
        monkeypatch.delattr(shares._signing_secret, "_fb")
    sessions = dict(app_mod._sessions)
    pairing = dict(app_state._pairing_tokens)
    app_state._pairing_tokens.clear()
    yield tmp_path
    app_mod._sessions.clear()
    app_mod._sessions.update(sessions)
    app_state._pairing_tokens.clear()
    app_state._pairing_tokens.update(pairing)


@pytest.fixture
def authed(iso, monkeypatch):
    monkeypatch.setitem(app_mod._app_cfg, "auth_token", _MASTER)
    assert app_mod._accepted_tokens(), "auth token not configured"
    return iso


def _client(source=None):
    c = app.test_client()
    if source:
        c.set_cookie("bd_session", app_mod._session_create(source=source))
    return c


# ── A1 ───────────────────────────────────────────────────────────────────

def test_a1_token_hook_runs_before_csrf_hook():
    names = [f.__name__ for f in app.before_request_funcs[None]]
    assert names.index("_check_token") < names.index("_check_csrf"), names


# Built at runtime: a bdapi_-shaped literal trips gitleaks generic-api-key.
_BOGUS_BDAPI = "bdapi_" + "aacfu1.bogus.sig"

_UNVERIFIED = [
    {"Authorization": "Bearer " + _BOGUS_BDAPI},
    {"X-BD-API-Token": _BOGUS_BDAPI},
    {"Authorization": "Bearer not-the-master"},
]


@pytest.mark.parametrize("headers", _UNVERIFIED, ids=["bearer-bdapi", "x-api-token", "bearer-junk"])
def test_a1_unverified_header_cred_on_login_cookie_needs_csrf(authed, headers):
    r = _client("pair_redeem").post(_PROBE, headers=headers)
    assert r.status_code == 403 and _CSRF in r.data, \
        f"A1-UNVERIFIED-HEADER-CRED-CSRF-EXEMPT status={r.status_code}"


@pytest.mark.parametrize("headers", _UNVERIFIED, ids=["bearer-bdapi", "x-api-token", "bearer-junk"])
def test_a1_unverified_header_cred_unconfigured_needs_csrf(iso, headers):
    r = _client("csrf_bootstrap").post(_PROBE, headers=headers)
    assert r.status_code == 403 and _CSRF in r.data, \
        f"A1-UNVERIFIED-HEADER-CRED-CSRF-EXEMPT status={r.status_code}"


def test_a1_neg_master_bearer_still_exempt(authed):
    r = _client("pair_redeem").post(_PROBE, headers={"Authorization": f"Bearer {_MASTER}"})
    assert r.status_code == 404, (r.status_code, r.data[:120])
    r = _client().post(_PROBE, headers={"Authorization": f"Bearer {_MASTER}",
                                        "Origin": "https://cli.example"})
    assert r.status_code == 404, (r.status_code, r.data[:120])


def test_a1_neg_verified_api_token_still_exempt(authed):
    tok = api_tokens.create_token(scope="admin", label="aac-fu1")
    assert tok.get("ok") is True, tok
    r = _client().post("/api/api_tokens", json={"scope": "read", "label": "aac-fu1-child"},
                       headers={"X-BD-API-Token": tok["token"],
                                "Origin": "https://cli.example"})
    assert r.status_code < 400, (r.status_code, r.data[:160])


@pytest.mark.parametrize("configured", [False, True], ids=["noauth", "auth"])
def test_a1_neg_vault_route_bearer_reaches_handler(iso, monkeypatch, configured):
    if configured:
        monkeypatch.setitem(app_mod._app_cfg, "auth_token", _MASTER)
    r = _client().post("/api/secrets/extension/fetch_one", json={},
                       headers={"Authorization": "Bearer vt-aacfu1-bogus",
                                "Origin": "chrome-extension://aacfu1"})
    assert b"cross-origin request refused" not in r.data, (r.status_code, r.data[:160])
    assert r.status_code in (401, 503), (r.status_code, r.data[:160])


def test_a1_neg_cookie_post_without_cred_still_needs_csrf(authed):
    r = _client("pair_redeem").post(_PROBE)
    assert r.status_code == 403 and _CSRF in r.data, r.status_code


# ── A2 ───────────────────────────────────────────────────────────────────

_CROSS = [
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
]


@pytest.mark.parametrize("headers", _CROSS, ids=["fetch-cross-site", "fetch-same-site",
                                                 "origin-evil", "origin-null"])
def test_a2_cross_site_pair_get_cannot_evict(iso, headers):
    c = _client()
    r = c.get("/api/pair", headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200, r.status_code
    mine = r.json["token"]
    cap = getattr(app_mod.__dict__.get("app_pair"), "PAIRING_TOKENS_MAX", 32) or 32
    codes = []
    for _ in range(cap + 1):
        r = c.get("/api/pair", headers=headers)
        codes.append(r.status_code)
    assert mine in app_state._pairing_tokens, f"A2-CROSS-SITE-PAIR-EVICTS statuses={set(codes)}"
    assert set(codes) == {403}, f"A2-CROSS-SITE-PAIR-MINTED statuses={set(codes)}"


@pytest.mark.parametrize("headers", [
    {}, {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"},
    {"Origin": "http://localhost"},
], ids=["no-headers", "fetch-same-origin", "fetch-none", "origin-same"])
def test_a2_neg_same_origin_pair_get_mints(iso, headers):
    r = _client().get("/api/pair", headers=headers)
    assert r.status_code == 200 and r.json["token"] in app_state._pairing_tokens, r.status_code


# ── A3 ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/api/dev/enabled", "/api/pair"])
@pytest.mark.parametrize("source,configured", [("csrf_bootstrap", False), ("pair_redeem", True)],
                         ids=["noauth", "auth"])
def test_a3_decided_open_cookie_get_without_fetch_metadata_passes(iso, monkeypatch, path,
                                                                  source, configured):
    if configured:
        monkeypatch.setitem(app_mod._app_cfg, "auth_token", _MASTER)
    r = _client(source).get(path)
    assert b"must be same-origin" not in r.data, \
        f"A3-DECIDED-OPEN-REGRESSED path={path} status={r.status_code}"


@pytest.mark.parametrize("headers", [{"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"},
                                     {"Origin": "http://localhost"}],
                         ids=["fetch-same-origin", "fetch-none", "origin-same"])
def test_a3_neg_cookie_get_with_same_origin_proof_admitted(iso, headers):
    r = _client("csrf_bootstrap").get("/api/dev/enabled", headers=headers)
    assert r.status_code == 200, r.status_code


def test_a3_neg_cookieless_and_verified_cred_admitted(authed):
    r = _client().get("/api/dev/enabled", headers={"Authorization": f"Bearer {_MASTER}"})
    assert r.status_code == 200, r.status_code
    r = _client("pair_redeem").get("/api/dev/enabled",
                                   headers={"Authorization": f"Bearer {_MASTER}"})
    assert r.status_code == 200, r.status_code


def test_a3_neg_cookieless_bare_get_admitted(iso):
    r = _client().get("/api/dev/enabled")
    assert r.status_code == 200, r.status_code
    assert _DEV_HINT not in r.data
