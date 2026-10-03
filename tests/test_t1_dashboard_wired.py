"""T1 dashboard wiring is exercised by the real React route and API hooks."""

from tests.frontend_vitest import build_manifest, run_vitest


BD_GATE_SCOPE = "repo-wide"


def test_t1_dashboard_runtime_contract():
    # 5, not 4: the endpoint set is now DERIVED from the hook rather than
    # hand-maintained, and one test guards that derivation against being vacuous.
    # 7, not 5: F061 adds the /api/dashboard failure alert test and its
    # no-alert positive control.
    # 9, not 7: o1698-f061-retry-coverage clicks Retry (outage recovery + persistent-failure control).
    spec = "src/routes/Dashboard.wired.test.tsx"
    receipt = run_vitest(spec, expected_tests=9)
    expected = {
        "spec": spec,
        "files_passed": 1,
        "files_collected": 1,
        "tests_passed": 9,
        "tests_collected": 9,
    }
    assert receipt == expected, (
        "Vitest delegation evidence missing or mismatched for Dashboard: "
        f"expected={expected!r}, observed={receipt!r}"
    )
    manifest = build_manifest()
    dashboard = manifest.get("src/routes/Dashboard.tsx")
    assert isinstance(dashboard, dict), "Dashboard missing from Vite manifest"
    assert dashboard.get("isDynamicEntry") is True, (
        "Dashboard must remain a lazy, separately built route"
    )
