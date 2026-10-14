"""F017: execute the Template Manager draft create / edit / delete flow."""

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"


def test_f017_template_crud_runtime_contract():
    spec = "src/routes/TemplateManager.crud.test.tsx"
    receipt = run_vitest(spec, expected_tests=3)
    expected = {
        "spec": spec,
        "files_passed": 1,
        "files_collected": 1,
        "tests_passed": 3,
        "tests_collected": 3,
    }
    assert receipt == expected, (
        "Vitest delegation evidence missing or mismatched for F017: "
        f"expected={expected!r}, observed={receipt!r}"
    )
