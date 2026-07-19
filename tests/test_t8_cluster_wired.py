"""T8 fed/edge_deploy/pair tranche — migration pins (v3.66.211).

Proves the 8 legacy-only fed/edge_deploy/pair families are now SPA-wired
(they drop out of the legacy_parity legacy-only set), the /cluster route is
lazy-loaded with an inbound nav link, the federation TRUST write
(manual_register) and the fleet-wide write (edge_deploy/all) are A-tier
(No-default yes/no + amber label), sync_pull is wired as a MUTATION (never a
query, so it cannot auto-fire), the pair/redeem token is a write-only (R)
secret (SecretField, never seeded, cleared after submit), no write is
one-click, and the ratchet baseline committed the 34 -> 10 shrink.

RED on pristine v3.66.210 (none of these were wired; baseline was 34;
no useCluster hook; no /cluster route).

run_tests.py conventions: zero-arg test functions; repo root from __file__;
no pytest builtins.
"""
import importlib.util
import json
import re
from pathlib import Path

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


def _load_legacy_parity():
    spec = importlib.util.spec_from_file_location(
        "legacy_parity", REPO / "tools" / "legacy_parity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _norm(s):
    return {re.sub(r"\{[^}]+\}", "*", e) for e in s}


def test_all_8_t8_endpoints_are_spa_wired():
    """None of the 8 T8 families may remain in the legacy-only set."""
    lp = _load_legacy_parity()
    legacy_only = _norm(set(lp.measure()["legacy_only"]))
    still = [ep for ep in T8_ENDPOINTS if ep in legacy_only]
    assert not still, "T8 endpoints still legacy-only: " + repr(still)


def test_t8_full_literals_present_in_hook():
    """FULL /api/ literals — the scanner cannot credit concatenated bases."""
    hook = (SRC / "hooks" / "useCluster.ts").read_text(encoding="utf-8")
    for ep in T8_ENDPOINTS:
        assert f'"{ep}"' in hook or f"`{ep}" in hook, f"{ep} not a full literal in useCluster"


def test_t8_route_lazy_and_nav_linked():
    """/cluster is a lazy, default-exported route with an inbound nav link."""
    app = (SRC / "App.tsx").read_text(encoding="utf-8")
    assert 'import("./routes/Cluster")' in app
    assert 'path="/cluster"' in app
    route = (SRC / "routes" / "Cluster.tsx").read_text(encoding="utf-8")
    assert "export default function Cluster" in route
    cp = (SRC / "components" / "CommandPalette.tsx").read_text(encoding="utf-8")
    assert 'go("/cluster")' in cp


def test_t8_sync_pull_is_a_mutation_not_a_query():
    """sync_pull has a GET side-effect; it must be a useMutation so
    react-query never auto-fires it on mount/focus/interval."""
    hook = (SRC / "hooks" / "useCluster.ts").read_text(encoding="utf-8")
    m = re.search(r"export function useFedSyncPull\(\).*?\n}", hook, re.S)
    assert m, "useFedSyncPull not found"
    assert "useMutation" in m.group(0), "sync_pull must be a useMutation"
    assert not re.search(r"\buseQuery\b", m.group(0)), "sync_pull must NOT be a useQuery"


def test_t8_trust_and_fleet_writes_are_a_tier():
    """manual_register (trust boundary) and edge_deploy/all (fleet-wide) are
    A-tier: No-default focused + amber label."""
    route = (SRC / "routes" / "Cluster.tsx").read_text(encoding="utf-8")
    # the two A-tier arms set tier:"A" with an amberLabel
    assert route.count('tier: "A"') >= 2, "expected >=2 A-tier writes"
    assert "amberLabel" in route
    # A-tier dialog focuses the No, cancel button (No-default)
    assert re.search(r"autoFocus\s+variant=\"default\"", route), "No-default not autofocused"
    assert "text-amber-300" in route


def test_t8_pair_redeem_token_is_write_only():
    """(R) rule: the redeem token is a write-only secret — SecretField,
    starts empty, cleared after a successful redeem, never seeded from a GET."""
    route = (SRC / "routes" / "Cluster.tsx").read_text(encoding="utf-8")
    assert "SecretField" in route, "redeem token must use the write-only SecretField"
    assert 'useState("")' in route, "redeem token must start empty"
    assert 'setRedeemToken("")' in route, "redeem token must be cleared after submit"


def test_t8_writes_never_one_click():
    """No gated write fires straight from an onClick — each arms a Pending /
    confirm and dispatches from the dialog."""
    route = (SRC / "routes" / "Cluster.tsx").read_text(encoding="utf-8")
    assert not re.search(r"onClick=\{[^}]*register\.mutate", route)
    assert not re.search(r"onClick=\{[^}]*edgeAll\.mutate", route)
    assert not re.search(r"onClick=\{[^}]*redeem\.mutate", route)
    assert "const confirmRun = () =>" in route


def test_t8_ratchet_committed_to_10():
    """Baseline committed the 34 -> 10 shrink; none of the 8 T8 endpoints
    remain in it."""
    base = json.loads((REPO / "reports" / "legacy_parity_baseline.json").read_text())
    # Ceiling, not equality: the ratchet is monotonic-down and later tranches
    # (T9a -> 5, T9b -> 1) drive it below 10. The invariant is that the 8 T8
    # families never return to the baseline (checked below); == 10 only re-broke
    # on the next migration (stale-magnitude-floor lesson, cf. the T7 fix).
    assert base["legacy_only_count"] <= 10, base["legacy_only_count"]
    norm = _norm(set(base["legacy_only"]))
    for ep in T8_ENDPOINTS:
        assert ep not in norm, f"{ep} still in baseline"
