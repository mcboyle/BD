"""MOD-1 C-5 (RED-first): the remote_vnc transport -- dedicated headful browser
on an Xvnc display, bound into the C-1 registry as kind="vnc", with a DERIVED
probe and a sweep census.

RED on pristine @805: bulk_downloader/takeover_vnc.py does not exist
(ImportError on the first line).

The real browser launch is verified LIVE against KasmVNC out of band; these
unit tests inject a fake session so the registry/census/probe contracts run fast
and deterministically.
"""
from __future__ import annotations

import pytest

from bulk_downloader import takeover_vnc as tv
from bulk_downloader import takeover


class _FakeSession:
    """Stand-in for VncTakeoverSession: no browser, controllable liveness."""
    def __init__(self, config, sid, display, url=None):
        self.sid = sid
        self.display = display
        self.url = url
        self._alive = True
        self.stopped = False

    def start(self, timeout=25.0):
        return self

    def is_alive(self):
        return self._alive

    def stop(self, timeout=10.0):
        self.stopped = True
        self._alive = False


def _reset():
    tv._reset_for_tests()
    for sid in list(takeover.list_channel_sids()):
        takeover.close_channel(sid)
    tv._session_factory = _FakeSession


# ── config resolution ────────────────────────────────────────────────────────

def test_display_and_port_defaults_and_overrides():
    assert tv.resolve_vnc_display({}) == ":5"
    assert tv.resolve_vnc_display({"captcha_vnc_display": "7"}) == ":7"
    assert tv.resolve_vnc_display({"captcha_vnc_display": ":9"}) == ":9"
    assert tv.resolve_ws_port({}) == 8444
    assert tv.resolve_ws_port({"captcha_vnc_websocket_port": "6080"}) == 6080
    assert tv.resolve_ws_port({"captcha_vnc_websocket_port": "junk"}) == 8444


# ── the DERIVED probe: observed, and UNKNOWN downgrades ───────────────────────

def test_probe_unavailable_when_nothing_is_up(monkeypatch):
    monkeypatch.setattr(tv, "_xvnc_alive", lambda: False)
    monkeypatch.setattr(tv, "_port_open", lambda h, p, timeout=0.5: False)
    available, reason = tv.probe_endpoint({})
    assert available is False and "not running" in reason


def test_probe_available_only_when_BOTH_proc_and_port(monkeypatch):
    monkeypatch.setattr(tv, "_xvnc_alive", lambda: True)
    monkeypatch.setattr(tv, "_port_open", lambda h, p, timeout=0.5: True)
    assert tv.probe_endpoint({}) == (True, "")


def test_probe_indeterminate_downgrades_to_unavailable(monkeypatch):
    # proc alive but port silent -> UNKNOWN -> unavailable (never assume vnc).
    monkeypatch.setattr(tv, "_xvnc_alive", lambda: True)
    monkeypatch.setattr(tv, "_port_open", lambda h, p, timeout=0.5: False)
    available, reason = tv.probe_endpoint({})
    assert available is False and "indeterminate" in reason


# ── lifecycle bound into the C-1 registry ─────────────────────────────────────

def test_launch_registers_kind_vnc_and_census_sees_it():
    _reset()
    tv.launch({}, "vnc-1", url=None)
    assert takeover.channel_kind("vnc-1") == "vnc"
    assert takeover.active_channel_count(kind="vnc") == 1
    assert takeover.active_channel_count() == 1          # shared cap denominator
    assert tv.census() == ["vnc-1"]                       # sweep can verify it


def test_teardown_closes_channel_and_session():
    _reset()
    sess = tv.launch({}, "vnc-2")
    tv.teardown("vnc-2")
    assert takeover.channel_kind("vnc-2") is None
    assert takeover.active_channel_count(kind="vnc") == 0
    assert sess.stopped is True
    assert tv.census() == []


def test_census_raises_when_a_session_died():
    # a dead browser must fail the sweep so it reaps -- unknown-fails, not silent.
    _reset()
    sess = tv.launch({}, "vnc-3")
    sess._alive = False
    with pytest.raises(RuntimeError):
        tv.census()


def test_launch_failure_unwinds_the_channel():
    # if the browser will not start, the registry must not keep a phantom vnc sid.
    _reset()

    class _Boom(_FakeSession):
        def start(self, timeout=25.0):
            raise RuntimeError("no display")

    tv._session_factory = _Boom
    with pytest.raises(RuntimeError):
        tv.launch({}, "vnc-4")
    assert takeover.channel_kind("vnc-4") is None
    assert takeover.active_channel_count(kind="vnc") == 0
    assert tv.census() == []


def test_shared_cap_spans_cdp_and_vnc():
    # C-1 invariant the cap relies on: one vnc + one cdp -> count(all) == 2.
    _reset()
    tv.launch({}, "vnc-5")
    takeover.open_channel("cdp-1", kind="cdp")
    assert takeover.active_channel_count() == 2
    assert takeover.active_channel_count(kind="vnc") == 1
    assert takeover.active_channel_count(kind="cdp") == 1
