"""dss-global-secrets (deep-settings-secrets F4, F8): global secret typing + redaction.

F4: POST /api/global_config stored str() of a non-string ai_api_key (["secret"] ->
"['secret']", true -> "True") as the live AI key and answered 200; GET then showed
"<configured>", so the user could not tell. Every schema secret (auth_token,
bd_token, oidc_client_secret) already answers 400 "must be str".

F8: GET /api/settings/global/effective masked by the site-config secret rule only,
so *_key global values (master_key, signing_key, ...) came back in plaintext while
GET /api/global_config masked them. The two read views must agree.

The values below are fixed test strings, not credentials.
"""

from __future__ import annotations

import json

import pytest
from flask import Flask

BD_GATE_SCOPE = "module"

_STORED = "dss-global-secrets-canary-stored-key"


def _global_config_client(monkeypatch, cfg):
    from bulk_downloader import aiassist
    from bulk_downloader import app_global_config as agc

    monkeypatch.setattr(agc, "_app__app_cfg", lambda: cfg)
    monkeypatch.setattr(agc, "_save_app_config", lambda: None)
    monkeypatch.setattr(aiassist, "configure", lambda **kw: None)
    app = Flask(__name__)
    app.register_blueprint(agc.global_config_bp)
    return app.test_client()


@pytest.mark.parametrize("bad", [["secret"], {"k": "v"}, True, 12345])
def test_non_string_ai_api_key_is_refused_and_not_stored(monkeypatch, bad):
    cfg = {"ai_api_key": _STORED}
    r = _global_config_client(monkeypatch, cfg).post(
        "/api/global_config", json={"ai_api_key": bad}
    )
    assert cfg.get("ai_api_key") == _STORED, (
        f"DSS_AI_KEY_NONSTR_STORED: ai_api_key={bad!r} replaced the stored key "
        f"with {cfg.get('ai_api_key')!r} (HTTP {r.status_code})"
    )
    assert r.status_code == 400, (
        f"DSS_AI_KEY_NONSTR_ACCEPTED: ai_api_key={bad!r} -> {r.status_code}, "
        f"not 400 like the schema secrets: {r.get_data(as_text=True)[:200]}"
    )
    assert "ai_api_key must be str" in r.get_json().get("error", ""), r.get_json()


@pytest.mark.parametrize("keep", [None, "", "<configured>"])
def test_keep_values_still_keep_the_stored_key(monkeypatch, keep):
    """Over-correction control: null, blank and the mask sentinel still mean 'keep'."""
    cfg = {"ai_api_key": _STORED}
    r = _global_config_client(monkeypatch, cfg).post(
        "/api/global_config", json={"ai_api_key": keep}
    )
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    assert cfg.get("ai_api_key") == _STORED


def test_string_ai_api_key_still_replaces(monkeypatch):
    cfg = {"ai_api_key": _STORED}
    r = _global_config_client(monkeypatch, cfg).post(
        "/api/global_config", json={"ai_api_key": "dss-new-key"}
    )
    assert r.status_code == 200 and cfg.get("ai_api_key") == "dss-new-key"


def test_schema_secret_type_check_is_the_reference(monkeypatch):
    """Positive control: the schema path already refuses a non-string secret."""
    cfg = {}
    r = _global_config_client(monkeypatch, cfg).post(
        "/api/global_config", json={"auth_token": ["x"]}
    )
    assert r.status_code == 400 and "auth_token must be str" in r.get_json()["error"]


_KEY_NEEDLES = {
    "master_key": "dss-needle-master",
    "signing_key": "dss-needle-signing",
    "hmac_key": "dss-needle-hmac",
    "webhook_key": "dss-needle-webhook",
    "encryption_key": "dss-needle-encryption",
}
# Masked by the config SoT (site_editor) but not by the *_key name rule: the union
# must keep them masked too.
_SOT_NEEDLES = {
    "passphrase": "dss-needle-passphrase",
    "cookie_file": "dss-needle-cookiefile",
}
_VISIBLE = {
    "theme": "dss-visible-dark",
    "cookie_max_age_hours": 7,
    "sort_by": "dss-visible-sort",
}


def _effective(monkeypatch, cfg):
    from bulk_downloader import app_settings_center as SC
    from bulk_downloader import global_config as GC

    monkeypatch.setattr(GC, "get_config", lambda: json.loads(json.dumps(cfg)))
    app = Flask(__name__)
    SC.register_routes(app)
    r = app.test_client().get("/api/settings/global/effective")
    assert r.status_code == 200
    return r.get_json()["keys"]


def test_global_effective_masks_every_key_global_config_masks(monkeypatch):
    cfg = dict(
        _KEY_NEEDLES,
        **_SOT_NEEDLES,
        nested={"signing_key": "dss-needle-nested"},
        **_VISIBLE,
    )
    keys = _effective(monkeypatch, cfg)
    body = json.dumps(keys)
    leaked = sorted(
        v
        for v in [*_KEY_NEEDLES.values(), *_SOT_NEEDLES.values(), "dss-needle-nested"]
        if v in body
    )
    assert not leaked, (
        f"DSS_EFFECTIVE_KEY_LEAK: /api/settings/global/effective returned *_key "
        f"global values in plaintext: {leaked}"
    )
    for k in [*_KEY_NEEDLES, *_SOT_NEEDLES]:
        assert keys[k] == {"present": True}, (k, keys[k])

    # The two read views agree: every name app_global_config masks is masked here.
    from bulk_downloader import app_global_config as agc

    redacted = agc._redact_secrets(cfg)
    for k, v in cfg.items():
        if redacted[k] != v and not isinstance(v, dict):
            assert keys[k] != v, (
                f"DSS_REDACTION_RULES_DISAGREE: {k} masked by GET /api/global_config only"
            )


def test_global_effective_keeps_non_secret_values_visible(monkeypatch):
    """Over-correction control: ordinary settings still round-trip in the clear."""
    keys = _effective(monkeypatch, dict(_VISIBLE))
    assert keys == _VISIBLE, keys
