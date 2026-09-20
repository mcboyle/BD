from __future__ import annotations

BD_GATE_SCOPE = "module"

import json


class _Redis:
    def __init__(self, payload=None, error=None):
        self.payload, self.error = payload, error
    def get(self, _key):
        if self.error:
            raise self.error
        return self.payload


def test_redis_update_reloads_valid_config_atomically(tmp_path):
    from bulk_downloader.distributed_config import DistributedConfigWatcher
    local = tmp_path / "app_config.json"
    local.write_text(json.dumps({"workers": 1}))
    target = {"workers": 1}
    watcher = DistributedConfigWatcher(local, target, _Redis(b'{"workers": 2}'),
                                       validator=lambda value: isinstance(value["workers"], int))
    assert watcher.reload() is True
    assert target == {"workers": 2}


def test_offline_redis_uses_local_config(tmp_path):
    from bulk_downloader.distributed_config import DistributedConfigWatcher
    local = tmp_path / "app_config.json"
    local.write_text(json.dumps({"workers": 1}))
    target = {}
    watcher = DistributedConfigWatcher(local, target, _Redis(error=OSError("offline")),
                                       validator=lambda _value: True)
    assert watcher.reload() is True
    assert target == {"workers": 1}


def test_invalid_redis_config_does_not_replace_live_config(tmp_path):
    from bulk_downloader.distributed_config import DistributedConfigWatcher
    local = tmp_path / "app_config.json"
    local.write_text("{}")
    target = {"workers": 1}
    watcher = DistributedConfigWatcher(local, target, _Redis(b'{"workers": "bad"}'),
                                       validator=lambda value: isinstance(value.get("workers"), int))
    assert watcher.reload() is False
    assert target == {"workers": 1}


# ---- fixer (O928) controls for the correctness REFUTE P1/P1/P2/P2 ---------

import queue
import threading


class _PubSub:
    def __init__(self, hub):
        self.hub, self.q, self.channels = hub, queue.Queue(), set()

    def subscribe(self, channel):
        self.channels.add(channel)
        self.hub.subs.append(self)

    def get_message(self, ignore_subscribe_messages=False, timeout=None):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        if self in self.hub.subs:
            self.hub.subs.remove(self)


class _RedisHub:
    """Fake Redis with GET/SET and Pub/Sub delivery to every subscriber."""

    def __init__(self):
        self.store, self.subs, self.gets = {}, [], 0

    def get(self, key):
        self.gets += 1
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value.encode("utf-8") if isinstance(value, str) else value

    def publish(self, channel, payload):
        n = 0
        for s in list(self.subs):
            if channel in s.channels:
                s.q.put({"type": "message", "channel": channel, "data": payload})
                n += 1
        return n

    def pubsub(self):
        return _PubSub(self)


def _wait(pred, timeout=5.0):
    deadline = threading.Event()
    import time
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        deadline.wait(0.01)
    return pred()


def test_broadcast_triggers_reload_across_subscribed_workers(tmp_path):
    """P1: one broadcast_config() reloads BOTH subscribed workers (no manual reload)."""
    from bulk_downloader.distributed_config import DistributedConfigWatcher, broadcast_config
    hub = _RedisHub()
    local = tmp_path / "app_config.json"
    local.write_text(json.dumps({"workers": 1}))
    targets = [{"workers": 1}, {"workers": 1}]
    watchers = [DistributedConfigWatcher(local, t, hub, schema={"workers": int}) for t in targets]
    try:
        assert [w.start() for w in watchers] == ["pubsub", "pubsub"]
        assert broadcast_config(hub, {"workers": 2}) == 2
        assert _wait(lambda: all(t == {"workers": 2} for t in targets)), targets
        # version 1 = the local file adopted at start() (E1), 2 = the broadcast
        assert all(w.version == 2 and w.last_source == "redis" for w in watchers)
        assert all(dict(w.current) == {"workers": 2} for w in watchers)
    finally:
        for w in watchers:
            w.stop()


