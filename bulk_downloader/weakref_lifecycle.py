"""bulk_downloader.weakref_lifecycle -- Row 1028: Weakref Callback Lifecycle Manager & Cache Pruning Engine.

Provides safe weak reference lifecycle tracking, callback execution on garbage collection,
deterministic bounded cache pruning (FIFO, size limit, TTL with fail-closed validation),
and self-evicting WeakValueCache for user-defined object instances.
Follows Fleet Rule 21 (zero site logins touched).
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, MutableMapping, Optional
import weakref

log = logging.getLogger(__name__)

POLICY_NOT_EVALUABLE = -1


class WeakrefCallbackManager:
    """Manages weak reference registration and safe lifecycle cleanup callback execution."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._callbacks: dict[int, Callable[[weakref.ref], None]] = {}
        self._refs: dict[int, weakref.ref] = {}
        self._total_finalizations = 0
        self._total_callbacks_invoked = 0
        self._callback_exceptions = 0

    def register(self, target: Any, callback: Optional[Callable[[weakref.ref], None]] = None) -> weakref.ref:
        """Register a weak reference to target with an optional cleanup callback."""
        def _internal_cb(ref: weakref.ref) -> None:
            self._handle_finalization(ref)

        ref = weakref.ref(target, _internal_cb)
        ref_id = id(ref)
        with self._lock:
            self._refs[ref_id] = ref
            if callback is not None:
                self._callbacks[ref_id] = callback
        return ref

    def _handle_finalization(self, ref: weakref.ref) -> None:
        ref_id = id(ref)
        cb = None
        with self._lock:
            self._refs.pop(ref_id, None)
            cb = self._callbacks.pop(ref_id, None)
            self._total_finalizations += 1

        if cb is not None:
            try:
                cb(ref)
                with self._lock:
                    self._total_callbacks_invoked += 1
            except Exception as exc:
                with self._lock:
                    self._callback_exceptions += 1
                log.debug("Weakref cleanup callback raised exception: %s", exc)

    def active_ref_count(self) -> int:
        """Return number of currently active weak references."""
        with self._lock:
            return len(self._refs)

    def get_metrics(self) -> dict[str, Any]:
        """Return telemetry metrics for weak reference lifecycle."""
        with self._lock:
            return {
                "active_refs": len(self._refs),
                "total_finalizations": self._total_finalizations,
                "total_callbacks_invoked": self._total_callbacks_invoked,
                "callback_exceptions": self._callback_exceptions,
                "timestamp": time.time(),
            }


