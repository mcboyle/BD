"""T8: EXECUTE the federation / edge-deploy / device-pairing runtime contract.

WHAT THIS FILE USED TO BE (backlog row 185). Six assertions, all
`Path.read_text()` + substring/regex over frontend/src. Nothing was rendered,
imported or executed. Its headline assertion -- "no write is one-click" -- was
three NEGATIVE regexes of the form `onClick=\\{[^}]*edgeAll\\.mutate`, which are
VACUOUS on their own subject: every onClick in Cluster.tsx binds a named handler
(`onClick={armEdgeAll}`), so a mutate call can never appear lexically inside the
onClick braces in this file's house style. Measured: rebinding the fleet-wide
edge_deploy/all button to a handler that dispatches on one click left the scan
6/6 GREEN over a live violation of the property it named.

WHAT IT IS NOW. The behavioural half is delegated to
`frontend/src/routes/Cluster.wired.test.tsx`, which renders the real route and
drives real interactions, so a respelling of the hazard cannot evade it. Per
CUT_TIERING.md the exemption is read PER ASSERTION, so two textual checks stay
-- with their blind spots DECLARED rather than implied:

  * test_t8_full_literals_present_in_hook -- genuinely textual. Its subject is
    what `tools/gui_parity_inventory.py` can SEE when it scans for SPA
    consumers; a concatenated base would be invisible to that scanner even
    though it works at runtime. Text is the real subject here, not a proxy.

  * test_t8_route_is_lazy_and_nav_linked -- a DECLARED FLOOR, not a proxy that
    thinks it is complete. Route REACHABILITY is judged behaviourally in the
    spec (`renderAppAt("/cluster")` walks App.tsx's real <Route> table). What
    stays textual is (a) that the import is LAZY -- a build/bundling property no
    jsdom render can observe -- and (b) that a command-palette entry exists.
    BLIND SPOTS, stated: an eager import respelled to keep the literal, or a
    palette entry that is present but unreachable, both pass this floor.

DECLARED EXCLUSIONS (writes on /cluster that are NOT in the gated set):
`/api/fed/set_trust`, dispatched one-click from the peer-row <select> onChange,
and `/api/fed/pending_review`, whose Reject arm is one-click. Both are recorded
as FACTS about this gate's denominator, not as product rulings -- gating them is
a separate backlog row. The spec's sweep drives both; it just does not fail on
them.
"""
from pathlib import Path

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "frontend" / "src"

T8_ENDPOINTS = [
    "/api/fed/peers",
    "/api/fed/status",
    "/api/fed/sync_pull",
    "/api/fed/manual_register",
    "/api/edge_deploy/compose",
    "/api/edge_deploy/all",
    "/api/pair",
    "/api/pair/redeem",
]


def test_t8_full_literals_present_in_hook():
    """FULL /api/ literals -- the parity scanner cannot credit a built-up base."""
    hook = (SRC / "hooks" / "useCluster.ts").read_text(encoding="utf-8")
    for ep in T8_ENDPOINTS:
        assert f'"{ep}"' in hook or f"`{ep}" in hook, f"{ep} not a full literal in useCluster"


def test_t8_route_is_lazy_and_nav_linked():
    """DECLARED TEXTUAL FLOOR -- see the module docstring for the blind spots.
    Reachability itself is proven at runtime by the Vitest spec."""
    app = (SRC / "App.tsx").read_text(encoding="utf-8")
    assert 'import("./routes/Cluster")' in app, "the /cluster route stopped being lazy"
    cp = (SRC / "components" / "CommandPalette.tsx").read_text(encoding="utf-8")
    assert 'go("/cluster")' in cp, "no command-palette entry navigates to /cluster"


def test_t8_cluster_runtime_contract():
    """The behavioural half: render the route, drive it, judge what it DOES."""
    run_vitest("src/routes/Cluster.wired.test.tsx", expected_tests=9)
