"""O1671 a07: the Flask app had no secret_key, so SSO login was a 500.

app_oidc.api_oidc_login stores the OIDC state/nonce in the Flask session. With
app.secret_key None, Flask hands out a NullSession and the first write raises
"The session is unavailable because no secret key was set" -> HTTP 500.

app._install_secret_key (run first in boot_once, which downloader_ui calls
before it serves) sets the key from BD_SECRET_KEY. With no key and OIDC enabled
it refuses to start; with OIDC disabled the app starts keyless as before. A
login attempt while the app has no key (OIDC switched on after boot) answers
503 with the reason instead of crashing.
"""
from __future__ import annotations

import pytest

import bulk_downloader.oidc as oidc

BD_GATE_SCOPE = "module"

_KEY = "k" * 64


@pytest.fixture
def app_mod(monkeypatch):
    from bulk_downloader import app as a
    monkeypatch.delenv("BD_SECRET_KEY", raising=False)
    monkeypatch.setattr(a.app, "secret_key", None)
    monkeypatch.setitem(a.app.config, "PROPAGATE_EXCEPTIONS", False)
    return a


def _install(mod):
    # Absent on the unfixed tree: nothing at startup sets a key, which is the bug.
    getattr(mod, "_install_secret_key", lambda: None)()


def _oidc_on(monkeypatch):
    monkeypatch.setattr(oidc, "is_enabled", lambda: True)
    monkeypatch.setattr(
        oidc, "build_authorize_url",
        lambda *, state, nonce, **_k: f"https://idp.example.test/authorize?state={state}")


def test_startup_takes_the_session_key_from_bd_secret_key(app_mod, monkeypatch):
    monkeypatch.setenv("BD_SECRET_KEY", _KEY)
    _install(app_mod)
    assert app_mod.app.secret_key == _KEY, (
        f"app.secret_key={app_mod.app.secret_key!r} after startup with BD_SECRET_KEY set")


def test_oidc_login_redirects_and_keeps_state_in_the_session(app_mod, monkeypatch):
    _oidc_on(monkeypatch)
    monkeypatch.setenv("BD_SECRET_KEY", _KEY)
    _install(app_mod)
    with app_mod.app.test_client() as c:
        r = c.get("/api/auth/oidc/login")
        assert r.status_code == 302, f"OIDC login -> HTTP {r.status_code}, want 302"
        with c.session_transaction() as s:
            state = s.get("oidc_state")
        assert state and r.headers["Location"].endswith(f"state={state}")


def test_startup_refuses_when_oidc_is_on_and_no_key_is_set(app_mod, monkeypatch):
    _oidc_on(monkeypatch)
    with pytest.raises(RuntimeError, match="BD_SECRET_KEY"):
        _install(app_mod)
    assert app_mod.app.secret_key is None


def test_startup_without_oidc_and_without_key_is_unchanged(app_mod, monkeypatch):
    monkeypatch.setattr(oidc, "is_enabled", lambda: False)
    _install(app_mod)
    assert app_mod.app.secret_key is None


def test_login_without_a_key_is_a_503_with_the_reason_not_a_500(app_mod, monkeypatch):
    # Boot with OIDC off (keyless start is allowed), then switch OIDC on: the
    # route must refuse with the reason, not crash on the session write.
    monkeypatch.setattr(oidc, "is_enabled", lambda: False)
    client = app_mod.app.test_client()
    assert client.get("/api/auth/oidc/status").status_code == 200
    _oidc_on(monkeypatch)
    r = client.get("/api/auth/oidc/login")
    assert r.status_code == 503, f"keyless OIDC login -> HTTP {r.status_code}, want 503"
    assert "BD_SECRET_KEY" in r.get_json()["error"]


def test_boot_refuses_before_serving_when_oidc_is_on_and_no_key_is_set(app_mod, monkeypatch):
    # downloader_ui calls boot_once() before it serves, so this is the startup gate.
    _oidc_on(monkeypatch)
    with pytest.raises(RuntimeError, match="BD_SECRET_KEY"):
        app_mod.boot_once(force=True)
