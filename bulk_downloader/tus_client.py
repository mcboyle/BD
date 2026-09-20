"""Small asynchronous client for resumable tus 1.0.0 uploads.

The client deliberately owns only protocol transfer state.  Callers provide a
storage endpoint (or an existing upload URL); no application configuration or
remote-storage policy is coupled to this module.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Protocol
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen  # urlopen: kept importable for the transport test seam only


TRANSPORT_TIMEOUT_S = 30.0


TUS_VERSION = "1.0.0"


@dataclass(frozen=True)
class TusResponse:
    status: int
    headers: Mapping[str, str]


@dataclass(frozen=True)
class TusUploadResult:
    resource_url: str
    offset: int
    retries: int
    sha256: str


class TusUploadError(RuntimeError):
    """A tus server response cannot safely continue the upload."""


class TusTransport(Protocol):
    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes = b"",
    ) -> Awaitable[TusResponse]: ...


class UrllibTusTransport:
    """Standard-library transport which leaves the caller's event loop free."""

    async def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes = b"",
    ) -> TusResponse:
        return await asyncio.to_thread(self._request, method, url, headers, body)

    @staticmethod
    def _request(
        method: str, url: str, headers: Mapping[str, str], body: bytes,
    ) -> TusResponse:
        # Every hop -- the configured endpoint AND the server-supplied upload
        # Location -- goes through the pinned opener the hooks use: LAN
        # storage admitted, link-local/metadata/CGNAT/multicast refused, one
        # vetted address per hop (a Location naming 169.254.169.254 is
        # refused before any byte is sent).
        from .hooks import _hook_urlopen
        request = Request(url, data=body or None, headers=dict(headers), method=method)
        try:
            with _hook_urlopen(request, timeout=TRANSPORT_TIMEOUT_S) as response:
                return TusResponse(response.status, dict(response.headers.items()))
        except HTTPError as error:
            return TusResponse(error.code, dict(error.headers.items()))


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return ""


class TusClient:
    """Upload local files in resumable chunks through a tus-compatible server."""

    def __init__(
        self,
        endpoint: str,
        *,
        transport: TusTransport | None = None,
        chunk_size: int = 8 * 1024 * 1024,
        max_retries: int = 3,
        retry_delay_s: float = 0.25,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("endpoint must use http or https")
        if chunk_size <= 0 or max_retries < 0 or retry_delay_s < 0:
            raise ValueError("invalid upload retry configuration")
        self.endpoint = endpoint.rstrip("/")
        self.transport = transport or UrllibTusTransport()
        self.chunk_size = chunk_size
        self.max_retries = max_retries
        self.retry_delay_s = retry_delay_s
        self.sleep = sleep

    async def upload(self, source_path: str | Path, *, resource_url: str = "") -> TusUploadResult:
        source = Path(source_path)
        if not source.is_file():
            raise FileNotFoundError(source)
        stat = source.stat()
        size, identity = stat.st_size, (stat.st_size, stat.st_mtime_ns)
        resource_url = resource_url or await self._create(size)
        offset = await self._offset(resource_url)
        if offset < 0 or offset > size:
            raise TusUploadError(f"invalid server upload offset: {offset}")

        retries = 0
        digest = hashlib.sha256()          # of the bytes the SERVER holds: local prefix + chunks sent
        with source.open("rb") as fileobj:
            hashed = 0
            while hashed < offset:         # the already-committed prefix of a resumed upload
                block = fileobj.read(min(1024 * 1024, offset - hashed))
                if not block:
                    raise TusUploadError("source is shorter than the server's upload offset")
                digest.update(block)
                hashed += len(block)
            while offset < size:
                # re-measure the file every chunk: a file that shrank under us
                # would otherwise feed buffered stale bytes or empty PATCHes
                current = os.fstat(fileobj.fileno()).st_size
                if current < size:
                    raise TusUploadError(f"source truncated to {current} bytes during upload (expected {size})")
                fileobj.seek(offset)
                chunk = fileobj.read(self.chunk_size)
                if not chunk:
                    raise TusUploadError(f"source truncated to {offset} bytes during upload (expected {size})")
                try:
                    confirmed = await self._patch(resource_url, offset, chunk)
                except (OSError, asyncio.TimeoutError, _RetryableTusResponse):
                    # the PATCH may or may not have been committed: recover the
                    # server's offset, itself retried with the same bounded backoff
                    recovered = None
                    while recovered is None:
                        if retries >= self.max_retries:
                            raise TusUploadError("retry limit exhausted") from None
                        await self.sleep(self.retry_delay_s * (2 ** retries))
                        retries += 1
                        try:
                            recovered = await self._offset(resource_url)
                        except (OSError, asyncio.TimeoutError, _RetryableTusResponse):
                            recovered = None
                    if recovered < offset or recovered > offset + len(chunk):
                        raise TusUploadError(f"invalid server upload offset: {recovered}")
                    digest.update(chunk[: recovered - offset])
                    offset = recovered
                    continue
                digest.update(chunk)
                offset = confirmed
            # the source must be the file we hashed: a rewrite during the
            # upload makes the local file and the remote object different things
            after = source.stat()
            if (after.st_size, after.st_mtime_ns) != identity:
                raise TusUploadError("source file changed during upload; the uploaded bytes are not the current file")

        return TusUploadResult(resource_url, offset, retries, digest.hexdigest())

    async def _create(self, length: int) -> str:
        response = await self.transport.request("POST", self.endpoint, {
            "Tus-Resumable": TUS_VERSION,
            "Upload-Length": str(length),
        })
        if response.status in range(500, 600):
            raise TusUploadError(f"create failed with HTTP {response.status}")
        location = _header(response.headers, "Location")
        if response.status != 201 or not location:
            raise TusUploadError("create response lacks a tus upload location")
        # RFC 3986 resolution against the REQUEST URI (tus permits a relative
        # Location): POST /uploads + "one" -> /one, "/one" -> /one, absolute kept
        resolved = urljoin(self.endpoint, location)
        if urlsplit(resolved).scheme not in ("http", "https"):
            raise TusUploadError(f"upload location must be http(s): {resolved}")
        return resolved

    async def _offset(self, resource_url: str) -> int:
        response = await self.transport.request("HEAD", resource_url, {
            "Tus-Resumable": TUS_VERSION,
        })
        if response.status in range(500, 600):
            raise _RetryableTusResponse()
        raw_offset = _header(response.headers, "Upload-Offset")
        if response.status not in (200, 204) or not raw_offset.isdecimal():
            raise TusUploadError("offset response lacks a valid Upload-Offset")
        return int(raw_offset)

    async def _patch(self, resource_url: str, offset: int, chunk: bytes) -> int:
        response = await self.transport.request("PATCH", resource_url, {
            "Tus-Resumable": TUS_VERSION,
            "Upload-Offset": str(offset),
            "Content-Type": "application/offset+octet-stream",
        }, chunk)
        if response.status in range(500, 600):
            raise _RetryableTusResponse()
        raw_offset = _header(response.headers, "Upload-Offset")
        expected = offset + len(chunk)
        if response.status != 204 or not raw_offset.isdecimal() or int(raw_offset) != expected:
            raise TusUploadError("patch response did not confirm the expected offset")
        return expected


class _RetryableTusResponse(Exception):
    pass


__all__ = ["TusClient", "TusResponse", "TusUploadError", "TusUploadResult"]
