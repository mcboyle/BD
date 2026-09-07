"""test2D-1: persistent browser profiles receive an imported cookie jar."""
from __future__ import annotations

import importlib
import json

import pytest

cloak = importlib.import_module("bulk_downloader.cloak")
cookies = importlib.import_module("bulk_downloader.cookies")
runner_browser = importlib.import_module("bulk_downloader.runner_browser")


BD_GATE_SCOPE = "repo-wide"

# Documented zero-entropy cookie fixtures; these are not credentials.
_JAR = [
    {
        "name": f"fixture-cookie-{index}",
        "value": "zero-entropy-fixture",
        "domain": "example.invalid",
        "path": "/",
        "sameSite": "None",
        "secure": False,
        "httpOnly": False,
    }
    for index in range(10)
]


class _Context:
    pages = ()

    def __init__(self, cookie_apply_fails=False):
        self.cookie_batches = []
        self.cookie_apply_fails = cookie_apply_fails
        self.cookie_attempts = 0

    def add_cookies(self, batch):
        self.cookie_attempts += 1
        if self.cookie_apply_fails:
            raise RuntimeError("fixture cookie application failure")
        self.cookie_batches.append(batch)


class _Runner(runner_browser.BrowserMixin):
    site_id = "fixture-site"

    def __init__(self, config, profile_dir, imported_cookies=()):
        self.config = dict(config)
        self._fixture_profile_dir = profile_dir
        self.cookies = list(imported_cookies)
        self.stealth_installs = 0

    def _profile_dir(self, worker_idx=None):
        return str(self._fixture_profile_dir)

    def set_cookies_from_file(self, path):
        self.cookies = cookies.load_cookies_from_file(path)
        return True, "fixture cookies loaded"

    def _install_stealth(self, context):
        assert isinstance(context, _Context)
        self.stealth_installs += 1


def _write_jar(path):
    path.write_text(json.dumps(_JAR), encoding="utf-8")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert len(raw) == 10, "precondition: imported jar population drifted"
    assert path.stat().st_size > 0, "precondition: imported jar is empty"
    return path


def _launch(
        monkeypatch, tmp_path, config, *, persistent, fail_first=False,
        imported_cookies=(), cookie_apply_fails=False):
    monkeypatch.setattr(runner_browser, "_VPN_RUNTIME_AVAILABLE", False)
    contexts = []
    calls = {"persistent": 0, "nonpersistent": 0}
    browser = object()

    def open_persistent_context(**_kwargs):
        calls["persistent"] += 1
        if fail_first and calls["persistent"] == 1:
            raise RuntimeError("fixture first launch failure")
        context = _Context(cookie_apply_fails)
        contexts.append(context)
        return context, None, cloak.PLAYWRIGHT

    def launch_browser(**_kwargs):
        calls["nonpersistent"] += 1
        return browser, None, cloak.PLAYWRIGHT

    monkeypatch.setattr(cloak, "open_persistent_context", open_persistent_context)
    monkeypatch.setattr(cloak, "launch_browser", launch_browser)
    runner = _Runner(config, tmp_path / "profile", imported_cookies)
    result = runner._launch_browser(headless=True, use_persistent=persistent)
    return runner, result, contexts, calls, browser


def test_persistent_profile_applies_all_imported_cookies(tmp_path, monkeypatch):
    jar_path = _write_jar(tmp_path / "cookies.json")
    imported_cookies = cookies.load_cookies_from_file(str(jar_path))
    assert imported_cookies == _JAR, "precondition: jar was not loadable"
    runner, result, contexts, calls, _browser = _launch(
        monkeypatch,
        tmp_path,
        {"cookie_file": str(jar_path), "use_persistent_profile": True},
        persistent=True,
        imported_cookies=imported_cookies,
    )

    assert calls == {"persistent": 1, "nonpersistent": 0}
    assert len(contexts) == 1
    assert result[0] is None and result[1] is contexts[0]
    assert runner.stealth_installs == 1
    assert len(contexts[0].cookie_batches) == 1, (
        "persistent context did not receive the imported cookie jar")
    assert len(contexts[0].cookie_batches[0]) == 10
    assert contexts[0].cookie_batches[0] == _JAR


