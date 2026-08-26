"""T10: execute template, macro, and developer-tool behavior."""

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "repo-wide"


def test_t10_devtools_runtime_contract():
    # 7, not 6: one test now renders the REAL route table, so the <Route>
    # binding is judged and not only the component behind it.
    spec = "src/routes/DevTools.wired.test.tsx"
    receipt = run_vitest(spec, expected_tests=7)
    expected = {
        "spec": spec,
        "files_passed": 1,
        "files_collected": 1,
        "tests_passed": 7,
        "tests_collected": 7,
    }
    assert receipt == expected, (
        "Vitest delegation evidence missing or mismatched for DevTools: "
        f"expected={expected!r}, observed={receipt!r}"
    )
