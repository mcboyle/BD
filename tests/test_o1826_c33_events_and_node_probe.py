"""O1826 BRIEF-33: events loop/producer reuse (M059) and node_sentinel HTTPS probe (M125).

RED on base 42cee834: publish_download_completion runs asyncio.run per event, so async
mode builds and starts a fresh producer per event and never stops any; NodeSentinel
probes https:// endpoints with plain HTTPConnection on port 80 and never closes it.
"""
from __future__ import annotations

import asyncio
import http.client
import json
import threading
import time

import pytest

from bulk_downloader import events, node_sentinel

BD_GATE_SCOPE = "module"

BOOTSTRAP = "10.0.70.25:9092"


@pytest.fixture
def fresh_runner(monkeypatch):
    """Isolate the shared loop per test; on base (no runner) this is a no-op."""
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", BOOTSTRAP)
    runner_cls = getattr(events, "_AsyncEventRunner", None)
    if runner_cls is None:
        yield None
        return
    runner = runner_cls()
    monkeypatch.setattr(events, "_event_runner", runner)
    yield runner
    runner.shutdown()


class _FakeAsyncProducer:
    def __init__(self, log, fail_sends=0):
        self.log = log
        self.fail_sends = fail_sends
        self.starts = 0
        self.stopped = False
        self.sent = []

    async def start(self):
        self.starts += 1

    async def stop(self):
        self.stopped = True

    async def send_and_wait(self, topic, value):
        self.log["loops"].append(asyncio.get_running_loop())
        if self.fail_sends:
            self.fail_sends -= 1
            raise RuntimeError("broker unavailable")
        self.sent.append((topic, value))


def _publish(i, **kwargs):
    return events.publish_download_completion(
        {"kafka_event_streaming_enabled": True, "use_async_streamer": True},
        downloaded_bytes=1000 + i,
        duration_seconds=0.5 + i,
        site_id=f"site-{i}",
        status="completed",
        **kwargs,
    )


def test_async_mode_reuses_one_started_producer_and_stops_it_on_shutdown(fresh_runner):
    log = {"loops": []}
    created = []

    def factory(**_kwargs):
        producer = _FakeAsyncProducer(log)
        created.append(producer)
        return producer

    results = [_publish(i, producer_factory=factory) for i in range(3)]

    assert results == [True, True, True]
    assert len(created) == 1, f"M059: {len(created)} producers for 3 events (expected 1 reused)"
    producer = created[0]
    assert producer.starts == 1
    assert len({id(loop) for loop in log["loops"]}) == 1, "M059: a fresh event loop per published event"
    assert not producer.stopped
    assert fresh_runner is not None
    fresh_runner.shutdown()
    assert producer.stopped, "M059: producer not stopped on shutdown"


def test_async_mode_event_payloads_unchanged(fresh_runner):
    log = {"loops": []}
    created = []

    def factory(**_kwargs):
        created.append(_FakeAsyncProducer(log))
        return created[-1]

    for i in range(3):
        assert _publish(i, producer_factory=factory) is True

    sent = [item for producer in created for item in producer.sent]
    assert [topic for topic, _ in sent] == [events.KAFKA_TOPIC] * 3
    assert [json.loads(value) for _, value in sent] == [
        {
            "event_type": "download.completed",
            "schema_version": events.SCHEMA_VERSION,
            "downloaded_bytes": 1000 + i,
            "duration_seconds": 0.5 + i,
            "site_id": f"site-{i}",
            "status": "completed",
        }
        for i in range(3)
    ]


def test_async_mode_failed_send_stops_and_replaces_producer(fresh_runner):
    log = {"loops": []}
    created = []

    def factory(**_kwargs):
        created.append(_FakeAsyncProducer(log, fail_sends=1 if not created else 0))
        return created[-1]

    assert _publish(0, producer_factory=factory) is False
    assert created[0].stopped, "failed producer must be stopped, not cached"
    assert _publish(1, producer_factory=factory) is True
    assert len(created) == 2
    assert _publish(2, producer_factory=factory) is True
    assert len(created) == 2


def test_failed_start_clears_key_so_next_publish_builds_new_producer(fresh_runner):
    log = {"loops": []}
    created = []

    class _BlipStart(_FakeAsyncProducer):
        async def start(self):
            self.starts += 1
            raise RuntimeError("bootstrap timeout")

    def factory(**_kwargs):
        created.append((_BlipStart if not created else _FakeAsyncProducer)(log))
        return created[-1]

    assert _publish(0, producer_factory=factory) is False
    assert len(created) == 1 and created[0].starts == 1
    assert _publish(1, producer_factory=factory) is True, "failed start stayed cached: the key is poisoned"
    assert len(created) == 2, f"{len(created)} producers: next publish after a failed start must build a new one"
    assert _publish(2, producer_factory=factory) is True
    assert len(created) == 2, f"{len(created)} producers: the replacement was not reused"
    assert created[1].starts == 1 and len(created[1].sent) == 2


