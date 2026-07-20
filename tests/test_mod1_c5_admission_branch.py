"""MOD-1 C-5 (RED-first): the admission path must route an effective remote_vnc
to the vnc transport (takeover_vnc.launch), NOT the CDP screencast browser.

RED on pristine @805 (after C-4's _admit_takeover wiring but before this branch):
start_captcha_solve_session has no remote_vnc branch, so a remote_vnc request
falls through to open_manual_login_browser -- takeover_vnc.launch is never
called and the returned mode is not "remote_vnc".
"""
from __future__ import annotations

import pytest

from bulk_downloader import runner_auth as ra
from bulk_downloader import takeover, takeover_vnc
from bulk_downloader import login as _login


class _FakeVnc:
    def __init__(self):
        self.stopped = False

    def is_alive(self):
        return True

    def stop(self, timeout=10.0):
        self.stopped = True


class _Runner:
    """Minimal carrier for the unbound AuthMixin method under test."""
    site_id = "siteX"

    def __init__(self, config):
        self.config = config
        self._captcha_solve_sessions = {}
        self.events = []

    def _manual_profile_dir(self):
        return None

    def log_event(self, *a, **k):
        self.events.append((a, k))

    start_captcha_solve_session = ra.AuthMixin.start_captcha_solve_session
    end_captcha_solve_session = ra.AuthMixin.end_captcha_solve_session


ON = {
    "captcha_takeover_enabled": True,
    "captcha_takeover_max_concurrent": "2",
    "captcha_takeover_mode": "remote_vnc",
    "manual_use_persistent_profile": False,
}


def _reset():
    ra._vnc_probe = None
    for sid in list(takeover.list_channel_sids()):
        takeover.close_channel(sid)
    takeover_vnc._reset_for_tests()


def test_remote_vnc_request_takes_the_vnc_branch(monkeypatch):
    _reset()
    # probe reports available -> ladder resolves effective remote_vnc.
    ra.register_vnc_probe(lambda cfg: (True, ""))

    launched = {}

    def _fake_launch(config, sid, url=None):
        launched["sid"] = sid
        launched["url"] = url
        takeover.open_channel(sid, kind="vnc")   # mirror real launch's C-1 bind
        return _FakeVnc()

    monkeypatch.setattr(takeover_vnc, "launch", _fake_launch)
    # if the branch is missing this would be reached -> make it loud, not a hang.
    monkeypatch.setattr(_login, "open_manual_login_browser",
                        lambda *a, **k: pytest.fail("fell through to the CDP path"),
                        raising=False)

    r = _Runner(dict(ON))
    out = r.start_captcha_solve_session("https://example.com/challenge")

    assert out["ok"] is True
    assert out["mode"] == "remote_vnc"
    assert out["mode_reason"] == ""              # promoted cleanly, no downgrade
    assert launched.get("url") == "https://example.com/challenge"
    assert takeover.channel_kind(launched["sid"]) == "vnc"
    _reset()


def test_vnc_launch_failure_falls_back_to_remote(monkeypatch):
    _reset()
    ra.register_vnc_probe(lambda cfg: (True, ""))

    def _boom(config, sid, url=None):
        raise RuntimeError("no display")
    monkeypatch.setattr(takeover_vnc, "launch", _boom)

    # the CDP fallback must be taken -- stub the browser open to a sentinel handle.
    class _Handle:
        def start_screencast(self, sid, timeout=15): pass
        def cancel(self): pass
    monkeypatch.setattr(_login, "open_manual_login_browser",
                        lambda *a, **k: _Handle(), raising=False)

    r = _Runner(dict(ON))
    out = r.start_captcha_solve_session("https://example.com/challenge")
    assert out["ok"] is True
    assert out["mode"] == "remote"                       # fell back, not blocked
    assert "vnc launch failed" in (out["mode_reason"] or "")
    _reset()


def test_teardown_of_a_vnc_session_closes_channel(monkeypatch):
    _reset()
    ra.register_vnc_probe(lambda cfg: (True, ""))
    fake = _FakeVnc()

    def _fake_launch(config, sid, url=None):
        takeover.open_channel(sid, kind="vnc")
        takeover_vnc._SESSIONS[sid] = fake       # so teardown() can find + stop it
        return fake
    monkeypatch.setattr(takeover_vnc, "launch", _fake_launch)

    r = _Runner(dict(ON))
    out = r.start_captcha_solve_session("https://example.com/challenge")
    sid = out["session_id"]
    assert takeover.channel_kind(sid) == "vnc"

    r.end_captcha_solve_session("https://example.com/challenge", resolution="dismissed")
    assert takeover.channel_kind(sid) is None             # channel closed
    assert fake.stopped is True                           # browser stopped
    _reset()
