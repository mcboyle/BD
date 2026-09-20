"""bulk_downloader/event_gateway.py -- Redis Pub/Sub WebSocket Event Gateway (Row 873).

Pushes download progress, queue updates, and alert notifications to connected
clients in real-time with sub-10ms latency.
Supports:
- Redis Pub/Sub integration using native asyncio RESP protocol.
- RFC 6455 compliant WebSocket server and client.
- Automatic reconnection on network drop.
- High-throughput isolated broadcast to connected UI clients.
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
import struct
import time
from typing import Any

logger = logging.getLogger(__name__)

WS_MAGIC_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# In-memory queue change listeners for application integration (E1)
_QUEUE_LISTENERS: list[Callable[[str, dict[str, Any]], None]] = []


def register_queue_listener(callback: Callable[[str, dict[str, Any]], None]) -> None:
    """Register an in-process queue listener callback."""
    if callback not in _QUEUE_LISTENERS:
        _QUEUE_LISTENERS.append(callback)


def unregister_queue_listener(callback: Callable[[str, dict[str, Any]], None]) -> None:
    """Unregister an in-process queue listener callback."""
    if callback in _QUEUE_LISTENERS:
        _QUEUE_LISTENERS.remove(callback)


def notify_queue_event(event_type: str, data: dict[str, Any]) -> None:
    """Synchronous entry point called by application code when queue changes occur (E1)."""
    for listener in list(_QUEUE_LISTENERS):
        try:
            listener(event_type, data)
        except Exception as exc:
            logger.debug("Queue listener error: %s", exc)


def publish_queue_sync(event_type: str, data: dict[str, Any]) -> None:
    """Alias for notify_queue_event."""
    notify_queue_event(event_type, data)


@dataclass
class EventPayload:
    event: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        merged = {"event": self.event, "timestamp": self.timestamp, **self.data}
        return json.dumps(merged)

    @classmethod
    def from_json(cls, text: str) -> EventPayload:
        raw = json.loads(text)
        evt = raw.pop("event", "unknown")
        ts = raw.pop("timestamp", time.time())
        return cls(event=evt, data=raw, timestamp=ts)


def _encode_ws_frame(payload: bytes | str, opcode: int = 0x1, mask: bool = False) -> bytes:
    """Encode an RFC 6455 WebSocket frame."""
    payload_bytes = payload.encode("utf-8") if isinstance(payload, str) else payload

    length = len(payload_bytes)
    fin_and_opcode = 0x80 | (opcode & 0x0F)

    mask_bit = 0x80 if mask else 0x00
    if length <= 125:
        header = bytes([fin_and_opcode, mask_bit | length])
    elif length <= 65535:
        header = bytes([fin_and_opcode, mask_bit | 126]) + struct.pack("!H", length)
    else:
        header = bytes([fin_and_opcode, mask_bit | 127]) + struct.pack("!Q", length)

    if mask:
        mask_key = os.urandom(4)
        masked_payload = bytearray(length)
        for i in range(length):
            masked_payload[i] = payload_bytes[i] ^ mask_key[i % 4]
        return header + mask_key + bytes(masked_payload)
    return header + payload_bytes


async def _read_ws_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Read a single RFC 6455 frame from a stream reader."""
    head = await reader.readexactly(2)
    b1, b2 = head[0], head[1]

    opcode = b1 & 0x0F
    is_masked = bool(b2 & 0x80)
    length = b2 & 0x7F

    if length == 126:
        ext_len = await reader.readexactly(2)
        length = struct.unpack("!H", ext_len)[0]
    elif length == 127:
        ext_len = await reader.readexactly(8)
        length = struct.unpack("!Q", ext_len)[0]

    mask_key = b""
    if is_masked:
        mask_key = await reader.readexactly(4)

    payload_data = await reader.readexactly(length)

    if is_masked:
        unmasked = bytearray(length)
        for i in range(length):
            unmasked[i] = payload_data[i] ^ mask_key[i % 4]
        return opcode, bytes(unmasked)
    return opcode, payload_data


def _build_subscribe_command(channel: str) -> bytes:
    """Format RESP SUBSCRIBE command with exact UTF-8 byte length (Fixes E4)."""
    chan_bytes = channel.encode("utf-8")
    return (
        b"*2\r\n$9\r\nSUBSCRIBE\r\n$"
        + str(len(chan_bytes)).encode("ascii")
        + b"\r\n"
        + chan_bytes
        + b"\r\n"
    )


