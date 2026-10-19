"""aac-f8-dcm-c2: deep-api-auth-csrf F8 + dev-console-misc C2.

F8a  /api/pair cut an IPv6 Host "[fd00::5]:5555" at the first ":" and built
     "http://[:5555/".
F8b  api_tokens/shares _signing_secret ignored set_config's False and handed
     back an unpersisted secret per call: a freshly minted token then failed
     its own verify ("bad signature").
F8c  _session_create never swept expired sessions.
F8d  _expected_token read _app_config (no such name): the app_config
     auth_token fallback was dead.
F8e  _check_csrf exempted Authorization: Bearer but not X-BD-API-Token.
F8f  _pairing_tokens had no cap inside PAIRING_TTL.
C2   GET /api/dev/* ran with no Origin / Fetch-Metadata check, so a page in a
     browser on the box could fire the dev console's GETs blind.

Each test fails on BASE with a distinctive diagnostic and passes on the fix.
"""

# An ordinary module test: its subject is the modules under test, not the tree.
BD_GATE_SCOPE = "module"  # O906.1 gate marker (precollect guard)

import sqlite3
import time

import pytest

from bulk_downloader import api_tokens, app_pair, app_state, db, global_config, shares
from bulk_downloader import app as app_mod
from bulk_downloader.app import app

_DEV_REFUSAL_HINT = "dev console requests must be same-origin"


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """A private config store, sqlite DB, no auth token, and snapshots of the
    in-process session / pairing stores."""
    monkeypatch.setattr(global_config, "_CONFIG_FILE", tmp_path / "app_config.json")
    monkeypatch.setattr(global_config, "_cached", None)
    monkeypatch.setattr(global_config, "_cached_mtime", 0.0)
    for name in ("BD_AUTH_TOKEN", "BD_TOKEN", "BD_DEV_MODE_DISABLE"):
        monkeypatch.delenv(name, raising=False)
    for key in ("auth_token", "pair_lan_ip", "pair_port"):
        monkeypatch.delitem(app_mod._app_cfg, key, raising=False)
    database = tmp_path / "aac.db"
    real_db_conn = db.db_conn

    def db_conn(*args, **kwargs):
        if args or kwargs:   # an explicit path (the integrity thread): not ours
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


# ── F8a ──────────────────────────────────────────────────────────────────

def test_f8a_pair_url_keeps_ipv6_host(iso):
    c = app.test_client()
    r = c.get("/api/pair", headers={"Host": "[fd00::5]:5555"})
    assert r.status_code == 200, r.status_code
    base = r.json["base_url"]
    assert not base.startswith("http://[:"), f"F8A-IPV6-HOST-SPLIT base_url={base!r}"
    assert base == "http://[fd00::5]:5555/", f"F8A-IPV6-HOST-SPLIT base_url={base!r}"


def test_f8a_pair_url_ipv6_loopback_falls_back_to_lan_guess(iso):
    c = app.test_client()
    r = c.get("/api/pair", headers={"Host": "[::1]:5555"})
    assert r.status_code == 200, r.status_code
    base = r.json["base_url"]
    assert not base.startswith("http://[:"), f"F8A-IPV6-HOST-SPLIT base_url={base!r}"
    assert "::1" not in base, f"F8A-LOOPBACK-IN-PAIR-URL base_url={base!r}"


def test_f8a_neg_ipv4_host_unchanged(iso):
    c = app.test_client()
    r = c.get("/api/pair", headers={"Host": "192.168.1.9:5555"})
    assert r.status_code == 200, r.status_code
    assert r.json["base_url"] == "http://192.168.1.9:5555/", r.json["base_url"]


# ── F8f ──────────────────────────────────────────────────────────────────

def test_f8f_pairing_store_is_capped(iso):
    cap = getattr(app_pair, "PAIRING_TOKENS_MAX", 32)
    c = app.test_client()
    last = ""
    for _ in range(cap + 8):
        r = c.get("/api/pair", headers={"Host": "192.168.1.9:5555"})
        assert r.status_code == 200, r.status_code
        last = r.json["token"]
    n = len(app_state._pairing_tokens)
    assert n <= cap, f"F8F-PAIRING-STORE-UNBOUNDED n={n} cap={cap}"
    assert last in app_state._pairing_tokens, "F8F-NEWEST-CODE-EVICTED"


