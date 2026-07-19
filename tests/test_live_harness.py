"""Tests for the live-test harness (live_tests/harness.py).

The L22/L26 checks themselves need the running deployment on `stash`
and cannot run in the unit suite — but the harness around them
(registration, result recording, the artifact contract, the exit
code) is fully exercisable here with fake tests.
"""
import pytest

import live_tests.checks  # noqa: F401  (populates the registry)
import live_tests.harness as h


# T56 (v3.63.3): the live-test registry is a process-global. The
# tests below clear it to exercise registration in isolation. The
# original setup_module / teardown_module pair was a no-op under
# this project's custom run_tests.py runner (the runner only
# honours class-based setup/teardown and autouse fixtures, not
# pytest's module-level lifecycle), so the registry leaked to
# every subsequent test file in the same batch — turning
# test_u32..u43 and test_t46 into 100+ spurious failures. The
# autouse fixture below snapshots the real registry at import
# and restores it AROUND EVERY TEST in this file, which the
# runner does honour.
_REGISTRY_SNAPSHOT = list(h._REGISTRY)


@pytest.fixture(autouse=True)
def autouse_restore_live_registry():
    """Run test → restore the real-checks registry. Always restores
    to `_REGISTRY_SNAPSHOT` (the real registry as it stood at module
    import), never to "whatever was there when this test started" —
    because the previous test in this file may have left the
    registry full of fakes.

    T56 note: the custom run_tests.py runner skips underscore-
    prefixed module-level callables during autouse discovery
    (line ~393: `not name.startswith("_")`), so this name must be
    public-ish. Hence `autouse_*` rather than `_*`.
    """
    yield
    h._REGISTRY[:] = _REGISTRY_SNAPSHOT


def _reset():
    """Each test starts from an empty registry — registration is
    module-global, so tests must not leak into each other.

    Safe because the autouse fixture above restores the registry
    after every test."""
    h._REGISTRY.clear()


def test_decorator_registers_a_test():
    _reset()

    @h.live_test("T1", "fake-pass")
    def _t1(ctx):
        return h.PASS, "ok"

    reg = h.registry()
    assert len(reg) == 1
    assert reg[0].id == "T1"
    assert reg[0].name == "fake-pass"
    assert reg[0].disruptive is False


def test_run_writes_log_summary_and_fail_artifacts(tmp_path):
    _reset()
    rdir = tmp_path / "results"

    @h.live_test("T_PASS", "fake-pass")
    def _tp(ctx):
        ctx.log("doing the pass thing")
        return h.PASS, "all good"

    @h.live_test("T_FAIL", "fake-fail")
    def _tf(ctx):
        ctx.log("about to fail")
        return h.FAIL, "expected X, got Y"

    code = h.run_all("http://localhost:1", str(tmp_path),
                     results_dir=rdir)
    assert code == 1  # one test FAILED

    # per-test .log is always written
    assert (rdir / "T_PASS.log").is_file()
    assert (rdir / "T_FAIL.log").is_file()
    assert "doing the pass thing" in (rdir / "T_PASS.log").read_text()

    # SUMMARY.txt carries a header + one line per test
    summary = (rdir / "SUMMARY.txt").read_text()
    assert "live-test run" in summary
    assert "PASS  T_PASS  all good" in summary
    assert "FAIL  T_FAIL  expected X, got Y" in summary

    # .fail.txt ONLY for the failing test
    assert (rdir / "T_FAIL.fail.txt").is_file()
    assert not (rdir / "T_PASS.fail.txt").exists()
    fail = (rdir / "T_FAIL.fail.txt").read_text()
    assert "expected X, got Y" in fail
    assert "log tail" in fail


def test_run_all_pass_exits_zero(tmp_path):
    _reset()

    @h.live_test("T_OK", "fake-ok")
    def _tok(ctx):
        return h.PASS, "fine"

    code = h.run_all("http://localhost:1", str(tmp_path),
                     results_dir=tmp_path / "r")
    assert code == 0


def test_exception_in_a_test_is_recorded_as_fail(tmp_path):
    _reset()

    @h.live_test("T_BOOM", "fake-explode")
    def _boom(ctx):
        raise RuntimeError("kaboom")

    rdir = tmp_path / "r"
    code = h.run_all("http://localhost:1", str(tmp_path),
                     results_dir=rdir)
    assert code == 1
    assert "FAIL  T_BOOM" in (rdir / "SUMMARY.txt").read_text()
    assert "kaboom" in (rdir / "T_BOOM.fail.txt").read_text()


def test_stale_fail_artifact_is_cleared_on_pass(tmp_path):
    _reset()
    rdir = tmp_path / "r"
    rdir.mkdir()
    stale = rdir / "T_FLIP.fail.txt"
    stale.write_text("old failure")

    @h.live_test("T_FLIP", "now-passes")
    def _flip(ctx):
        return h.PASS, "recovered"

    h.run_all("http://localhost:1", str(tmp_path), results_dir=rdir)
    assert not stale.exists()  # cleared because the test now passes


def test_disruptive_tests_skipped_unless_flagged(tmp_path):
    _reset()

    @h.live_test("T_SAFE", "safe")
    def _ts(ctx):
        return h.PASS, "safe"

    @h.live_test("T_DANGER", "disruptive", disruptive=True)
    def _td(ctx):
        return h.PASS, "ran"

    rdir = tmp_path / "r"
    h.run_all("http://localhost:1", str(tmp_path), results_dir=rdir)
    assert (rdir / "T_SAFE.log").is_file()
    assert not (rdir / "T_DANGER.log").exists()  # skipped by default

    rdir2 = tmp_path / "r2"
    h.run_all("http://localhost:1", str(tmp_path),
              include_disruptive=True, results_dir=rdir2)
    assert (rdir2 / "T_DANGER.log").is_file()  # runs with the flag


def test_only_filter_selects_named_tests(tmp_path):
    _reset()

    @h.live_test("T_A", "a")
    def _ta(ctx):
        return h.PASS, "a"

    @h.live_test("T_B", "b")
    def _tb(ctx):
        return h.PASS, "b"

    rdir = tmp_path / "r"
    h.run_all("http://localhost:1", str(tmp_path),
              only=["T_B"], results_dir=rdir)
    assert (rdir / "T_B.log").is_file()
    assert not (rdir / "T_A.log").exists()
