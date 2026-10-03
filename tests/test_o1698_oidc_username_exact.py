"""O1698 oidc-username-exact: the OIDC claim that names the BD account is used EXACTLY.

claims_to_username() used to .strip() the chosen claim (preferred_username, else email, else sub). Now a chosen claim with
surrounding whitespace or a control character raises, the callback answers sso_error, and no user or session is created.
Exact claims provision byte-for-byte as before.

r3 is cut on train208b, where O1671 a07 (accounts bound to (iss, sub)) is not present. Fixtures still carry iss + sub, and
every refusal asserts THIS cut's reason ("not exact"), so no test can pass on some other refusal.
"""
import secrets

import pytest

import bulk_downloader.oidc as oidc

BD_GATE_SCOPE = "module"

ISS = "https://idp.example.test"
ADMIN_SUB = "sub-admin"
PADDED = {
    "preferred_username": ["admin ", "admin\t", " admin", "admin\n", "ad\x00min", "admin "],
    "email": ["a@b.c ", "\ta@b.c"],
    "sub": [" sub-1", "sub-1\r"],
}
CASES = [(k, v) for k, vs in PADDED.items() for v in vs]
EXACT = [({"iss": ISS, "sub": ADMIN_SUB, "preferred_username": "admin", "email": "x@y.z"}, "admin", False),
         ({"iss": ISS, "sub": "sub-ab", "email": "a@b.c"}, "a@b.c", True),
         ({"iss": ISS, "sub": "sub-1"}, "sub-1", True)]


def _claims(claim, value, sub="sub-other"):
    """Claims for one padded value; the sub path pads sub itself, the others carry a distinct exact sub."""
    return {"iss": ISS, "sub": value} if claim == "sub" else {"iss": ISS, "sub": sub, claim: value}


@pytest.fixture
def accounts(monkeypatch):
    """user_accounts doubles: existing BD user 'admin' bound to (ISS, ADMIN_SUB); records every create / session issue."""
    import bulk_downloader.user_accounts as ua
    users = {"admin": {"username": "admin", "role": "admin"}}
    bindings = {"admin": (ISS, ADMIN_SUB)}
    calls = {"create": [], "session": []}
    monkeypatch.setattr(ua, "get_user", lambda u, *a, **k: users.get(u))
    monkeypatch.setattr(ua, "bind_oidc_login", lambda u, subject, *a, **k:
                        (True, "bound") if bindings.get(u) == subject else (False, "bound to another OIDC subject"))

    def _create(u, pw, role="operator", *a, oidc_binding=None, **k):
        calls["create"].append(u)
        users[u] = {"username": u, "role": role}
        bindings[u] = oidc_binding
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
    with pytest.raises(ValueError, match="not exact"):
        oidc.provision_user(_claims(claim, value))
    assert calls["create"] == [] and set(users) == {"admin"}


def test_padded_new_user_is_refused_not_created_under_the_stripped_name(accounts):
    users, calls = accounts
    with pytest.raises(ValueError, match="not exact"):
        oidc.provision_user({"iss": ISS, "sub": "sub-newbie", "preferred_username": "newbie "})
    assert calls["create"] == [] and "newbie" not in users


def test_padded_admin_is_refused_even_with_admins_own_subject(accounts):
    users, calls = accounts
    for value in PADDED["preferred_username"]:
        with pytest.raises(ValueError, match="not exact"):
            oidc.provision_user({"iss": ISS, "sub": ADMIN_SUB, "preferred_username": value})
    assert users["admin"]["role"] == "admin" and calls["create"] == []


@pytest.mark.parametrize("claims,want,created", EXACT, ids=["preferred_username", "email", "sub"])
def test_exact_claims_provision_byte_equal(accounts, claims, want, created):
    users, calls = accounts
    got = oidc.claims_to_username(claims)
    assert got == want and type(got) is str
    assert oidc.provision_user(claims) == want
    assert calls["create"] == ([want] if created else [])   # existing admin reused, others created under the exact name


def test_non_string_claim_is_refused(accounts):
    with pytest.raises(ValueError, match="not a string"):
        oidc.claims_to_username({"sub": 12345})


# ---- the callback: sso_error, and no user / session for a padded claim ---------------------------------------------------
def _callback(monkeypatch, claims):
    from bulk_downloader import app as a
    monkeypatch.setattr(oidc, "exchange_code", lambda code: {"id_token": "fixture"})
    monkeypatch.setattr(oidc, "verify_id_token", lambda tok, nonce=None: dict(claims))
    monkeypatch.setattr(a.app, "secret_key", secrets.token_hex(16))   # runtime value; the callback reads the Flask session
    client = a.app.test_client()
    with client.session_transaction() as s:
        s["oidc_state"] = "st-1"
        s["oidc_nonce"] = "n-1"
    return client.get("/api/auth/oidc/callback?code=c-1&state=st-1")


@pytest.mark.parametrize("claim,value", [("preferred_username", "newbie "), ("email", "a@b.c "), ("sub", " sub-1")])
def test_callback_padded_claim_is_sso_error_with_no_session(monkeypatch, accounts, claim, value):
    users, calls = accounts
    r = _callback(monkeypatch, _claims(claim, value))
    assert r.status_code == 302 and r.headers["Location"].endswith("/?sso_error=exchange"), r.headers.get("Location")
    assert calls["session"] == [] and calls["create"] == [] and set(users) == {"admin"}
    assert "tok-" not in r.headers.get("Set-Cookie", "")


def test_callback_exact_bound_admin_still_logs_in(monkeypatch, accounts):
    users, calls = accounts
    r = _callback(monkeypatch, {"iss": ISS, "sub": ADMIN_SUB, "preferred_username": "admin"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/") and "sso_error" not in r.headers["Location"]
    assert calls["session"] == ["admin"] and "tok-admin" in r.headers.get("Set-Cookie", "")
