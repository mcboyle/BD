from __future__ import annotations

from ._common import (_DD_COUNTERS)


def get_metrics() -> dict:
    """Return a snapshot of the live-mode counters. Used by
    metrics_prom.render to expose as Prometheus gauges."""
    return dict(_DD_COUNTERS)


def reset_metrics() -> None:
    """Zero out the counters. For tests; never call from production."""
    for k in _DD_COUNTERS:
        _DD_COUNTERS[k] = 0
