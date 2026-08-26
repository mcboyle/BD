"""T9a live/stream wiring is exercised through the real React consumers."""

from tests.frontend_vitest import run_vitest


BD_GATE_SCOPE = "repo-wide"


def test_t9a_live_stream_runtime_contract():
    spec = "src/routes/LiveStream.wired.test.tsx"
    receipt = run_vitest(spec, expected_tests=5)
    expected = {
        "spec": spec,
        "files_passed": 1,
        "files_collected": 1,
        "tests_passed": 5,
        "tests_collected": 5,
    }
    assert receipt == expected, (
        "Vitest delegation evidence missing or mismatched for LiveStream: "
        f"expected={expected!r}, observed={receipt!r}"
    )
