"""v3.66.141 — canonical browser-backend resolution (cloak.resolve_backend).

Pure decision logic, no browser launch: every flow now resolves its backend
through cloak.resolve_backend, configurable as "cloakbrowser" | "playwright"
via per-call config / env / global Settings, with availability downgrade.
"""
import json
from pathlib import Path

import pytest

from bulk_downloader import cloak

DEFAULTS = __import__(
    "bulk_downloader.app_kernel", fromlist=["DEFAULTS"]).DEFAULTS


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


def _runner_launch(
        config, tmp_path, monkeypatch, *, use_persistent=True,
        fail_first=False, cookie_loads=None):
    _isolate(monkeypatch, available=True)
    runner_browser = __import__(
        "bulk_downloader.runner_browser", fromlist=["BrowserMixin"])
    monkeypatch.setattr(runner_browser, "_VPN_RUNTIME_AVAILABLE", False)
    calls = []
    if cookie_loads is None:
        cookie_loads = []

    class Context:
        pages = []

        def __init__(self):
            self.cookie_batches = []

        def add_cookies(self, cookies):
            self.cookie_batches.append(cookies)

    class Browser:
        pass

    def record(boundary, config, kwargs):
        backend = cloak.resolve_backend(config)
        effective = dict(kwargs)
        if backend == cloak.CLOAKBROWSER:
            effective.pop("channel", None)
        calls.append({
            "boundary": boundary,
            "backend": backend,
            "channel": effective.get("channel"),
            "config": dict(config),
            "user_data_dir": effective.get("user_data_dir"),
        })
        if fail_first and len(calls) == 1:
            raise RuntimeError("forced first launch failure")
        return backend

    def open_persistent_context(*, user_data_dir, config, **kwargs):
        backend = record(
            "open_persistent_context", config,
            {"user_data_dir": user_data_dir, **kwargs})
        context = Context()
        calls[-1]["context"] = context
        return context, None, backend

    def launch_browser(*, config, **kwargs):
        backend = record("launch_browser", config, kwargs)
        return Browser(), None, backend

    monkeypatch.setattr(cloak, "open_persistent_context", open_persistent_context)
    monkeypatch.setattr(cloak, "launch_browser", launch_browser)

    class Runner(runner_browser.BrowserMixin):
        site_id = "vip4k"

        def __init__(self):
            self.config = dict(config)
            self.cookies = []
            self.stealth_installs = 0

        def set_cookies_from_file(self, path):
            cookie_loads.append(path)
            try:
                raw = json.loads(Path(path).read_text(encoding="utf-8"))
                self.cookies = raw if isinstance(raw, list) else []
                return True, f"Loaded {len(self.cookies)} cookies"
            except Exception as exc:
                return False, str(exc)

        def _profile_dir(self, worker_idx=None):
            return str(tmp_path / "profile")

        def _install_stealth(self, context):
            assert isinstance(context, Context)
            self.stealth_installs += 1

    runner = Runner()
    assert cloak.resolve_backend(runner.config) == cloak.CLOAKBROWSER, (
        "precondition: fixture did not select the conflicting cloak backend")
    _browser, _context, _pw, backend = runner._launch_browser(
        headless=True, use_persistent=use_persistent)
    expected_stealth = 1 if use_persistent else 0
    assert runner.stealth_installs == expected_stealth, (
        "precondition: selected browser path did not complete")
    expected_calls = 2 if fail_first else 1
    assert len(calls) == expected_calls, (
        f"precondition: expected {expected_calls} launch boundary calls, "
        f"got {len(calls)}")
    assert backend == calls[-1]["backend"], (
        "precondition: returned backend did not describe the completed launch")
    return backend, calls


def test_explicit_backend_survives_real_chrome_request(
        tmp_path, monkeypatch):
    backend, calls = _runner_launch({
        "browser_backend": "cloakbrowser",
        "use_real_chrome": True,
        "use_persistent_profile": True,
    }, tmp_path, monkeypatch)

    assert backend == cloak.CLOAKBROWSER, (
        f"explicit cloakbrowser operator choice resolved to {backend!r}")
    launch = calls[0]
    assert launch["channel"] is None


