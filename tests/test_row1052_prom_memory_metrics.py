"""Row 1052 -- memory subsystem telemetry in the Prometheus exposition.

MEASURED ON BASE bc1544b7, not assumed:

    bulk_downloader/metrics_prom.py                            -> 530 lines, the /metrics document
    grep -niE 'rss|memory|mem_|tracemalloc|heap' metrics_prom.py -> 0 hits
    grep -rlniE 'prometheus|openmetrics' bulk_downloader/ tools/ -> 5 files
    positive control, same probe shape: 'turnstile'              -> 41 files

So the exporter publishes jobs, downloads, disk runway, account health and circuit-breaker state,
and not one byte about memory. The repo DOES measure memory -- ``perf_lab`` reads RSS, thread and
child-process counts, gc and tracemalloc -- but only through the dev-suite HTTP surface, which a
scraper does not visit. The consequence is the row's reason: a memory leak is visible in a live
debugging session and invisible to the monitoring that is supposed to page someone about it.

The first test below renders the REAL exporter, which exists at base, so it fails with an
AssertionError naming the absent series -- not an ImportError about the module this row adds.
"""
from __future__ import annotations

import importlib
import re

from bulk_downloader import metrics_prom

BD_GATE_SCOPE = "repo-wide"

_SERIES = re.compile(r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?P<labels>\{[^}]*\})?\s+(?P<value>\S+)$')


def _memory():
    """Import the subject lazily: on base this raises inside ONE test, not at collection."""
    return importlib.import_module('bulk_downloader.prom_memory_metrics')


def _series(document):
    """Every non-comment line of an exposition document, as {name: value}."""
    out = {}
    for line in document.splitlines():
        if not line or line.startswith('#'):
            continue
        found = _SERIES.match(line)
        if found:
            out[found.group('name') + (found.group('labels') or '')] = found.group('value')
    return out


# -- 1. the row's reason, asserted against the exporter that exists at base ----------

def test_the_exposition_carries_memory_subsystem_telemetry():
    document = metrics_prom.render()
    names = _series(document)
    # positive control: the exporter really did render, so a missing memory series below is a
    # MISSING SERIES and not a dead read of an empty document.
    assert 'bd_uptime_seconds' in names, f"the exporter rendered no uptime at all: {sorted(names)[:5]}"
    memory = [name for name in names if name.startswith('bd_memory_')]
    assert memory, (
        "the /metrics document exposes jobs, downloads, disk runway, account health and circuit "
        "state but no bd_memory_* series at all, so a memory leak is visible only in a live "
        "dev-suite session and never to the scraper that would page someone")


def test_the_memory_series_are_declared_with_help_and_type_like_every_other_block():
    document = metrics_prom.render()
    memory = sorted({name.split('{')[0] for name in _series(document)
                     if name.startswith('bd_memory_')})
    assert memory, "no bd_memory_* series to check"
    for name in memory:
        assert f"# HELP {name} " in document, f"{name} is exported with no HELP line"
        assert re.search(rf"^# TYPE {re.escape(name)} (gauge|counter)$", document, re.M), (
            f"{name} is exported with no TYPE line")


def test_rss_is_a_real_measurement_of_this_process():
    document = metrics_prom.render()
    rss = _series(document).get('bd_memory_rss_bytes')
    unknown = _series(document).get('bd_memory_rss_unknown')
    assert rss is not None and unknown is not None
    # Either we measured it -- and a live CPython process is never 0 bytes or a terabyte -- or we
    # said so. What must never happen is a plausible-looking zero standing in for "unreadable".
    if unknown == '0':
        assert 1 << 20 < float(rss) < 1 << 41, f"bd_memory_rss_bytes={rss} is not a live process"
    else:
        assert rss == '0'


# -- 2. the sampler itself ----------------------------------------------------------

def test_the_sampler_reports_unknown_instead_of_a_zero_it_did_not_measure():
    """NEGATIVE CONTROL, and the point of the row: an unreadable counter must set its _unknown
    flag, because a zero here reads like a process using no memory -- the most reassuring possible
    value for the most alarming possible state."""
    memory = _memory()
    good = memory.sample(rss_reader=lambda: 4096)
    assert good['rss_bytes'] == 4096 and good['rss_unknown'] == 0
    blind = memory.sample(rss_reader=lambda: None)
    assert blind['rss_bytes'] == 0 and blind['rss_unknown'] == 1
    raised = memory.sample(rss_reader=_boom)
    assert raised['rss_bytes'] == 0 and raised['rss_unknown'] == 1


def test_one_broken_sampler_does_not_cost_the_others():
    memory = _memory()
    sample = memory.sample(rss_reader=_boom)
    assert sample['threads'] >= 1, "a live interpreter always has at least the main thread"
    assert sample['gc_objects'] > 0
    assert sample['gc_collections'] and all(
        isinstance(count, int) for count in sample['gc_collections'])


def test_the_sampler_renders_to_prometheus_lines_with_help_and_type():
    memory = _memory()
    lines = memory.render_lines(memory.sample(rss_reader=lambda: 4096))
    document = "\n".join(lines)
    assert 'bd_memory_rss_bytes 4096' in document
    assert '# TYPE bd_memory_rss_bytes gauge' in document
    assert '# TYPE bd_memory_gc_collections_total counter' in document
    assert 'bd_memory_gc_collections_total{generation="0"}' in document
    # Every emitted series must be declared: an undeclared series is what breaks a scraper's
    # type inference, and it is exactly what a hand-maintained list drifts into.
    emitted = {line.split()[0].split('{')[0] for line in lines if not line.startswith('#')}
    declared = {line.split()[2] for line in lines if line.startswith('# TYPE ')}
    assert emitted == declared, f"undeclared: {sorted(emitted - declared)}; unused: {sorted(declared - emitted)}"


def test_tracemalloc_is_reported_as_off_rather_than_as_zero_bytes():
    memory = _memory()
    sample = memory.sample(rss_reader=lambda: 4096)
    assert sample['tracemalloc_tracing'] in (0, 1)
    if not sample['tracemalloc_tracing']:
        assert sample['tracemalloc_traced_bytes'] == 0
        assert sample['tracemalloc_unknown'] == 1, (
            "tracemalloc that is not running has not measured 0 bytes -- it has measured nothing")


def _boom():
    raise OSError("/proc is not readable here")
