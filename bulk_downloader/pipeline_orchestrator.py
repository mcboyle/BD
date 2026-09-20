"""pipeline_orchestrator -- producer side of the decoupled discovery/transfer pipeline (row 928).

Discovery walks pages and streams one manifest per discovered link onto a
manifest queue; it never waits on a transfer. The consumer side lives in
``runner_transport.consume_manifest_queue`` (transport workers pulling from the
same queue). Both ends share ``PipelineStats`` so throughput is measured on
completed transfers, not on enqueues.

Queue backends: ``MemoryManifestQueue`` (stdlib, in-process) is the default;
``RedisManifestQueue`` is a Redis list (RPUSH/BLPOP, JSON payloads) so
consumers on other cluster nodes can drain the same stream. ``open_manifest_queue``
selects by URL and fails closed when ``redis://`` is asked for but the client
library is absent.
"""
from __future__ import annotations

import json
import queue
import threading
import time

CLOSE_MARKER = {"__pipeline_closed__": True}
_CLOSE_KEY = "__pipeline_closed__"


def is_close_marker(manifest) -> bool:
    return isinstance(manifest, dict) and manifest.get(_CLOSE_KEY) is True


class QueueBackendUnavailable(RuntimeError):
    """A queue backend was requested whose client library is not installed."""


class MemoryManifestQueue:
    def __init__(self):
        self._q = queue.Queue()

    def put(self, manifest: dict) -> None:
        self._q.put(manifest)

    def get(self, timeout: float | None = None):
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._q.put(dict(CLOSE_MARKER))

    def depth(self) -> int:
        return self._q.qsize()


class RedisManifestQueue:
    """Redis list backend. ``client`` is any object with rpush/blpop/llen
    (a redis.Redis or a fake); the module never imports redis itself."""

    def __init__(self, client, key: str):
        self._client = client
        self._key = key

    def put(self, manifest: dict) -> None:
        self._client.rpush(self._key, json.dumps(manifest, sort_keys=True))

    def get(self, timeout: float | None = None):
        # BLPOP takes whole seconds; 0 blocks forever, so a sub-second timeout rounds up to 1.
        secs = 0 if timeout is None else max(1, int(timeout))
        item = self._client.blpop([self._key], timeout=secs)
        if item is None:
            return None
        payload = item[1]
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        return json.loads(payload)

    def close(self) -> None:
        self.put(dict(CLOSE_MARKER))

    def depth(self) -> int:
        return int(self._client.llen(self._key))


def open_manifest_queue(url: str | None, *, key: str = "bd:pipeline:manifests"):
    if not url:
        return MemoryManifestQueue()
    if url.startswith(("redis://", "rediss://", "unix://")):
        try:
            import redis
        except ImportError as e:
            raise QueueBackendUnavailable(
                f"manifest queue {url!r} needs the redis client library: {e}") from e
        return RedisManifestQueue(redis.Redis.from_url(url), key)
    raise QueueBackendUnavailable(f"unsupported manifest queue url {url!r}")


class PipelineStats:
    """Counters shared by producer and consumers; every mutation is locked."""

    def __init__(self):
        self._lock = threading.Lock()
        self.discovered = 0
        self.enqueued = 0
        self.dequeued = 0
        self.completed = 0
        self.failed = 0
        self.in_flight = 0
        self.peak_in_flight = 0
        self._started = time.monotonic()
        self._last_completed_at = self._started

    def add_discovered(self, n: int = 1) -> None:
        with self._lock:
            self.discovered += n

    def add_enqueued(self, n: int = 1) -> None:
        with self._lock:
            self.enqueued += n

    def transfer_started(self) -> None:
        with self._lock:
            self.dequeued += 1
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)

    def transfer_finished(self, ok: bool) -> None:
        with self._lock:
            self.in_flight -= 1
            if ok:
                self.completed += 1
            else:
                self.failed += 1
            self._last_completed_at = time.monotonic()

    @property
    def elapsed(self) -> float:
        return max(self._last_completed_at - self._started, 1e-9)

    @property
    def throughput_per_s(self) -> float:
        return self.completed / self.elapsed


class PipelineOrchestrator:
    """Producer: ``discover(page_url) -> iterable[manifest]`` per page, every
    manifest streamed to ``manifest_queue`` as soon as it is found."""

    def __init__(self, manifest_queue, discover, *, stats: PipelineStats | None = None):
        self.queue = manifest_queue
        self.discover = discover
        self.stats = stats or PipelineStats()
        self._thread: threading.Thread | None = None
        self.discovery_error: BaseException | None = None

    def run_discovery(self, page_urls) -> int:
        n = 0
        try:
            for page_url in page_urls:
                for manifest in self.discover(page_url):
                    self.stats.add_discovered()
                    self.queue.put(manifest)
                    self.stats.add_enqueued()
                    n += 1
        except BaseException as e:
            self.discovery_error = e
            raise
        finally:
            self.queue.close()
        return n

    def start_discovery(self, page_urls) -> threading.Thread:
        self._thread = threading.Thread(
            target=self.run_discovery, args=(list(page_urls),),
            name="bd-pipeline-discovery", daemon=True)
        self._thread.start()
        return self._thread

    def join_discovery(self, timeout: float | None = None) -> bool:
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()
