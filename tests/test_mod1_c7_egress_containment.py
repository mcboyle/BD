"""MOD-1 C-7 (RED-first): KASM-T8 egress containment for the takeover browser.

Two leak vectors, both closed by construction (verify, do not assume):

1. X-over-TCP -- the display MUST be a unix-domain ``:<n>``, never ``host:<n>``,
   so the X protocol cannot egress over TCP. resolve_vnc_display drops any host.
2. Uncontained network egress -- the takeover browser MUST honour BD's egress
   isolation posture (capture_netns) exactly like every other browser: wg0 (or
   default-drop) becomes its only route, FAIL-CLOSED (if isolation is required
   but the netns can't be created, the launch RAISES -- never uncontained).

RED on pristine @805: resolve_vnc_display keeps the host part (":evil-host:7"),
_contained_launch_kwargs does not exist, and VncTakeoverSession launches raw
(bypassing capture_netns), so a fail-closed netns failure does NOT stop it.
"""
from __future__ import annotations

import contextlib

import pytest

from bulk_downloader import takeover_vnc as tv
from bulk_downloader import takeover
from bulk_downloader import netns_isolation as ni


def _reset():
    tv._reset_for_tests()
    for sid in list(takeover.list_channel_sids()):
        takeover.close_channel(sid)


# ── 1. unix-domain X by construction ──────────────────────────────────────────

def test_display_is_always_unix_domain_no_host():
    assert tv.resolve_vnc_display({"captcha_vnc_display": "evil-host:7"}) == ":7"
    assert tv.resolve_vnc_display({"captcha_vnc_display": "10.0.0.5:3"}) == ":3"
    assert tv.resolve_vnc_display({"captcha_vnc_display": ":5"}) == ":5"
    assert tv.resolve_vnc_display({"captcha_vnc_display": "9"}) == ":9"
    assert tv.resolve_vnc_display({}) == ":5"
    # a nonsense value falls back to the safe default, never a TCP form.
    assert tv.resolve_vnc_display({"captcha_vnc_display": "junk"}) == ":5"


# ── 2. the launch is routed through the netns shim when contained ─────────────

def test_launch_kwargs_uncontained_when_ns_none():
    kw = tv._contained_launch_kwargs(None, ":5", "/opt/chrome", ["--a"])
    assert kw["headless"] is False
    assert kw["executable_path"] == "/opt/chrome"     # normal launch, no shim
    assert "NETNS_NS" not in kw["env"]
    assert kw["env"]["DISPLAY"] == ":5"               # unix form always


def test_launch_kwargs_routes_through_shim_when_contained():
    kw = tv._contained_launch_kwargs("bd_takeover_dead", ":5", "/opt/chrome", ["--a"])
    assert "shim" in kw["executable_path"]            # exec the netns shim, not chrome
    assert kw["env"]["NETNS_NS"] == "bd_takeover_dead"
    assert kw["env"]["NETNS_BROWSER_BIN"] == "/opt/chrome"  # shim execs the real browser
    assert kw["env"]["DISPLAY"] == ":5"


# ── 3. FAIL-CLOSED: a required-but-unavailable netns must stop the launch ─────

def test_fail_closed_netns_prevents_launch_and_unwinds_channel(monkeypatch):
    _reset()
    tv._session_factory = tv.VncTakeoverSession   # the REAL session, so _run runs

    @contextlib.contextmanager
    def _raising_capture(cfg, kind, ident, **kw):
        raise ni.NetnsRequiredError("isolation required but unavailable — failing closed")
        yield  # pragma: no cover

    monkeypatch.setattr(ni, "capture_netns", _raising_capture)

    with pytest.raises(RuntimeError) as ei:
        tv.launch({"captcha_vnc_display": ":5"}, "vnc-fc", url=None)
    # must fail for the FAIL-CLOSED reason, not incidentally (e.g. a missing
    # browser) -- otherwise this proves nothing about containment.
    assert "failing closed" in str(ei.value)
    # the browser never launched uncontained AND the C-1 channel was unwound.
    assert takeover.channel_kind("vnc-fc") is None
    assert takeover.active_channel_count(kind="vnc") == 0
    _reset()


def test_isolation_off_yields_none_and_does_not_raise(monkeypatch):
    # posture off -> capture_netns yields None -> a raw (uncontained) launch is
    # expected; prove the plumbing calls capture_netns and tolerates None without
    # forcing a shim. We stop before a real browser by making launch() use a fake
    # session that records the resolved display.
    _reset()

    @contextlib.contextmanager
    def _none_capture(cfg, kind, ident, **kw):
        yield None

    monkeypatch.setattr(ni, "capture_netns", _none_capture)

    seen = {}

    class _Fake:
        def __init__(self, config, sid, display, url=None):
            seen["display"] = display
        def start(self, timeout=25.0):
            return self
        def is_alive(self):
            return True
        def stop(self, timeout=10.0):
            pass

    tv._session_factory = _Fake
    tv.launch({"captcha_vnc_display": "some-host:5"}, "vnc-off")
    assert seen["display"] == ":5"                    # unix form even when uncontained
    assert takeover.channel_kind("vnc-off") == "vnc"
    _reset()
