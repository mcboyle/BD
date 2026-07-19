"""v3.66.776 -- Plugin-v3 V3-E residual: metric percentiles + quarantine join.

The @496/@499 metrics surface records calls/fails/avg/last. The V3-E plan's
residual is latency PERCENTILES (p50/p95) and the QUARANTINE state joined into
the same snapshot, so the metrics view answers "is this plugin slow at the
tail?" and "is it currently quarantined?" without a second lookup.

Design: `_record_metric` keeps a bounded per-key duration ring
(deque(maxlen=128)) -- memory-bounded, cheap, and the documented
never-raises property is preserved. `plugin_metrics()` computes p50_ms /
p95_ms (nearest-rank on the ring) and joins `quarantined` from the
_quarantine map (same key -- both are written at the _call_guarded seam).
NO new route: /api/plugins/status already carries `metrics` (the deliberate
deviation from the plan's /api/plugins/metrics endpoint -- a new route buys
nothing but the full route-wiring band).

RED on pristine v3.66.775: entries lack p50_ms / p95_ms / quarantined.
GREEN after.

run_tests.py conventions: zero-arg test functions.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


def _key_for(metrics, needle):
    for m in metrics:
        if needle in m["key"]:
            return m
    return None


def test_metrics_entries_carry_percentiles():
    """p50_ms / p95_ms computed from recorded durations (nearest-rank).
    Deterministic: 1..20 ms -> p50 = 10ms, p95 = 19ms (rank on n-1)."""
    P.reset()
    for i in range(1, 21):
        P._record_metric("processor:pct.demo", True, i / 1000.0)
    m = _key_for(P.plugin_metrics(), "pct.demo")
    assert m is not None, P.plugin_metrics()
    assert "p50_ms" in m and "p95_ms" in m, m
    # nearest-rank on sorted [1..20]ms: idx int(0.5*19)=9 -> 10ms;
    # idx int(0.95*19)=18 -> 19ms
    assert abs(m["p50_ms"] - 10.0) < 0.001, m
    assert abs(m["p95_ms"] - 19.0) < 0.001, m
    P.reset()


def test_percentile_ring_is_bounded_to_recent_window():
    """The ring is bounded (maxlen 128): old samples age out, so percentiles
    reflect the RECENT window, and memory cannot grow with call count."""
    P.reset()
    for _ in range(400):
        P._record_metric("hook:ring.demo", True, 0.001)
    for _ in range(128):
        P._record_metric("hook:ring.demo", True, 0.002)
    m = _key_for(P.plugin_metrics(), "ring.demo")
    assert m is not None
    # every sample in the window is 2ms; a 1ms p50 would mean unbounded history
    assert abs(m["p50_ms"] - 2.0) < 0.001, m
    assert m["calls"] == 528, m  # counters still cover ALL calls
    P.reset()


def test_metrics_entries_carry_quarantined_flag():
    """The quarantine state joins into the metrics snapshot on the SAME key
    (both written at the _call_guarded seam): a plugin past the fail budget
    reports quarantined=True; a healthy plugin reports False."""
    P.reset()

    @P.processor(name="always_bad")
    def always_bad(payload):
        raise RuntimeError("boom")

    @P.processor(name="healthy")
    def healthy(payload):
        return 1

    for _ in range(P._FAIL_BUDGET):
        P.run_processors({"site_id": "s", "url": "u"})
    bad = _key_for(P.plugin_metrics(), "always_bad")
    good = _key_for(P.plugin_metrics(), "healthy")
    assert bad is not None and good is not None, P.plugin_metrics()
    assert bad.get("quarantined") is True, bad
    assert good.get("quarantined") is False, good
    P.reset()


def test_record_metric_never_raises_on_garbage():
    """The documented never-raises property survives the ring addition."""
    P.reset()
    P._record_metric("processor:garbage.demo", True, "not-a-float")
    P._record_metric("processor:garbage.demo", False, None)
    P._record_metric("processor:garbage.demo", True, -5)
    m = _key_for(P.plugin_metrics(), "garbage.demo")
    assert m is not None and m["calls"] == 3 and m["fails"] == 1, m
    # garbage/negative coerce to 0.0 exactly as before; percentiles well-formed
    assert m["p50_ms"] == 0.0 and m["p95_ms"] == 0.0, m
    P.reset()


def test_single_sample_percentiles_are_that_sample():
    """n=1 edge: p50 == p95 == the one duration (no divide-by-zero shapes)."""
    P.reset()
    P._record_metric("sink:single.demo", True, 0.004)
    m = _key_for(P.plugin_metrics(), "single.demo")
    assert m is not None
    assert abs(m["p50_ms"] - 4.0) < 0.001 and abs(m["p95_ms"] - 4.0) < 0.001, m
    P.reset()


def test_reset_clears_percentile_state():
    """reset() wipes the ring with the rest of the metric state."""
    P.reset()
    P._record_metric("processor:wipe.demo", True, 0.010)
    assert P.plugin_metrics() != []
    P.reset()
    assert P.plugin_metrics() == []
