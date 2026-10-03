"""O1671 a07 (AUDIT-07 HIGH SEC): OIDC logins are bound to the verified
(iss, sub) pair, never to the self-asserted username claim.

On d80dd76 provision_user mapped claims -> username (preferred_username
first) and reused ANY existing local user of that name, so an IdP account
that set preferred_username=admin got the local admin's session. Now the
first login records (iss, sub) on the user it creates; a later login reuses
only a user bound to the same pair; any other name collision is refused and
the callback redirects with sso_error instead of issuing a session.
"""
import pytest

from bulk_downloader import oidc
import bulk_downloader.user_accounts as ua

BD_GATE_SCOPE = "module"
ISS = "https://idp.example.test"


@pytest.fixture
def store(tmp_path, monkeypatch):
    # user_accounts keeps its store in the cwd when no base_dir is passed,
    # which is how provision_user and the callback call it.
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _claims(sub, name, iss=ISS):
    return {"iss": iss, "sub": sub, "preferred_username": name}


def test_asserted_username_cannot_take_over_local_admin(store):
    assert ua.create_user("admin", "local-pw", role="admin") == (True, "created")
    with pytest.raises(ValueError, match="OIDC_ACCOUNT_BINDING_REFUSED"):
        oidc.provision_user(_claims("attacker", "admin"))
    assert ua.get_user("admin")["role"] == "admin"
    assert ua.get_oidc_binding("admin") is None
    assert ua.verify_password("admin", "local-pw")


def test_first_login_binds_and_same_subject_reuses(store):
    assert oidc.provision_user(_claims("sub-carol", "carol")) == "carol"
    assert ua.get_user("carol")["role"] == "operator"
    assert ua.get_oidc_binding("carol") == (ISS, "sub-carol")
    ua.set_role("carol", "admin")
    # trailing slash: verify_id_token compares iss with rstrip("/")
    assert oidc.provision_user(_claims("sub-carol", "carol", iss=ISS + "/")) == "carol"
    assert ua.get_user("carol")["role"] == "admin"
    assert ua.count() == 1


@pytest.mark.parametrize("claims", [
    _claims("sub-other", "carol"),
    _claims("sub-carol", "carol", iss="https://other-idp.example.test"),
])
def test_name_collision_with_differently_bound_user_refused(store, claims):
    oidc.provision_user(_claims("sub-carol", "carol"))
    with pytest.raises(ValueError, match="OIDC_ACCOUNT_BINDING_REFUSED"):
        oidc.provision_user(claims)
    assert ua.get_oidc_binding("carol") == (ISS, "sub-carol")


@pytest.mark.parametrize("sub", ["sub-carol ", " sub-carol", "SUB-CAROL", "sub-carol\n"])
def test_subject_is_compared_exactly(store, sub):
    # lens REFUTE F1: a trimmed/case-folded sub must not reuse another subject's account
    oidc.provision_user(_claims("sub-carol", "carol"))
    with pytest.raises(ValueError, match="OIDC_ACCOUNT_BINDING_REFUSED"):
        oidc.provision_user(_claims(sub, "carol"))
    assert ua.get_oidc_binding("carol") == (ISS, "sub-carol")


def test_failed_create_is_refused_not_returned(store):
    # "a@b" fails user_accounts' name rule: no account, so no name may be handed back
    with pytest.raises(ValueError, match="could not provision"):
        oidc.provision_user({"iss": ISS, "sub": "sub-mail", "email": "a@b.example"})
    assert ua.get_user("a@b.example") is None and ua.count() == 0


def test_claims_without_iss_or_sub_refused(store):
    with pytest.raises(ValueError):
        oidc.provision_user({"iss": ISS, "preferred_username": "dave"})
    with pytest.raises(ValueError):
        oidc.provision_user({"sub": "sub-dave", "preferred_username": "dave"})
    with pytest.raises(ValueError):
        oidc.provision_user({"iss": ISS, "sub": 123, "preferred_username": "dave"})
    assert ua.get_user("dave") is None


def test_callback_refuses_hijack_and_issues_session_to_bound_user(store, monkeypatch):
    from bulk_downloader import app as a
    # The app sets no secret_key on d80dd76 (live login 500s on session[...]),
    # so give the test app one; this spec is about who gets a session.
    monkeypatch.setattr(a.app, "secret_key", "o1671-a07-test")
    ua.create_user("admin", "local-pw", role="admin")
    current = {}
    monkeypatch.setattr(oidc, "exchange_code", lambda code: {"id_token": "t"})
    monkeypatch.setattr(oidc, "verify_id_token",
                        lambda tok, nonce=None, disco=None: dict(current))

    def callback():
        c = a.app.test_client()
        with c.session_transaction() as s:
            s["oidc_state"], s["oidc_nonce"] = "st", "n"
        return c.get("/api/auth/oidc/callback?code=c&state=st")

    current.update(_claims("attacker", "admin"))
    r = callback()
    assert r.status_code == 302 and "sso_error=" in r.headers["Location"]
    assert "bd_user=" not in r.headers.get("Set-Cookie", "")

    current.clear()
    current.update(_claims("sub-erin", "erin"))
    r = callback()
    assert r.status_code == 302 and "sso_error" not in r.headers["Location"]
    assert "bd_user=" in r.headers.get("Set-Cookie", "")
