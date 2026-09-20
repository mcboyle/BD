"""Distributed work stealing across per-site queues via Redis (Row 870).

Implements dynamic work stealing for fleet workers:
- Atomic queue pop via RPOPLPUSH / LMOVE.
- Timed lease ownership (SET NX EX) to prevent duplicate processing.
- Fail-soft return of abandoned and expired jobs.
- Hash-tagged keys ({site}) ensuring zero CROSSSLOT errors in Redis Cluster.
- Self-describing job entries guaranteeing multi-site queue recovery accuracy.
"""
from __future__ import annotations

import json
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Any, List, Optional, cast

DEFAULT_LEASE_SECONDS = 60


def queue_key(site: str) -> str:
    """Queue key for a site, hash-tagged with {site} for Redis Cluster."""
    return f"work_stealing:{{{site}}}:queue"


def processing_key(site: str, worker_id: str) -> str:
    """Processing key for a worker on a site, hash-tagged with {site}."""
    return f"work_stealing:{{{site}}}:proc:{worker_id}"


def lease_key(site: str, job_id: str) -> str:
    """Lease key for a job, hash-tagged with {site}."""
    return f"work_stealing:{{{site}}}:lease:{job_id}"


class RedisProtocolError(RuntimeError):
    """The Redis server returned an error reply, or the reply did not parse."""


class RedisConnectionError(RuntimeError):
    """The connection to Redis could not be established or was lost."""


