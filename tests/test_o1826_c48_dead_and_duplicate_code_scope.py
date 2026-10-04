"""O1826 BRIEF-48 M205: one CSRF refetch-retry helper that keeps error bodies."""

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"


def test_o1826_c48_m205_csrf_retry_keeps_error_body():
    spec = "src/lib/api-client.retry.test.ts"
    receipt = run_vitest(spec, expected_tests=6)
    assert receipt["tests_passed"] == 6, receipt
