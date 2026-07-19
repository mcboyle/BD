"""T10 template/macros/dev/plugins/synthetic/i18n tranche — pins (v3.66.211).

Proves the 16 NAMED legacy-only T10 families are now SPA-wired (template 4 ·
macros 3 · dev 4 · plugins 2 · synthetic_tests 2 · i18n 1) and drop out of
the legacy-only set; the three section components carry FULL /api/ literals
via their hooks and are mounted in the existing /templates, /pools-macros and
/settings/advanced routes; the B-tier writes (template/sandbox, macros/replay,
dev/run, synthetic_tests/run_all) are confirm-gated (never one-click);
macros/replay carries the INV-001 pause-workers warning; and the /api/{x}
catch-all is a DOCUMENTED Phase-4 residual (the legacy app.js dynamic-dispatch
artifact), so the ratchet lands at 10 (not 9) by design.

RED on pristine v3.66.210 (none wired; baseline 34; no useDevTools/
useMacrosOps/useTemplateAuthoring hooks; no section components).

run_tests.py conventions: zero-arg test functions; repo root from __file__.
"""
import importlib.util
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "frontend" / "src"

# 16 NAMED T10 families (the catch-all /api/{x} is NOT here — it is a P4
# residual, asserted separately below).
T10_NAMED = [
    "/api/template/extract",
    "/api/template/refine",
    "/api/template/sandbox",
    "/api/templates",
    "/api/macros/get/{x}",
    "/api/macros/save",
    "/api/macros/replay/{x}",
    "/api/dev/enabled",
    "/api/dev/discover",
    "/api/dev/run",
    "/api/dev/runs/{x}",
    "/api/plugins/status",
    "/api/plugins/events",
    "/api/synthetic_tests/list",
    "/api/synthetic_tests/run_all",
    "/api/i18n/load/{x}",
]

HOOKS = {
    "useTemplateAuthoring.ts": [
        "/api/templates", "/api/template/extract", "/api/template/refine",
        "/api/template/sandbox",
    ],
    "useMacrosOps.ts": [
        "/api/macros/get/", "/api/macros/save", "/api/macros/replay/",
    ],
    "useDevTools.ts": [
        "/api/dev/enabled", "/api/dev/discover", "/api/dev/run", "/api/dev/runs/",
        "/api/plugins/status", "/api/plugins/events",
        "/api/synthetic_tests/list", "/api/synthetic_tests/run_all",
        "/api/i18n/load/",
    ],
}

SECTIONS = {
    "TemplateAuthoringSection.tsx": ("routes/TemplateManager.tsx", "useTemplateAuthoring"),
    "MacrosOpsSection.tsx": ("routes/PoolsMacros.tsx", "useMacrosOps"),
    "DevToolsSection.tsx": ("routes/Advanced.tsx", "useDevTools"),
}


def _load_legacy_parity():
    spec = importlib.util.spec_from_file_location(
        "legacy_parity", REPO / "tools" / "legacy_parity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _norm(s):
    return {re.sub(r"\{[^}]+\}", "*", e) for e in s}


def test_all_16_named_t10_endpoints_are_spa_wired():
    lp = _load_legacy_parity()
    legacy_only = _norm(set(lp.measure()["legacy_only"]))
    want = _norm(set(T10_NAMED))
    still = sorted(want & legacy_only)
    assert not still, "T10 named endpoints still legacy-only: " + repr(still)


def test_t10_full_literals_present_in_hooks():
    for hookname, eps in HOOKS.items():
        src = (SRC / "hooks" / hookname).read_text(encoding="utf-8")
        for ep in eps:
            assert f'"{ep}' in src or f"`{ep}" in src, f"{ep} not a full literal in {hookname}"


def test_t10_sections_mounted_and_consume_hooks():
    for section, (routerel, hook) in SECTIONS.items():
        sec = (SRC / "components" / "sections" / section).read_text(encoding="utf-8")
        assert hook in sec, f"{section} must import {hook}"
        route = (SRC / routerel).read_text(encoding="utf-8")
        comp = section[:-4]  # strip .tsx
        assert f"<{comp} />" in route, f"{comp} not mounted in {routerel}"
        assert comp in route and "import" in route


def test_t10_b_tier_writes_are_confirm_gated():
    """sandbox / replay / dev-run / run-all never fire straight from an
    onClick — each opens a confirm dialog first."""
    tpl = (SRC / "components" / "sections" / "TemplateAuthoringSection.tsx").read_text()
    assert not re.search(r"onClick=\{[^}]*sandbox\.mutate", tpl)
    assert "setConfirmSandbox(true)" in tpl

    mac = (SRC / "components" / "sections" / "MacrosOpsSection.tsx").read_text()
    assert not re.search(r"onClick=\{[^}]*replay\.mutate", mac)
    assert "setConfirmReplay(true)" in mac

    dev = (SRC / "components" / "sections" / "DevToolsSection.tsx").read_text()
    assert not re.search(r"onClick=\{[^}]*\brun\.mutate", dev)
    assert not re.search(r"onClick=\{[^}]*runAll\.mutate", dev)
    assert "setConfirmRun(true)" in dev and "setConfirmRunAll(true)" in dev


def test_t10_macros_replay_carries_inv001_warning():
    """The replay confirm must warn the operator to pause running workers
    (INV-001 nested-Playwright collision)."""
    mac = (SRC / "components" / "sections" / "MacrosOpsSection.tsx").read_text()
    assert "INV-001" in mac
    assert "pause" in mac.lower() and "worker" in mac.lower()


def test_t10_dev_console_gated_behind_dev_enabled():
    """The dev test runner renders only when /api/dev/enabled reports
    enabled (release trees default OFF)."""
    dev = (SRC / "components" / "sections" / "DevToolsSection.tsx").read_text()
    assert "useDevEnabled" in dev
    assert "Dev mode is disabled" in dev


def test_t10_api_x_catchall_is_documented_p4_residual():
    """/api/{x} was the legacy app.js dynamic-dispatch artifact — the last
    legacy-only endpoint, NOT SPA-wireable without re-introducing the dynamic
    pattern T10 removes. Phase 4 (v3.66.334) deleted the legacy shell, so the
    residual is now CLEARED: the baseline legacy-only surface is empty."""
    base = json.loads((REPO / "reports" / "legacy_parity_baseline.json").read_text())
    # Phase 4 cleared the last residual: floor is now 0 (was 1, the /api/{x} tombstone).
    assert base["legacy_only_count"] == 0, base["legacy_only_count"]
    assert "/api/{x}" not in base["legacy_only"], "{x} residual must be cleared at Phase 4"
    assert base["legacy_only"] == [], base["legacy_only"]
    assert "Phase 4" in base["note"] or "P4" in base["note"]
    # the new T10 hooks must NOT root-dispatch via a dynamic /api/${...}
    # (every literal carries a named first segment; ${x} only on path params).
    for hookname in HOOKS:
        txt = (SRC / "hooks" / hookname).read_text(encoding="utf-8")
        assert "`/api/${" not in txt, f"root dynamic /api/${{}} dispatch in {hookname} — defeats T10"
