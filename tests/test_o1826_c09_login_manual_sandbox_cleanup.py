"""O1826 BRIEF-09 -- the manual/takeover login session cleans up after itself.

  * M110: ``_launch`` raising AFTER ``cloak.launch_browser`` /
    ``open_persistent_context`` returned lost its browser/ctx/pw locals, so
    ``_run``'s finally closed only ``None`` handles -- a live browser (and the
    Playwright driver) nothing owned.
  * M111: when the ready-wait in ``ManualLoginSession.__init__`` timed out,
    ``open_manual_login_browser`` raised without cancelling the session, so the
    worker thread could still open a browser that nothing held.

Offline: no browser, no network, no site. Every launch entry point is
monkeypatched; the browser/context/page are duck-typed fakes.
"""
import sys
import threading
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import bulk_downloader.cloak as cloak
import bulk_downloader.interstitial as interstitial
import bulk_downloader.learn as learn
import bulk_downloader.secrets_store as secrets_store
import bulk_downloader.stealth as stealth
from bulk_downloader.login_impl import manual as m

BD_GATE_SCOPE = "module"

LOGIN_URL = "https://login.example.invalid/login"


class _Page:
    url = LOGIN_URL

    def goto(self, *a, **k): return None
    def evaluate(self, *a, **k): return None
    def wait_for_timeout(self, *a, **k): return None


class _Ctx:
    def __init__(self, new_page_error=None):
        self.pages = []
        self.closed = threading.Event()
        self._new_page_error = new_page_error

    def add_init_script(self, *a, **k): return None

    def new_page(self):
        if self._new_page_error:
            raise self._new_page_error
        return _Page()

    def cookies(self): return []
    def close(self): self.closed.set()


class _Browser:
    def __init__(self, ctx):
        self.ctx = ctx
        self.closed = threading.Event()

    def new_context(self, **k): return self.ctx
    def is_connected(self): return not self.closed.is_set()
    def close(self): self.closed.set()


class _Pw:
    def __init__(self): self.stopped = threading.Event()
    def stop(self): self.stopped.set()


def _config(**over):
    cfg = {"name": "c09", "login_url": LOGIN_URL,
           "use_real_chrome": False, "use_stealth": False}
    cfg.update(over)
    return cfg


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """Everything _launch reaches past the browser is neutral."""
    monkeypatch.setattr(interstitial, "dismiss_gates", lambda *a, **k: [])
    monkeypatch.setattr(stealth, "apply_to_page", lambda *a, **k: None)
    monkeypatch.setattr(secrets_store, "resolve_password_state",
                        lambda v: ("", "ok"))
    monkeypatch.setattr(learn, "harvest_recordings",
                        lambda ctx: {"clicks": [], "inputs": []})


@pytest.fixture
def sessions(monkeypatch):
    """Every ManualLoginSession built in the test; cancelled at teardown so a
    RED run never leaks a session thread."""
    made = []

    class _Recorded(m.ManualLoginSession):
        def __init__(self, *a, **k):
            made.append(self)
            super().__init__(*a, **k)

    monkeypatch.setattr(m, "ManualLoginSession", _Recorded)
    yield made
    for s in made:
        s.cancel(timeout=5)


def test_m110_launch_failure_after_browser_start_closes_it(monkeypatch, sessions):
    ctx = _Ctx(new_page_error=RuntimeError("c09 new_page boom"))
    browser, pw = _Browser(ctx), _Pw()
    monkeypatch.setattr(cloak, "launch_browser",
                        lambda **kw: (browser, pw, "fixture"))

    with pytest.raises(RuntimeError, match="failed to start: RuntimeError: c09 new_page boom"):
        m.open_manual_login_browser(_config())
    assert sessions[0]._closed.wait(10), "session thread never exited"

    assert browser.closed.is_set(), (
        "M110: _launch raised after launch_browser returned; browser.close() "
        "was never called -- the takeover browser is orphaned")
    assert ctx.closed.is_set(), "M110: the context _launch opened was never closed"
    assert pw.stopped.is_set(), "M110: the Playwright driver _launch started was never stopped"


