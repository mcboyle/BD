"""U42 — L31 (memory-stability-long-run) + L32 (no-thread-growth) +
L33 (no-leaked-chromium), the three resource-stability samplers.

They poll a dev endpoint N times over a window and check the metric
stays bounded (lesson J1 — unbounded retention is the real leak
class). All disruptive=False (read-only polling). Unit-testable here:
registration, the _sample_over_time early-bail, the _trend math, and
the verdict branches against synthetic series.
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


class _SeriesContext(h.Context):
    """Context whose .get() replays a fixed list of response bodies —
    one per poll — so the samplers can be driven with synthetic data
    and no real sleeping."""

    def __init__(self, bodies, reachable=True):
        super().__init__("http://localhost:1", "/tmp")
        self._bodies = bodies
        self._i = 0
        self._reachable = reachable

    def get(self, path, timeout=15):
        if not self._reachable:
            return False, None, "unreachable", 1.0
        b = self._bodies[min(self._i, len(self._bodies) - 1)]
        self._i += 1
        return True, 200, b, 1.0


# The custom run_tests.py is NOT pytest and does not call
# setup_module/teardown_module. Zero the sampler's window at IMPORT
# time instead — the runner does import this module, so this runs.
# _sample_over_time reads _SAMPLE_COUNT/_SAMPLE_GAP_S at call time,
# so this makes every sampler test instant (no real sleeping).
checks._SAMPLE_COUNT = 4
checks._SAMPLE_GAP_S = 0.0


# ── registration ───────────────────────────────────────────────────

def test_l31_l32_l33_registered():
    ids = {t.id for t in h.registry()}
    assert {"L31", "L32", "L33"} <= ids


def test_resource_tests_are_not_disruptive():
    # read-only polling — safe on a default run
    for tid in ("L31", "L32", "L33"):
        assert _get_test(tid).disruptive is False


# ── _trend helper math ─────────────────────────────────────────────

def test_trend_of_empty_series_is_none():
    assert checks._trend([]) is None


def test_trend_computes_first_last_peak_growth():
    tr = checks._trend([100.0, 102.0, 101.0, 130.0])
    assert tr["first"] == 100.0
    assert tr["last"] == 130.0
    assert tr["peak"] == 130.0
    assert tr["min"] == 100.0
    assert tr["growth"] == 30.0


def test_trend_of_flat_series_has_zero_growth():
    assert checks._trend([10, 10, 10])["growth"] == 0


# ── _sample_over_time early-bail ───────────────────────────────────

def test_sampler_bails_early_on_an_unreachable_endpoint():
    # an unreachable first poll must stop the loop immediately rather
    # than sleeping through the whole window
    ctx = _SeriesContext([], reachable=False)
    samples, errors = checks._sample_over_time(
        ctx, "/api/dev/mem_audit", lambda b: 1)
    assert samples == []
    assert any("bailing early" in e for e in errors)


def test_sampler_collects_all_samples_when_reachable():
    bodies = [{"rss_mb": 100}, {"rss_mb": 101},
              {"rss_mb": 100}, {"rss_mb": 102}]
    ctx = _SeriesContext(bodies)
    # pass count explicitly — _sample_over_time's kwarg default is
    # bound at definition time, so mutating the module global would
    # not change it
    samples, errors = checks._sample_over_time(
        ctx, "/api/dev/mem_audit", lambda b: b["rss_mb"],
        count=4, gap=0.0)
    assert samples == [100, 101, 100, 102]
    assert errors == []


# ── graceful degradation ───────────────────────────────────────────

def test_all_three_warn_when_unreachable():
    ctx = _SeriesContext([], reachable=False)
    for tid in ("L31", "L32", "L33"):
        level, detail = _get_test(tid).fn(ctx)
        assert level == h.WARN
        assert "testable" in detail or "could not" in detail


# ── verdict branches against synthetic series ──────────────────────

def test_l31_warns_on_rising_rss():
    rising = [{"rss_mb": 100}, {"rss_mb": 130},
              {"rss_mb": 160}, {"rss_mb": 200}]
    level, detail = _get_test("L31").fn(_SeriesContext(rising))
    assert level == h.WARN
    assert "leak" in detail or "rose" in detail


def test_l31_passes_on_stable_rss():
    flat = [{"rss_mb": 100}] * 4
    level, detail = _get_test("L31").fn(_SeriesContext(flat))
    assert level == h.PASS
    assert "stable" in detail


def test_l32_warns_on_thread_growth():
    growing = [{"thread_count": 10}, {"thread_count": 12},
               {"thread_count": 14}, {"thread_count": 16}]
    level, detail = _get_test("L32").fn(_SeriesContext(growing))
    assert level == h.WARN
    assert "leak" in detail or "rose" in detail


def test_l32_passes_on_stable_thread_count():
    flat = [{"thread_count": 12}] * 4
    level, detail = _get_test("L32").fn(_SeriesContext(flat))
    assert level == h.PASS


def test_l33_passes_with_zero_orphans():
    clean = [{"orphan_browsers": 0}] * 4
    level, detail = _get_test("L33").fn(_SeriesContext(clean))
    assert level == h.PASS
    assert "no browser leak" in detail


def test_l33_warns_on_growing_orphans():
    leaking = [{"orphan_browsers": 0}, {"orphan_browsers": 1},
               {"orphan_browsers": 2}, {"orphan_browsers": 3}]
    level, detail = _get_test("L33").fn(_SeriesContext(leaking))
    assert level == h.WARN


def test_l33_passes_on_windows_shape_empty_procs_with_no_leak_verdict():
    # On Windows the leak_scan endpoint's _child_process_count walks
    # /proc which doesn't exist, so it returns {} — the chromium key
    # is never present. When the endpoint's own verdict says no leaks
    # were detected AND findings is empty, the helper must trust that
    # and treat as zero orphans. Pre-this-fix, L33 WARN'd on every
    # Windows deployment even when the endpoint reported a clean bill.
    windows_clean = [
        {"processes": {}, "findings": [], "verdict": "no leak signals"}
    ] * 4
    level, detail = _get_test("L33").fn(_SeriesContext(windows_clean))
    assert level == h.PASS, f"got {level} — {detail}"
    assert "no browser leak" in detail


def test_l33_does_not_pass_on_empty_procs_with_actual_findings():
    # Belt-and-suspenders: if processes is empty BUT findings reports
    # an actual leak signal, the verdict-trust path must NOT mask it.
    # The helper returns None (can't sample) and L33 WARNs honestly.
    suspicious = [
        {"processes": {}, "findings": ["something is leaking"],
         "verdict": "leak detected"}
    ] * 4
    level, detail = _get_test("L33").fn(_SeriesContext(suspicious))
    assert level == h.WARN, f"got {level} — {detail}"
    assert "could not sample" in detail or "not testable" in detail


# ── harness integration ────────────────────────────────────────────

def test_resource_tests_run_via_harness(tmp_path):
    # non-disruptive -> they run on a default run; against a dead
    # target they WARN -> exit 0
    rdir = tmp_path / "results"
    code = h.run_all("http://localhost:1", str(tmp_path),
                     only=["L31", "L32", "L33"], results_dir=rdir)
    assert code == 0
    for tid in ("L31", "L32", "L33"):
        assert (rdir / f"{tid}.log").is_file()