def test_persistent_fallback_also_applies_imported_cookies(
        tmp_path, monkeypatch):
    jar_path = _write_jar(tmp_path / "cookies.json")
    imported_cookies = cookies.load_cookies_from_file(str(jar_path))
    assert imported_cookies == _JAR, "precondition: jar was not loadable"
    runner, result, contexts, calls, _browser = _launch(
        monkeypatch,
        tmp_path,
        {
            "cookie_file": str(jar_path),
            "use_persistent_profile": True,
            "use_real_chrome": True,
        },
        persistent=True,
        fail_first=True,
        imported_cookies=imported_cookies,
    )

    assert calls == {"persistent": 2, "nonpersistent": 0}
    assert len(contexts) == 1
    assert result[0] is None and result[1] is contexts[0]
    assert runner.stealth_installs == 1
    assert len(contexts[0].cookie_batches) == 1
    assert len(contexts[0].cookie_batches[0]) == 10
    assert contexts[0].cookie_batches[0] == _JAR


@pytest.mark.parametrize("shape", ("unset", "missing", "empty"))
def test_persistent_profile_without_usable_cookie_file_is_unchanged(
        shape, tmp_path, monkeypatch):
    config = {"use_persistent_profile": True}
    jar_path = tmp_path / f"{shape}.json"
    if shape == "missing":
        config["cookie_file"] = str(jar_path)
        assert not jar_path.exists()
    elif shape == "empty":
        jar_path.write_text("", encoding="utf-8")
        config["cookie_file"] = str(jar_path)
        assert jar_path.is_file() and jar_path.stat().st_size == 0
    else:
        assert "cookie_file" not in config

    runner, result, contexts, calls, _browser = _launch(
        monkeypatch, tmp_path, config, persistent=True)

    assert calls == {"persistent": 1, "nonpersistent": 0}
    assert len(contexts) == 1
    assert result[0] is None and result[1] is contexts[0]
    assert runner.stealth_installs == 1
    assert contexts[0].cookie_batches == []


def test_nonpersistent_launch_does_not_read_the_cookie_file(
        tmp_path, monkeypatch):
    jar_path = _write_jar(tmp_path / "cookies.json")
    cookie_loads = []

    def record_cookie_load(path):
        cookie_loads.append(path)
        return list(_JAR)

    monkeypatch.setattr(cookies, "load_cookies_from_file", record_cookie_load)
    runner, result, contexts, calls, browser = _launch(
        monkeypatch,
        tmp_path,
        {"cookie_file": str(jar_path), "use_persistent_profile": False},
        persistent=False,
        imported_cookies=_JAR,
    )

    assert calls == {"persistent": 0, "nonpersistent": 1}
    assert contexts == []
    assert result == (browser, None, None, cloak.PLAYWRIGHT)
    assert runner.stealth_installs == 0
    assert cookie_loads == []


def test_cookie_application_failure_cannot_return_a_cookieless_context(
        tmp_path, monkeypatch):
    jar_path = _write_jar(tmp_path / "cookies.json")
    runner, result, contexts, calls, browser = _launch(
        monkeypatch,
        tmp_path,
        {"cookie_file": str(jar_path), "use_persistent_profile": True},
        persistent=True,
        imported_cookies=_JAR,
        cookie_apply_fails=True,
    )

    assert calls == {"persistent": 1, "nonpersistent": 1}
    assert len(contexts) == 1
    assert contexts[0].cookie_attempts == 1
    assert contexts[0].cookie_batches == []
    assert result == (browser, None, None, cloak.PLAYWRIGHT)
    assert runner.stealth_installs == 0


def test_transform_control_only_imports_the_browser_mixin():
    assert runner_browser.BrowserMixin.__name__ == "BrowserMixin"


class _OwnedContext(_Context):
    def __init__(self, events, label, *, fails=False, close_fails=False):
        super().__init__(fails)
        self.events, self.label = events, label
        self.close_fails = close_fails
        self.navigations = []

    def close(self):
        self.events.append(("close", self))
        if self.close_fails:
            raise RuntimeError("fixture context close failure")

    def new_page(self):
        return self

    def goto(self, url, **kwargs):
        self.navigations.append(url)


class _OwnedPlaywright:
    def __init__(self, events):
        self.events = events

    def stop(self):
        self.events.append(("stop", self))


