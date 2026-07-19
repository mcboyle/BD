"""v3.66.681 (B2/P6): OIDC / SSO login.

Covers the pure surface (config, is_enabled, authorize-URL construction, claims
-> username, user provisioning) and the status/login routes. Live token
exchange + JWKS id_token verification are operator-side (need a real IdP).
"""
import pytest
import bulk_downloader.oidc as oidc


_DISCO = {
    "authorization_endpoint": "https://idp.example.test/authorize",
    "token_endpoint": "https://idp.example.test/token",
    "jwks_uri": "https://idp.example.test/jwks",
}


def _cfg(**over):
    base = {"oidc_enabled": True, "oidc_issuer": "https://idp.example.test",
            "oidc_client_id": "bd-client", "oidc_client_secret": "shh",
            "oidc_redirect_uri": "https://bd.example.test/api/auth/oidc/callback",
            "oidc_scopes": "openid email profile"}
    base.update(over)
    return base


def test_is_enabled_requires_issuer_and_client(monkeypatch):
    monkeypatch.setattr(oidc, "oidc_config",
                        lambda: {"enabled": True, "issuer": "", "client_id": "x",
                                 "client_secret": "", "redirect_uri": "", "scopes": ""})
    assert oidc.is_enabled() is False
    monkeypatch.setattr(oidc, "oidc_config",
                        lambda: {"enabled": True, "issuer": "https://i", "client_id": "x",
                                 "client_secret": "", "redirect_uri": "", "scopes": ""})
    assert oidc.is_enabled() is True
    monkeypatch.setattr(oidc, "oidc_config",
                        lambda: {"enabled": False, "issuer": "https://i", "client_id": "x",
                                 "client_secret": "", "redirect_uri": "", "scopes": ""})
    assert oidc.is_enabled() is False


def test_oidc_config_reads_store(monkeypatch):
    import bulk_downloader.global_config as gc
    monkeypatch.setattr(gc, "get_config", lambda: _cfg())
    c = oidc.oidc_config()
    assert c["issuer"] == "https://idp.example.test"
    assert c["client_id"] == "bd-client"
    assert c["enabled"] is True


def test_build_authorize_url(monkeypatch):
    import bulk_downloader.global_config as gc
    monkeypatch.setattr(gc, "get_config", lambda: _cfg())
    url = oidc.build_authorize_url(state="ST", nonce="NO", disco=_DISCO)
    assert url.startswith("https://idp.example.test/authorize?")
    for frag in ("client_id=bd-client", "state=ST", "nonce=NO",
                 "response_type=code", "scope=openid"):
        assert frag in url
    assert "redirect_uri=https%3A%2F%2Fbd.example.test" in url


def test_claims_to_username_precedence():
    assert oidc.claims_to_username({"preferred_username": "alice", "email": "a@x", "sub": "s"}) == "alice"
    assert oidc.claims_to_username({"email": "bob@x", "sub": "s"}) == "bob@x"
    assert oidc.claims_to_username({"sub": "opaque-123"}) == "opaque-123"
    assert oidc.claims_to_username({}) == ""


def test_provision_user_creates_then_reuses(monkeypatch):
    created = {}
    import bulk_downloader.user_accounts as ua
    monkeypatch.setattr(ua, "get_user", lambda u, *a, **k: created.get(u))
    def _create(u, pw, role="operator", *a, **k):
        created[u] = {"username": u, "role": role}
        return True, "ok"
    monkeypatch.setattr(ua, "create_user", _create)
    name = oidc.provision_user({"preferred_username": "carol"})
    assert name == "carol" and "carol" in created
    # second time: get_user returns the user -> no re-create (would overwrite role)
    created["carol"]["role"] = "admin"
    oidc.provision_user({"preferred_username": "carol"})
    assert created["carol"]["role"] == "admin"


def test_provision_user_no_claim_raises():
    with pytest.raises(ValueError):
        oidc.provision_user({})


# ── routes ──
def test_status_route(monkeypatch):
    from bulk_downloader import app as a
    monkeypatch.setattr(oidc, "is_enabled", lambda: False)
    r = a.app.test_client().get("/api/auth/oidc/status")
    assert r.status_code == 200 and r.get_json()["enabled"] is False


def test_login_route_disabled_returns_400(monkeypatch):
    from bulk_downloader import app as a
    monkeypatch.setattr(oidc, "is_enabled", lambda: False)
    r = a.app.test_client().get("/api/auth/oidc/login")
    assert r.status_code == 400


def test_oidc_routes_registered():
    from bulk_downloader import app as a
    rules = {str(r.rule) for r in a.app.url_map.iter_rules()}
    for rule in ("/api/auth/oidc/status", "/api/auth/oidc/login", "/api/auth/oidc/callback"):
        assert rule in rules
