"""Row 1052 -- memory subsystem telemetry, in the form a scraper can read.

``metrics_prom`` publishes jobs, downloads, disk runway, account health and circuit-breaker state.
It publishes nothing about memory. ``perf_lab`` measures memory in detail -- RSS, threads, child
processes, gc, tracemalloc -- but only through the dev-suite HTTP surface, which a Prometheus
scraper never visits. So a leak has always been visible in a live debugging session and invisible
to the monitoring that is supposed to page someone about it. This module closes that gap.

ONE RULE SHAPES EVERYTHING HERE: a number that was not measured is never rendered as a number.
Every reading that can fail carries a companion ``*_unknown`` gauge, and the value falls back to 0
only once that flag says the 0 means nothing. A bare 0 for RSS would be the most reassuring
possible value for the most alarming possible state -- a process reporting no memory -- and an
alert rule cannot tell it apart from a genuinely small process. The exporter already uses this
idiom (``bd_jobs_active_unknown``); this module follows it rather than inventing a second one.

The sampler takes its readers as arguments so the unknown path is reachable in a test without
breaking /proc, and the render step is pure: dicts in, exposition lines out.
"""
from __future__ import annotations

import gc
import sys
import threading
import tracemalloc
from typing import Any, Callable, Dict, List, Optional

#: Every series this module emits, with its HELP text and Prometheus type. Rendering is driven
#: FROM this table, so a series can never be emitted without a declaration -- the drift that
#: breaks a scraper's type inference is structurally impossible rather than merely discouraged.
SERIES: Dict[str, tuple] = {
    "bd_memory_rss_bytes": ("gauge", "Resident set size of the BD process in bytes"),
    "bd_memory_rss_unknown": ("gauge", "1 = RSS could not be read; the bytes value means nothing"),
    "bd_memory_threads": ("gauge", "Live Python threads in the BD process"),
    "bd_memory_gc_objects": ("gauge", "Objects currently tracked by the garbage collector"),
    "bd_memory_gc_collections_total": (
        "counter", "Garbage collections performed, per generation, since process start"),
    "bd_memory_tracemalloc_tracing": ("gauge", "1 = tracemalloc is running in this process"),
    "bd_memory_tracemalloc_traced_bytes": (
        "gauge", "Bytes currently traced by tracemalloc; 0 and unknown when it is not running"),
    "bd_memory_tracemalloc_unknown": (
        "gauge", "1 = tracemalloc reported nothing, so the traced-bytes value means nothing"),
}

#: Series that carry a per-generation label rather than a bare value.
_LABELLED = {"bd_memory_gc_collections_total": "generation"}


def _default_rss_reader() -> Optional[int]:
    """RSS via perf_lab, which already owns that measurement (/proc, then getrusage).

    Imported lazily and defensively: this module is rendered inside the /metrics request path,
    and an import problem there must degrade to ``rss_unknown``, never to a 500.
    """
    from . import perf_lab
    return perf_lab._rss_bytes()


def sample(rss_reader: Optional[Callable[[], Optional[int]]] = None) -> Dict[str, Any]:
    """Take one memory reading. Never raises; every field is independently failure-isolated.

    A single unreadable counter must not cost the others: that is why each block below has its own
    try, rather than one try around the whole function. The dict is flat and JSON-safe so the same
    sample can be rendered to Prometheus here or returned from a dev endpoint unchanged.
    """
    out: Dict[str, Any] = {
        "rss_bytes": 0, "rss_unknown": 1,
        "threads": 0,
        "gc_objects": 0,
        "gc_collections": (),
        "tracemalloc_tracing": 0,
        "tracemalloc_traced_bytes": 0,
        "tracemalloc_unknown": 1,
    }

    try:
        rss = (rss_reader or _default_rss_reader)()
        if isinstance(rss, int) and rss > 0:
            out["rss_bytes"], out["rss_unknown"] = rss, 0
    except Exception:                      # noqa: BLE001 -- an unreadable RSS is DATA, see module docstring
        pass

    try:
        out["threads"] = threading.active_count()
    except Exception:                      # noqa: BLE001
        pass

    try:
        out["gc_objects"] = len(gc.get_objects())
    except Exception:                      # noqa: BLE001
        pass

    try:
        out["gc_collections"] = tuple(int(stat.get("collections", 0)) for stat in gc.get_stats())
    except Exception:                      # noqa: BLE001
        pass

    try:
        tracing = bool(tracemalloc.is_tracing())
        out["tracemalloc_tracing"] = 1 if tracing else 0
        if tracing:
            # traced_memory is (current, peak); the current figure is the one a leak moves.
            out["tracemalloc_traced_bytes"] = int(tracemalloc.get_traced_memory()[0])
            out["tracemalloc_unknown"] = 0
    except Exception:                      # noqa: BLE001
        pass

    return out


def _render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return "%.6g" % value
    return str(value)


def render_lines(sampled: Optional[Dict[str, Any]] = None) -> List[str]:
    """Render a sample as Prometheus exposition lines, HELP and TYPE included.

    Driven from :data:`SERIES`, so every emitted series is declared and every declared series is
    emitted; the test asserts that equality rather than trusting it.
    """
    sampled = sample() if sampled is None else sampled
    lines: List[str] = []

    def declare(name: str) -> None:
        metric_type, help_text = SERIES[name]
        lines.append("# HELP %s %s" % (name, help_text))
        lines.append("# TYPE %s %s" % (name, metric_type))

    plain = [
        ("bd_memory_rss_bytes", sampled.get("rss_bytes", 0)),
        ("bd_memory_rss_unknown", sampled.get("rss_unknown", 1)),
        ("bd_memory_threads", sampled.get("threads", 0)),
        ("bd_memory_gc_objects", sampled.get("gc_objects", 0)),
        ("bd_memory_tracemalloc_tracing", sampled.get("tracemalloc_tracing", 0)),
        ("bd_memory_tracemalloc_traced_bytes", sampled.get("tracemalloc_traced_bytes", 0)),
        ("bd_memory_tracemalloc_unknown", sampled.get("tracemalloc_unknown", 1)),
    ]
    for name, value in plain:
        declare(name)
        lines.append("%s %s" % (name, _render_value(value)))

    name = "bd_memory_gc_collections_total"
    declare(name)
    label = _LABELLED[name]
    collections = sampled.get("gc_collections") or ()
    if not collections:
        # A generation-labelled series with no generations would vanish from the document
        # entirely, and a series that disappears reads to a scraper as a process that stopped
        # collecting -- not as a reading that failed. Emit the shape with a zeroed generation 0.
        collections = (0,)
    for generation, count in enumerate(collections):
        lines.append('%s{%s="%d"} %s' % (name, label, generation, _render_value(int(count))))

    return lines


def render(sampled: Optional[Dict[str, Any]] = None) -> str:
    """The same document as :func:`render_lines`, as one string."""
    return "\n".join(render_lines(sampled))