def test_client_without_pubsub_polls_the_key(tmp_path):
    from bulk_downloader.distributed_config import DistributedConfigWatcher
    hub = _RedisHub()
    del hub.__class__.pubsub  # this client cannot subscribe
    try:
        local = tmp_path / "app_config.json"
        local.write_text(json.dumps({"workers": 1}))
        target = {"workers": 1}
        w = DistributedConfigWatcher(local, target, hub, schema={"workers": int})
        assert w.start(poll_interval=0.01) == "poll"
        hub.set("bulk_downloader:config", '{"workers": 3}')
        assert _wait(lambda: target == {"workers": 3}), target
        w.stop()
    finally:
        _RedisHub.pubsub = lambda self: _PubSub(self)


def test_publication_is_atomic_for_concurrent_readers(tmp_path):
    """P1: a plain-dict reader never observes {} (or a partial mapping) mid-reload;
    the immutable snapshot flips old -> new in one reference swap."""
    from bulk_downloader.distributed_config import DistributedConfigWatcher
    local = tmp_path / "app_config.json"
    old = {"workers": 1, "retries": 3}
    new = {"workers": 2, "retries": 5, "extra": True}
    local.write_text(json.dumps(new))
    target = dict(old)
    w = DistributedConfigWatcher(local, target, None, schema={"workers": int})
    seen_bad, snapshots, stop = [], set(), threading.Event()

    import time

    def reader():
        while not stop.is_set():
            view = dict(target)
            if not view or not {"workers", "retries"} <= set(view):
                seen_bad.append(view)
            snapshots.add(tuple(sorted(w.current.items())))
            time.sleep(0)  # yield the GIL so the writer makes progress

    threads = [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    for _ in range(100):
        local.write_text(json.dumps(new)); assert w.reload()
        local.write_text(json.dumps(old)); assert w.reload()
    stop.set()
    for t in threads:
        t.join()
    assert not seen_bad, seen_bad[:3]
    assert snapshots <= {tuple(sorted(old.items())), tuple(sorted(new.items()))}, snapshots
    assert dict(w.current) == old and w.version == 200


def test_schema_rejects_invalid_input_and_keeps_configuration(tmp_path):
    """P2: workers='bad' is rejected by the schema; a validator raising (KeyError on {})
    is a rejection, not an exception; the live configuration is untouched either way."""
    import pytest
    from bulk_downloader.distributed_config import DistributedConfigWatcher, schema_validator
    local = tmp_path / "app_config.json"
    local.write_text("{}")
    target = {"workers": 1}
    w = DistributedConfigWatcher(local, target, _Redis(b'{"workers": "bad"}'), schema={"workers": int})
    assert w.reload() is False and target == {"workers": 1} and w.version == 0
    w = DistributedConfigWatcher(local, target, _Redis(b'{"workers": true}'), schema={"workers": int})
    assert w.reload() is False and target == {"workers": 1}
    w = DistributedConfigWatcher(local, target, _Redis(b'{}'), validator=lambda value: isinstance(value["workers"], int))
    assert w.reload() is False and target == {"workers": 1}
    assert schema_validator({"workers": int})({"workers": 4, "more": 1}) is True
    with pytest.raises(ValueError):
        DistributedConfigWatcher(local, target, None, validator=lambda v: True, schema={"workers": int})


def test_undecodable_local_file_is_rejected_not_raised(tmp_path):
    """P2: a local file with byte 0xFF (not UTF-8) -> False, target unchanged."""
    from bulk_downloader.distributed_config import DistributedConfigWatcher
    local = tmp_path / "app_config.json"
    local.write_bytes(b'{"workers": \xff}')
    target = {"workers": 1}
    w = DistributedConfigWatcher(local, target, _Redis(error=OSError("offline")), schema={"workers": int})
    assert w.reload() is False
    assert target == {"workers": 1} and w.version == 0
    local.write_bytes(b'\xff\xfe')
    assert w.reload() is False and target == {"workers": 1}


# ---- fixer (O928) controls: correctness REFUTE E1 / E2 / E3 ----

def test_start_adopts_the_already_published_configuration(tmp_path):
    """E1: a worker joining a fleet takes the configuration already in Redis
    at start() -- synchronously, before any broadcast -- in both modes."""
    from bulk_downloader.distributed_config import DistributedConfigWatcher
    local = tmp_path / "app_config.json"
    local.write_text(json.dumps({"workers": 1}))
    for pubsub in (True, False):
        hub = _RedisHub()
        hub.set("bulk_downloader:config", '{"workers": 9}')
        if not pubsub:
            hub.pubsub = None  # no subscription on this client
        target = {"workers": 1}
        w = DistributedConfigWatcher(local, target, hub, schema={"workers": int})
        try:
            mode = w.start(poll_interval=0.01)
            assert mode == ("pubsub" if pubsub else "poll")
            assert target == {"workers": 9} and w.last_source == "redis" and w.version == 1
        finally:
            w.stop()
    # Redis offline at start: the local file is adopted
    target = {"workers": 1}
    w = DistributedConfigWatcher(local, target, None, schema={"workers": int})
    local.write_text(json.dumps({"workers": 4}))
    try:
        w.start(poll_interval=0.01)
        assert target == {"workers": 4} and w.last_source == "local"
    finally:
        w.stop()


def test_same_shape_reloads_never_interrupt_a_reader_iterating_target(tmp_path):
    """E2: value changes rewrite existing keys in place (no size change), so
    a reader iterating the plain dict never gets 'dictionary changed size
    during iteration'."""
    from bulk_downloader.distributed_config import DistributedConfigWatcher
    local = tmp_path / "app_config.json"
    target = {"workers": 1, "retries": 3, "a": 0, "b": 0, "c": 0, "d": 0}
    w = DistributedConfigWatcher(local, target, None, schema={"workers": int})
    errors, stop = [], threading.Event()

    def reader():
        while not stop.is_set():
            try:
                for _k, _v in target.items():
                    pass
                for _k in target:
                    pass
            except RuntimeError as exc:
                errors.append(str(exc))
                return

    threads = [threading.Thread(target=reader) for _ in range(3)]
    for t in threads:
        t.start()
    for i in range(300):
        local.write_text(json.dumps({"workers": i, "retries": 3, "a": i, "b": i, "c": i, "d": i}))
        assert w.reload()
    stop.set()
    for t in threads:
        t.join(5)
    assert errors == []
    assert target["workers"] == 299 and w.version == 300


def test_dropped_pubsub_socket_resubscribes_or_falls_back_to_polling(tmp_path):
    """E3: get_message raising (socket gone) no longer leaves the watcher
    blind: it resubscribes (or polls when it cannot) and reloads what it
    missed."""
    from bulk_downloader.distributed_config import DistributedConfigWatcher
    local = tmp_path / "app_config.json"
    local.write_text(json.dumps({"workers": 1}))

    drop = threading.Event()

    class DroppingHub(_RedisHub):
        def __init__(self):
            super().__init__()
            self.made = 0

        def pubsub(self):
            self.made += 1
            ps = _PubSub(self)
            if self.made == 1:                     # first subscription dies on read once told to
                def boom(*a, **k):
                    drop.wait(5)
                    raise ConnectionError("socket closed")
                ps.get_message = boom
            return ps

    hub = DroppingHub()
    target = {"workers": 1}
    w = DistributedConfigWatcher(local, target, hub, schema={"workers": int})
    try:
        assert w.start(poll_interval=0.01) == "pubsub"
        hub.set("bulk_downloader:config", '{"workers": 5}')   # written while the socket is dying
        drop.set()
        assert _wait(lambda: w.resubscribes >= 1 and target == {"workers": 5}), (w.resubscribes, target)
        assert hub.made == 2                                    # resubscribed once
        from bulk_downloader.distributed_config import broadcast_config
        assert broadcast_config(hub, {"workers": 6}) == 1       # the NEW subscription receives broadcasts
        assert _wait(lambda: target == {"workers": 6}), target
    finally:
        w.stop()

    drop.clear()

    class NeverAgain(DroppingHub):
        def pubsub(self):
            if self.made >= 1:
                self.made += 1
                raise ConnectionError("redis gone")
            return super().pubsub()

    hub = NeverAgain()
    target = {"workers": 1}
    w = DistributedConfigWatcher(local, target, hub, schema={"workers": int})
    try:
        assert w.start(poll_interval=0.01) == "pubsub"
        hub.set("bulk_downloader:config", '{"workers": 7}')
        drop.set()
        assert _wait(lambda: target == {"workers": 7}), target  # served by polling
        assert w._pubsub is None
    finally:
        w.stop()
