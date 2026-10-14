"""F041: /budget edits the global daily byte cap in place; Cancel discards the draft."""

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"


def test_f041_budget_edit_then_cancel():
    spec = "src/routes/Budget.editCancel.test.tsx"
    receipt = run_vitest(spec, expected_tests=5)
    assert receipt["tests_passed"] == 5, receipt