# ── F8b ──────────────────────────────────────────────────────────────────

def _unwritable_store(monkeypatch):
    monkeypatch.setattr(global_config, "set_config", lambda updates: False)


def test_f8b_api_token_never_minted_with_unpersisted_secret(iso, monkeypatch):
    _unwritable_store(monkeypatch)
    res = api_tokens.create_token(scope="read", label="aac-f8b")
    if res.get("ok"):
        v = api_tokens.verify_token(res["token"], request_path="/api/status", method="GET")
        assert v.get("reason") != "bad signature", \
            "F8B-MINTED-TOKEN-FAILS-OWN-SIGNATURE api_tokens"
    assert res.get("ok") is False, f"F8B-MINT-NOT-REFUSED api_tokens res_ok={res.get('ok')!r}"
    assert "could not be persisted" in res.get("error", ""), res.get("error")


def test_f8b_share_token_never_minted_with_unpersisted_secret(iso, monkeypatch):
    _unwritable_store(monkeypatch)
    res = shares.create_token(scopes=["all_stats"], label="aac-f8b")
    if res.get("ok", True) and res.get("token"):
        v = shares.verify_token(res["token"], required_scope="all_stats",
                                request_path="/api/capacity")
        assert v.get("reason") != "bad signature", \
            "F8B-MINTED-TOKEN-FAILS-OWN-SIGNATURE shares"
    assert res.get("ok") is False, f"F8B-MINT-NOT-REFUSED shares res_ok={res.get('ok')!r}"
    assert "could not be persisted" in res.get("error", ""), res.get("error")


def test_f8b_verify_refuses_when_secret_unavailable(iso, monkeypatch):
    # Minted while the store is writable; the store then loses the secret and
    # cannot take a new one: verification names the cause, not a forged token.
    tok = api_tokens.create_token(scope="read", label="aac-f8b")
    sh = shares.create_token(scopes=["all_stats"], label="aac-f8b")
    assert tok.get("ok") and sh.get("token"), "F8B-NEG-WRITABLE-MINT-FAILED"
    global_config._CONFIG_FILE.unlink()
    monkeypatch.setattr(global_config, "_cached", None)
    _unwritable_store(monkeypatch)
    v1 = api_tokens.verify_token(tok["token"], request_path="/api/status", method="GET")
    v2 = shares.verify_token(sh["token"], required_scope="all_stats",
                             request_path="/api/capacity")
    assert v1.get("reason") == "signing secret unavailable", f"F8B-VERIFY-CAUSE api_tokens {v1.get('reason')!r}"
    assert v2.get("reason") == "signing secret unavailable", f"F8B-VERIFY-CAUSE shares {v2.get('reason')!r}"


def test_f8b_neg_writable_store_mints_verifiable_tokens(iso):
    tok = api_tokens.create_token(scope="read", label="aac-f8b")
    assert tok.get("ok") is True, tok
    v = api_tokens.verify_token(tok["token"], request_path="/api/status", method="GET")
    assert v.get("ok") is True, v
    sh = shares.create_token(scopes=["all_stats"], label="aac-f8b")
    assert sh.get("token"), sh
    v = shares.verify_token(sh["token"], required_scope="all_stats", request_path="/api/capacity")
    assert v.get("ok") is True, v


# ── F8c ──────────────────────────────────────────────────────────────────

