"""Asynchronous Protocol Message Event Dispatching (Row 1043).

Provides decoupled, asynchronous event ingestion and pattern-matched dispatching
for protocol messages (such as Chrome DevTools Protocol, WebSocket, and IPC messages).
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

_LOG = logging.getLogger(__name__)


class Subscription:
    """Represents an active protocol message subscription."""

    def __init__(
        self,
        sub_id: str,
        pattern: str,
        handler: Callable[[str, Dict[str, Any]], Any],
        priority: int = 0,
    ):
        self.sub_id = sub_id
        self.pattern = pattern
        self.handler = handler
        self.priority = priority

        if pattern == "*":
            self.regex = None
            self.match_all = True
        elif pattern.endswith(".*"):
            prefix = re.escape(pattern[:-2])
            self.regex = re.compile(rf"^{prefix}\.[^.]+$")
            self.match_all = False
        else:
            self.regex = re.compile(rf"^{re.escape(pattern)}$")
            self.match_all = False

    def matches(self, method: str) -> bool:
        if self.match_all:
            return True
        if self.regex is not None:
            return bool(self.regex.match(method))
        return False


class AsyncProtocolDispatcher:
    """Thread-safe, decoupled asynchronous protocol message dispatcher."""

    def __init__(self, max_queue_size: int = 10000, num_workers: int = 2):
        self.max_queue_size = max_queue_size
        self.num_workers = num_workers
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._subscriptions: Dict[str, Subscription] = {}
        self._sub_lock = threading.RLock()

        self._workers: List[threading.Thread] = []
        self._running = False
        self._stop_event = threading.Event()

        # Telemetry metrics
        self._metrics_lock = threading.Lock()
        self._dispatched_count = 0
        self._processed_count = 0
        self._dropped_count = 0
        self._error_count = 0

    def subscribe(
        self,
        pattern: str,
        handler: Callable[[str, Dict[str, Any]], Any],
        priority: int = 0,
    ) -> str:
        """Register a handler for protocol methods matching pattern."""
        sub_id = f"sub_{uuid.uuid4().hex[:12]}"
        sub = Subscription(sub_id=sub_id, pattern=pattern, handler=handler, priority=priority)
        with self._sub_lock:
            self._subscriptions[sub_id] = sub
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Remove an existing subscription by ID."""
        with self._sub_lock:
            return self._subscriptions.pop(sub_id, None) is not None

    def start(self) -> None:
        """Start background worker threads."""
        if self._running or self.num_workers <= 0:
            return
        self._running = True
        self._stop_event.clear()
        for i in range(self.num_workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"bd-protocol-worker-{i}",
                daemon=True,
            )
            self._workers.append(t)
            t.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop worker threads and drain pending events."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        # Wake up workers
        for _ in self._workers:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
        deadline = time.time() + timeout
        for t in self._workers:
            rem = max(0.01, deadline - time.time())
            t.join(timeout=rem)
        self._workers.clear()

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until all currently queued events are processed."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    def dispatch(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        priority: int = 0,
        block: bool = True,
        timeout: Optional[float] = None,
    ) -> bool:
        """Ingest a protocol message for asynchronous dispatching."""
        payload = params if params is not None else {}
        item = (method, payload, priority)

        try:
            if block:
                self._queue.put(item, block=True, timeout=timeout)
            else:
                self._queue.put_nowait(item)
            with self._metrics_lock:
                self._dispatched_count += 1
            return True
        except (queue.Full, queue.Empty):
            with self._metrics_lock:
                self._dropped_count += 1
            return False

    def dispatch_nowait(self, method: str, params: Optional[Dict[str, Any]] = None) -> bool:
        """Non-blocking ingest shorthand."""
        return self.dispatch(method, params, block=False)

    def dispatch_sync(self, method: str, params: Optional[Dict[str, Any]] = None) -> List[Any]:
        """Dispatch immediately to matching handlers in the calling thread."""
        payload = params if params is not None else {}
        with self._sub_lock:
            subs = [s for s in self._subscriptions.values() if s.matches(method)]

        # Sort by priority descending
        subs.sort(key=lambda s: s.priority, reverse=True)
        results = []
        for sub in subs:
            try:
                res = sub.handler(method, payload)
                results.append(res)
                with self._metrics_lock:
                    self._processed_count += 1
            except Exception as e:
                _LOG.warning("Exception in synchronous protocol handler %s: %s", sub.sub_id, e)
                with self._metrics_lock:
                    self._error_count += 1
        return results

    def _worker_loop(self) -> None:
        """Worker thread processing queued protocol events."""
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if item is None:
                self._queue.task_done()
                break

            method, params, _ = item
            try:
                with self._sub_lock:
                    subs = [s for s in self._subscriptions.values() if s.matches(method)]
                subs.sort(key=lambda s: s.priority, reverse=True)

                for sub in subs:
                    try:
                        sub.handler(method, params)
                    except Exception as e:
                        _LOG.warning("Exception in protocol handler %s: %s", sub.sub_id, e)
                        with self._metrics_lock:
                            self._error_count += 1
                    finally:
                        with self._metrics_lock:
                            self._processed_count += 1
            finally:
                self._queue.task_done()

    def get_metrics(self) -> Dict[str, Any]:
        """Retrieve telemetry snapshot."""
        with self._metrics_lock:
            with self._sub_lock:
                active_subs = len(self._subscriptions)
            return {
                "dispatched_count": self._dispatched_count,
                "processed_count": self._processed_count,
                "dropped_count": self._dropped_count,
                "error_count": self._error_count,
                "active_subscriptions": active_subs,
                "queue_size": self._queue.qsize(),
            }


_GLOBAL_DISPATCHER: Optional[AsyncProtocolDispatcher] = None
_GLOBAL_DISPATCHER_LOCK = threading.Lock()


def get_global_protocol_dispatcher() -> AsyncProtocolDispatcher:
    """Retrieve or initialize process-wide protocol dispatcher singleton."""
    global _GLOBAL_DISPATCHER
    with _GLOBAL_DISPATCHER_LOCK:
        if _GLOBAL_DISPATCHER is None:
            _GLOBAL_DISPATCHER = AsyncProtocolDispatcher(num_workers=2)
            _GLOBAL_DISPATCHER.start()
        return _GLOBAL_DISPATCHER
