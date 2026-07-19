"""v3.66.658 -- OBS-2: expose the per-route p50/p95 latency percentile panel.

dev_metrics.route_percentiles() (the "OBS-2 core", {rule: {count,p50,p95,max}} over
the recent-request ring buffer) already existed but had NO consumer -- there was no
route serving it, so the p50/p95 panel data was unreachable. This adds a thin
read-only dev route (/api/dev/route_timing) delegating to a dev_suite wrapper
(route_timing) that returns route_percentiles(), matching the existing
slow_endpoints / error_rate pattern exactly. OBS-4 (capture-success trend) was
already served by /api/hourly_stats, so only OBS-2's route was missing.
"""
from bulk_downloader import dev_suite


def test_dev_suite_exposes_route_timing():
    assert hasattr(dev_suite, "route_timing"), "dev_suite must export route_timing"


def test_route_timing_passes_through_percentiles(monkeypatch):
    from bulk_downloader import dev_metrics
    monkeypatch.setattr(dev_metrics, "route_percentiles",
                        lambda: {"GET /api/x": {"count": 3, "p50": 5.0, "p95": 9.0, "max": 12.0}})
    out = dev_suite.route_timing()
    # a thin wrapper carrying the percentile map (may add a light envelope)
    assert "routes" in out or "GET /api/x" in out
    blob = str(out)
    assert "p95" in blob and "p50" in blob


def test_route_timing_route_registered():
    from bulk_downloader.app import app
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/dev/route_timing" in rules, "OBS-2 route must be registered"
