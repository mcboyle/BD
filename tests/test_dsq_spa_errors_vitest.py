"""dsq-spa-errors (deep-spa-sites-queue-history F2, F5, F7): failed reads and
no-op writes are visible on NeedsReview, BulkEnqueue, History and Sites.

The behaviour runs in the tracked vitest spec below (real route components,
mocked transport with the answers the finder measured from the backend).
"""

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"

_SPEC = "src/routes/dsqSpaErrors.test.tsx"


def test_dsq_spa_errors_vitest():
    receipt = run_vitest(_SPEC, expected_tests=25)
    expected = {
        "spec": _SPEC,
        "files_passed": 1,
        "files_collected": 1,
        "tests_passed": 25,
        "tests_collected": 25,
    }
    assert receipt == expected, (
        f"dsq-spa-errors vitest receipt mismatch: expected={expected!r}, observed={receipt!r}"
    )
