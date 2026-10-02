"""tpl95-site-ma-brazzers-2 / O1658 P1: the LOGIN browser takes the site's egress, like the worker browser does.

MEASURED on test2 2026-10-02T14:13Z (harness-work/APP-DL-FIX-O1649/tpl95-site-ma-brazzers-2/): site-ma-brazzers'
first app login landed on www.brazzers.com/badlogin. Operator fact (O1656): brazzers only works over the VPN, and the
attempt left test2 on its clear-net address. Separately from that host's routing, ``do_login`` launched its browser
with no proxy at all -- it read neither the site ``proxy`` field nor ``vpn_runtime`` -- while
``runner_browser`` (worker launch) and ``download_egress`` (payload clients) both do. So even a site with a VPN tunnel
configured logged in from the clear interface.

Contract after the fix (same precedence as runner_browser: explicit ``proxy`` wins, else the site's tunnel):
  * explicit per-site ``proxy`` -> the login browser launches with it (Playwright {server, username, password});
  * no ``proxy`` and a tunnel -> launches with ``vpn_runtime.playwright_proxy_for_site(sid)``;
  * no ``proxy``, no tunnel, site NOT vpn_required -> launches unproxied (degrade-open control, unchanged);
  * site vpn_required and the tunnel is unmapped / down / killed / unresolvable, its requirement cannot be read, or
    the explicit proxy cannot be parsed -> REFUSED before any browser launches (never leak);
  * the human-challenge attach path (a browser this login did not launch) is refused for a vpn_required site.

Hermetic: cloak.launch_browser and sync_playwright are stubs that record their kwargs and stop the flow; vpn_runtime
is stubbed per case. No browser, no network, no credentials (literal fixture values).
"""

from __future__ import annotations

import pytest

BD_GATE_SCOPE = "module"

SID = "bz2egress"
CONFIG = {
    "name": "fixture-egress",
    "login_url": "https://login.fixture.invalid/login",
    "username": "fixture-user",
    "password": "fixture-pass",
    "auto_teach_first_run": False,
}
TUNNEL = {"server": "socks5://127.0.0.1:41999"}
STOP = "BZ2-STUB-LAUNCH-REACHED"  # do_login turns the stub's exception into ("login error: <this>")


class _Stop(Exception):
    """Raised by the launch stub: the flow reached a browser launch."""


@pytest.fixture
def rig(monkeypatch):
    from bulk_downloader import cloak, vpn_runtime

    launched: list[dict] = []
    asked: list[tuple[str, str]] = []
    state = {"required": False, "tunnel": None, "required_raises": None, "tunnel_raises": None}

    def fake_launch(headless=True, args=None, config=None, **extra):
        launched.append(dict(extra))
        raise _Stop(STOP)

    def fake_required(site_id):
        asked.append(("required", site_id))
        if state["required_raises"] is not None:
            raise state["required_raises"]
        return state["required"]

    def fake_tunnel(site_id):
        asked.append(("tunnel", site_id))
        if state["tunnel_raises"] is not None:
            raise state["tunnel_raises"]
        return state["tunnel"]

    class _FakePW:
        def start(self):
            launched.append({"attach": True})
            raise _Stop(STOP)

    monkeypatch.setattr(cloak, "launch_browser", fake_launch)
    monkeypatch.setattr(vpn_runtime, "is_vpn_required_for_site", fake_required)
    monkeypatch.setattr(vpn_runtime, "playwright_proxy_for_site", fake_tunnel)
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _FakePW())
    return launched, asked, state


def _login(config=None):
    from bulk_downloader.login_impl.submit import do_login

    try:
        result = do_login(dict(config or CONFIG), allow_manual_takeover=False, site_id=SID)
    except _Stop:
        return "LAUNCHED"
    if isinstance(result, tuple) and len(result) >= 2 and STOP in str(result[1]):
        return "LAUNCHED"
    return result


# ── the browser gets the site's egress ─────────────────────────────────────


def test_explicit_site_proxy_reaches_the_login_browser(rig):
    launched, asked, _ = rig
    assert _login(dict(CONFIG, proxy="socks5://pu:pp@10.9.9.9:1080")) == "LAUNCHED"
    assert launched, "positive control: the flow must reach the launch seam"
    assert launched[0].get("proxy") == {"server": "socks5://10.9.9.9:1080", "username": "pu", "password": "pp"}, (
        f"BZ2_LOGIN_IGNORES_SITE_PROXY: login launched with {launched[0]!r}"
    )
    assert ("tunnel", SID) not in asked, "an explicit proxy wins over the tunnel (runner_browser precedence)"


def test_site_tunnel_reaches_the_login_browser(rig):
    launched, asked, state = rig
    state["tunnel"] = dict(TUNNEL)
    assert _login() == "LAUNCHED"
    assert launched[0].get("proxy") == TUNNEL, f"BZ2_LOGIN_IGNORES_VPN_TUNNEL: login launched with {launched[0]!r}"
    assert ("tunnel", SID) in asked, f"the tunnel must be resolved for THIS site: {asked!r}"


def test_no_proxy_no_tunnel_not_required_launches_unproxied(rig):
    """Degrade-open control: a site with no egress configured logs in exactly as before."""
    launched, _, _ = rig
    assert _login() == "LAUNCHED"
    assert "proxy" not in launched[0], f"unexpected proxy on a site with none: {launched[0]!r}"


def test_tunnel_resolution_error_on_optional_site_launches_unproxied(rig):
    launched, _, state = rig
    state["tunnel_raises"] = RuntimeError("socks carrier hiccup")
    assert _login() == "LAUNCHED"
    assert "proxy" not in launched[0]


# ── never leak: vpn_required refuses before any launch ─────────────────────


def _assert_refused(result, launched, needle):
    assert result != "LAUNCHED" and not launched, (
        f"BZ2_LOGIN_LEAKED_CLEAR_NET: a vpn_required login launched a browser: {launched!r}"
    )
    ok, msg, cookies = result
    assert ok is False and cookies == []
    assert needle in msg, f"refusal must name its cause ({needle!r}): {msg!r}"


def test_required_with_no_tunnel_mapped_is_refused(rig):
    launched, _, state = rig
    state["required"] = True  # playwright_proxy_for_site -> None: no tunnel mapped to the site
    _assert_refused(_login(), launched, "VPN required")


def test_required_with_tunnel_down_is_refused(rig):
    from bulk_downloader import vpn_runtime

    launched, _, state = rig
    state["required"] = True
    state["tunnel_raises"] = vpn_runtime.VPNRequiredError("tunnel t1 failed to start")
    _assert_refused(_login(), launched, "tunnel t1 failed to start")


def test_required_with_unresolvable_tunnel_is_refused(rig):
    launched, _, state = rig
    state["required"] = True
    state["tunnel_raises"] = RuntimeError("socks carrier hiccup")
    _assert_refused(_login(), launched, "VPN required")


def test_unknown_requirement_is_refused(rig):
    launched, _, state = rig
    state["required_raises"] = RuntimeError("vpn config unreadable")
    _assert_refused(_login(), launched, "cannot tell")


def test_unparseable_explicit_proxy_is_refused(rig):
    launched, _, _ = rig
    _assert_refused(_login(dict(CONFIG, proxy="not a proxy url")), launched, "proxy")


def test_required_site_does_not_attach_to_an_unproxied_challenge_browser(rig):
    launched, _, state = rig
    state["required"] = True
    state["tunnel"] = dict(TUNNEL)
    cfg = dict(CONFIG, _human_clearance={"cdp_url": "http://127.0.0.1:9/fixture"})
    _assert_refused(_login(cfg), launched, "challenge browser")
