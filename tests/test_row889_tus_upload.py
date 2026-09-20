"""TUS uploads resume exactly at the server-confirmed byte offset."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass

from bulk_downloader import tus_client
from bulk_downloader.tus_client import TusClient, TusResponse, UrllibTusTransport

BD_GATE_SCOPE = "module"


@dataclass
class _FakeTusServer:
    """Protocol-level fake; the upload client remains real."""

    remote: bytearray
    fail_after_commit: bool = True

    async def request(self, method, url, headers, body=b""):
        if method == "POST":
            assert headers["Tus-Resumable"] == "1.0.0"
            return TusResponse(201, {"Location": "/uploads/one"})
        if method == "HEAD":
            return TusResponse(200, {"Upload-Offset": str(len(self.remote))})
        assert method == "PATCH"
        assert int(headers["Upload-Offset"]) == len(self.remote)
        self.remote.extend(body)
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise OSError("connection lost after remote commit")
        return TusResponse(204, {"Upload-Offset": str(len(self.remote))})


def test_interrupted_upload_resumes_at_confirmed_offset_and_preserves_hash(tmp_path):
    """Removing recovery would duplicate bytes after an uncertain PATCH result."""
    source = tmp_path / "archive.bin"
    payload = b"archive-block-" * 600
    source.write_bytes(payload)
    server = _FakeTusServer(bytearray())
    delays = []

    async def cooperative_sleep(delay):
        delays.append(delay)
        await asyncio.sleep(0)

    result = asyncio.run(TusClient(
        "https://storage.example/uploads",
        transport=server,
        chunk_size=1024,
        retry_delay_s=0.25,
        sleep=cooperative_sleep,
    ).upload(source))

    assert result.offset == len(payload)
    assert result.retries == 1
    assert bytes(server.remote) == payload
    assert hashlib.sha256(server.remote).hexdigest() == hashlib.sha256(payload).hexdigest()
    assert delays == [0.25]


def test_retry_backoff_yields_to_event_loop(tmp_path):
    """Replacing async backoff with blocking sleep prevents other tasks progressing."""
    source = tmp_path / "single.bin"
    source.write_bytes(b"abc")
    server = _FakeTusServer(bytearray())
    yielded = asyncio.Event()

    async def cooperative_sleep(_delay):
        await asyncio.sleep(0)
        yielded.set()

    async def upload():
        result = await TusClient(
            "https://storage.example/uploads",
            transport=server,
            chunk_size=3,
            sleep=cooperative_sleep,
        ).upload(source)
        assert yielded.is_set()
        return result

    assert asyncio.run(upload()).retries == 1


def test_default_transport_returns_the_http_response(monkeypatch):
    """Removing the standard-library request must not masquerade as a response."""
    class Response:
        status = 204
        headers = {"Upload-Offset": "3"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    from bulk_downloader import hooks
    monkeypatch.setattr(hooks, "_hook_urlopen", lambda _request, timeout=None: Response())
    result = asyncio.run(UrllibTusTransport().request(
        "HEAD", "https://storage.example/uploads/one", {"Tus-Resumable": "1.0.0"},
    ))
    assert result == TusResponse(204, {"Upload-Offset": "3"})


# ---- fixer (O928): correctness REFUTE E1-E5 ----------------------------------

import os
import pytest
from bulk_downloader.tus_client import TusUploadError


def _client(server, **kw):
    delays = []

    async def cooperative_sleep(delay):
        delays.append(delay)
        await asyncio.sleep(0)
    kw.setdefault("chunk_size", 1024)
    kw.setdefault("retry_delay_s", 0.25)
    return TusClient("https://storage.example/uploads", transport=server, sleep=cooperative_sleep, **kw), delays


def test_e1_a_failing_recovery_head_is_retried_within_the_same_budget(tmp_path):
    """E1: after a committed-but-unacknowledged PATCH, the recovery HEAD
    failing (OSError, then 503) does not escape while retries remain; the
    upload resumes at the confirmed offset and the remote holds every byte."""
    source = tmp_path / "a.bin"
    payload = b"archive-block-" * 600
    source.write_bytes(payload)

    @dataclass
    class Server:
        remote: bytearray
        head_failures: list
        lost: bool = False

        async def request(self, method, url, headers, body=b""):
            if method == "POST":
                return TusResponse(201, {"Location": "/uploads/one"})
            if method == "HEAD":
                if self.lost and self.head_failures:
                    failure = self.head_failures.pop(0)
                    if isinstance(failure, Exception):
                        raise failure
                    return TusResponse(failure, {})
                return TusResponse(200, {"Upload-Offset": str(len(self.remote))})
            self.remote.extend(body)
            if len(self.remote) == 1024:
                self.lost = True
                raise OSError("connection lost after remote commit")
            return TusResponse(204, {"Upload-Offset": str(len(self.remote))})
    server = Server(bytearray(), [OSError("head lost"), 503])
    client, delays = _client(server, max_retries=3)
    result = asyncio.run(client.upload(source))
    assert bytes(server.remote) == payload and result.offset == len(payload)
    assert result.retries == 3 and delays == [0.25, 0.5, 1.0]
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    # negative control: one more HEAD failure than the budget allows is the bounded failure
    server = Server(bytearray(), [OSError(), OSError(), OSError()])
    client, delays = _client(server, max_retries=2)
    with pytest.raises(TusUploadError, match="retry limit exhausted"):
        asyncio.run(client.upload(source))
    assert bytes(server.remote) == payload[:1024]        # nothing duplicated, nothing invented


@pytest.mark.parametrize("location,expected", [
    ("one", "https://storage.example/one"),
    ("/one", "https://storage.example/one"),
    ("uploads/one", "https://storage.example/uploads/one"),
    ("https://cdn.example/x/one", "https://cdn.example/x/one"),
])
def test_e2_location_resolves_against_the_request_uri(tmp_path, location, expected):
    """E2: tus permits a relative Location; it resolves per RFC 3986 against
    the POST's URI (/uploads + "one" -> /one), not against /uploads/."""
    source = tmp_path / "s.bin"; source.write_bytes(b"x" * 10)
    seen = []

    @dataclass
    class Server:
        async def request(self, method, url, headers, body=b""):
            seen.append((method, url))
            if method == "POST":
                return TusResponse(201, {"Location": location})
            if method == "HEAD":
                return TusResponse(200, {"Upload-Offset": "0"})
            return TusResponse(204, {"Upload-Offset": str(len(body))})
    client, _ = _client(Server())
    result = asyncio.run(client.upload(source))
    assert result.resource_url == expected and seen[1:] == [("HEAD", expected), ("PATCH", expected)]


