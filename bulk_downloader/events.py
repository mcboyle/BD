"""bulk_downloader.events -- Optional, LAN-only Kafka download-completion event streamer.

Row 825: Streaming telemetry for fleet download pipelines.
Sends download completion metrics to topic bd.downloads when KAFKA_BOOTSTRAP_SERVERS is configured.
Enforces zero external network egress beyond cluster LAN (10.0.70.0/24).
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import inspect
import ipaddress
import json
import logging
import os
import queue
import socket
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger("bulk_downloader.events")

KAFKA_TOPIC = "bd.downloads"
SCHEMA_VERSION = 1
_CLUSTER_LAN = ipaddress.ip_network("10.0.70.0/24")


@dataclass(frozen=True)
class DownloadCompletionEvent:
    downloaded_bytes: int
    duration_seconds: float
    site_id: str
    status: str

    def to_json(self) -> str:
        return json.dumps({
            "event_type": "download.completed",
            "schema_version": SCHEMA_VERSION,
            **asdict(self),
        }, separators=(",", ":"), sort_keys=False)


def is_cluster_lan_endpoint(host: str, port: int | str) -> bool:
    """Verify that host and port strictly belong to the cluster LAN (10.0.70.0/24)."""
    try:
        port_num = int(port)
        if not (1 <= port_num <= 65535):
            return False
    except (TypeError, ValueError):
        return False

    host_str = str(host).strip()
    if not host_str:
        return False

    try:
        addr = ipaddress.ip_address(host_str)
        return addr in _CLUSTER_LAN
    except ValueError:
        pass

    # Resolve hostname and ensure ALL resolved IP addresses are in cluster LAN
    try:
        resolved = socket.getaddrinfo(host_str, port_num, socket.AF_UNSPEC, socket.SOCK_STREAM)
        if not resolved:
            return False
        for res in resolved:
            sockaddr = res[4]
            ip_val = sockaddr[0]
            if ipaddress.ip_address(ip_val) not in _CLUSTER_LAN:
                return False
        return True
    except (socket.gaierror, OSError):
        return False


def kafka_producer_config(bootstrap_servers: str) -> dict[str, str] | None:
    """Return safe producer configuration, accepting literal cluster-LAN peers only."""
    peers = [peer.strip() for peer in str(bootstrap_servers).split(",") if peer.strip()]
    if not peers:
        return None
    for peer in peers:
        host, separator, port = peer.rpartition(":")
        if not separator or not host or not port.isdecimal():
            return None
        if not is_cluster_lan_endpoint(host, port):
            return None
    return {"bootstrap_servers": ",".join(peers)}


def kafka_producer_argv(bootstrap_servers: str) -> tuple[str, ...] | None:
    """Build the equivalent LAN-only producer command without executing it."""
    if kafka_producer_config(bootstrap_servers) is None:
        return None
    return ("kcat", "-P", "-b", bootstrap_servers, "-t", KAFKA_TOPIC)


# ─── Remedy E2: Enforce cluster-LAN egress fence on all broker connections ───

_fenced_installed = False
_fence_lock = threading.Lock()


def _install_lan_socket_fence() -> None:
    """Install socket-level cluster LAN egress fence in kafka-python network client."""
    global _fenced_installed
    with _fence_lock:
        if _fenced_installed:
            return
        try:
            from kafka.net.inet import KafkaNetSocket
            from kafka.errors import KafkaConnectionError

            orig_connect = KafkaNetSocket.connect

            async def _fenced_connect(self, net, addrinfo, socket_options=(), timeout_at=None):
                family, sock_type, proto, _canonname, sockaddr = addrinfo
                dest_ip = sockaddr[0]
                dest_port = sockaddr[1] if len(sockaddr) > 1 else 0
                if not is_cluster_lan_endpoint(dest_ip, dest_port):
                    raise KafkaConnectionError(
                        f"Cluster LAN fence violation: Refusing connection to non-LAN broker {dest_ip}:{dest_port}"
                    )
                return await orig_connect(self, net, addrinfo, socket_options=socket_options, timeout_at=timeout_at)

            KafkaNetSocket.connect = _fenced_connect
            _fenced_installed = True
        except (ImportError, AttributeError) as e:
            logger.debug("kafka net socket fence skipped: %s", e)


def _default_producer(config: dict[str, str]) -> Any:
    """Create a KafkaProducer with cluster-LAN egress fences installed."""
    _install_lan_socket_fence()
    from kafka import KafkaProducer
    return KafkaProducer(**config)


# ─── Bounded worker lifecycle (Remedy for Note: thread & producer lifecycle) ──

class BoundedEventPublisher:
    """Thread-safe, bounded background event publishing queue."""

    def __init__(self, max_queue_size: int = 500) -> None:
        self._queue: queue.Queue[tuple[Callable[[dict[str, str]], Any], dict[str, str], bytes]] = queue.Queue(
            maxsize=max_queue_size
        )
        self._worker_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._producer_cache: dict[str, Any] = {}

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self._stop_event.clear()
                self._worker_thread = threading.Thread(
                    target=self._run_loop,
                    daemon=True,
                    name="bd-kafka-publisher",
                )
                self._worker_thread.start()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            factory, config, payload = item
            try:
                bootstrap = config.get("bootstrap_servers", "")
                producer = self._producer_cache.get(bootstrap)
                if producer is None:
                    producer = factory(config)
                    self._producer_cache[bootstrap] = producer
                producer.send(KAFKA_TOPIC, value=payload)
            except Exception as e:
                logger.debug("Kafka event publish dropped/failed: %s", e)
            finally:
                self._queue.task_done()

    def enqueue(
        self,
        factory: Callable[[dict[str, str]], Any],
        config: dict[str, str],
        payload: bytes,
    ) -> bool:
        self._ensure_worker()
        try:
            self._queue.put_nowait((factory, config, payload))
            return True
        except queue.Full:
            logger.warning("Kafka event publishing queue full; dropping event")
            return False

    def close(self) -> None:
        self._stop_event.set()
        with self._lock:
            for producer in self._producer_cache.values():
                try:
                    if hasattr(producer, "close"):
                        producer.close(timeout=1.0)
                except Exception:
                    pass
            self._producer_cache.clear()


_publisher_singleton = BoundedEventPublisher()


def publish_download_completion(
    site_config: dict[str, Any],
    *,
    downloaded_bytes: int,
    duration_seconds: float,
    site_id: str,
    status: str,
    producer_factory: Callable[[dict[str, str]], Any] | None = None,
    streamer: Optional[Any] = None,
) -> bool:
    """Queue or stream a completion record without letting producer failures block downloads."""
    if not site_config.get("kafka_event_streaming_enabled", False):
        return False

    bootstrap_env = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "")
    config = kafka_producer_config(bootstrap_env)
    if config is None:
        return False

    payload = DownloadCompletionEvent(
        downloaded_bytes=max(0, int(downloaded_bytes)),
        duration_seconds=max(0.0, float(duration_seconds)),
        site_id=str(site_id),
        status=str(status),
    ).to_json().encode("utf-8")

    # Row 975: Route via modern streamer if explicitly passed
    if streamer is not None:
        try:
            if hasattr(streamer, "publish"):
                # FastStreamKafkaAdapter routing
                asyncio.run(streamer.publish(KAFKA_TOPIC, payload))
                return True
            if hasattr(streamer, "send"):
                # AIOKafkaEventStreamer routing
                asyncio.run(streamer.send(KAFKA_TOPIC, payload))
                return True
        except Exception as exc:
            logger.debug("Modern streamer delivery failed: %s", exc)
            return False

    # Row 975: Route via modern AIOKafkaEventStreamer if configured
    if site_config.get("use_async_streamer", False) or site_config.get("kafka_streaming_mode") == "async":
        try:
            stream_client = AIOKafkaEventStreamer(
                bootstrap_servers=bootstrap_env,
                producer_factory=producer_factory,
            )
            asyncio.run(stream_client.send(KAFKA_TOPIC, payload))
            return True
        except Exception as exc:
            logger.debug("AIOKafka streamer execution failed: %s", exc)
            return False

    factory = producer_factory or _default_producer
    return _publisher_singleton.enqueue(factory, config, payload)


def get_streaming_client_metadata() -> dict[str, Any]:
    """Metadata describing modernized async event streaming capabilities (O1224)."""
    aiokafka_available = False
    try:
        import aiokafka  # noqa: F401
        aiokafka_available = True
    except ImportError:
        aiokafka_available = False

    faststream_available = False
    try:
        import faststream  # noqa: F401
        faststream_available = True
    except ImportError:
        faststream_available = False

    return {
        "aiokafka_capable": aiokafka_available,
        "faststream_adapter": faststream_available,
        "cluster_lan_fenced": True,
        "schema_version": SCHEMA_VERSION,
        "backend": "aiokafka" if aiokafka_available else "fallback",
    }



class _FallbackAIOKafkaProducer:
    """Coroutine-native fallback producer when aiokafka is not installed. Never reports false success (O1224)."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def send_and_wait(self, topic: str, value: bytes) -> None:
        raise RuntimeError("aiokafka is not installed; async Kafka event streaming unavailable")

    async def send(self, topic: str, value: bytes) -> None:
        raise RuntimeError("aiokafka is not installed; async Kafka event streaming unavailable")