class RedisPubSubClient:
    """Async Redis Pub/Sub client speaking native RESP protocol."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        auto_reconnect: bool = True,
        reconnect_interval: float = 0.5,
    ):
        self.host = host
        self.port = port
        self.auto_reconnect = auto_reconnect
        self.reconnect_interval = reconnect_interval
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._subscriptions: dict[str, list[Callable[[str, str], Awaitable[None]]]] = {}
        self._connected = False
        self._closing = False
        self._read_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected and self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        self._closing = False
        try:
            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
            self._connected = True
            self._read_task = asyncio.create_task(self._read_loop())
        except OSError:
            self._connected = False
            if not self.auto_reconnect:
                raise
            if not self._reconnect_task or self._reconnect_task.done():
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def subscribe(self, channel: str, callback: Callable[[str, str], Awaitable[None]]) -> None:
        self._subscriptions.setdefault(channel, []).append(callback)
        if self.is_connected and self._writer:
            cmd = _build_subscribe_command(channel)
            self._writer.write(cmd)
            await self._writer.drain()

    async def _read_resp(self) -> Any:
        if not self._reader:
            return None
        line = await self._reader.readline()
        if not line:
            raise ConnectionResetError("Redis connection closed")
        prefix = line[:1]
        content = line[1:-2]

        if prefix == b"+":
            return content.decode("utf-8")
        if prefix == b"-":
            return RuntimeError(content.decode("utf-8"))
        if prefix == b":":
            return int(content)
        if prefix == b"$":
            length = int(content)
            if length == -1:
                return None
            data = await self._reader.readexactly(length + 2)
            return data[:-2]
        if prefix == b"*":
            num_elements = int(content)
            if num_elements == -1:
                return None
            elements = []
            for _ in range(num_elements):
                elements.append(await self._read_resp())
            return elements
        return None

    async def _read_loop(self) -> None:
        while self.is_connected and not self._closing:
            try:
                resp = await self._read_resp()
                if isinstance(resp, list) and len(resp) >= 3:
                    msg_type = resp[0]
                    if isinstance(msg_type, bytes):
                        msg_type = msg_type.decode("utf-8")
                    if msg_type == "message":
                        ch = resp[1].decode("utf-8") if isinstance(resp[1], bytes) else str(resp[1])
                        data = resp[2].decode("utf-8") if isinstance(resp[2], bytes) else str(resp[2])
                        callbacks = self._subscriptions.get(ch, [])
                        for cb in callbacks:
                            try:
                                await cb(ch, data)
                            except Exception as exc:
                                logger.exception("Error in redis pubsub callback: %s", exc)
            except (ConnectionResetError, asyncio.IncompleteReadError, OSError):
                self._connected = False
                if not self._closing and self.auto_reconnect:
                    if not self._reconnect_task or self._reconnect_task.done():
                        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
                break

    async def _reconnect_loop(self) -> None:
        while not self._closing and not self.is_connected:
            await asyncio.sleep(self.reconnect_interval)
            try:
                self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
                self._connected = True
                # Re-subscribe all channels using exact UTF-8 byte lengths (Fixes E4)
                for channel in list(self._subscriptions.keys()):
                    cmd = _build_subscribe_command(channel)
                    self._writer.write(cmd)
                    await self._writer.drain()
                self._read_task = asyncio.create_task(self._read_loop())
                break
            except OSError:
                continue

    async def close(self) -> None:
        self._closing = True
        self._connected = False
        if self._read_task:
            self._read_task.cancel()
        if self._reconnect_task:
            self._reconnect_task.cancel()
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass


class EventPublisher:
    """Async Redis event publisher."""

    def __init__(self, host: str = "127.0.0.1", port: int = 6379):
        self.host = host
        self.port = port
        self._reader: Any = None
        self._writer: Any = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)

    async def publish(self, channel: str, message: dict[str, Any] | str) -> int:
        """Publish message to Redis channel using RESP. Raises on Redis error/EOF (Fixes E2)."""
        payload_str = json.dumps(message) if isinstance(message, dict) else str(message)
        chan_bytes = channel.encode("utf-8")
        payload_bytes = payload_str.encode("utf-8")

        cmd = (
            b"*3\r\n$7\r\nPUBLISH\r\n$"
            + str(len(chan_bytes)).encode("ascii")
            + b"\r\n"
            + chan_bytes
            + b"\r\n$"
            + str(len(payload_bytes)).encode("ascii")
            + b"\r\n"
            + payload_bytes
            + b"\r\n"
        )

        async with self._lock:
            if not self._writer or self._writer.is_closing():
                await self.connect()

            assert self._writer is not None
            assert self._reader is not None

            self._writer.write(cmd)
            await self._writer.drain()

            line = await self._reader.readline()
            if not line:
                raise ConnectionResetError("Redis server closed connection (EOF)")
            if line.startswith(b"-"):
                raise RuntimeError(f"Redis error: {line.decode('utf-8', errors='replace').strip()}")
            if line.startswith(b":"):
                return int(line[1:-2])
            return 1

    async def close(self) -> None:
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass


@dataclass
class WebSocketConnection:
    reader: Any
    writer: Any
    path: str
    is_open: bool = True

    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other


class EventGateway:
    """Redis Pub/Sub WebSocket event gateway."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8730,
        redis_host: str = "127.0.0.1",
        redis_port: int = 6379,
    ):
        self.host = host
        self.port = port
        self.redis_host = redis_host
        self.redis_port = redis_port
        self._server: asyncio.Server | None = None
        self._clients: set[WebSocketConnection] = set()
        self._redis_sub: RedisPubSubClient | None = None
        self._redis_pub: EventPublisher | None = None
        self._running = False

    @property
    def bound_port(self) -> int:
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return self.port

    async def start(self) -> None:
        self._running = True
        self._server = await asyncio.start_server(self._handle_http, self.host, self.port)

        try:
            self._redis_sub = RedisPubSubClient(
                host=self.redis_host, port=self.redis_port, auto_reconnect=True
            )
            await self._redis_sub.connect()
            for ch in ["bd:events:queue", "bd:events:progress", "bd:events:alerts"]:
                await self._redis_sub.subscribe(ch, self._on_redis_event)
        except OSError as exc:
            logger.debug("Redis pubsub listener unavailable: %s", exc)

        try:
            self._redis_pub = EventPublisher(host=self.redis_host, port=self.redis_port)
            await self._redis_pub.connect()
        except OSError as exc:
            logger.debug("Redis publisher unavailable: %s", exc)

    async def _on_redis_event(self, channel: str, message: str) -> None:
        await self.broadcast(message)

    async def _handle_http(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_line = await reader.readline()
        if not request_line:
            writer.close()
            return

        parts = request_line.decode().split()
        if len(parts) < 2 or parts[0] != "GET":
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        path = parts[1]
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if not line or line == b"\r\n":
                break
            key, _, val = line.decode().partition(":")
            headers[key.strip().lower()] = val.strip()

        if (
            headers.get("upgrade", "").lower() != "websocket"
            or "sec-websocket-key" not in headers
        ):
            writer.write(b"HTTP/1.1 426 Upgrade Required\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        key = headers["sec-websocket-key"]
        accept = base64.b64encode(
            hashlib.sha1((key + WS_MAGIC_GUID).encode()).digest()
        ).decode()

        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        )
        writer.write(response.encode())
        await writer.drain()

        conn = WebSocketConnection(reader=reader, writer=writer, path=path)
        self._clients.add(conn)

        try:
            while conn.is_open and self._running:
                opcode, payload = await _read_ws_frame(reader)
                if opcode == 0x8:  # Close frame
                    break
                elif opcode == 0x9:  # Ping
                    pong = _encode_ws_frame(payload, opcode=0xA, mask=False)
                    writer.write(pong)
                    await writer.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            conn.is_open = False
            self._clients.discard(conn)
            writer.close()

    async def broadcast(self, message: str | dict[str, Any]) -> None:
        """Broadcast message to all connected clients. Isolated slow-client handling (Fixes E3)."""
        text = json.dumps(message) if isinstance(message, dict) else message
        frame = _encode_ws_frame(text, opcode=0x1, mask=False)

        async def _deliver(client: WebSocketConnection) -> bool:
            if not client.is_open:
                return False
            try:
                client.writer.write(frame)
                # Short timeout prevents stalled client writer from blocking broadcast (Fixes E3)
                await asyncio.wait_for(client.writer.drain(), timeout=0.08)
                return True
            except Exception:
                client.is_open = False
                try:
                    client.writer.close()
                except Exception:
                    pass
                return False

        if not self._clients:
            return

        await asyncio.gather(*[_deliver(c) for c in list(self._clients)], return_exceptions=True)
        self._clients = {c for c in self._clients if c.is_open}

    async def publish(self, channel: str, event_data: dict[str, Any] | str) -> None:
        text = json.dumps(event_data) if isinstance(event_data, dict) else event_data
        await self.broadcast(text)
        if self._redis_pub:
            try:
                await self._redis_pub.publish(channel, text)
            except Exception as exc:
                logger.debug("Redis publish forward failed: %s", exc)

    async def close(self) -> None:
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._redis_sub:
            await self._redis_sub.close()
        if self._redis_pub:
            await self._redis_pub.close()
        for client in list(self._clients):
            try:
                client.writer.close()
            except Exception:
                pass
        self._clients.clear()


class WebSocketClient:
    """Async WebSocket client for connecting to EventGateway."""

    def __init__(self, uri: str):
        self.uri = uri
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.is_connected = False

    async def connect(self) -> None:
        parts = self.uri.replace("ws://", "").split(":")
        host = parts[0]
        port = int(parts[1].split("/")[0])
        path = "/" + parts[1].split("/", 1)[1] if "/" in parts[1] else "/"

        self._reader, self._writer = await asyncio.open_connection(host, port)
        key = base64.b64encode(os.urandom(16)).decode()

        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._writer.write(req.encode())
        await self._writer.drain()

        # Read handshake response
        resp = await self._reader.readline()
        if not resp.startswith(b"HTTP/1.1 101"):
            raise ConnectionError(f"WebSocket handshake failed: {resp.decode()}")

        while True:
            line = await self._reader.readline()
            if not line or line == b"\r\n":
                break

        self.is_connected = True

    async def recv(self, timeout: float = 5.0) -> str:
        assert self._reader is not None
        _opcode, payload = await asyncio.wait_for(_read_ws_frame(self._reader), timeout=timeout)
        return payload.decode("utf-8")

    async def close(self) -> None:
        self.is_connected = False
        if self._writer:
            close_frame = _encode_ws_frame(b"", opcode=0x8, mask=True)
            try:
                self._writer.write(close_frame)
                await self._writer.drain()
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
