"""v3.66.141 — canonical browser-backend resolution (cloak.resolve_backend).

Pure decision logic, no browser launch: every flow now resolves its backend
through cloak.resolve_backend, configurable as "cloakbrowser" | "playwright"
via per-call config / env / global Settings, with availability downgrade.
"""
from bulk_downloader import cloak


def _isolate(monkeypatch, *, available, glob=None):
    """Neutralize env + global layers and pin cloakbrowser availability."""
    for k in ("BD_BROWSER_BACKEND", "BD_USE_CLOAK",
              "BD_SESSION_KEEPER_USE_CLOAKBROWSER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(cloak, "is_available", lambda: available)
    glob = glob or {}
    import bulk_downloader.global_config as gc
    monkeypatch.setattr(gc, "get", lambda key, default=None: glob.get(key, default))


def test_default_prefers_cloakbrowser_when_available(monkeypatch):
    _isolate(monkeypatch, available=True)
    assert cloak.resolve_backend() == "cloakbrowser"
    assert cloak.use_cloak() is True


def test_default_falls_back_to_playwright_when_unavailable(monkeypatch):
    _isolate(monkeypatch, available=False)
    assert cloak.resolve_backend() == "playwright"
    assert cloak.use_cloak() is False


def test_percall_browser_backend_wins(monkeypatch):
    _isolate(monkeypatch, available=True)
    assert cloak.resolve_backend({"browser_backend": "playwright"}) == "playwright"
    assert cloak.resolve_backend({"browser_backend": "cloakbrowser"}) == "cloakbrowser"
    # alias + case-insensitivity
    assert cloak.resolve_backend({"browser_backend": "Cloak"}) == "cloakbrowser"
    assert cloak.resolve_backend({"browser_backend": "PW"}) == "playwright"


def test_legacy_bool_keys_still_honoured(monkeypatch):
    _isolate(monkeypatch, available=True)
    assert cloak.resolve_backend({"use_cloak": False}) == "playwright"
    assert cloak.resolve_backend({"use_cloak": True}) == "cloakbrowser"
    assert cloak.resolve_backend(
        {"session_keeper_use_cloakbrowser": False}) == "playwright"


def test_env_layer(monkeypatch):
    _isolate(monkeypatch, available=True)
    monkeypatch.setenv("BD_BROWSER_BACKEND", "playwright")
    assert cloak.resolve_backend() == "playwright"          # no config -> env wins
    # per-call config beats env
    assert cloak.resolve_backend({"browser_backend": "cloakbrowser"}) == "cloakbrowser"


def test_global_settings_layer(monkeypatch):
    _isolate(monkeypatch, available=True, glob={"browser_backend": "playwright"})
    assert cloak.resolve_backend() == "playwright"          # global wins over default
    # env beats global
    monkeypatch.setenv("BD_BROWSER_BACKEND", "cloakbrowser")
    assert cloak.resolve_backend() == "cloakbrowser"


def test_cloakbrowser_request_downgrades_when_unavailable(monkeypatch):
    _isolate(monkeypatch, available=False)
    # explicit request can't conjure a missing package
    assert cloak.resolve_backend({"browser_backend": "cloakbrowser"}) == "playwright"
    assert cloak.resolve_backend({"use_cloak": True}) == "playwright"
    # playwright request honoured
    assert cloak.resolve_backend({"browser_backend": "playwright"}) == "playwright"


def test_coerce_backend_values():
    c = cloak._coerce_backend
    assert c("cloakbrowser") == "cloakbrowser"
    assert c("playwright") == "playwright"
    assert c(True) == "cloakbrowser"
    assert c(False) == "playwright"
    assert c("1") == "cloakbrowser"
    assert c("off") == "playwright"
    assert c(None) is None
    assert c("nonsense") is None