def test_f8c_session_create_sweeps_expired(iso):
    now = time.time()
    with app_mod._session_lock:
        app_mod._sessions["aac-idle"] = {"created": now - 60,
                                         "last_used": now - app_mod.SESSION_IDLE_TTL - 60,
                                         "source": "csrf_bootstrap"}
        app_mod._sessions["aac-old"] = {"created": now - app_mod.SESSION_ABSOLUTE_TTL - 60,
                                        "last_used": now - 1, "source": "csrf_bootstrap"}
        app_mod._sessions["aac-live"] = {"created": now - 60, "last_used": now - 1,
                                         "source": "csrf_bootstrap"}
    new = app_mod._session_create(source="csrf_bootstrap")
    assert "aac-idle" not in app_mod._sessions, "F8C-EXPIRED-SESSION-NOT-SWEPT idle"
    assert "aac-old" not in app_mod._sessions, "F8C-EXPIRED-SESSION-NOT-SWEPT absolute"
    assert "aac-live" in app_mod._sessions, "F8C-LIVE-SESSION-SWEPT"
    assert new in app_mod._sessions, "F8C-NEW-SESSION-MISSING"


# ── F8d ──────────────────────────────────────────────────────────────────

def test_f8d_app_config_auth_token_fallback_is_read(iso, monkeypatch):
    assert app_mod._expected_token() == "", "F8D-NEG-ISOLATION-LEAK"
    monkeypatch.setitem(app_mod._app_cfg, "auth_token", "aac-f8d-cfg-token")
    got = app_mod._expected_token()
    assert got == "aac-f8d-cfg-token", f"F8D-APP-CONFIG-FALLBACK-DEAD got={got!r}"


# ── F8e ──────────────────────────────────────────────────────────────────

def _session_client():
    c = app.test_client()
    c.set_cookie("bd_session", app_mod._session_create(source="csrf_bootstrap"))
    return c


def test_f8e_api_token_header_exempt_from_cookie_csrf(iso, monkeypatch):
    # aac-fu1 A1: the exemption needs a VERIFIED token, so mint a real one on a
    # token-protected install (a bogus bdapi_ value no longer exempts).
    monkeypatch.setitem(app_mod._app_cfg, "auth_token", "aac-f8e-master")
    tok = api_tokens.create_token(scope="admin", label="aac-f8e")
    assert tok.get("ok") is True, tok
    r = app.test_client().post("/api/api_tokens", json={"scope": "read", "label": "aac-f8e-child"},
                               headers={"X-BD-API-Token": tok["token"],
                                        "Origin": "https://cli.example"})
    assert r.status_code < 400, \
        f"F8E-API-TOKEN-HEADER-CSRF-403 status={r.status_code}"


def test_f8e_neg_cookie_post_without_token_still_needs_csrf(iso):
    r = _session_client().post("/api/zzz_aac_probe")
    assert r.status_code == 403 and b"csrf token" in r.data, r.status_code
    r = _session_client().post("/api/zzz_aac_probe", headers={"X-BD-API-Token": "notbdapi"})
    assert r.status_code == 403 and b"csrf token" in r.data, r.status_code


# ── C2 ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("headers", [
    (("Sec-Fetch-Site", "cross-site"),),
    (("Sec-Fetch-Site", "same-site"),),
    (("Origin", "https://evil.example"),),
    (("Origin", "null"),),
], ids=["fetch-cross-site", "fetch-same-site", "origin-evil", "origin-null"])
def test_c2_cross_origin_dev_get_refused(iso, headers):
    r = app.test_client().get("/api/dev/enabled", headers=list(headers))
    assert r.status_code == 403 and _DEV_REFUSAL_HINT.encode() in r.data, \
        f"C2-DEV-GET-CROSS-SITE-ADMITTED status={r.status_code}"


@pytest.mark.parametrize("headers", [
    (),
    (("Sec-Fetch-Site", "same-origin"),),
    (("Sec-Fetch-Site", "none"),),
    (("Origin", "http://localhost"),),
], ids=["no-headers", "fetch-same-origin", "fetch-none", "origin-same"])
def test_c2_neg_same_origin_dev_get_admitted(iso, headers):
    r = app.test_client().get("/api/dev/enabled", headers=list(headers))
    assert r.status_code == 200, r.status_code


def test_c2_neg_non_dev_get_untouched(iso):
    r = app.test_client().get("/api/health", headers=[("Sec-Fetch-Site", "cross-site")])
    assert _DEV_REFUSAL_HINT.encode() not in r.data, r.status_code