def test_passed_streamer_runs_on_one_shared_loop(fresh_runner):
    loops = []

    class Streamer:
        async def send(self, topic, payload):
            loops.append(asyncio.get_running_loop())

    streamer = Streamer()
    cfg = {"kafka_event_streaming_enabled": True}
    for i in range(3):
        assert events.publish_download_completion(
            cfg, downloaded_bytes=i, duration_seconds=0.1, site_id="s", status="completed", streamer=streamer
        ) is True
    assert len(loops) == 3
    assert len({id(loop) for loop in loops}) == 1, "M059: a fresh event loop per published event"


def test_publish_inside_running_loop_still_refuses(fresh_runner, monkeypatch):
    ran = []
    debug_args = []
    monkeypatch.setattr(events.logger, "debug", lambda _msg, *args: debug_args.append(args))

    class Streamer:
        async def send(self, topic, payload):
            ran.append(topic)

    async def caller():
        return events.publish_download_completion(
            {"kafka_event_streaming_enabled": True},
            downloaded_bytes=1, duration_seconds=0.1, site_id="s", status="completed", streamer=Streamer(),
        )

    assert asyncio.run(caller()) is False
    assert ran == [], "publish scheduled the send from inside a running event loop"
    refusals = [args[0] for args in debug_args if args and isinstance(args[0], RuntimeError)]
    assert any("running event loop" in str(exc) for exc in refusals), f"no running-loop refusal: {debug_args}"


class _GatedProducer:
    """aiokafka-like: stop() flushes; send on a stopped producer raises; a gated send fails on release."""

    def __init__(self, n, gates=()):
        self.n = n
        self.gates = list(gates)
        self.stopped = False
        self.sent = 0

    async def start(self):
        pass

    async def stop(self):
        self.stopped = True

    async def send_and_wait(self, topic, value):
        if self.stopped:
            raise RuntimeError(f"P{self.n} closed")
        if self.gates:
            entered, release = self.gates.pop(0)
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            raise RuntimeError(f"P{self.n} broker timeout")
        self.sent += 1


def _in_thread(results, name, i, factory):
    thread = threading.Thread(
        target=lambda: results.__setitem__(name, _publish(i, producer_factory=factory)), name=name, daemon=True
    )
    thread.start()
    return thread


