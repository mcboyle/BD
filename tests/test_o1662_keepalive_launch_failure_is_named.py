"""O1662 item 2: a keep-alive heartbeat whose browser launch fails must say so.

tests/test_keepalive_browser.py x6 flaked on the CPU farm (.81, baseline e626d248) with
"no cookies stored yet (never logged in)": _launch_browser() returned False, the heartbeat fell back
to httpx, and the httpx verdict was the only text that reached the junit. The launch failure itself was
written to stderr and lost, so neither the farm nor two flaky hunts could name it. The same text reaches
the dashboard in production: a keeper whose Chromium will not start reports "never logged in".

The heartbeat keeps the fallback's verdict and appends the reason the launch failed.
"""
from __future__ import annotations

import threading

from bulk_downloader import session_keeper as sk

BD_GATE_SCOPE = "module"


def _keeper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return sk.SessionKeeper(
        "o1662", 0,
        {"login_url": "http://127.0.0.1:9/", "success_url": "http://127.0.0.1:9/", "password": "p"},
        lambda *_: (False, "test"),
    )


def test_a_launch_exception_is_named_in_the_heartbeat_detail(tmp_path, monkeypatch):
    def boom(**_kw):
        raise RuntimeError("O1662-DISTINCT launch refusal")

    monkeypatch.setattr(sk._cloak, "open_persistent_context", boom)
    keeper = _keeper(tmp_path, monkeypatch)
    try:
        verdict, detail = keeper._heartbeat()
    finally:
        keeper._teardown_browser()
    assert not verdict
    assert "no cookies stored yet" in detail
    assert "browser launch failed: RuntimeError: O1662-DISTINCT launch refusal" in detail, detail


def test_a_takeover_held_profile_is_named_in_the_heartbeat_detail(tmp_path, monkeypatch):
    def must_not_launch(**_kw):
        raise AssertionError("launched while the takeover held the profile")

    monkeypatch.setattr(sk._cloak, "open_persistent_context", must_not_launch)
    keeper = _keeper(tmp_path, monkeypatch)
    held, release = threading.Event(), threading.Event()

    def hold():
        with sk.get_takeover_lock("o1662", 0):
            held.set()
            release.wait(10)

    t = threading.Thread(target=hold, daemon=True)
    t.start()
    assert held.wait(10)
    try:
        verdict, detail = keeper._heartbeat()
    finally:
        release.set()
        t.join(10)
        keeper._teardown_browser()
    assert not verdict
    assert "profile locked by takeover" in detail, detail


class _Page:
    def is_closed(self):
        return False

    def route(self, *_a, **_kw):
        pass

    def close(self):
        pass


class _Ctx:
    def __init__(self):
        self.pages = [_Page()]

    def close(self):
        pass


def test_a_successful_launch_clears_the_previous_failure(tmp_path, monkeypatch):
    """Negative control: the reason belongs to the LAST launch, never a stale one."""
    keeper = _keeper(tmp_path, monkeypatch)

    def boom(**_kw):
        raise RuntimeError("first launch fails")

    monkeypatch.setattr(sk._cloak, "open_persistent_context", boom)
    assert keeper._launch_browser() is False
    assert keeper._last_launch_failure == "browser launch failed: RuntimeError: first launch fails"

    monkeypatch.setattr(sk._cloak, "open_persistent_context", lambda **_kw: (_Ctx(), None, "fake"))
    monkeypatch.setattr(sk._cloak, "log_choice", lambda *a, **kw: None)
    try:
        assert keeper._launch_browser() is True
        assert keeper._last_launch_failure is None
    finally:
        keeper._teardown_browser()


def test_the_fallback_called_directly_carries_no_launch_reason(tmp_path, monkeypatch):
    """Negative control: only a heartbeat that ATTEMPTED a launch appends a reason."""
    keeper = _keeper(tmp_path, monkeypatch)
    _verdict, detail = keeper._heartbeat_httpx_fallback()
    assert "httpx fallback:" not in detail, detail
