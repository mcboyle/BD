"""O1698 oidc-username-exact: the OIDC claim that names the BD account is used EXACTLY.

claims_to_username() used to .strip() the chosen claim (preferred_username, else email, else sub), and
provision_user() returns an EXISTING account unchanged -- so an IdP-issued preferred_username "admin " logged in
as BD user "admin". Now a chosen claim with surrounding whitespace or a control character raises, the callback
answers sso_error, and no user or session is created. Exact claims provision byte-for-byte as before.
"""
import pytest

import bulk_downloader.oidc as oidc

BD_GATE_SCOPE = "module"

PADDED = {
    "preferred_username": ["admin ", "admin\t", " admin", "admin\n", "ad\x00min", "admin "],
    "email": ["a@b.c ", "\ta@b.c"],
    "sub": [" sub-1", "sub-1\r"],
}
CASES = [(k, v) for k, vs in PADDED.items() for v in vs]
EXACT = [({"preferred_username": "admin", "email": "x@y.z", "sub": "s"}, "admin"),
         ({"email": "a@b.c", "sub": "s"}, "a@b.c"),
         ({"sub": "sub-1"}, "sub-1")]


@pytest.fixture
def accounts(monkeypatch):
    """user_accounts doubles: an existing BD user 'admin'; records every create / session issue."""
    import bulk_downloader.user_accounts as ua
    users = {"admin": {"username": "admin", "role": "admin"}}
    calls = {"create": [], "session": []}
    monkeypatch.setattr(ua, "get_user", lambda u, *a, **k: users.get(u))

    def _create(u, pw, role="operator", *a, **k):
        calls["create"].append(u)
        users[u] = {"username": u, "role": role}
        return True, "ok"

    def _issue(u, *a, **k):
        calls["session"].append(u)
        return "tok-" + u

    monkeypatch.setattr(ua, "create_user", _create)
    monkeypatch.setattr(ua, "issue_session", _issue)
    return users, calls


@pytest.mark.parametrize("claim,value", CASES, ids=[f"{k}={v!r}" for k, v in CASES])
def test_padded_or_control_claim_is_refused_not_normalised(accounts, claim, value):
    users, calls = accounts
    with pytest.raises(ValueError, match="not exact"):
        oidc.claims_to_username({claim: value})
    with pytest.raises(ValueError):
        oidc.provision_user({claim: value})
    assert calls["create"] == [] and set(users) == {"admin"}


def test_padded_admin_never_returns_the_existing_admin(accounts):
    users, calls = accounts
    for value in PADDED["preferred_username"]:
        with pytest.raises(ValueError):
            assert oidc.provision_user({"preferred_username": value}) != "admin"
    assert users["admin"]["role"] == "admin" and calls["create"] == []


@pytest.mark.parametrize("claims,want", EXACT, ids=["preferred_username", "email", "sub"])
def test_exact_claims_provision_byte_equal(accounts, claims, want):
    users, calls = accounts
    got = oidc.claims_to_username(claims)
    assert got == want and type(got) is str
    assert oidc.provision_user(claims) == want
    assert calls["create"] == ([] if want == "admin" else [want])   # existing admin is reused, others created


def test_non_string_claim_is_refused(accounts):
    with pytest.raises(ValueError, match="not a string"):
        oidc.claims_to_username({"sub": 12345})


# ---- the callback: sso_error, and no user / session for a padded claim ---------------------------------------------------
def _callback(monkeypatch, claims):
    from bulk_downloader import app as a
    monkeypatch.setattr(oidc, "exchange_code", lambda code: {"id_token": "fixture"})
    monkeypatch.setattr(oidc, "verify_id_token", lambda tok, nonce=None: dict(claims))
    monkeypatch.setattr(a.app, "secret_key", "o1698-fixture")   # the callback reads oidc_state from the Flask session
    client = a.app.test_client()
    with client.session_transaction() as s:
        s["oidc_state"] = "st-1"
        s["oidc_nonce"] = "n-1"
    return client.get("/api/auth/oidc/callback?code=c-1&state=st-1")


@pytest.mark.parametrize("claim,value", [("preferred_username", "admin "), ("email", "a@b.c "), ("sub", " sub-1")])
def test_callback_padded_claim_is_sso_error_with_no_session(monkeypatch, accounts, claim, value):
    users, calls = accounts
    r = _callback(monkeypatch, {claim: value})
    assert r.status_code == 302 and r.headers["Location"].endswith("/?sso_error=exchange"), r.headers.get("Location")
    assert calls["session"] == [] and calls["create"] == [] and set(users) == {"admin"}
    assert "tok-" not in r.headers.get("Set-Cookie", "")


def test_callback_exact_admin_still_logs_in(monkeypatch, accounts):
    users, calls = accounts
    r = _callback(monkeypatch, {"preferred_username": "admin"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/") and "sso_error" not in r.headers["Location"]
    assert calls["session"] == ["admin"] and "tok-admin" in r.headers.get("Set-Cookie", "")
