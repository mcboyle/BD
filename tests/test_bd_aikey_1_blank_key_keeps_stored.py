"""BD-AIKEY-1: a Settings save with a BLANK ai_api_key must keep the stored key.

Settings.tsx renders the field empty when a key is set and promises "Leave
blank to keep the current key -- a blank field never clears it". The explicit
ai_api_key branch of POST /api/global_config treated only the "<configured>"
mask as keep, so clearing the box (or a whitespace-only value) and saving
silently wiped the key. Blank now keeps it (write-only); a non-blank value
still replaces it.

The values below are fixed test strings, not credentials.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

_STORED = "bdaikey1-canary-stored-key-AAAA"


def _client():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return A, c, csrf


def _save(c, csrf, value):
    return c.post("/api/global_config", json={"ai_api_key": value},
                  headers={"X-CSRF-Token": csrf})


def _with_stored_key(fn):
    from bulk_downloader import aiassist
    A, c, csrf = _client()
    before = dict(A._app_cfg)
    saved_ai = aiassist.get_config()
    A._app_cfg["ai_api_key"] = _STORED
    try:
        fn(A, c, csrf)
    finally:
        A._app_cfg.clear()
        A._app_cfg.update(before)
        aiassist.configure(provider=saved_ai.get("provider", "ollama"),
                           api_key=saved_ai.get("api_key", ""))


def test_blank_save_keeps_the_stored_key():
    def body(A, c, csrf):
        for sent in ("", "   "):
            r = _save(c, csrf, sent)
            assert r.status_code == 200, r.get_data(as_text=True)
            assert A._app_cfg.get("ai_api_key") == _STORED, (
                "blank ai_api_key save %r cleared the stored key" % sent)
            assert c.get("/api/global_config").get_json()["ai_api_key"] == "<configured>"
    _with_stored_key(body)


def test_mask_save_still_keeps_and_new_value_still_replaces():
    def body(A, c, csrf):
        assert _save(c, csrf, "<configured>").status_code == 200
        assert A._app_cfg.get("ai_api_key") == _STORED
        assert _save(c, csrf, "bdaikey1-canary-new-key-BBBB").status_code == 200
        assert A._app_cfg.get("ai_api_key") == "bdaikey1-canary-new-key-BBBB"
    _with_stored_key(body)
