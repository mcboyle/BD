"""dsw-fix F10: a write that never completed toasts the bare "Failed to fetch".

The behaviour runs render-free in the tracked vitest spec below (api-client
fetch wrapper + the global mutation toast text).
"""
from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"

_SPEC = "src/lib/api-client.network.test.ts"


def test_dsw_fix_network_error_vitest():
    receipt = run_vitest(_SPEC, expected_tests=5)
    expected = {
        "spec": _SPEC,
        "files_passed": 1,
        "files_collected": 1,
        "tests_passed": 5,
        "tests_collected": 5,
    }
    assert receipt == expected, (
        f"dsw-fix F10 vitest receipt mismatch: expected={expected!r}, observed={receipt!r}"
    )
