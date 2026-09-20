"""ROW 928. Discovery is decoupled from download execution.

Before this row a browser worker loop discovered a page's media and then
downloaded it inline, so a slow transfer stalled the next page's discovery
and only one transfer ran per worker. ``bulk_downloader/pipeline_orchestrator``
is the producer: it walks pages, turns each discovered link into a manifest
and streams it onto a manifest queue (in-memory by default, a Redis list when
a ``redis://`` URL is configured) without ever waiting on a transfer.
``bulk_downloader/runner_transport.consume_manifest_queue`` is the consumer:
N transport workers pull manifests from the SAME queue and transfer them in
parallel; ``TransportMixin._run_transport_consumers`` binds that consumer to
the runner's own ``_do_direct_http_download``.

Nothing here touches the network, a browser, a site login or a real Redis:
transfers are fixture callables gated by threading primitives, and the Redis
backend is exercised through a duck-typed fake client.
"""
from __future__ import annotations

import sys
import threading
import time

import pytest

from bulk_downloader import pipeline_orchestrator as po
from bulk_downloader import runner_transport as rt


BD_GATE_SCOPE = "module"

_PAGES = [f"https://site.example/page/{i}" for i in range(5)]
_LINKS_PER_PAGE = 3


def _discover(page_url):
    return [
        {"page_url": page_url,
         "file_url": f"{page_url}/media/{j}.mp4",
         "output_path": f"/tmp/row928/{page_url.rsplit('/', 1)[1]}-{j}.mp4",
         "referer": page_url}
        for j in range(_LINKS_PER_PAGE)
    ]


def _run_pipeline(transfer, *, workers, queue=None):
    q = queue if queue is not None else po.MemoryManifestQueue()
    orch = po.PipelineOrchestrator(q, _discover)
    consumers = rt.consume_manifest_queue(q, transfer, workers=workers, stats=orch.stats)
    orch.start_discovery(_PAGES)
    return orch, consumers


def test_discovery_streams_every_manifest_while_every_transfer_is_stalled():
    release = threading.Event()
    started = threading.Semaphore(0)

    def transfer(manifest):
        started.release()
        assert release.wait(10), "fixture: transfer never released"
        return True

    orch, consumers = _run_pipeline(transfer, workers=2)
    assert orch.join_discovery(10), "discovery did not finish while transfers were stalled"

    expected = len(_PAGES) * _LINKS_PER_PAGE
    assert expected == 15, "precondition: fixture shape"
    assert orch.stats.discovered == expected
    assert orch.stats.enqueued == expected
    assert orch.stats.completed == 0, (
        "discovery finished only because a transfer completed -- the producer is "
        "still blocking on the consumer")
    # Both workers hold a manifest; the other 13 are queued, not lost.
    assert started.acquire(timeout=10) and started.acquire(timeout=10)
    assert orch.stats.in_flight == 2

    release.set()
    assert consumers.join(10), "consumers did not drain after release"
    assert orch.stats.completed == expected
    assert orch.stats.failed == 0
    assert orch.stats.dequeued == expected


def test_transport_workers_transfer_in_parallel_up_to_the_worker_count():
    workers = 4
    gate = threading.Barrier(workers, timeout=10)
    first_wave = threading.Semaphore(workers)

    def transfer(manifest):
        if first_wave.acquire(blocking=False):
            gate.wait()  # only passes when `workers` transfers are in flight at once
        return True

    orch, consumers = _run_pipeline(transfer, workers=workers)
    assert consumers.join(10), "barrier never filled: transfers are not parallel"
    assert orch.stats.peak_in_flight == workers
    assert orch.stats.completed == len(_PAGES) * _LINKS_PER_PAGE


def test_a_single_worker_is_the_serial_negative_control():
    def transfer(manifest):
        return True

    orch, consumers = _run_pipeline(transfer, workers=1)
    assert consumers.join(10)
    assert orch.stats.peak_in_flight == 1
    assert orch.stats.completed == len(_PAGES) * _LINKS_PER_PAGE