@pytest.mark.parametrize("path", ["no-channel", "chrome", "bundled"])
@pytest.mark.parametrize("close_fails", [False, True])
def test_cookie_failure_releases_abandoned_pair(
        path, close_fails, tmp_path, monkeypatch):
    jar = _write_jar(tmp_path / "jar.json")
    monkeypatch.setattr(runner_browser, "_VPN_RUNTIME_AVAILABLE", False)
    events, pairs, launches = [], [], []
    fallback = object()

    def persistent(**kwargs):
        launches.append(kwargs.get("channel"))
        if path == "bundled" and len(launches) == 1:
            raise RuntimeError("fixture Chrome unavailable")
        ctx = _OwnedContext(events, len(pairs), fails=not pairs,
                            close_fails=close_fails)
        pw = _OwnedPlaywright(events)
        pairs.append((ctx, pw))
        return ctx, pw, cloak.PLAYWRIGHT

    def nonpersistent(**kwargs):
        launches.append("nonpersistent")
        return fallback, None, cloak.PLAYWRIGHT

    monkeypatch.setattr(cloak, "open_persistent_context", persistent)
    monkeypatch.setattr(cloak, "launch_browser", nonpersistent)
    runner = _Runner({"cookie_file": str(jar),
                      "use_real_chrome": path != "no-channel"}, tmp_path,
                     cookies.load_cookies_from_file(str(jar)))
    assert runner.cookies == _JAR
    result = runner._launch_browser(use_persistent=True)
    assert launches == {
        "no-channel": [None, "nonpersistent"],
        "chrome": ["chrome", None],
        "bundled": ["chrome", None, "nonpersistent"],
    }[path]
    abandoned, abandoned_pw = pairs[0]
    assert abandoned.cookie_attempts == 1
    assert abandoned.cookie_batches == []
    assert events == [("close", abandoned), ("stop", abandoned_pw)], (
        f"abandoned cleanup counts: close={events.count(('close', abandoned))}, "
        f"stop={events.count(('stop', abandoned_pw))}")
    if path == "chrome":
        assert len(pairs) == 2
        assert result == (None, pairs[1][0], pairs[1][1], cloak.PLAYWRIGHT)
        assert pairs[1][0].cookie_batches == [_JAR]
        assert pairs[1][0].cookie_attempts == 1
    else:
        assert len(pairs) == 1
        assert result == (fallback, None, None, cloak.PLAYWRIGHT)


def _caller_fixture(tmp_path, monkeypatch):
    """Real launcher and cookie loader; only browser engines are doubles."""
    events, contexts, engine_calls = [], [], []
    jar = _write_jar(tmp_path / "jar.json")
    runner = _Runner({"cookie_file": str(jar)}, tmp_path,
                     cookies.load_cookies_from_file(str(jar)))
    assert runner.cookies == _JAR
    monkeypatch.setattr(runner_browser, "_VPN_RUNTIME_AVAILABLE", False)

    class Browser:
        def new_context(self, **kwargs):
            ctx = _OwnedContext(events, "fresh")
            contexts.append(ctx)
            return ctx

        def close(self):
            events.append(("close", self))

    def persistent(**kwargs):
        engine_calls.append(("persistent", kwargs))
        ctx = _OwnedContext(events, "persistent")
        contexts.append(ctx)
        return ctx, _OwnedPlaywright(events), cloak.PLAYWRIGHT

    def nonpersistent(**kwargs):
        engine_calls.append(("nonpersistent", kwargs))
        return Browser(), _OwnedPlaywright(events), cloak.PLAYWRIGHT

    monkeypatch.setattr(cloak, "open_persistent_context", persistent)
    monkeypatch.setattr(cloak, "launch_browser", nonpersistent)
    return runner, contexts, engine_calls, events


@pytest.mark.parametrize("retry_fails_at", ["launch", "cookies"])
def test_failed_retry_does_not_reclose_first_pair(
        retry_fails_at, tmp_path, monkeypatch):
    runner, contexts, calls, events = _caller_fixture(tmp_path, monkeypatch)
    runner.config["use_real_chrome"] = True
    pairs = []

    def persistent(**kwargs):
        calls.append(kwargs.get("channel"))
        if len(calls) == 2 and retry_fails_at == "launch":
            raise RuntimeError("fixture bundled launch failure")
        ctx = _OwnedContext(events, len(pairs), fails=True)
        pw = _OwnedPlaywright(events)
        contexts.append(ctx)
        pairs.append((ctx, pw))
        return ctx, pw, cloak.PLAYWRIGHT

    monkeypatch.setattr(cloak, "open_persistent_context", persistent)
    result = runner._launch_browser(use_persistent=True)
    assert calls[:2] == ["chrome", None]
    assert len(calls) == 3 and calls[2][0] == "nonpersistent"
    assert len(pairs) == (1 if retry_fails_at == "launch" else 2)
    assert all(ctx.cookie_attempts == 1 for ctx, pw in pairs)
    assert events == [event for ctx, pw in pairs
                      for event in [("close", ctx), ("stop", pw)]]
    assert result[0] is not None and result[1] is None


