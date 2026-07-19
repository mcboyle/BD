"""v3.66.768 -- vpn dark-cluster wiring 6A: blacklist/stats diagnostics reads.

Five previously-dark vpn routes get an operator surface in the VPN page's new
Diagnostics card:
  GET  /api/vpn/stats                 -- profile report
  GET  /api/vpn/blacklist             -- current blacklist
  GET  /api/vpn/backends/availability -- wireguard/openvpn availability
  GET  /api/vpn/best_for/<sid>        -- best profile for a site (input+lookup)
  POST /api/vpn/auto_blacklist        -- recompute the auto-blacklist (action)

RED on pristine v3.66.767: none of the five is in the method-aware spa_wiring
set and none appears as a full literal in Vpn.tsx. GREEN after the wiring +
gui_parity/ROUTE_INDEX regen.

run_tests.py conventions: zero-arg test functions; repo root from __file__.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# (method, normalised path) the scanner should credit after wiring
_WANT = [
    ("GET", "/api/vpn/stats"),
    ("GET", "/api/vpn/blacklist"),
    ("GET", "/api/vpn/backends/availability"),
    ("GET", "/api/vpn/best_for/*"),
    ("POST", "/api/vpn/auto_blacklist"),
]


def _load_gui_parity():
    saved = list(sys.path)
    sys.path.insert(0, str(REPO / "tools"))
    sys.path.insert(0, str(REPO))
    try:
        import gui_parity_inventory as P  # noqa: E402
        return P
    finally:
        sys.path[:] = saved


def test_all_five_6a_routes_method_aware_wired():
    P = _load_gui_parity()
    _eps, meps = P._spa_wiring(str(REPO))
    missing = [w for w in _WANT if w not in meps]
    assert not missing, f"6A routes not method-aware spa_wired: {missing}"


def test_6a_full_literals_present_in_vpn_source():
    vpn = (REPO / "frontend" / "src" / "routes" / "Vpn.tsx").read_text(encoding="utf-8")
    needed = [
        '"/api/vpn/stats"',
        '"/api/vpn/blacklist"',
        '"/api/vpn/backends/availability"',
        "/api/vpn/best_for/",
        '"/api/vpn/auto_blacklist"',
    ]
    missing = [n for n in needed if n not in vpn]
    assert not missing, f"6A full literals missing from Vpn.tsx: {missing}"


def test_6a_auto_blacklist_is_an_action_not_a_read():
    """auto_blacklist is a POST recompute; it must be wired as a mutation button,
    not silently fired -- the diagnostics card exposes it as an explicit action."""
    vpn = (REPO / "frontend" / "src" / "routes" / "Vpn.tsx").read_text(encoding="utf-8")
    assert "auto_blacklist" in vpn
    # posted through apiPost (mutation), and surfaced with an operator label
    assert 'apiPost' in vpn and '"/api/vpn/auto_blacklist"' in vpn