def test_e3_the_digest_is_of_the_uploaded_bytes_and_a_rewritten_source_is_refused(tmp_path):
    """E3: the result's sha256 is what the server holds; a source rewritten
    during the upload is refused explicitly instead of reported as success
    with the new file's digest."""
    source = tmp_path / "s.bin"
    payload = b"abcdef" * 400
    source.write_bytes(payload)
    server = _FakeTusServer(bytearray(), fail_after_commit=False)
    client, _ = _client(server)
    result = asyncio.run(client.upload(source))
    assert result.sha256 == hashlib.sha256(payload).hexdigest() == hashlib.sha256(server.remote).hexdigest()

    @dataclass
    class RewritingServer(_FakeTusServer):
        async def request(self, method, url, headers, body=b""):
            response = await super().request(method, url, headers, body)
            if method == "PATCH" and len(self.remote) >= len(payload):
                source.write_bytes(b"XYZXYZ" * 400)            # rewritten after the final PATCH
                os.utime(source, ns=(1, 1))
            return response
    server = RewritingServer(bytearray(), fail_after_commit=False)
    client, _ = _client(server)
    with pytest.raises(TusUploadError, match="changed during upload"):
        asyncio.run(client.upload(source))
    assert bytes(server.remote) == payload                     # the remote holds the original bytes

    # a resumed upload hashes the already-committed local prefix plus the rest
    source.write_bytes(payload)
    server = _FakeTusServer(bytearray(payload[:1500]), fail_after_commit=False)
    client, _ = _client(server)
    result = asyncio.run(client.upload(source, resource_url="https://storage.example/uploads/one"))
    assert result.offset == len(payload) and result.sha256 == hashlib.sha256(payload).hexdigest()


def test_e4_a_source_truncated_after_the_offset_check_fails_boundedly(tmp_path):
    """E4: a file that shrinks mid-upload cannot produce endless empty
    PATCHes at an unchanged offset: one bounded failure, no empty PATCH."""
    source = tmp_path / "s.bin"
    source.write_bytes(b"q" * 4096)
    patches = []

    @dataclass
    class Server:
        remote: bytearray

        async def request(self, method, url, headers, body=b""):
            if method == "POST":
                return TusResponse(201, {"Location": "/uploads/one"})
            if method == "HEAD":
                return TusResponse(200, {"Upload-Offset": str(len(self.remote))})
            patches.append(len(body))
            self.remote.extend(body)
            if len(self.remote) == 1024:
                source.write_bytes(b"q" * 1024)                # truncated after the first chunk landed
            return TusResponse(204, {"Upload-Offset": str(len(self.remote))})
    client, _ = _client(Server(bytearray()))
    with pytest.raises(TusUploadError, match="truncated"):
        asyncio.run(client.upload(source))
    assert patches == [1024]                                   # never an empty PATCH


def test_e5_a_forbidden_upload_location_is_refused_before_any_connection(tmp_path, monkeypatch):
    """E5: the default transport pins every hop; a server Location naming
    the metadata address (or a link-local/CGNAT host) is refused with no
    socket opened. Control: the same pinning admits a public literal."""
    import socket
    from bulk_downloader.urllib_ssrf import PinnedUrlRefused
    connections = []
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: connections.append(a) or (_ for _ in ()).throw(OSError("no network in test")))
    transport = UrllibTusTransport()
    for forbidden in ("http://169.254.169.254/upload", "http://[fe80::1]/upload", "http://100.64.0.1/upload"):
        with pytest.raises(Exception) as excinfo:
            asyncio.run(transport.request("HEAD", forbidden, {"Tus-Resumable": "1.0.0"}))
        assert isinstance(excinfo.value, (PinnedUrlRefused, ValueError, OSError)) and not isinstance(excinfo.value, TusResponse)
    assert connections == []
    # positive control: a public literal passes the pin and reaches the socket layer
    with pytest.raises(OSError, match="no network in test"):
        asyncio.run(transport.request("HEAD", "http://203.0.113.9/upload", {"Tus-Resumable": "1.0.0"}))
    assert len(connections) == 1
