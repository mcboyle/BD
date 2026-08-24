"""T9a live/stream wiring is exercised through the real React consumers."""

from tests.frontend_vitest import run_vitest


BD_GATE_SCOPE = "repo-wide"


def test_t9a_live_stream_runtime_contract():
    run_vitest("src/routes/LiveStream.wired.test.tsx", expected_tests=5)
