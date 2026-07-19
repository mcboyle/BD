"""T3+T4 batched tranche — migration pins (v3.66.207, amended pacing).

Proves the 23 legacy-only families (T3: library 4 · tags 6 · scene_score 1 ·
storage_rebalance 1; T4: sites-bulk 3 · runners 2 · concurrent 1 · rate_limit 1
· retry_policy 1 · crash_recovery 2 · file 1) are now SPA-wired and drop out
of the legacy_parity legacy-only set, the dangerous-selection writes carry
TYPED confirm tokens (never one-click), and the ratchet baseline committed the
94 -> 71 shrink. RED on pristine v3.66.206 (none of these were wired; baseline
was 94).

Ratchet pins here use MONOTONIC semantics (<= ceiling + own-endpoints-absent)
so they stay green when later tranches shrink the baseline further — the
lesson from the T1 == 106 pin failing on-stash after T2 landed.

run_tests.py conventions: zero-arg test functions; repo root from __file__;
no pytest builtins.
"""
import importlib.util
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The 23 families this batched cut ports (baseline spelling; {x} -> *).
T3_ENDPOINTS = [
    "/api/library/audit",
    "/api/library/orphans",
    "/api/library/regen_nfos",
    "/api/library/stats",
    "/api/tags/add",
    "/api/tags/for_many",
    "/api/tags/remove",
    "/api/tags/rename",
    "/api/tags/rows/*",
    "/api/tags/suggest/*",
    "/api/scene_score/bottom",
    "/api/storage_rebalance/inventory",
]
T4_ENDPOINTS = [
    "/api/sites/bulk_csv",
    "/api/sites/csv_template",
    "/api/sites/xlsx_template",
    "/api/runners/pause_all",
    "/api/runners/resume_all",
    "/api/concurrent/*",
    "/api/rate_limit/status",
    "/api/retry_policy",
    "/api/crash_recovery/scan",
    "/api/crash_recovery/*",
    "/api/file/reveal",
]


def _load_legacy_parity():
    spec = importlib.util.spec_from_file_location(
        "legacy_parity", REPO / "tools" / "legacy_parity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _norm(endpoints):
    return {re.sub(r"\{[^}]+\}", "*", e) for e in endpoints}


def test_all_23_t3_t4_endpoints_are_spa_wired():
    """None of the 23 batched families may remain in the legacy-only set."""
    lp = _load_legacy_parity()
    legacy_only = _norm(lp.measure()["legacy_only"])
    still = [ep for ep in T3_ENDPOINTS + T4_ENDPOINTS if ep in legacy_only]
    assert not still, (
        "T3/T4 endpoints still legacy-only (not SPA-wired): " + repr(still))


def test_t3_full_literals_present_in_spa_source():
    """T3 wiring must be FULL /api/ literals in useLibraryOps.ts."""
    hooks = (REPO / "frontend" / "src" / "hooks" / "useLibraryOps.ts").read_text(
        encoding="utf-8")
    needed = [
        '"/api/library/stats"',
        '"/api/library/audit"',
        '"/api/library/orphans"',
        '"/api/library/regen_nfos"',
        '"/api/tags/for_many"',
        '"/api/tags/add"',
        '"/api/tags/remove"',
        '"/api/tags/rename"',
        "/api/tags/rows/${",
        "/api/tags/suggest/${hid}",
        "/api/scene_score/bottom?limit=",
        '"/api/storage_rebalance/inventory"',
    ]
    missing = [n for n in needed if n not in hooks]
    assert not missing, "full literals missing from useLibraryOps.ts: " + repr(missing)


def test_t4_full_literals_present_in_spa_source():
    """T4 wiring must be FULL /api/ literals in useOpsControls.ts."""
    hooks = (REPO / "frontend" / "src" / "hooks" / "useOpsControls.ts").read_text(
        encoding="utf-8")
    needed = [
        '"/api/runners/pause_all"',
        '"/api/runners/resume_all"',
        "/api/concurrent/${",
        '"/api/rate_limit/status"',
        '"/api/retry_policy"',
        '"/api/crash_recovery/scan"',
        "/api/crash_recovery/${action}",
        '"/api/file/reveal"',
        '"/api/sites/bulk_csv"',
        '"/api/sites/csv_template"',
        '"/api/sites/xlsx_template"',
    ]
    missing = [n for n in needed if n not in hooks]
    assert not missing, "full literals missing from useOpsControls.ts: " + repr(missing)


def test_dangerous_selection_writes_are_confirm_tiered():
    """v3.66.209 confirm tiers: pause_all / resume_all are SINGLE-TAP
    (reversible — token: ""), crash delete (deletes files) stays in the
    destructive tier; everything still dispatches only from confirmRun —
    never one-click."""
    maint = (REPO / "frontend" / "src" / "routes" / "Maintenance.tsx").read_text(
        encoding="utf-8")
    assert '{ kind: "pauseAll"; token: "" }' in maint, "pauseAll not single-tap tier"
    assert '{ kind: "resumeAll"; token: "" }' in maint, "resumeAll not single-tap tier"
    assert 'token: "DELETE PART"' in maint, "crash delete left the destructive tier"
    for kind in ("pauseAll", "resumeAll", "crashDelete", "crashIgnore",
                 "crashResume", "setConcurrent", "fileReveal"):
        assert f'kind: "{kind}"' in maint, f"Pending kind {kind!r} missing"
    assert not re.search(r"onClick=\{[^}]*\.mutate", maint), (
        "a Maintenance write mutation is wired one-click")

    lib = (REPO / "frontend" / "src" / "routes" / "Library.tsx").read_text(
        encoding="utf-8")
    # v3.66.209: bulk-tag + NFO writes are single-tap tier (reversible /
    # additive) but must still arm through the confirm dialog.
    for kind in ("tagAdd", "tagRemove", "tagRename", "regenNfos"):
        assert f'kind: "{kind}"' in lib, f"Library kind {kind!r} missing"
    assert not re.search(r"onClick=\{[^}]*\.(?:mutate)\(\{ overwrite", lib), (
        "regenNfos real run wired one-click")

    imports = (REPO / "frontend" / "src" / "routes" / "ImportsCenter.tsx").read_text(
        encoding="utf-8")
    assert 'kind: "bulkSites"' in imports, "bulk site import not confirm-armed"


def test_ratchet_baseline_committed_at_or_below_71():
    """The legacy_parity baseline must carry the T3+T4 shrink. MONOTONIC pin:
    <= 71 (may shrink further in later tranches, may never grow), and none of
    this cut's endpoints may remain in it."""
    b = json.loads((REPO / "reports" / "legacy_parity_baseline.json").read_text(
        encoding="utf-8"))
    assert b["legacy_only_count"] <= 71, (
        f"baseline count {b['legacy_only_count']} > 71 — T3/T4 shrink not committed")
    assert b["legacy_only_count"] == len(b["legacy_only"])
    leftovers = _norm(b["legacy_only"]) & set(T3_ENDPOINTS + T4_ENDPOINTS)
    assert not leftovers, "T3/T4 endpoints still in baseline: " + repr(sorted(leftovers))