def test_configured_real_chrome_selects_playwright_channel(
        tmp_path, monkeypatch):
    backend, calls = _runner_launch({
        "use_real_chrome": True,
        "use_persistent_profile": True,
    }, tmp_path, monkeypatch)

    assert backend == cloak.PLAYWRIGHT, (
        f"use_real_chrome configured but launch resolved {backend!r}")
    launch = calls[0]
    assert launch["boundary"] == "open_persistent_context"
    assert launch["channel"] == "chrome"
    assert launch["user_data_dir"] == str(tmp_path / "profile")


def test_configured_real_chrome_persistent_retry_keeps_playwright(
        tmp_path, monkeypatch):
    backend, calls = _runner_launch({
        "use_real_chrome": True,
        "use_persistent_profile": True,
    }, tmp_path, monkeypatch, fail_first=True)

    assert [call["boundary"] for call in calls] == [
        "open_persistent_context", "open_persistent_context"]
    assert [call["backend"] for call in calls] == [
        cloak.PLAYWRIGHT, cloak.PLAYWRIGHT]
    assert [call["channel"] for call in calls] == ["chrome", None]
    assert backend == cloak.PLAYWRIGHT


def test_configured_real_chrome_nonpersistent_reaches_playwright_channel(
        tmp_path, monkeypatch):
    backend, calls = _runner_launch({
        "use_real_chrome": True,
        "use_persistent_profile": False,
    }, tmp_path, monkeypatch, use_persistent=False)

    assert len(calls) == 1
    assert calls[0]["boundary"] == "launch_browser"
    assert calls[0]["backend"] == cloak.PLAYWRIGHT
    assert calls[0]["channel"] == "chrome"
    assert backend == cloak.PLAYWRIGHT


def test_configured_real_chrome_nonpersistent_retry_keeps_playwright(
        tmp_path, monkeypatch):
    backend, calls = _runner_launch({
        "use_real_chrome": True,
        "use_persistent_profile": False,
    }, tmp_path, monkeypatch, use_persistent=False, fail_first=True)

    assert [call["boundary"] for call in calls] == [
        "launch_browser", "launch_browser"]
    assert [call["backend"] for call in calls] == [
        cloak.PLAYWRIGHT, cloak.PLAYWRIGHT]
    assert [call["channel"] for call in calls] == ["chrome", None]
    assert backend == cloak.PLAYWRIGHT


def test_app_default_population_keeps_base_launch_behavior(
        tmp_path, monkeypatch):
    assert DEFAULTS["use_real_chrome"] is False, (
        "precondition: app default still silently requests real Chrome")

    configs = ({}, dict(DEFAULTS))
    assert len(configs) == 2, "precondition: default populations are missing"
    results = [
        _runner_launch(config, tmp_path, monkeypatch)
        for config in configs
    ]

    assert [backend for backend, _calls in results] == [
        cloak.CLOAKBROWSER, cloak.CLOAKBROWSER]
    assert [calls[0]["channel"] for _backend, calls in results] == [None, None]


def test_disabled_real_chrome_preserves_cloakbrowser(
        tmp_path, monkeypatch):
    backend, calls = _runner_launch({
        "browser_backend": "cloakbrowser",
        "use_real_chrome": False,
        "use_persistent_profile": True,
    }, tmp_path, monkeypatch)

    assert backend == cloak.CLOAKBROWSER
    launch = calls[0]
    assert launch["channel"] is None


def _write_imported_cookie_jar(tmp_path):
    # Documented zero-entropy fixture values; these are not credentials.
    cookies = [
        {"name": "test-session-a", "value": "cookie-fixture-not-a-secret",
         "domain": "example.invalid", "path": "/", "sameSite": "Lax",
         "secure": True, "httpOnly": True},
        {"name": "test-session-b", "value": "cookie-fixture-not-a-secret",
         "domain": ".example.invalid", "path": "/member",
         "sameSite": "Strict", "secure": False, "httpOnly": False},
    ]
    cookie_file = tmp_path / "imported-cookies.json"
    cookie_file.write_text(json.dumps(cookies), encoding="utf-8")
    assert cookie_file.is_file()
    assert cookie_file.stat().st_size > 0
    assert json.loads(cookie_file.read_text(encoding="utf-8")) == cookies
    assert len(cookies) == 2
    return cookie_file, cookies


