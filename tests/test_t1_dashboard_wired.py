"""T1 dashboard wiring is exercised by the real React route and API hooks."""

from tests.frontend_vitest import build_manifest, run_vitest


BD_GATE_SCOPE = "repo-wide"


def test_t1_dashboard_runtime_contract():
    # 5, not 4: the endpoint set is now DERIVED from the hook rather than
    # hand-maintained, and one test guards that derivation against being vacuous.
    run_vitest("src/routes/Dashboard.wired.test.tsx", expected_tests=5)
    manifest = build_manifest()
    dashboard = manifest.get("src/routes/Dashboard.tsx")
    assert isinstance(dashboard, dict), "Dashboard missing from Vite manifest"
    assert dashboard.get("isDynamicEntry") is True, (
        "Dashboard must remain a lazy, separately built route"
    )
