"""v3.66.496 O2 (plugin-v3): per-plugin invocation metrics.

Every plugin call already funnels through ``plugins._call_guarded`` (the quarantine
seam). O2 records per-key metrics there -- call count, failure count, cumulative +
last wall time -- with zero new call sites. The snapshot is exposed via
``plugins.plugin_metrics()`` and folded into ``plugins.status()`` so the existing
``/api/plugins/status`` endpoint surfaces it (NO new route); the cockpit panel reads
that field. ``reset()`` clears metrics with the rest of the registry state.

Logic-only slice: no route, no api bump, no guard. Runner-safe.
"""
import os
import sys
import time
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


def test_plugin_metrics_accessor_exists():
    P.reset()
    assert hasattr(P, "plugin_metrics")
    assert P.plugin_metrics() == []


def test_successful_processor_records_a_call():
    P.reset()

    @P.processor(name="good_proc")
    def good_proc(payload):
        return {"ok": True}

    P.run_processors({"site_id": "s", "url": "u"})
    m = _key_for(P.plugin_metrics(), "good_proc")
    assert m is not None, P.plugin_metrics()
    assert m["calls"] == 1 and m["fails"] == 0
    assert m["total_s"] >= 0.0 and "avg_ms" in m and "last_ms" in m


def test_failing_processor_records_a_fail():
    P.reset()

    @P.processor(name="bad_proc")
    def bad_proc(payload):
        raise RuntimeError("boom")

    P.run_processors({"site_id": "s", "url": "u"})
    m = _key_for(P.plugin_metrics(), "bad_proc")
    assert m is not None and m["calls"] == 1 and m["fails"] == 1


def test_calls_accumulate_across_invocations():
    P.reset()

    @P.processor(name="counter")
    def counter(payload):
        return 1

    for _ in range(3):
        P.run_processors({})
    m = _key_for(P.plugin_metrics(), "counter")
    assert m["calls"] == 3 and m["fails"] == 0


def test_metrics_in_status_snapshot():
    P.reset()

    @P.processor(name="statusy")
    def statusy(payload):
        return 1

    P.run_processors({})
    snap = P.status()
    assert "metrics" in snap
    assert any("statusy" in m["key"] for m in snap["metrics"])


def test_reset_clears_metrics():
    P.reset()

    @P.processor(name="ephemeral")
    def ephemeral(payload):
        return 1

    P.run_processors({})
    assert P.plugin_metrics() != []
    P.reset()
    assert P.plugin_metrics() == []


def test_hook_path_also_metered():
    """Hooks fired via fire_hook go through _call_guarded too."""
    P.reset()

    @P.hook("download.done")
    def obs(payload):
        return None

    P.fire_hook("download.done", {"site_id": "s"})
    m = _key_for(P.plugin_metrics(), "obs")
    assert m is not None and m["calls"] == 1


# ── plugin metrics surface (v3.66.499: now a first-class SPA route) ──────────
_APP_TSX = _REPO / "frontend" / "src" / "App.tsx"
_ROUTE_TSX = _REPO / "frontend" / "src" / "routes" / "PluginMetrics.tsx"


def test_plugin_metrics_spa_route_wired_in_source():
    app = _APP_TSX.read_text(encoding="utf-8")
    # registered as a real SPA route (replaces the deploy-excluded cockpit panel)
    assert "PluginMetrics" in app
    assert '/plugins/metrics' in app
    # the route reads the EXISTING status endpoint + the metrics field
    route = _ROUTE_TSX.read_text(encoding="utf-8")
    assert "/api/plugins/status" in route
    assert "metrics" in route
