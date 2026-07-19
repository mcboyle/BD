"""C4 (12.1) -- per-file timeout BUDGET surfacing in the test report.

run_tests already emits slowest-N and enforces a HARD per-file wall timeout
(_FILE_TIMEOUT_S, default 900s, which fails the file). This adds a SOFT budget:
a per-file wall-time threshold whose only effect is to SURFACE over-budget files
in the report (summary section + JSON `budget` block) so a newly-slow file is
visible long before it hits the hard kill. It never changes pass/fail.

The budget math is a pure helper (_files_over_budget) tested directly here.
RED on pristine 3.66.618: run_tests has no _files_over_budget / _FILE_BUDGET_S.
"""
import run_tests


def test_helper_exists():
    assert hasattr(run_tests, "_files_over_budget"), (
        "run_tests must expose the pure per-file budget helper")
    assert hasattr(run_tests, "_FILE_BUDGET_S"), (
        "run_tests must expose the env-tunable soft budget threshold")


def _durations():
    # (duration_seconds, file, test) -- the shape run_tests.all_durations uses.
    return [
        (40.0, "test_alpha.py", "t1"),
        (50.0, "test_alpha.py", "t2"),   # alpha total = 90s
        (30.0, "test_beta.py", "t1"),    # beta total = 30s (under a 60s budget)
        (25.0, "test_gamma.py", "t1"),
        (25.0, "test_gamma.py", "t2"),
        (25.0, "test_gamma.py", "t3"),   # gamma total = 75s
    ]


def test_over_budget_files_identified_and_sorted():
    over = run_tests._files_over_budget(_durations(), 60)
    files = [f for f, _s in over]
    # alpha (90) and gamma (75) exceed 60; beta (30) does not.
    assert files == ["test_alpha.py", "test_gamma.py"], files
    # per-file totals reported, slowest first.
    assert abs(dict(over)["test_alpha.py"] - 90.0) < 1e-6
    assert abs(dict(over)["test_gamma.py"] - 75.0) < 1e-6
    assert "test_beta.py" not in dict(over)


def test_high_budget_flags_nothing():
    assert run_tests._files_over_budget(_durations(), 10_000) == []


def test_empty_durations_is_empty():
    assert run_tests._files_over_budget([], 60) == []


def test_boundary_is_strictly_over():
    # A file exactly at the budget is NOT over-budget (strictly greater).
    rows = [(60.0, "test_exact.py", "t1")]
    assert run_tests._files_over_budget(rows, 60) == []
    rows2 = [(60.001, "test_exact.py", "t1")]
    assert [f for f, _ in run_tests._files_over_budget(rows2, 60)] == \
        ["test_exact.py"]
