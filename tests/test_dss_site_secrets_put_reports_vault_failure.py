"""dss-site-secrets (deep-settings-secrets F2, F5): site PUT secret honesty.

F2: PUT /api/sites/<sid> with a new password answered 200 {"ok": true} when the
vault store FAILED (backend.set raised, or a @cred: reference was refused). The
reason went to stderr only, and the old password stayed live. POST /api/sites
already reports cred_stored:false + cred_error; the PUT now says the same.
F5: the PUT stored non-string secrets as-is (list/dict/int/bool), and a list
captcha_api_key then broke GET /api/sites/<sid>/captcha/stats with a 400.
Driven through the real route and the real _store_site_password_in_vault, with
an unlocked stand-in vault backend.
"""

from __future__ import annotations

BD_GATE_SCOPE = "module"

import pytest

ACK = "captcha_egress_disclosure_ack"


class _Vault:
    name = "master_password"

    def __init__(self, fail=None):
        self.stored = {}
        self.fail = fail

    def is_unlocked(self):
        return True

    def set(self, key, value):
        if self.fail is not None:
            raise self.fail
        self.stored[key] = value

    def get(self, key):
        return self.stored.get(key)


@pytest.fixture
def site(fresh_app, monkeypatch):
    from bulk_downloader import secrets_store as ss

    vault = _Vault()
    monkeypatch.setattr(ss, "get_backend", lambda: vault)
    r = fresh_app.post(
        "/api/sites",
        json={"name": "dss-site-secrets", "login_url": "https://dss.example/login"},
    )
    assert r.status_code == 200
    return fresh_app, r.get_json()["id"], vault


def _cfg(sid):
    from bulk_downloader import app as app_module

    return app_module.s_cfg[sid]


def test_control_password_put_stores_and_says_so(site):
    client, sid, vault = site
    r = client.put(f"/api/sites/{sid}", json={"password": "FIRST-PW"})
    body = r.get_json()
    assert r.status_code == 200 and body.get("ok") is True
    assert body.get("cred_stored") is True, body
    assert "FIRST-PW" in vault.stored.values()


def test_failed_vault_store_is_reported_not_ok_silent(site):
    client, sid, vault = site
    client.put(f"/api/sites/{sid}", json={"password": "OLD-PW"})
    ref_before = _cfg(sid).get("password")
    vault.fail = OSError(28, "No space left on device")
    r = client.put(f"/api/sites/{sid}", json={"password": "NEW-PW"})
    body = r.get_json()
    assert body.get("cred_stored") is False and "No space left" in body.get(
        "cred_error", ""
    ), f"dss F2: vault store failed but the PUT reply hides it: {r.status_code} {body}"
    assert _cfg(sid).get("password") == ref_before
    assert "NEW-PW" not in vault.stored.values()


def test_refused_cred_ref_is_reported(site):
    client, sid, _vault = site
    r = client.put(f"/api/sites/{sid}", json={"password": "@cred:bulkdl-site-nosuch"})
    body = r.get_json()
    assert body.get("cred_stored") is False and "cred-ref refused" in body.get(
        "cred_error", ""
    ), f"dss F2: refused @cred: reference reported as plain ok: {r.status_code} {body}"


def test_control_blank_password_keeps_existing_and_sets_no_flag(site):
    client, sid, _vault = site
    client.put(f"/api/sites/{sid}", json={"password": "KEEP-PW"})
    ref = _cfg(sid).get("password")
    r = client.put(f"/api/sites/{sid}", json={"password": "", "name": "renamed"})
    body = r.get_json()
    assert (
        r.status_code == 200 and "cred_stored" not in body and "cred_error" not in body
    )
    assert _cfg(sid).get("password") == ref


@pytest.mark.parametrize(
    "field,value",
    [
        ("captcha_api_key", ["k"]),
        ("qb_password", {"a": 1}),
        ("plex_token", 123),
        ("stash_api_key", True),
        ("jellyfin_api_key", ["j"]),
        ("ha_token", 7),
        ("tpdb_api_key", {"t": 1}),
    ],
)
def test_non_string_secret_is_refused_and_not_stored(site, field, value):
    client, sid, _vault = site
    before = _cfg(sid).get(field)
    r = client.put(f"/api/sites/{sid}", json={field: value, ACK: True})
    assert r.status_code == 400, (
        f"dss F5: {field}={value!r} was accepted: {r.status_code} {r.get_json()}"
    )
    assert field in r.get_json()["error"]
    assert _cfg(sid).get(field) == before


@pytest.mark.parametrize("field", ["captcha_api_key", "plex_token", "tpdb_api_key"])
def test_control_string_and_blank_secret_accepted(site, field):
    client, sid, _vault = site
    r = client.put(f"/api/sites/{sid}", json={field: "SECRET-STR", ACK: True})
    assert r.status_code == 200, r.get_json()
    assert _cfg(sid).get(field) == "SECRET-STR"
    r = client.put(f"/api/sites/{sid}", json={field: "", ACK: True})
    assert r.status_code == 200 and _cfg(sid).get(field) == "SECRET-STR"


def test_captcha_stats_still_answers_after_a_list_key_put(site):
    client, sid, _vault = site
    client.put(f"/api/sites/{sid}", json={"captcha_api_key": ["k"], ACK: True})
    r = client.get(f"/api/sites/{sid}/captcha/stats")
    assert r.status_code == 200, (
        f"dss F5: captcha/stats after a list-key PUT -> {r.status_code} {r.get_json()}"
    )
