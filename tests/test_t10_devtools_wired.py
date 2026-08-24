"""T10: execute template, macro, and developer-tool behavior."""

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "repo-wide"


def test_t10_devtools_runtime_contract():
    # 7, not 6: one test now renders the REAL route table, so the <Route>
    # binding is judged and not only the component behind it.
    run_vitest("src/routes/DevTools.wired.test.tsx", expected_tests=7)