def test_broker_outage_start_does_not_serialize_publishers(fresh_runner):
    delay = 0.4

    class _DownBroker:
        async def start(self):
            await asyncio.sleep(delay)
            raise RuntimeError("bootstrap timeout")

        async def stop(self):
            pass

        async def send_and_wait(self, topic, value):
            pass

    def factory(**_kwargs):
        return _DownBroker()

    latencies = []

    def one(i):
        started = time.monotonic()
        ok = _publish(i, producer_factory=factory)
        latencies.append((round(time.monotonic() - started, 2), ok))

    threads = [threading.Thread(target=one, args=(i,), daemon=True) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(latencies) == 6 and all(ok is False for _, ok in latencies), latencies
    worst = max(lat for lat, _ in latencies)
    assert worst < 3 * delay, f"F1: publishers serialized behind one failing start(): {sorted(latencies)}"


def test_pending_start_does_not_block_other_publishers(fresh_runner):
    entered, release = threading.Event(), threading.Event()
    log = {"loops": []}

    class _SlowStart(_FakeAsyncProducer):
        async def start(self):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            raise RuntimeError("bootstrap timeout")

    results = {}
    slow = _in_thread(results, "slow", 0, lambda **_kw: _SlowStart(log))
    try:
        assert entered.wait(5)
        fast = _in_thread(results, "fast", 1, lambda **_kw: _FakeAsyncProducer(log))
        fast.join(5)
        assert results.get("fast") is True, "F1: a pending start() held the streamer lock and blocked a download"
    finally:
        release.set()
        slow.join(5)
    assert results["slow"] is False


def test_stale_failure_does_not_discard_healthy_replacement(fresh_runner):
    made = []
    a_in, a_go, b_in, b_go = (threading.Event() for _ in range(4))

    def factory(**_kwargs):
        made.append(_GatedProducer(len(made) + 1, gates=[(a_in, a_go), (b_in, b_go)] if not made else ()))
        return made[-1]

    results = {}
    b = None
    try:
        a = _in_thread(results, "A", 0, factory)
        assert a_in.wait(5)
        b = _in_thread(results, "B", 1, factory)
        assert b_in.wait(5)
        a_go.set()
        a.join(5)
        results["C"] = _publish(2, producer_factory=factory)
    finally:
        a_go.set()
        b_go.set()
        if b is not None:
            b.join(5)
    assert results == {"A": False, "C": True, "B": False}
    assert not made[1].stopped, "F3: a stale failure on P1 discarded and stopped the healthy P2"
    assert _publish(3, producer_factory=factory) is True
    assert len(made) == 2, f"F3: {len(made)} producers; the healthy replacement was not reused"


def test_discarded_streamer_is_not_restarted_by_stale_holder(fresh_runner, monkeypatch):
    made = []
    fail_now = threading.Event()
    fail_now.set()

    def factory(**_kwargs):
        made.append(_GatedProducer(len(made) + 1, gates=[(threading.Event(), fail_now)] if not made else ()))
        return made[-1]

    real_streamer_for = fresh_runner.streamer_for
    got, hold = threading.Event(), threading.Event()

    def holding_streamer_for(bootstrap_servers, producer_factory):
        streamer = real_streamer_for(bootstrap_servers, producer_factory)
        if threading.current_thread().name == "stale":
            got.set()
            hold.wait(5)
        return streamer

    monkeypatch.setattr(fresh_runner, "streamer_for", holding_streamer_for)
    results = {}
    try:
        stale = _in_thread(results, "stale", 0, factory)
        assert got.wait(5)
        results["A"] = _publish(1, producer_factory=factory)
    finally:
        hold.set()
    stale.join(5)
    results["F"] = _publish(2, producer_factory=factory)
    fresh_runner.shutdown()
    assert [p.stopped for p in made] == [True] * len(made), f"F2: orphan producer never stopped: {[(p.n, p.stopped) for p in made]}"
    assert len(made) == 2, f"F2: discarded streamer restarted by a stale holder ({len(made)} producers)"
    assert results == {"A": False, "stale": False, "F": True}


class _ConnRecorder:
    def __init__(self, status=200, raise_on_response=False):
        self.status = status
        self.raise_on_response = raise_on_response
        self.conns = []

    def factory(self, scheme):
        recorder = self

        class _Conn:
            def __init__(self, host, port, timeout=None):
                self.scheme, self.host, self.port = scheme, host, port
                self.closed = False
                recorder.conns.append(self)

            def request(self, method, path, headers=None):
                self.path = path

            def getresponse(self):
                if recorder.raise_on_response:
                    raise http.client.RemoteDisconnected("gone")
                return type("Resp", (), {"status": recorder.status})()

            def close(self):
                self.closed = True

        return _Conn


def _probe(monkeypatch, endpoint, recorder):
    monkeypatch.setattr(node_sentinel.http.client, "HTTPConnection", recorder.factory("http"))
    monkeypatch.setattr(node_sentinel.http.client, "HTTPSConnection", recorder.factory("https"))
    sentinel = node_sentinel.NodeSentinel(nodes=[{"node_id": "n1", "endpoint": endpoint}])
    return sentinel._probe_health(sentinel._nodes["n1"])


@pytest.mark.parametrize(
    "endpoint,scheme,port",
    [
        ("https://10.0.20.41/health", "https", 443),
        ("https://10.0.20.41:8443/health", "https", 8443),
        ("http://10.0.20.42/health", "http", 80),
        ("http://10.0.20.42:8080/health", "http", 8080),
    ],
)
def test_probe_uses_scheme_port_and_closes(monkeypatch, endpoint, scheme, port):
    recorder = _ConnRecorder()
    assert _probe(monkeypatch, endpoint, recorder) is True
    assert [(c.scheme, c.host, c.port, c.path) for c in recorder.conns] == [
        (scheme, endpoint.split("//")[1].split("/")[0].split(":")[0], port, "/health")
    ], f"M125: {endpoint} probed as {[(c.scheme, c.port) for c in recorder.conns]}"
    assert recorder.conns[0].closed, "M125: probe connection never closed"


def test_probe_closes_connection_on_failure(monkeypatch):
    recorder = _ConnRecorder(raise_on_response=True)
    assert _probe(monkeypatch, "https://10.0.20.41/health", recorder) is False
    assert len(recorder.conns) == 1
    assert recorder.conns[0].closed, "M125: probe connection never closed"
