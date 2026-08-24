"""T9b: execute push survival, mutation, and service-worker behavior."""

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "repo-wide"


def test_t9b_push_runtime_contract():
    run_vitest("src/routes/Push.wired.test.tsx", expected_tests=6)