class RedisClient:
    """Minimal RESP (REdis Serialization Protocol) client over standard socket."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        timeout: float = 5.0,
        sock: Optional[socket.socket] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._sock: Optional[socket.socket] = sock
        self._buf = b""

    def _ensure_connected(self) -> socket.socket:
        if self._sock is not None:
            return self._sock
        try:
            sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        except OSError as exc:
            raise RedisConnectionError(
                f"cannot connect to Redis at {self._host}:{self._port} "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        self._sock = sock
        return sock

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self._buf = b""

    def __enter__(self) -> RedisClient:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _recv_line(self, sock: socket.socket) -> bytes:
        while b"\r\n" not in self._buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise RedisConnectionError("Redis connection closed while reading a reply")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\r\n")
        return line

    def _recv_exact(self, sock: socket.socket, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = sock.recv(4096)
            if not chunk:
                raise RedisConnectionError("Redis connection closed while reading a reply body")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def _read_reply(self, sock: socket.socket) -> Any:
        line = self._recv_line(sock)
        if not line:
            raise RedisProtocolError("empty reply line from Redis")
        kind, payload = line[:1], line[1:]
        if kind == b"+":
            return payload
        if kind == b"-":
            raise RedisProtocolError(payload.decode("utf-8", "replace"))
        if kind == b":":
            return int(payload)
        if kind == b"$":
            length = int(payload)
            if length == -1:
                return None
            data = self._recv_exact(sock, length + 2)
            return data[:length]
        if kind == b"*":
            count = int(payload)
            if count == -1:
                return None
            return [self._read_reply(sock) for _ in range(count)]
        raise RedisProtocolError(f"unrecognised RESP reply type {kind!r} in line {line!r}")

    def command(self, *args: Any) -> Any:
        sock = self._ensure_connected()
        encoded = [a if isinstance(a, bytes) else str(a).encode("utf-8") for a in args]
        parts = [f"*{len(encoded)}\r\n".encode("ascii")]
        for part in encoded:
            parts.append(f"${len(part)}\r\n".encode("ascii"))
            parts.append(part)
            parts.append(b"\r\n")
        try:
            sock.sendall(b"".join(parts))
            return self._read_reply(sock)
        except RedisProtocolError:
            raise
        except OSError as exc:
            self.close()
            raise RedisConnectionError(
                f"Redis command {args[0]!r} failed ({type(exc).__name__}: {exc})"
            ) from exc

    def rpoplpush(self, source: str, destination: str) -> Optional[bytes]:
        return cast(Optional[bytes], self.command("RPOPLPUSH", source, destination))

    def lpush(self, key: str, *values: Any) -> int:
        return cast(int, self.command("LPUSH", key, *values))

    def rpush(self, key: str, *values: Any) -> int:
        return cast(int, self.command("RPUSH", key, *values))

    def lrem(self, key: str, count: int, value: Any) -> int:
        return cast(int, self.command("LREM", key, count, value))

    def lrange(self, key: str, start: int, stop: int) -> List[bytes]:
        return cast(List[bytes], self.command("LRANGE", key, start, stop) or [])

    def llen(self, key: str) -> int:
        return cast(int, self.command("LLEN", key))

    def set_nx_ex(self, key: str, value: Any, ex_seconds: int | float) -> bool:
        reply = self.command("SET", key, value, "NX", "EX", int(ex_seconds))
        return reply is not None

    def get(self, key: str) -> Optional[bytes]:
        return cast(Optional[bytes], self.command("GET", key))

    def ttl(self, key: str) -> int:
        return cast(int, self.command("TTL", key))

    def delete(self, *keys: str) -> int:
        return cast(int, self.command("DEL", *keys))


@dataclass(frozen=True)
class StolenJob:
    """A self-describing job claimed from a site queue."""
    job_id: str
    site: str
    source_queue: str
    worker_id: str
    payload: str = ""
    raw_bytes: bytes = b""


class WorkStealingCoordinator:
    """Coordinates atomic work stealing and lease management across sites."""

    def __init__(
        self,
        redis: RedisClient,
        worker_id: Optional[str] = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self.redis = redis
        self.worker_id = worker_id or uuid.uuid4().hex
        self.lease_seconds = lease_seconds

    def push_job(self, site: str, job_id: str, payload: str = "") -> int:
        """Enqueue a job on the site's queue as self-describing payload."""
        data = {
            "id": job_id,
            "site": site,
            "payload": payload,
            "ts": time.time(),
        }
        encoded = json.dumps(data).encode("utf-8")
        return self.redis.rpush(queue_key(site), encoded)

    def queue_length(self, site: str) -> int:
        """Get the current depth of a site's pending queue."""
        return self.redis.llen(queue_key(site))

    def steal(self, site: str) -> Optional[StolenJob]:
        """Atomically steal a job from site's queue into this worker's processing queue."""
        src = queue_key(site)
        dst = processing_key(site, self.worker_id)
        raw = self.redis.rpoplpush(src, dst)
        if raw is None:
            return None

        # Parse self-describing payload
        job_id = ""
        payload = ""
        try:
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, dict):
                job_id = str(parsed.get("id", ""))
                payload = str(parsed.get("payload", ""))
        except Exception:
            job_id = raw.decode("utf-8", "replace")
            payload = job_id

        if not job_id:
            job_id = uuid.uuid4().hex

        # Record timed lease
        self.redis.set_nx_ex(lease_key(site, job_id), self.worker_id, self.lease_seconds)

        return StolenJob(
            job_id=job_id,
            site=site,
            source_queue=src,
            worker_id=self.worker_id,
            payload=payload,
            raw_bytes=raw,
        )

    def complete(self, job: StolenJob) -> None:
        """Acknowledge completion: remove from processing queue and drop lease."""
        dst = processing_key(job.site, job.worker_id)
        self.redis.lrem(dst, 1, job.raw_bytes)
        self.redis.delete(lease_key(job.site, job.job_id))

    def abandon(self, job: StolenJob) -> None:
        """Fail-soft return: remove from processing list, delete lease, and LPUSH to source."""
        dst = processing_key(job.site, job.worker_id)
        self.redis.lrem(dst, 1, job.raw_bytes)
        self.redis.delete(lease_key(job.site, job.job_id))
        self.redis.lpush(job.source_queue, job.raw_bytes)

    def reap_expired(self, site: str, worker_id: Optional[str] = None) -> List[StolenJob]:
        """Recover abandoned jobs from a worker's processing queue whose lease expired."""
        target_worker = worker_id or self.worker_id
        dst = processing_key(site, target_worker)
        entries = self.redis.lrange(dst, 0, -1)
        recovered: List[StolenJob] = []

        for raw in entries:
            job_id = ""
            payload = ""
            origin_site = site
            try:
                parsed = json.loads(raw.decode("utf-8"))
                if isinstance(parsed, dict):
                    job_id = str(parsed.get("id", ""))
                    origin_site = str(parsed.get("site", site))
                    payload = str(parsed.get("payload", ""))
            except Exception:
                job_id = raw.decode("utf-8", "replace")
                payload = job_id

            if not job_id:
                continue

            # Check lease expiration
            rem = self.redis.ttl(lease_key(origin_site, job_id))
            if rem > 0:
                continue  # Still actively held by a live worker

            # Reclaim expired job
            self.redis.lrem(dst, 1, raw)
            self.redis.delete(lease_key(origin_site, job_id))
            self.redis.rpush(queue_key(origin_site), raw)

            recovered.append(
                StolenJob(
                    job_id=job_id,
                    site=origin_site,
                    source_queue=queue_key(origin_site),
                    worker_id=target_worker,
                    payload=payload,
                    raw_bytes=raw,
                )
            )

        return recovered