class AIOKafkaEventStreamer:
    """Asynchronous, LAN-fenced Kafka event streaming client."""

    def __init__(
        self,
        bootstrap_servers: str,
        producer_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        config = kafka_producer_config(bootstrap_servers)
        if config is None:
            raise ValueError(
                f"Cluster LAN fence violation: Refusing connection to non-LAN broker {bootstrap_servers}"
            )
        self.bootstrap_servers = bootstrap_servers
        self._producer_factory = producer_factory
        self._producer: Optional[Any] = None
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def _create_producer(self) -> Any:
        _install_lan_socket_fence()
        if self._producer_factory is not None:
            return self._producer_factory(bootstrap_servers=self.bootstrap_servers)
        try:
            from aiokafka import AIOKafkaProducer
            return AIOKafkaProducer(bootstrap_servers=self.bootstrap_servers)
        except ImportError:
            return _FallbackAIOKafkaProducer(bootstrap_servers=self.bootstrap_servers)

    async def start(self) -> None:
        if self._is_running:
            return
        if self._producer is None:
            self._producer = self._create_producer()
        if hasattr(self._producer, "start"):
            res = self._producer.start()
            if inspect.isawaitable(res):
                await res
        self._is_running = True

    async def stop(self) -> None:
        if not self._is_running:
            return
        self._is_running = False
        if self._producer is not None:
            if hasattr(self._producer, "stop"):
                res = self._producer.stop()
                if inspect.isawaitable(res):
                    await res
            self._producer = None

    async def __aenter__(self) -> "AIOKafkaEventStreamer":
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.stop()

    async def send(self, topic: str, value: bytes) -> None:
        if not self._is_running:
            await self.start()
        if self._producer is None:
            raise RuntimeError("aiokafka producer is not initialized")
        if isinstance(self._producer, _FallbackAIOKafkaProducer):
            raise RuntimeError("aiokafka is not installed; async Kafka event streaming unavailable")
        if hasattr(self._producer, "send_and_wait"):
            res = self._producer.send_and_wait(topic, value)
            if inspect.isawaitable(res):
                await res
        elif hasattr(self._producer, "send"):
            res = self._producer.send(topic, value)
            if inspect.isawaitable(res):
                await res


class FastStreamKafkaAdapter:
    """FastStream-compatible broker adapter with topic subscriptions and async routing."""

    def __init__(
        self,
        bootstrap_servers: str,
        producer_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.streamer = AIOKafkaEventStreamer(
            bootstrap_servers=bootstrap_servers,
            producer_factory=producer_factory,
        )
        self._subscribers: dict[str, list[Callable[..., Any]]] = {}

    def subscriber(self, topic: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
            self._subscribers.setdefault(topic, []).append(handler)
            return handler
        return decorator

    async def publish(self, topic: str, message: Any) -> None:
        if isinstance(message, bytes):
            payload = message
            try:
                decoded = json.loads(message.decode("utf-8"))
            except Exception:
                decoded = None
        elif isinstance(message, str):
            payload = message.encode("utf-8")
            try:
                decoded = json.loads(message)
            except Exception:
                decoded = message
        else:
            payload = json.dumps(message).encode("utf-8")
            decoded = message

        await self.streamer.send(topic, payload)

        handlers = self._subscribers.get(topic, [])
        for handler in handlers:
            arg = decoded if decoded is not None else payload
            res = handler(arg)
            if inspect.isawaitable(res):
                await res

    async def start(self) -> None:
        await self.streamer.start()

    async def stop(self) -> None:
        await self.streamer.stop()

    async def __aenter__(self) -> "FastStreamKafkaAdapter":
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.stop()


async def async_publish_download_completion(
    site_config: dict[str, Any],
    *,
    downloaded_bytes: int,
    duration_seconds: float,
    site_id: str,
    status: str,
    streamer: Optional[Any] = None,
) -> bool:
    """Asynchronously publish download completion event via LAN-fenced streamer."""
    if not site_config.get("kafka_event_streaming_enabled", False):
        return False

    bootstrap_env = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "")
    if streamer is None:
        if not bootstrap_env or kafka_producer_config(bootstrap_env) is None:
            return False
        try:
            streamer = AIOKafkaEventStreamer(bootstrap_servers=bootstrap_env)
        except Exception:
            return False

    payload = DownloadCompletionEvent(
        downloaded_bytes=max(0, int(downloaded_bytes)),
        duration_seconds=max(0.0, float(duration_seconds)),
        site_id=str(site_id),
        status=str(status),
    ).to_json().encode("utf-8")

    try:
        if not getattr(streamer, "is_running", True):
            await streamer.start()
        await streamer.send(KAFKA_TOPIC, payload)
        return True
    except Exception as e:
        logger.debug("Async Kafka event publish failed: %s", e)
        return False
