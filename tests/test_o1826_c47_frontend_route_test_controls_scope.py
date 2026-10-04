"""O1826 BRIEF-47: frontend route test negative controls (TECH_DEBT M213, M214, M219).

M213  History.route.test.tsx's negative control waited a fixed 500 ms for a lazy
      route the positive case gives 20 s, so it passed while the chunk loaded.
M219  Notifications.route.test.tsx: the same 500 ms against 20 s.
M214  Home.onboarding.test.tsx's negative waitFor passed on the first render,
      before the mocked fetches resolved.

Each negative control now waits on a positive signal before asserting absence.
The three tracked vitest specs run through the bridge below.
"""

import pytest

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"

_SPECS = [
    ("src/routes/History.route.test.tsx", 3),
    ("src/routes/Home.onboarding.test.tsx", 2),
    ("src/routes/Notifications.route.test.tsx", 3),
]


@pytest.mark.parametrize(("spec", "expected_tests"), _SPECS)
def test_route_test_controls_vitest(spec, expected_tests):
    receipt = run_vitest(spec, expected_tests=expected_tests)
    expected = {
        "spec": spec,
        "files_passed": 1,
        "files_collected": 1,
        "tests_passed": expected_tests,
        "tests_collected": expected_tests,
    }
    assert receipt == expected, (
        f"O1826-C47 vitest receipt mismatch: expected={expected!r}, observed={receipt!r}"
    )
