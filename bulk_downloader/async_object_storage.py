"""Row 1019: Asynchronous Object Storage & Cloud Archive Client Upgrade (aioboto3 / s3fs).

Provides asynchronous non-blocking object storage operations, multipart upload/download,
integrity verification, and seamless integration with bulk_downloader.storage_tier.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import io
import ipaddress
import json
import logging
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("bulk_downloader.async_object_storage")

MIN_S3_PART_SIZE = 5 * 1024 * 1024
DEFAULT_S3_PART_SIZE = 8 * 1024 * 1024
S3_POINTER_SUFFIX = ".s3_pointer.json"


@dataclass
class AsyncObjectStorageConfig:
    endpoint_url: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    region_name: Optional[str] = None
    bucket: Optional[str] = None
    part_size: int = DEFAULT_S3_PART_SIZE
    backend: str = "aioboto3"
    allow_private_hosts: bool = False


@dataclass
class CloudArchiveResult:
    ok: bool
    action: str = "archived_to_s3_async"
    bytes_moved: int = 0
    dest_path: str = ""
    etag: str = ""
    error: Optional[str] = None


@dataclass
class S3ObjectMetadata:
    content_length: int
    etag: str
    metadata: Dict[str, str] = field(default_factory=dict)


class S3FSAsyncAdapter:
    """Filesystem-style adapter for S3FS and object storage interfaces."""

    def __init__(self, s3fs_instance: Any = None) -> None:
        self.fs = s3fs_instance

    async def stat(self, path: str) -> Dict[str, Any]:
        if hasattr(self.fs, "info"):
            res = self.fs.info(path)
            if asyncio.iscoroutine(res):
                return await res
            return res
        return {"size": 0, "type": "file"}

    async def open_async(self, path: str, mode: str = "rb"):
        if hasattr(self.fs, "open_async"):
            return await self.fs.open_async(path, mode=mode)
        if hasattr(self.fs, "open"):
            return self.fs.open(path, mode=mode)
        raise NotImplementedError("Underlying S3FS instance lacks open implementation")


def _precheck_endpoint(endpoint_url: str, allow_private_hosts: bool) -> None:
    """Judge the configured endpoint once, at construction.

    A literal-IP endpoint never reaches aiohttp's resolver, so this is the only
    check it gets: public-only unless the operator admits private hosts, and
    never an address that cannot be a legitimate peer (cloud metadata).
    """
    import ipaddress
    from urllib.parse import urlparse
    from bulk_downloader.provider_resolve_impl._common import _is_safe_public_host, SSRFBlocked
    from bulk_downloader.ssrf_transport import GuardedTransportRefused, never_admissible

    host = urlparse(endpoint_url).hostname or ""
    if not allow_private_hosts:
        ok, reason = _is_safe_public_host(host)
        if not ok:
            raise SSRFBlocked(f"SSRF guard blocked S3 endpoint_url {endpoint_url!r}: {reason}")
        return
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        return
    refusal = never_admissible(literal)
    if refusal is not None:
        raise GuardedTransportRefused(
            f"SSRF guard blocked S3 endpoint_url {endpoint_url!r}: {refusal} address",
            host=host, reason=refusal)


@functools.cache
def _guarded_resolver_cls() -> type:
    from aiohttp.abc import AbstractResolver
    from bulk_downloader.provider_resolve_impl._common import _classify_ip
    from bulk_downloader.ssrf_transport import GuardedTransportRefused, never_admissible

    class GuardedS3Resolver(AbstractResolver):
        """aiohttp resolver that is the connect-time SSRF seam for the S3 client.

        aiohttp opens its socket to exactly the addresses a resolver returns, so
        judging every answer here pins the connection to what was judged: no
        second lookup happens between the check and connect.  Public-only by
        default (the ``_SSRFGuardedTransport`` predicate); with private hosts
        admitted, the PINNED policy (refuse only the never-admissible).
        """

        def __init__(self, allow_private_hosts: bool) -> None:
            self.allow_private_hosts = allow_private_hosts

        def _refusal(self, addr, host: str) -> Optional[str]:
            if self.allow_private_hosts:
                return never_admissible(addr)
            ok, reason = _classify_ip(addr, host)
            return None if ok else str(reason)

        async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
            loop = asyncio.get_running_loop()
            try:
                infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM, family=family)
            except (OSError, UnicodeError) as exc:
                raise GuardedTransportRefused(
                    f"SSRF guard (S3 resolver): resolution failed for {host!r}: {exc}",
                    host=host, reason="resolution-failed") from exc
            results = []
            for fam, _stype, proto, _canon, sockaddr in infos:
                if fam not in (socket.AF_INET, socket.AF_INET6):
                    continue
                addr = ipaddress.ip_address(sockaddr[0])
                # One bad sibling poisons the answer: a retry could land on it.
                refusal = self._refusal(addr, host)
                if refusal is not None:
                    raise GuardedTransportRefused(
                        f"SSRF guard (S3 resolver): {host!r} resolves to {addr}: {refusal}",
                        host=host, reason="refused")
                results.append({
                    "hostname": host, "host": sockaddr[0], "port": sockaddr[1],
                    "family": fam, "proto": proto,
                    "flags": socket.AI_NUMERICHOST | socket.AI_NUMERICSERV,
                })
            if not results:
                raise GuardedTransportRefused(
                    f"SSRF guard (S3 resolver): no usable address for {host!r}",
                    host=host, reason="no-address")
            return results

        async def close(self) -> None:
            return None

    return GuardedS3Resolver


def guarded_resolver(allow_private_hosts: bool = False):
    """The aiohttp resolver every aioboto3 client this module builds connects through."""
    return _guarded_resolver_cls()(allow_private_hosts)


class AsyncObjectStorageClient:
    """High-level asynchronous client for S3/MinIO and object storage tiers."""

    def __init__(self, config: Optional[AsyncObjectStorageConfig] = None, raw_client: Any = None) -> None:
        self.config = config or AsyncObjectStorageConfig()
        self._raw_client = raw_client

    @contextlib.asynccontextmanager
    async def open_client(self):
        """Yield a live S3 client for ONE operation.

        An injected raw_client is yielded as-is (its owner manages it). Otherwise
        aioboto3's Session.client() returns a client CONTEXT, not a client (N4-A E1):
        it is entered here and closed when the operation ends. Per operation, because
        storage_tier._run_async gives every upload its own event loop and an
        aiobotocore client is bound to the loop it was opened on.
        """
        if self._raw_client is not None:
            yield self._raw_client
            return
        if self.config.endpoint_url:
            _precheck_endpoint(self.config.endpoint_url, self.config.allow_private_hosts)

        # Lazy initialization for aioboto3
        try:
            import aioboto3
            from aiobotocore.config import AioConfig
        except ImportError:
            raise RuntimeError(
                "aioboto3 is not installed; install aioboto3 or pass an initialized raw_client"
            )
        session = aioboto3.Session()
        client_kwargs = {
            # F2: every hostname aiobotocore connects to is resolved, judged
            # and pinned by guarded_resolver; proxies={} keeps an ambient
            # HTTP(S)_PROXY from resolving the endpoint past it (row 439).
            "config": AioConfig(
                connector_args={"resolver": guarded_resolver(self.config.allow_private_hosts)},
                proxies={},
            ),
        }
        if self.config.endpoint_url:
            client_kwargs["endpoint_url"] = self.config.endpoint_url
        if self.config.access_key_id:
            client_kwargs["aws_access_key_id"] = self.config.access_key_id
        if self.config.secret_access_key:
            client_kwargs["aws_secret_access_key"] = self.config.secret_access_key
        if self.config.region_name:
            client_kwargs["region_name"] = self.config.region_name
        async with session.client("s3", **client_kwargs) as client:
            yield client

    async def stat_object(self, bucket: str, key: str) -> Optional[S3ObjectMetadata]:
        from .storage_tier import _is_s3_not_found

        async with self.open_client() as client:
            try:
                head = await client.head_object(Bucket=bucket, Key=key)
            except Exception as exc:
                if _is_s3_not_found(exc):
                    return None
                raise
        return S3ObjectMetadata(
            content_length=int(head.get("ContentLength", 0)),
            etag=str(head.get("ETag", "")).strip('"'),
            metadata=head.get("Metadata") or {},
        )

    async def delete_object(self, bucket: str, key: str) -> bool:
        async with self.open_client() as client:
            try:
                await client.delete_object(Bucket=bucket, Key=key)
                return True
            except Exception as exc:
                log.warning("delete_object failed for s3://%s/%s: %s", bucket, key, exc)
                return False

    async def upload_file(
        self,
        source_path: str,
        bucket: str,
        key: str,
        part_size: Optional[int] = None,
    ) -> CloudArchiveResult:
        from .storage_tier import archive_to_s3_async

        size = part_size or self.config.part_size
        async with self.open_client() as client:
            res = await archive_to_s3_async(
                source_path=source_path,
                bucket=bucket,
                key=key,
                client=client,
                part_size=size,
            )
        return CloudArchiveResult(
            ok=res.get("ok", False),
            action=res.get("action", "archived_to_s3_async"),
            bytes_moved=res.get("bytes_moved", 0),
            dest_path=res.get("dest_path", ""),
            etag=res.get("etag", ""),
            error=res.get("error"),
        )


def get_async_object_storage_client(cfg: Optional[Dict[str, Any]] = None) -> AsyncObjectStorageClient:
    """Factory creating an AsyncObjectStorageClient from configuration dictionary."""
    cfg = cfg or {}
    config = AsyncObjectStorageConfig(
        endpoint_url=cfg.get("storage_tier_s3_endpoint"),
        access_key_id=cfg.get("storage_tier_s3_access_key"),
        secret_access_key=cfg.get("storage_tier_s3_secret_key"),
        region_name=cfg.get("storage_tier_s3_region"),
        bucket=cfg.get("storage_tier_s3_bucket"),
        backend=cfg.get("storage_tier_s3_backend", "aioboto3"),
        allow_private_hosts=bool(cfg.get("storage_tier_allow_private_hosts", False)),
    )
    raw_client = cfg.get("storage_tier_s3_client")
    return AsyncObjectStorageClient(config=config, raw_client=raw_client)
