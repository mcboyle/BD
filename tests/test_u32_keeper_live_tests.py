"""U32 — L5 (nested-Playwright collision guard) + L10 (keeper-relaunch),
the two session-keeper INV-001 live tests.

The checks themselves need the running deployment on `stash`; they
cannot truly run in the unit suite. What IS unit-testable: that they
are registered, well-formed, and — crucially — degrade gracefully
(a valid (level, detail) tuple, never an exception) when the target
deployment is unreachable, since the harness records a raised
exception as a hard FAIL.
"""
import live_tests.checks as checks  # noqa: F401 (registers the checks)
import live_tests.harness as h


# Read the harness tuple rather than restating it: a local copy goes stale
# the moment the verdict vocabulary grows (it did -- h.NA was added and every
# copy of this line silently began rejecting a valid level).
_LEVELS = h._LEVELS


def _get_test(test_id):
    for t in h.registry():
        if t.id == test_id:
            return t
    return None


# ── registration ───────────────────────────────────────────────────

def test_l5_and_l10_are_registered():
    ids = {t.id for t in h.registry()}
    assert "L5" in ids
    assert "L10" in ids


def test_l5_and_l10_are_not_disruptive():
    # both are read-only black-box checks — safe to run any time
    assert _get_test("L5").disruptive is False
    assert _get_test("L10").disruptive is False


def test_l5_l10_have_descriptive_names():
    assert "collision" in _get_test("L5").name
    assert "keeper" in _get_test("L10").name


# ── graceful degradation against an unreachable target ─────────────

def _unreachable_ctx():
    # port 1 is unreachable — every ctx.get() returns (False, ...)
    return h.Context("http://localhost:1", "/tmp", disruptive=False)


def test_l5_returns_a_valid_tuple_when_target_unreachable():
    t = _get_test("L5")
    res = t.fn(_unreachable_ctx())
    # must be a (level, detail) tuple — a raised exception would be
    # recorded by the harness as a hard FAIL
    assert isinstance(res, tuple) and len(res) == 2
    level, detail = res
    assert level in _LEVELS
    assert isinstance(detail, str) and detail


def test_l10_returns_a_valid_tuple_when_target_unreachable():
    t = _get_test("L10")
    res = t.fn(_unreachable_ctx())
    assert isinstance(res, tuple) and len(res) == 2
    level, detail = res
    assert level in _LEVELS
    assert isinstance(detail, str) and detail


def test_l5_unreachable_keepers_endpoint_is_warn_not_fail():
    # /api/dev/keepers unreachable -> the test cannot verify INV-001,
    # so it must WARN (cannot conclude), not FAIL (would be a false
    # alarm) and not crash
    level, detail = _get_test("L5").fn(_unreachable_ctx())
    assert level == h.WARN
    assert "keeper" in detail.lower()


def test_l10_unreachable_invariant_endpoint_is_warn_not_fail():
    level, detail = _get_test("L10").fn(_unreachable_ctx())
    assert level == h.WARN
    assert "invariant" in detail.lower() or "resume" in detail.lower()


# ── L5/L10 run through the harness like any live test ──────────────

def test_l5_l10_run_via_harness_and_record_artifacts(tmp_path):
    # the harness runs them; against an unreachable target they WARN,
    # so the run exits 0 (nothing FAILED) and writes per-test logs
    rdir = tmp_path / "results"
    code = h.run_all("http://localhost:1", str(tmp_path),
                     only=["L5", "L10"], results_dir=rdir)
    assert code == 0  # WARN is not FAIL
    assert (rdir / "L5.log").is_file()
    assert (rdir / "L10.log").is_file()
    summary = (rdir / "SUMMARY.txt").read_text()
    assert "L5" in summary and "L10" in summary
    # no spurious failure artifacts
    assert not (rdir / "L5.fail.txt").exists()
    assert not (rdir / "L10.fail.txt").exists()
