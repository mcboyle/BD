"""Optional Redis-backed configuration reload with a local-file fallback (Row 869).

A ``DistributedConfigWatcher`` keeps a process's live configuration mapping in
step with a shared Redis key. Updates are BROADCAST by the writer
(:func:`broadcast_config`: ``SET key`` + ``PUBLISH key:changed``) and every
subscribed worker reloads on the message (:meth:`DistributedConfigWatcher.start`);
a worker whose Redis client cannot Pub/Sub falls back to polling the key. With
Redis offline the local file is the source. Nothing is applied before it decodes
and validates; publication is atomic for readers (:attr:`current` is swapped by
reference, and the caller's ``target`` dict is updated before stale keys are
removed, so a concurrent reader never observes an empty mapping).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

CHANGED_SUFFIX = ":changed"


def broadcast_config(redis_client, value: Mapping[str, Any], *, key: str = "bulk_downloader:config") -> int:
    """Writer side: store ``value`` under ``key`` and notify every watcher.
    Returns the number of subscribers Redis reports for the change channel."""
    payload = json.dumps(dict(value), sort_keys=True)
    redis_client.set(key, payload)
    published = redis_client.publish(key + CHANGED_SUFFIX, payload)
    return int(published or 0)


def schema_validator(schema: Mapping[str, type]) -> Callable[[Mapping[str, Any]], bool]:
    """A validator requiring every schema key to be present with the given type
    (bool is never accepted for int). Extra keys are allowed."""
    def _validate(value: Mapping[str, Any]) -> bool:
        for name, typ in schema.items():
            if name not in value:
                return False
            v = value[name]
            if typ is int and isinstance(v, bool):
                return False
            if not isinstance(v, typ):
                return False
        return True
    return _validate


class DistributedConfigWatcher:
    def __init__(self, local_path, target: dict, redis_client=None, *,
                 key="bulk_downloader:config", validator=None, schema: Optional[Mapping[str, type]] = None):
        self.local_path = Path(local_path)
        self.target = target
        self.redis_client = redis_client
        self.key = key
        if validator is not None and schema is not None:
            raise ValueError("pass either validator or schema, not both")
        self.validator = validator or (schema_validator(schema) if schema else (lambda value: True))
        self._lock = RLock()
        self.current: Mapping[str, Any] = MappingProxyType(dict(target))
        self.version = 0
        self.last_source: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pubsub = None
        self.resubscribes = 0   # Pub/Sub recoveries (dropped socket -> resubscribe/poll)

    # ---- decoding / validation -------------------------------------------------
    def _decode(self, raw) -> Optional[dict]:
        """A validated dict, or None. Every decoding/validation failure -- bytes that
        are not UTF-8, non-JSON, non-object, schema mismatch, a validator that
        raises -- is a rejection, never an exception."""
        try:
            if isinstance(raw, (bytes, bytearray)):
                raw = bytes(raw).decode("utf-8")
            value = json.loads(raw)
            if not isinstance(value, dict):
                return None
            if not self.validator(value):
                return None
        except Exception:
            return None
        return value

    def _read_redis(self):
        if self.redis_client is None:
            return None
        try:
            return self.redis_client.get(self.key)
        except Exception:
            return None

    def _read_local(self):
        try:
            return self.local_path.read_bytes()
        except OSError:
            return None

    def _publish(self, value: dict) -> None:
        """Atomic for readers: ``current`` is a new immutable snapshot swapped by
        reference; ``target`` gets the new keys first, then loses the stale ones,
        so a plain-dict reader sees old, old+new or new -- never {}."""
        with self._lock:
            # same-shape reloads (the common case: a value changed) rewrite
            # the existing keys IN PLACE -- no size change, so a reader
            # iterating the plain dict is never interrupted (E2). Only a
            # changed key set adds/removes keys; iterate ``current`` (an
            # immutable snapshot) when the key set can change underneath you.
            for k, v in value.items():
                self.target[k] = v
            for stale in [k for k in self.target if k not in value]:
                del self.target[stale]
            self.current = MappingProxyType(dict(value))
            self.version += 1

    def reload(self) -> bool:
        """Replace the live mapping only after decoding and validation succeed.
        Redis first; the local file when Redis is offline, absent or invalid."""
        for source, raw in (("redis", self._read_redis()), ("local", None)):
            if source == "local":
                raw = self._read_local()
            if raw is None:
                continue
            value = self._decode(raw)
            if value is None:
                continue
            self._publish(value)
            self.last_source = source
            return True
        return False

    # ---- broadcast-triggered reload ------------------------------------------
    def start(self, *, poll_interval: float = 1.0) -> str:
        """Subscribe to ``key:changed`` so a broadcast reloads this worker; when
        the client has no ``pubsub()`` poll the key instead. Returns the mode."""
        if self._thread is not None:
            return "pubsub" if self._pubsub is not None else "poll"
        self._stop.clear()
        # a worker joining a fleet adopts the configuration that is ALREADY
        # published (Redis, else the local file) before it serves anything;
        # it does not wait for the next broadcast (E1)
        self.reload()
        self._pubsub = self._subscribe()
        mode = "pubsub" if self._pubsub is not None else "poll"
        self._thread = threading.Thread(target=self._run, args=(poll_interval,), daemon=True,
                                        name=f"config-watcher:{self.key}")
        self._thread.start()
        return mode

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._pubsub is not None:
            try:
                self._pubsub.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        self._pubsub = None

    def _subscribe(self):
        """A fresh Pub/Sub subscription to ``key:changed``, or None when the
        client has no ``pubsub()`` or the subscription fails."""
        pubsub_factory = getattr(self.redis_client, "pubsub", None)
        if not callable(pubsub_factory):
            return None
        try:
            ps = pubsub_factory()
            ps.subscribe(self.key + CHANGED_SUFFIX)
            return ps
        except Exception:
            return None

    def _run(self, poll_interval: float) -> None:
        last_raw = self._read_redis()
        while not self._stop.is_set():
            if self._pubsub is not None:
                try:
                    msg = self._pubsub.get_message(ignore_subscribe_messages=True, timeout=poll_interval)
                except Exception:
                    # a dropped socket: resubscribe, else fall back to polling
                    # the key (never stay blind on a dead subscription, E3);
                    # a reload catches what was missed while disconnected
                    try:
                        self._pubsub.close()
                    except Exception:
                        pass
                    self._pubsub = self._subscribe()
                    self.resubscribes += 1
                    self.reload()
                    last_raw = self._read_redis()
                    self._stop.wait(poll_interval)
                    continue
                if msg is not None and msg.get("type") == "message":
                    self.reload()
                continue
            raw = self._read_redis()
            if raw != last_raw:
                last_raw = raw
                self.reload()
            self._stop.wait(poll_interval)
