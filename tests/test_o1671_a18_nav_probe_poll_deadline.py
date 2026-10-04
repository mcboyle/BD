"""Commit polling has its own budget after navigation, with total elapsed time retained."""
import pytest

from tools import nav_probe

BD_GATE_SCOPE = "module"


@pytest.mark.parametrize("navigation_s,ready_after_s,expected_polls,outcome", [
    (0.0, 0.5, 2, "usable"),
    (5.0, 0.5, 2, "usable"),
    (5.0, None, 4, "timeout_unusable"),
])
def test_poll_budget_starts_after_navigation(
    monkeypatch, navigation_s, ready_after_s, expected_polls, outcome,
):
    now = [0.0]
    goto_calls = []
    sleeps = []
    monkeypatch.setattr(nav_probe.time, "monotonic", lambda: now[0])

    def goto(strategy, timeout_ms):
        goto_calls.append((strategy, timeout_ms))
        now[0] += navigation_s

    def read_state():
        ready = ready_after_s is not None and now[0] >= navigation_s + ready_after_s
        return ("interactive", 10, "title", "https://example.invalid/") if ready else (
            "loading", -1, "title", "https://example.invalid/",
        )

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    result = nav_probe.attempt_strategy(
        "commit", goto, read_state, timeout_ms=6000, poll_ms=1000,
        sleep_fn=sleep, poll_interval_ms=250,
    )
    assert result["polls"] == expected_polls, "A18_NAV_CONSUMED_POLL_BUDGET: navigation reduced self-poll time"
    assert result["outcome"] == outcome
    assert result["resolved"] is True
    assert result["elapsed_ms"] == int((navigation_s + expected_polls * 0.25) * 1000)
    assert goto_calls == [("commit", 6000)]
    assert sleeps == [0.25] * expected_polls
