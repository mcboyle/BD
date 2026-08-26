"""T9b: execute push survival, mutation, and service-worker behavior."""

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "repo-wide"


def test_t9b_push_runtime_contract():
    spec = "src/routes/Push.wired.test.tsx"
    receipt = run_vitest(spec, expected_tests=6)
    expected = {
        "spec": spec,
        "files_passed": 1,
        "files_collected": 1,
        "tests_passed": 6,
        "tests_collected": 6,
    }
    assert receipt == expected, (
        "Vitest delegation evidence missing or mismatched for Push: "
        f"expected={expected!r}, observed={receipt!r}"
    )
