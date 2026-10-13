"""BD-SEC-1: GET /api/global_config must never carry a secret value.

The unauthenticated GET echoed dict(_app_cfg). It masked only ai_api_key,
auth_token and bd_token, so the runtime-minted signing secrets
api_auth_token_secret (api_tokens) and stream_token_secret (stream_relay) went
out in plaintext, and so did oidc_client_secret. Anyone who could reach :5555
could read them. Secret-named fields are now presence-only: "<configured>" when
set, "" when unset. A save that sends the marker or a blank keeps the stored
value (write-only semantics), so the Settings form still round-trips.

The canaries below are fixed test strings, not credentials.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

_CANARY = {
    "api_auth_token_secret": "bdsec1-canary-signing-secret-AAAA",
    "stream_token_secret": "bdsec1-canary-stream-secret-BBBB",
    "oidc_client_secret": "bdsec1-canary-oidc-secret-CCCC",
    "auth_token": "bdsec1-canary-auth-token-DDDD",
    # Census hits minted at runtime by shares.py / read by federation.py.
    "share_token_secret": "bdsec1-canary-share-secret-GGGG",
    "fed_token": "bdsec1-canary-fed-token-HHHH",
}


def _client():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return A, c, csrf


def _seed(A, extra=None):
    before = dict(A._app_cfg)
    A._app_cfg.update(_CANARY)
    if extra:
        A._app_cfg.update(extra)
    return before


def _restore(A, before):
    A._app_cfg.clear()
    A._app_cfg.update(before)


def test_get_never_carries_a_secret_value():
    A, c, _ = _client()
    before = _seed(A, {"bdsec1_nested": {"hook": {"secret": "bdsec1-canary-nested-EEEE"}}})
    try:
        r = c.get("/api/global_config")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        leaked = [k for k, v in _CANARY.items() if v in body]
        assert not leaked, "GET /api/global_config echoed secret value(s) for: %s" % leaked
        assert "bdsec1-canary-nested-EEEE" not in body, "nested secret-named field echoed"
        g = r.get_json()
        for k in _CANARY:
            assert g.get(k) == "<configured>", (k, "set secret must read as presence marker")
        assert g["bdsec1_nested"]["hook"]["secret"] == "<configured>"
    finally:
        _restore(A, before)


def test_unset_secret_reads_empty_and_lookalikes_untouched():
    A, c, _ = _client()
    before = dict(A._app_cfg)
    A._app_cfg.update({"oidc_client_secret": "", "secrets_audit": True,
                       "auth_throttle": True, "ai_api_key": "@cred:bdsec1-ref"})
    try:
        g = c.get("/api/global_config").get_json()
        assert g["oidc_client_secret"] == ""
        assert g["secrets_audit"] is True
        assert g["auth_throttle"] is True
        assert g["ai_api_key"] == "@cred:bdsec1-ref"
    finally:
        _restore(A, before)


def test_post_response_is_masked_too():
    A, c, csrf = _client()
    before = _seed(A)
    try:
        r = c.post("/api/global_config", json={"watch_archive": True},
                   headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.get_json()
        body = r.get_data(as_text=True)
        assert not [k for k, v in _CANARY.items() if v in body]
    finally:
        _restore(A, before)


def test_save_keeps_stored_secrets_write_only():
    A, c, csrf = _client()
    before = _seed(A)
    try:
        # A save carrying the marker / a blank keeps the stored value.
        for sent in ("<configured>", "", "   "):
            r = c.post("/api/global_config", json={"oidc_client_secret": sent},
                       headers={"X-CSRF-Token": csrf})
            assert r.status_code == 200, r.get_json()
            assert A._app_cfg["oidc_client_secret"] == _CANARY["oidc_client_secret"], repr(sent)
        # A save that omits them keeps the runtime-minted signing secrets.
        c.post("/api/global_config", json={"watch_archive": False},
               headers={"X-CSRF-Token": csrf})
        assert A._app_cfg["api_auth_token_secret"] == _CANARY["api_auth_token_secret"]
        assert A._app_cfg["stream_token_secret"] == _CANARY["stream_token_secret"]
        # A real new value still writes.
        r = c.post("/api/global_config", json={"oidc_client_secret": "bdsec1-new-FFFF"},
                   headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        assert A._app_cfg["oidc_client_secret"] == "bdsec1-new-FFFF"
        assert c.get("/api/global_config").get_json()["oidc_client_secret"] == "<configured>"
    finally:
        _restore(A, before)


# ---- /api/notify/apprise/settings (census hit: tg_bot_token rides the same file) ----
_TG = "bdsec1-canary-tg-bot-token-JJJJ"
_URL = "tgram://bdsec1-canary-url-token-KKKK/123"


def _notify_store(monkeypatch, A):
    store = {"notify_apprise_enabled": True, "notify_apprise_urls": _URL,
             "tg_bot_token": _TG, "tg_chat_id": "42"}
    monkeypatch.setattr(A, "_load_global_notify_settings", lambda: dict(store))

    def _save(d):
        store.clear()
        store.update(d)
        return True
    monkeypatch.setattr(A, "_save_global_notify_settings", _save)
    monkeypatch.setattr(A, "_apply_global_notify_config", lambda *a, **k: None)
    return store


def test_notify_settings_get_never_carries_the_bot_token(monkeypatch):
    A, c, _ = _client()
    _notify_store(monkeypatch, A)
    r = c.get("/api/notify/apprise/settings")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert _TG not in body, "GET /api/notify/apprise/settings echoed tg_bot_token"
    assert "bdsec1-canary-url-token-KKKK" not in body
    s = r.get_json()["settings"]
    assert "tg_bot_token" not in s and s["tg_bot_token_set"] is True
    assert s["notify_apprise_urls_set"] is True and s["notify_apprise_urls_count"] == 1
    assert s["tg_chat_id"] == "42"


def test_notify_settings_post_echo_is_masked_and_keeps_token(monkeypatch):
    A, c, csrf = _client()
    store = _notify_store(monkeypatch, A)
    r = c.post("/api/notify/apprise/settings", json={"notify_apprise_enabled": False},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.get_json()
    body = r.get_data(as_text=True)
    assert _TG not in body and "bdsec1-canary-url-token-KKKK" not in body
    assert store["tg_bot_token"] == _TG and store["notify_apprise_urls"] == _URL
    assert store["notify_apprise_enabled"] is False