def test_worker_caller_delivers_cookie_jar(tmp_path, monkeypatch):
    import contextlib
    import threading
    module = importlib.import_module("bulk_downloader.runner")
    runner, contexts, calls, events = _caller_fixture(tmp_path, monkeypatch)
    runner._worker_heartbeats_lock = threading.Lock()
    runner._worker_run_generation = 1
    runner._stop = threading.Event()
    runner._stop.set()  # Startup still runs; stop before any queued work.
    monkeypatch.setattr(module, "_VPN_RUNTIME_AVAILABLE", True)
    monkeypatch.setattr(module.netns_isolation, "capture_netns",
                        lambda *args: contextlib.nullcontext(None))
    module.SiteRunner._worker_loop(runner, worker_idx=3)
    assert len(contexts) == 1, "worker launch delivered no context"
    assert contexts[0].cookie_batches == [_JAR]
    assert contexts[0].cookie_attempts == 1
    assert len(calls) == 1 and calls[0][0] == "persistent"
    assert events.count(("close", contexts[0])) == 1
    assert len(events) == 2 and events[1][0] == "stop"


def test_unavailable_cookie_loader_preserves_existing_profile(
        tmp_path, monkeypatch):
    runner, contexts, calls, events = _caller_fixture(tmp_path, monkeypatch)
    loads = []

    def unavailable(path):
        loads.append(path)
        raise OSError("fixture cookie file became unreadable")

    runner.set_cookies_from_file = unavailable
    result = runner._launch_browser(use_persistent=True)
    assert loads == [runner.config["cookie_file"]]
    assert len(contexts) == 1 and result[1] is contexts[0]
    assert contexts[0].cookie_attempts == 0
    assert contexts[0].cookie_batches == []
    assert len(calls) == 1 and calls[0][0] == "persistent"
    assert events == []


def _manual_caller(tmp_path, monkeypatch, persistent):
    module = importlib.import_module("bulk_downloader.runner_manual")
    learn = importlib.import_module("bulk_downloader.learn")
    runner, contexts, calls, events = _caller_fixture(tmp_path, monkeypatch)
    runner.config["manual_use_persistent_profile"] = persistent
    runner._manual_profile_dir = lambda: str(tmp_path / "manual")
    runner._apply_stealth_library_to_page = lambda page: None
    if not persistent:
        runner.cookies = cookies.load_cookies_from_file(runner.config["cookie_file"])
        assert runner.cookies == _JAR
    monkeypatch.setattr(learn, "install_recorder", lambda *args: None)
    monkeypatch.setattr(learn, "install_teach_overlay", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_fire_capture_lifecycle", lambda *args: None)
    session = object.__new__(module._ManualDownloadSession)
    session._runner = runner
    session.target_url = "https://example.invalid/fixture"
    session._teach_base_url = "http://127.0.0.1"
    error = None
    try:
        result = session._launch()
    except Exception as exc:
        error = exc
    assert len(contexts) == 1, "manual launch delivered no context"
    assert contexts[0].cookie_batches == [_JAR] * (2 if persistent else 1)
    assert contexts[0].cookie_attempts == (2 if persistent else 1)
    assert contexts[0].navigations == [session.target_url]
    assert error is None, repr(error)
    assert result[1] is contexts[0] and result[2] is contexts[0]
    assert len(calls) == 1
    assert calls[0][0] == ("persistent" if persistent else "nonpersistent")
    if persistent:
        assert calls[0][1]["user_data_dir"] == str(tmp_path / "manual")


def test_manual_persistent_caller_delivers_cookie_jar(tmp_path, monkeypatch):
    _manual_caller(tmp_path, monkeypatch, True)


def test_manual_fresh_caller_delivers_cookie_jar(tmp_path, monkeypatch):
    _manual_caller(tmp_path, monkeypatch, False)


def test_crawler_caller_delivers_cookie_jar(tmp_path, monkeypatch):
    module = importlib.import_module("bulk_downloader.scene_crawler")
    keeper = importlib.import_module("bulk_downloader.session_keeper")
    runner, contexts, calls, events = _caller_fixture(tmp_path, monkeypatch)
    runner.cookies = cookies.load_cookies_from_file(runner.config["cookie_file"])
    assert runner.cookies == _JAR
    monkeypatch.setattr(keeper, "pause_site_keepers", lambda *args: None)
    yielded, errors = [], []
    try:
        with module._runner_page(runner, runner.site_id) as page:
            yielded.append(page)
    except Exception as exc:
        errors.append(exc)
    assert len(contexts) == 1, "crawler launch delivered no context"
    assert contexts[0].cookie_batches == [_JAR]
    assert contexts[0].cookie_attempts == 1
    assert yielded == [contexts[0]]
    assert errors == []
    assert len(calls) == 1 and calls[0][0] == "nonpersistent"