@pytest.mark.parametrize(("fail_first", "expected_launches"), [
    (False, 1),
    (True, 2),
])
def test_persistent_context_applies_imported_cookie_file(
        tmp_path, monkeypatch, fail_first, expected_launches):
    cookie_file, expected_cookies = _write_imported_cookie_jar(tmp_path)
    cookie_loads = []

    backend, calls = _runner_launch({
        "cookie_file": str(cookie_file),
        "use_real_chrome": True,
        "use_persistent_profile": True,
    }, tmp_path, monkeypatch, fail_first=fail_first,
        cookie_loads=cookie_loads)

    assert backend == cloak.PLAYWRIGHT
    assert len(calls) == expected_launches
    assert calls[-1]["boundary"] == "open_persistent_context"
    assert cookie_loads == [str(cookie_file)]
    assert calls[-1]["context"].cookie_batches == [expected_cookies]


def test_persistent_context_without_cookie_file_does_not_load_cookies(
        tmp_path, monkeypatch):
    cookie_loads = []
    backend, calls = _runner_launch({
        "browser_backend": "cloakbrowser",
        "use_persistent_profile": True,
    }, tmp_path, monkeypatch, cookie_loads=cookie_loads)

    assert backend == cloak.CLOAKBROWSER
    assert len(calls) == 1
    assert cookie_loads == []
    assert calls[0]["context"].cookie_batches == []


@pytest.mark.parametrize("kind", ["missing", "empty"])
def test_persistent_context_ignores_missing_or_empty_cookie_file(
        tmp_path, monkeypatch, kind):
    cookie_file = tmp_path / f"{kind}-cookies.json"
    if kind == "empty":
        cookie_file.write_text("", encoding="utf-8")
    assert cookie_file.exists() is (kind == "empty")
    assert not cookie_file.exists() or cookie_file.stat().st_size == 0

    cookie_loads = []
    backend, calls = _runner_launch({
        "browser_backend": "cloakbrowser",
        "cookie_file": str(cookie_file),
        "use_persistent_profile": True,
    }, tmp_path, monkeypatch, cookie_loads=cookie_loads)

    assert backend == cloak.CLOAKBROWSER
    assert len(calls) == 1
    assert cookie_loads == []
    assert calls[0]["context"].cookie_batches == []


def test_persistent_context_keeps_context_when_cookie_file_is_unusable(
        tmp_path, monkeypatch):
    cookie_file = tmp_path / "malformed-cookies.json"
    cookie_file.write_text("not-json", encoding="utf-8")
    assert cookie_file.is_file()
    assert cookie_file.stat().st_size == len("not-json")

    cookie_loads = []
    backend, calls = _runner_launch({
        "browser_backend": "cloakbrowser",
        "cookie_file": str(cookie_file),
        "use_persistent_profile": True,
    }, tmp_path, monkeypatch, cookie_loads=cookie_loads)

    assert backend == cloak.CLOAKBROWSER
    assert len(calls) == 1
    assert calls[0]["boundary"] == "open_persistent_context"
    assert cookie_loads == [str(cookie_file)]
    assert calls[0]["context"].cookie_batches == []


def test_cookie_file_does_not_change_nonpersistent_launch(
        tmp_path, monkeypatch):
    cookie_file, _expected_cookies = _write_imported_cookie_jar(tmp_path)
    loads = []
    backend, calls = _runner_launch({
        "browser_backend": "cloakbrowser",
        "cookie_file": str(cookie_file),
        "use_persistent_profile": False,
    }, tmp_path, monkeypatch, use_persistent=False, cookie_loads=loads)

    assert backend == cloak.CLOAKBROWSER
    assert len(calls) == 1
    assert calls[0]["boundary"] == "launch_browser"
    assert "context" not in calls[0]
    assert loads == []
