"""v3.66.770 -- vpn dark-cluster wiring 6C: killswitch-available read + webrtc
worker-data-plane reclassification.

Three routes remained "dark", but only ONE is a real operator control:
  GET /api/vpn/system_killswitch/available -- operator read (WIRE it)

The other two are worker/browser-context data-plane endpoints, NOT operator UI:
  POST /api/vpn/tunnels/<id>/webrtc_result -- workers POST probe results here
  GET  /api/vpn/webrtc_js                  -- serves the JS snippet workers inject
                                              into their Playwright contexts
Forcing an SPA fetch for those would be a dead control. They are instead
classified as non-SPA surfaces (like the browser-extension data-plane), so they
correctly stop counting as SPA-wireable operator gaps.

RED on pristine v3.66.769: killswitch/available is unwired with no FE literal,
and both webrtc endpoints still read as SPA surfaces (spa_surface True). GREEN
after wiring killswitch/available + reclassifying the webrtc pair.

run_tests.py conventions: zero-arg test functions; repo root from __file__.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_gui_parity():
    saved = list(sys.path)
    sys.path.insert(0, str(REPO / "tools"))
    sys.path.insert(0, str(REPO))
    try:
        import gui_parity_inventory as P  # noqa: E402
        return P
    finally:
        sys.path[:] = saved


def test_system_killswitch_available_is_wired():
    P = _load_gui_parity()
    _eps, meps = P._spa_wiring(str(REPO))
    assert ("GET", "/api/vpn/system_killswitch/available") in meps, (
        "GET /api/vpn/system_killswitch/available not method-aware spa_wired")


def test_killswitch_available_full_literal_in_vpn_source():
    vpn = (REPO / "frontend" / "src" / "routes" / "Vpn.tsx").read_text(encoding="utf-8")
    assert '"/api/vpn/system_killswitch/available"' in vpn, (
        "full /api/vpn/system_killswitch/available literal missing from Vpn.tsx")


def test_webrtc_endpoints_are_non_spa_worker_data_plane():
    """The two webrtc endpoints are worker/browser-context data-plane, not
    operator UI -- they must be classified non-SPA so they stop counting as
    SPA-wireable operator gaps (never fake-wired)."""
    P = _load_gui_parity()
    assert P._is_non_spa_surface("/api/vpn/webrtc_js") is True
    assert P._is_non_spa_surface("/api/vpn/tunnels/<tunnel_id>/webrtc_result") is True


def test_webrtc_endpoints_drop_out_of_operator_gap():
    """In the built inventory, the webrtc endpoints carry spa_surface=False, so
    they are excluded from the SPA-wireable operator-gap denominator."""
    P = _load_gui_parity()
    inv = P.build(str(REPO))
    by_ce = {}
    for it in inv["items"]:
        ce = it.get("command_or_endpoint", "")
        by_ce[ce] = it
    js = by_ce.get("GET /api/vpn/webrtc_js")
    res = by_ce.get("POST /api/vpn/tunnels/<tunnel_id>/webrtc_result")
    assert js is not None and js.get("spa_surface") is False, "webrtc_js still SPA surface"
    assert res is not None and res.get("spa_surface") is False, "webrtc_result still SPA surface"