class CachePruningEngine:
    """Manages bounded/unbounded caches and executes deterministic pruning policies."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._caches: dict[str, dict[str, Any]] = {}
        self._total_prune_cycles = 0
        self._total_items_evicted = 0
        self._policy_evaluation_errors = 0

    def register_cache(
        self,
        name: str,
        cache_obj: Any,
        max_size: Optional[int] = None,
        ttl_seconds: Optional[float] = None,
        timestamp_getter: Optional[Callable[[Any], float]] = None,
    ) -> None:
        """Register a dictionary-like or mapping cache object for managed pruning."""
        # F2 fail-closed validation: reject TTL registration if timestamp_getter is missing and values cannot be interpreted
        if ttl_seconds is not None and timestamp_getter is None:
            if isinstance(cache_obj, MutableMapping):
                for sample in cache_obj.values():
                    if not isinstance(sample, (int, float)):
                        raise ValueError(
                            f"Cache '{name}' registered with ttl_seconds={ttl_seconds} requires timestamp_getter "
                            f"because values are of non-numeric type {type(sample).__name__}"
                        )

        with self._lock:
            self._caches[name] = {
                "cache": cache_obj,
                "max_size": max_size,
                "ttl_seconds": ttl_seconds,
                "timestamp_getter": timestamp_getter,
            }

    def unregister_cache(self, name: str) -> None:
        """Unregister a cache from the pruning engine."""
        with self._lock:
            self._caches.pop(name, None)

    def get_cache_max_size(self, name: str, default: Optional[int] = None) -> Optional[int]:
        """Return registered max_size for a cache without mutating policy."""
        with self._lock:
            entry = self._caches.get(name)
            if entry is not None and entry.get("max_size") is not None:
                return entry["max_size"]
            return default

    def prune_cache(self, name: str, max_size_override: Optional[int] = None) -> int:
        """Execute pruning on the specified cache based on registered rules.

        max_size_override allows a one-off diagnostic or bounded prune without mutating registered policy (F1).
        Returns number of evicted items, or POLICY_NOT_EVALUABLE (-1) if any entry's TTL could not be
        evaluated (F2). Unevaluable entries are kept and each one is counted in policy_evaluation_errors;
        evaluable expired entries are still evicted and max_size is enforced either way.
        """
        with self._lock:
            entry = self._caches.get(name)
            if entry is None:
                return 0
            cache = entry["cache"]
            max_size = max_size_override if max_size_override is not None else entry["max_size"]
            ttl = entry["ttl_seconds"]
            ts_getter = entry["timestamp_getter"]
            evicted = 0
            ttl_unevaluable = False

            # 1. TTL expiration pruning
            if ttl is not None and isinstance(cache, MutableMapping):
                now = time.time()
                expired_keys = []
                unevaluable_count = 0
                for k, v in list(cache.items()):
                    item_ts = ts_getter(v) if ts_getter else (v if isinstance(v, (int, float)) else None)
                    if item_ts is not None:
                        if (now - item_ts) > ttl:
                            expired_keys.append(k)
                    else:
                        unevaluable_count += 1

                # F2: fail closed per entry -- an unevaluable entry is kept and counted, never
                # guessed expired; the size bound below still applies.
                self._policy_evaluation_errors += unevaluable_count
                ttl_unevaluable = unevaluable_count > 0

                for k in expired_keys:
                    cache.pop(k, None)
                    evicted += 1

            # 2. Size limit pruning (FIFO order: oldest inserted keys evicted first)
            if max_size is not None and len(cache) > max_size and isinstance(cache, MutableMapping):
                excess = len(cache) - max_size
                # In Python 3.7+, dict keys preserve insertion order; first keys are oldest (FIFO)
                keys_to_remove = list(cache.keys())[:excess]
                for k in keys_to_remove:
                    cache.pop(k, None)
                    evicted += 1

            self._total_prune_cycles += 1
            self._total_items_evicted += evicted
            return POLICY_NOT_EVALUABLE if ttl_unevaluable else evicted

    def prune_all(self) -> int:
        """Prune all registered caches."""
        total = 0
        with self._lock:
            for name in list(self._caches.keys()):
                res = self.prune_cache(name)
                if res > 0:
                    total += res
        return total

    def get_metrics(self) -> dict[str, Any]:
        """Return metrics snapshot."""
        with self._lock:
            return {
                "registered_caches": len(self._caches),
                "total_prune_cycles": self._total_prune_cycles,
                "total_items_evicted": self._total_items_evicted,
                "policy_evaluation_errors": self._policy_evaluation_errors,
                "timestamp": time.time(),
            }


class WeakValueCache(MutableMapping):
    """Thread-safe mapping where user-defined object instances are held weakly and auto-evict on garbage collection.

    Note: Primitive builtin types (dict, str, int, list, tuple) cannot be weakly referenced
    and will raise TypeError.
    """

    def __init__(self) -> None:
        self._data: dict[Any, weakref.ref] = {}
        self._lock = threading.RLock()

    def __getitem__(self, key: Any) -> Any:
        with self._lock:
            ref = self._data[key]
            val = ref()
            if val is None:
                del self._data[key]
                raise KeyError(key)
            return val

    def __setitem__(self, key: Any, value: Any) -> None:
        def _cleanup(ref: weakref.ref, k: Any = key) -> None:
            with self._lock:
                if self._data.get(k) is ref:
                    del self._data[k]

        try:
            weak_ref = weakref.ref(value, _cleanup)
        except TypeError as exc:
            raise TypeError(
                f"WeakValueCache requires a weakly-referenceable object instance (not primitive {type(value).__name__}): {exc}"
            ) from exc

        with self._lock:
            self._data[key] = weak_ref

    def __delitem__(self, key: Any) -> None:
        with self._lock:
            del self._data[key]

    def __iter__(self) -> Iterator[Any]:
        with self._lock:
            active_keys = [k for k, ref in self._data.items() if ref() is not None]
        return iter(active_keys)

    def __len__(self) -> int:
        with self._lock:
            return len([ref for ref in self._data.values() if ref() is not None])

    def __contains__(self, key: Any) -> bool:
        with self._lock:
            ref = self._data.get(key)
            if ref is None:
                return False
            if ref() is None:
                del self._data[key]
                return False
            return True

    def get(self, key: Any, default: Any = None) -> Any:
        """Return value for key if present and referenced, else default."""
        with self._lock:
            ref = self._data.get(key)
            if ref is None:
                return default
            val = ref()
            if val is None:
                del self._data[key]
                return default
            return val


_GLOBAL_WEAKREF_MANAGER: Optional[WeakrefCallbackManager] = None
_GLOBAL_PRUNING_ENGINE: Optional[CachePruningEngine] = None
_INIT_LOCK = threading.Lock()


def get_weakref_lifecycle_manager() -> WeakrefCallbackManager:
    """Return global WeakrefCallbackManager instance."""
    global _GLOBAL_WEAKREF_MANAGER
    if _GLOBAL_WEAKREF_MANAGER is None:
        with _INIT_LOCK:
            if _GLOBAL_WEAKREF_MANAGER is None:
                _GLOBAL_WEAKREF_MANAGER = WeakrefCallbackManager()
    return _GLOBAL_WEAKREF_MANAGER


def get_cache_pruning_engine() -> CachePruningEngine:
    """Return global CachePruningEngine instance."""
    global _GLOBAL_PRUNING_ENGINE
    if _GLOBAL_PRUNING_ENGINE is None:
        with _INIT_LOCK:
            if _GLOBAL_PRUNING_ENGINE is None:
                _GLOBAL_PRUNING_ENGINE = CachePruningEngine()
    return _GLOBAL_PRUNING_ENGINE