def test_m110_persistent_launch_failure_closes_context(monkeypatch, sessions, tmp_path):
    ctx, pw = _Ctx(), _Pw()
    monkeypatch.setattr(cloak, "open_persistent_context",
                        lambda **kw: (ctx, pw, "fixture"))

    def _boom(*a, **k):
        raise RuntimeError("c09 gate boom")
    monkeypatch.setattr(interstitial, "dismiss_gates", _boom)

    with pytest.raises(RuntimeError, match="failed to start: RuntimeError: c09 gate boom"):
        m.open_manual_login_browser(_config(), manual_profile_dir=str(tmp_path))
    assert sessions[0]._closed.wait(10), "session thread never exited"

    assert ctx.closed.is_set(), (
        "M110: _launch raised after open_persistent_context returned; the "
        "persistent context was never closed")
    assert pw.stopped.is_set(), "M110: the Playwright driver _launch started was never stopped"


def test_m111_ready_timeout_cancels_the_session(monkeypatch, sessions):
    # 45 s in product; shortened through the module seam (absent on base, where
    # the RED run simply waits the full 45 s).
    monkeypatch.setattr(m, "_READY_WAIT_S", 0.3, raising=False)
    gate = threading.Event()
    ctx = _Ctx()
    browser, pw = _Browser(ctx), _Pw()

    def _slow_launch(**kw):
        gate.wait(90)  # outlasts the 45 s product wait the base RED runs
        return browser, pw, "fixture"
    monkeypatch.setattr(cloak, "launch_browser", _slow_launch)

    with pytest.raises(RuntimeError, match="timed out before ready"):
        m.open_manual_login_browser(_config())
    gate.set()  # the slow launch now completes on the worker thread

    assert browser.closed.wait(10), (
        "M111: the ready-wait timed out and the caller raised without "
        "cancel(); the worker then opened a browser nothing holds")
    assert sessions[0]._closed.wait(10), "M111: the session thread is still running"
    assert pw.stopped.is_set()


def test_neg_normal_takeover_launches_and_finalizes(monkeypatch, sessions):
    ctx = _Ctx()
    browser, pw = _Browser(ctx), _Pw()
    monkeypatch.setattr(cloak, "launch_browser",
                        lambda **kw: (browser, pw, "fixture"))

    session = m.open_manual_login_browser(_config())
    assert session.ready and session.error is None
    assert not browser.closed.is_set(), "a ready session must keep its browser open"

    ok, msg, cookies, harvest = session.finalize()
    assert ok, msg
    assert cookies == []
    assert session._closed.wait(10)
    assert browser.closed.is_set() and ctx.closed.is_set() and pw.stopped.is_set()


class _CountingCtx(_Ctx):
    def __init__(self):
        super().__init__()
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        super().close()


class _CountingPw(_Pw):
    def __init__(self):
        super().__init__()
        self.stop_calls = 0

    def stop(self):
        self.stop_calls += 1
        super().stop()


@pytest.mark.parametrize("fault", [False, True],
                         ids=["healthy-stderr", "first-log-choice-write-fails"])
def test_m110_persistent_retry_closes_each_acquired_handle_once(
        monkeypatch, sessions, tmp_path, fault):
    """The persistent Chrome context opens, then cloak.log_choice's stderr
    write fails once: _launch retries with bundled Chromium. The first
    context/driver pair must still be closed/stopped, exactly once."""
    contexts, drivers = [], []

    def _open(**kw):
        ctx, pw = _CountingCtx(), _CountingPw()
        contexts.append(ctx)
        drivers.append(pw)
        return ctx, pw, "fixture"
    monkeypatch.setattr(cloak, "open_persistent_context", _open)

    real_stderr = sys.stderr

    class _FailFirstLogChoice:
        failed = False

        def write(self, s):
            if fault and not self.failed and "[browser] manual login" in s:
                self.failed = True
                raise OSError("c09 log_choice stderr write failure")
            return real_stderr.write(s)

        def flush(self): return real_stderr.flush()
    monkeypatch.setattr(sys, "stderr", _FailFirstLogChoice())

    session = m.open_manual_login_browser(
        _config(use_real_chrome=True), manual_profile_dir=str(tmp_path))
    assert session.ready and session.error is None
    ok, msg, _cookies, _harvest = session.finalize(timeout=5)
    assert ok, msg
    assert session._closed.wait(10)

    opened = 2 if fault else 1
    counts = {"opened_contexts": len(contexts), "opened_drivers": len(drivers),
              "close_calls": [c.close_calls for c in contexts],
              "stop_calls": [p.stop_calls for p in drivers]}
    assert len(contexts) == opened and len(drivers) == opened, counts
    assert counts["close_calls"] == [1] * opened, (
        f"M110: every persistent context _launch acquired must close exactly "
        f"once (the retry overwrote the first pair): {counts}")
    assert counts["stop_calls"] == [1] * opened, (
        f"M110: every Playwright driver _launch acquired must stop exactly "
        f"once: {counts}")
