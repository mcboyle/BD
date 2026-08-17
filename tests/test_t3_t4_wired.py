"""Current library, tags, sites, and runner SPA contract.

Proves the 23 endpoint families (library 4 · tags 6 · scene_score 1 ·
storage_rebalance 1; T4: sites-bulk 3 · runners 2 · concurrent 1 · rate_limit 1
· retry_policy 1 · crash_recovery 2 · file 1) are now SPA-wired and drop out
remain SPA-wired and dangerous-selection writes carry typed confirm tokens.

run_tests.py conventions: zero-arg test functions; repo root from __file__;
no pytest builtins.
"""
import re
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

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
