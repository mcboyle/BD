"""v3.66.719 (Cut 8) -- the control surface for the exec bridge.

717 built the bridge (tools_exec_bridged 0 -> 1) and its endpoints are live, but
/api/tools/run had NO control -- it was classified in the reachability ledger as an
exec-bridge endpoint awaiting its GUI. This cut lands that GUI: a Tools page that
renders /api/tools/available and drives /api/tools/run with type-validated inputs.

The point of the bridge was that all 739 flags become addressable through ONE
validated seam. This is the surface that makes them addressable BY A HUMAN.

Pins:
  * a Tools route exists in the SPA and calls both bridge endpoints;
  * the control renders the allowlist DERIVED from the server, never a hardcoded copy
    (a second copy would drift from the allowlist the moment a tool is added/removed);
  * the run endpoint becomes GUI-reachable in the ledger (dark -> wired), and the dark
    ratchet FALLS.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend", "src")


def _fe_source():
    src = ""
    for dp, _dn, fns in os.walk(FE):
        for fn in sorted(fns):
            if fn.endswith((".ts", ".tsx")) and not fn.endswith(".test.tsx"):
                with open(os.path.join(dp, fn), encoding="utf-8", errors="replace") as fh:
                    src += fh.read()
    return src


def test_tools_route_is_registered_in_the_spa():
    app = open(os.path.join(FE, "App.tsx"), encoding="utf-8", errors="replace").read()
    assert '"/tools"' in app or "'/tools'" in app, "no /tools route in App.tsx"


def test_control_calls_both_bridge_endpoints():
    src = _fe_source()
    assert "/api/tools/available" in src, "the control never fetches the allowlist"
    assert "/api/tools/run" in src, "the control never calls the run endpoint"


def test_allowlist_is_fetched_not_hardcoded():
    """The rendered tool list must come FROM the server. A hardcoded copy of the
    allowlist would drift from tool_bridge.ALLOWLIST the instant a tool changes."""
    src = _fe_source()
    # the tool names must NOT be hardcoded as a literal array in the FE
    assert not ('["yt-dlp", "ffprobe"]' in src or "['yt-dlp', 'ffprobe']" in src), (
        "the allowlist is hardcoded in the frontend -- it must be fetched from "
        "/api/tools/available so it cannot drift from the server allowlist")


def test_run_endpoint_is_now_gui_reachable():
    from tools import endpoint_reachability as er

    d = er.build(ROOT)
    run = [e for e in d["endpoints"] if e["rule"] == "/api/tools/run"]
    assert run, "/api/tools/run missing from the ledger"
    assert run[0]["reach"] != "dark", (
        "/api/tools/run is still dark -- the control does not reach it")


def test_dark_ratchet_fell():
    from tools import endpoint_reachability as er

    d = er.build(ROOT)
    base = json.load(open(os.path.join(ROOT, "reports", "endpoint_reachability.json"),
                          encoding="utf-8"))
    now = len([e for e in d["endpoints"] if e["reach"] == "dark"])
    assert now == base["dark_count"], (
        "dark=%d but baseline says %d -- re-pin the ledger in this cut"
        % (now, base["dark_count"]))
    assert "/api/tools/run" not in base.get("dark", []), (
        "/api/tools/run is still ledgered dark")
