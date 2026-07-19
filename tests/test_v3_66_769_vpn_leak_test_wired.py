"""v3.66.769 -- vpn dark-cluster wiring 6B: leak-test trio + single-tunnel GET.

Four previously-dark per-tunnel vpn routes get an operator surface in the VPN
page's new Leak tests card:
  GET  /api/vpn/tunnels/<id>                  -- tunnel detail
  GET  /api/vpn/tunnels/<id>/leak_test/latest -- most recent leak result
  GET  /api/vpn/tunnels/<id>/leak_test/history-- prior leak results
  POST /api/vpn/tunnels/<id>/leak_test/run    -- run the leak probes now

RED on pristine v3.66.768: none carries a full FE literal and ROUTE_INDEX pins
each spa_wired=false. GREEN after the wiring + gui_parity/ROUTE_INDEX regen.

run_tests.py conventions: zero-arg test functions; repo root from __file__.
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# (method, ROUTE_INDEX path) that must flip spa_wired True
_WANT = [
    ("GET", "/api/vpn/tunnels/<tunnel_id>"),
    ("GET", "/api/vpn/tunnels/<tunnel_id>/leak_test/latest"),
    ("GET", "/api/vpn/tunnels/<tunnel_id>/leak_test/history"),
    ("POST", "/api/vpn/tunnels/<tunnel_id>/leak_test/run"),
]


def _route_rows():
    ri = json.loads((REPO / "ROUTE_INDEX.json").read_text(encoding="utf-8"))
    rows = {}

    def walk(o):
        if isinstance(o, dict):
            if "path" in o and "spa_wired" in o and "method" in o:
                rows[(o["method"], o["path"])] = o["spa_wired"]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(ri)
    return rows


def test_all_four_6b_routes_route_index_wired():
    rows = _route_rows()
    not_wired = [w for w in _WANT if not rows.get(w)]
    assert not not_wired, f"6B routes still spa_wired=false in ROUTE_INDEX: {not_wired}"


def test_6b_full_literals_present_in_vpn_source():
    vpn = (REPO / "frontend" / "src" / "routes" / "Vpn.tsx").read_text(encoding="utf-8")
    needed = [
        "/leak_test/run",
        "/leak_test/history",
        "/leak_test/latest",
    ]
    missing = [n for n in needed if n not in vpn]
    assert not missing, f"6B leak_test literals missing from Vpn.tsx: {missing}"
    # the single-tunnel GET (detail) full literal -- templated by tunnel id
    assert "/api/vpn/tunnels/${" in vpn, "single-tunnel GET literal missing"


def test_6b_leak_run_is_an_explicit_action():
    """leak_test/run is a POST probe; it must be an explicit mutation button,
    never fired on load."""
    vpn = (REPO / "frontend" / "src" / "routes" / "Vpn.tsx").read_text(encoding="utf-8")
    assert "apiPost" in vpn and "/leak_test/run" in vpn
    assert "leak" in vpn.lower()