def test_decoupled_pipeline_measures_a_higher_throughput_than_the_inline_loop():
    per_transfer = 0.02

    def transfer(manifest):
        time.sleep(per_transfer)
        return True

    t0 = time.monotonic()
    inline_done = 0
    for page in _PAGES:
        for manifest in _discover(page):
            transfer(manifest)
            inline_done += 1
    inline_elapsed = time.monotonic() - t0
    inline_throughput = inline_done / inline_elapsed

    orch, consumers = _run_pipeline(transfer, workers=4)
    assert consumers.join(10)
    stats = orch.stats
    assert stats.completed == inline_done == 15
    assert stats.elapsed > 0
    assert stats.throughput_per_s > inline_throughput * 1.5, (
        f"decoupled {stats.throughput_per_s:.1f}/s vs inline {inline_throughput:.1f}/s")


def test_a_failing_transfer_is_counted_and_does_not_stop_the_consumers():
    def transfer(manifest):
        if manifest["file_url"].endswith("/1.mp4"):
            raise RuntimeError("row928 fixture failure")
        return manifest["file_url"].endswith("/0.mp4")

    orch, consumers = _run_pipeline(transfer, workers=2)
    assert consumers.join(10)
    assert orch.stats.dequeued == 15
    assert orch.stats.completed == 5
    assert orch.stats.failed == 10
    assert orch.stats.in_flight == 0


class _FakeRedis:
    def __init__(self):
        self.lists = {}

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def blpop(self, keys, timeout=0):
        key = keys[0] if isinstance(keys, (list, tuple)) else keys
        items = self.lists.get(key)
        if not items:
            return None
        return key, items.pop(0)

    def llen(self, key):
        return len(self.lists.get(key, []))


def test_redis_backend_round_trips_manifests_as_json_lists():
    client = _FakeRedis()
    q = po.RedisManifestQueue(client, "row928:manifests")
    manifest = _discover(_PAGES[0])[0]
    q.put(manifest)
    assert client.llen("row928:manifests") == 1
    assert isinstance(client.lists["row928:manifests"][0], (bytes, str))
    assert q.get(timeout=1) == manifest
    assert q.get(timeout=1) is None

    def transfer(m):
        return True

    orch, consumers = _run_pipeline(transfer, workers=3, queue=q)
    assert consumers.join(10)
    assert orch.stats.completed == 15
    assert client.llen("row928:manifests") == 1, "the close marker stays for late consumers"


def test_open_manifest_queue_defaults_to_memory_and_fails_closed_without_redis(monkeypatch):
    assert isinstance(po.open_manifest_queue(None), po.MemoryManifestQueue)
    assert isinstance(po.open_manifest_queue(""), po.MemoryManifestQueue)
    monkeypatch.setitem(sys.modules, "redis", None)
    with pytest.raises(po.QueueBackendUnavailable, match="redis"):
        po.open_manifest_queue("redis://127.0.0.1:6379/0")


def test_transport_mixin_binds_the_consumer_to_its_direct_http_download():
    calls = []
    lock = threading.Lock()

    class _Runner(rt.TransportMixin):
        def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
            with lock:
                calls.append((page_url, file_url, output_path, referer))
            return True

    assert hasattr(rt.TransportMixin, "_run_transport_consumers"), (
        "runner_transport has no consumer entry point: transport workers cannot "
        "pull manifests from the discovery queue")
    q = po.MemoryManifestQueue()
    orch = po.PipelineOrchestrator(q, _discover)
    consumers = _Runner()._run_transport_consumers(q, workers=3, stats=orch.stats)
    orch.start_discovery(_PAGES)
    assert consumers.join(10)
    assert len(calls) == 15
    expected = sorted(
        (m["page_url"], m["file_url"], m["output_path"], m["referer"])
        for p in _PAGES for m in _discover(p))
    assert sorted(calls) == expected
    assert orch.stats.completed == 15
