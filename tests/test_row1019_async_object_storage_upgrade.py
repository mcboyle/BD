"""Row 1019: Asynchronous Object Storage & Cloud Archive Client Upgrade (aioboto3 / s3fs).

Validates non-blocking async object storage operations, multipart upload/download,
checksum verification, atomic pointer generation, fail-closed immutability,
SSRF transport safety, and concrete product integration with bulk_downloader.storage_tier.

RED on baseline: fails with explicit behavioral AssertionError, not an unhandled ImportError.
Includes positive control test passing on baseline to prove the probe can say YES.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
import pytest

from bulk_downloader import storage_tier

BD_GATE_SCOPE = "module"


def test_positive_control_storage_tier_baseline():
    """Positive control (Rule 7): proves test runner and probe can say YES on baseline capabilities."""
    assert hasattr(storage_tier, "archive_to_s3")
    assert callable(storage_tier.archive_to_s3)
    assert hasattr(storage_tier, "S3_POINTER_SUFFIX")
    assert storage_tier.S3_POINTER_SUFFIX == ".s3_pointer.json"


class MockSyncS3Client:
    """Mock sync S3 client for storage_tier.archive_to_s3 tests."""

    def __init__(self, existing: dict = None, head_error: Exception = None):
        self.objects = dict(existing or {})  # key -> {"data": bytes, "metadata": dict}
        self.head_error = head_error
        self.uploads = 0
        self.parts = []
        self.completed = False

    def head_object(self, Bucket: str, Key: str):
        if self.head_error is not None:
            raise self.head_error
        obj = self.objects.get(Key)
        if obj is None:
            raise RuntimeError(f"NoSuchKey: s3://{Bucket}/{Key}")
        return {
            "ContentLength": len(obj["data"]),
            "ETag": f'"{hashlib.md5(obj["data"]).hexdigest()}"',
            "Metadata": obj.get("metadata", {}),
        }

    def create_multipart_upload(self, Bucket: str, Key: str, Metadata: dict = None):
        self.uploads += 1
        self.parts = []
        return {"UploadId": "sync-upload-1"}

    def upload_part(self, Bucket: str, Key: str, UploadId: str, PartNumber: int, Body=None):
        payload = Body.read() if hasattr(Body, "read") else Body
        self.parts.append(payload)
        return {"ETag": f'"{hashlib.md5(payload).hexdigest()}"'}

    def complete_multipart_upload(self, Bucket: str, Key: str, UploadId: str, MultipartUpload: dict):
        self.completed = True
        combined = b"".join(self.parts)
        self.objects[Key] = {"data": combined, "metadata": {}}
        return {"ETag": f'"{hashlib.md5(combined).hexdigest()}-1"'}

    def abort_multipart_upload(self, Bucket: str, Key: str, UploadId: str):
        self.parts = []
        return {}


def test_sync_archive_head_500_refuses_fail_closed_and_preserves_files():
    """Verify E4/M2 fix: sync archive_to_s3 must fail closed on 500 error and never overwrite.

    Kills mutant M2 (which reverts head-error handling back to existing=None and succeeds).
    Behavioral RED on baseline: baseline catches Exception, sets existing=None, uploads,
    and returns ok=True, failing this test with AssertionError.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "sync_500_test.bin")
        test_payload = b"S" * (6 * 1024 * 1024)
        with open(src_path, "wb") as f:
            f.write(test_payload)

        mock_s3 = MockSyncS3Client(head_error=RuntimeError("500 Internal Server Error: backend degraded"))
        res = storage_tier.archive_to_s3(src_path, "my-bucket", "files/sync_500.bin", mock_s3)

        assert res["ok"] is False, "archive_to_s3 must return ok=False on 500 head error"
        assert "unverifiable" in res["error"].lower() or "refusing to overwrite" in res["error"].lower()
        assert mock_s3.uploads == 0, "Must not initiate multipart upload when existence is unverifiable"
        assert os.path.exists(src_path), "Local source file must NOT be deleted when existence check fails"
        pointer_path = f"{src_path}{storage_tier.S3_POINTER_SUFFIX}"
        assert not os.path.exists(pointer_path), "Pointer file must NOT be created when archive fails"


def test_sync_archive_head_403_refuses_fail_closed():
    """Verify E4/M2 fix: sync archive_to_s3 must fail closed on 403 AccessDenied."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "sync_403_test.bin")
        with open(src_path, "wb") as f:
            f.write(b"A" * (6 * 1024 * 1024))

        mock_s3 = MockSyncS3Client(head_error=RuntimeError("403 AccessDenied"))
        res = storage_tier.archive_to_s3(src_path, "my-bucket", "files/sync_403.bin", mock_s3)

        assert res["ok"] is False, "archive_to_s3 must return ok=False on 403 head error"
        assert mock_s3.uploads == 0
        assert os.path.exists(src_path)


def test_property_sync_transient_500_remote_content_preserved():
    """Verify Property P1 on sync path: transient 500 preserves existing remote content.

    Kills mutant M1 by property (asserting stored bytes), not message string.
    On mutant M1, fail-open causes existing remote bytes to be overwritten by local file.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "local_new.bin")
        local_content = b"NEW_LOCAL_PAYLOAD" + (b"0" * (6 * 1024 * 1024))
        with open(src_path, "wb") as f:
            f.write(local_content)

        remote_preserved_content = b"CRITICAL_EXISTING_REMOTE_CONTENT"
        existing_store = {
            "important/target.bin": {
                "data": remote_preserved_content,
                "metadata": {"sha256": hashlib.sha256(remote_preserved_content).hexdigest()},
            }
        }
        mock_s3 = MockSyncS3Client(existing=existing_store, head_error=RuntimeError("500 Internal Server Error"))

        res = storage_tier.archive_to_s3(src_path, "my-bucket", "important/target.bin", mock_s3)

        assert res["ok"] is False
        # Property: remote data must remain intact, never overwritten
        assert mock_s3.objects["important/target.bin"]["data"] == remote_preserved_content
        # Property: local file must remain intact
        assert os.path.exists(src_path)


class MockAsyncS3Client:
    """In-memory mock async S3 client adhering to aioboto3 / botocore async interfaces."""

    def __init__(self, existing: dict = None):
        self.store = dict(existing or {})
        self.multipart = {}
        self.aborted = []

    async def head_object(self, Bucket: str, Key: str):
        item = self.store.get((Bucket, Key))
        if item is None:
            raise RuntimeError(f"NoSuchKey: s3://{Bucket}/{Key}")
        return {
            "ContentLength": len(item["bytes"]),
            "ETag": item["etag"],
            "Metadata": item.get("metadata", {}),
        }

    async def create_multipart_upload(self, Bucket: str, Key: str, Metadata: dict = None):
        upload_id = f"up_{len(self.multipart) + 1}"
        self.multipart[upload_id] = {
            "bucket": Bucket,
            "key": Key,
            "metadata": Metadata or {},
            "parts": {},
        }
        return {"UploadId": upload_id}

    async def upload_part(self, Bucket: str, Key: str, UploadId: str, PartNumber: int, Body=None):
        raw = Body.read() if hasattr(Body, "read") else Body
        part_md5 = hashlib.md5(raw).hexdigest()
        self.multipart[UploadId]["parts"][PartNumber] = raw
        return {"ETag": f'"{part_md5}"'}

    async def complete_multipart_upload(self, Bucket: str, Key: str, UploadId: str, MultipartUpload: dict):
        info = self.multipart[UploadId]
        parts_data = []
        md5_digests = []
        for p in MultipartUpload["Parts"]:
            pnum = p["PartNumber"]
            raw = info["parts"][pnum]
            parts_data.append(raw)
            md5_digests.append(hashlib.md5(raw).digest())

        combined_bytes = b"".join(parts_data)
        etag = f"{hashlib.md5(b''.join(md5_digests)).hexdigest()}-{len(md5_digests)}"
        self.store[(Bucket, Key)] = {
            "bytes": combined_bytes,
            "etag": f'"{etag}"',
            "metadata": info["metadata"],
        }
        return {"ETag": f'"{etag}"', "Bucket": Bucket, "Key": Key}

    async def abort_multipart_upload(self, Bucket: str, Key: str, UploadId: str):
        self.aborted.append(UploadId)
        self.multipart.pop(UploadId, None)
        return {}

    async def get_object(self, Bucket: str, Key: str):
        item = self.store.get((Bucket, Key))
        if item is None:
            raise RuntimeError("NoSuchKey")
        import io
        return {"Body": io.BytesIO(item["bytes"])}


@pytest.mark.anyio
async def test_property_async_transient_500_remote_content_preserved():
    """Verify Property P1 on async path: transient 500 preserves existing remote content.

    Kills mutant M1 by property (asserting stored bytes), not message string.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "async_local.bin")
        local_data = b"ASYNC_NEW_LOCAL_PAYLOAD" + (b"1" * (6 * 1024 * 1024))
        with open(src_path, "wb") as f:
            f.write(local_data)

        remote_preserved_data = b"CRITICAL_ASYNC_EXISTING_CONTENT"
        mock_s3 = MockAsyncS3Client(existing={
            ("my-bucket", "archived/item.bin"): {
                "bytes": remote_preserved_data,
                "etag": '"remote-etag"',
                "metadata": {"sha256": hashlib.sha256(remote_preserved_data).hexdigest()},
            }
        })

        async def head_500(Bucket: str, Key: str):
            raise RuntimeError("500 Internal Server Error")

        mock_s3.head_object = head_500

        res = await storage_tier.archive_to_s3_async(
            source_path=src_path,
            bucket="my-bucket",
            key="archived/item.bin",
            client=mock_s3,
            part_size=5 * 1024 * 1024,
        )

        assert res["ok"] is False
        # Property: remote data must remain intact, never overwritten
        assert mock_s3.store[("my-bucket", "archived/item.bin")]["bytes"] == remote_preserved_data
        # Property: local file must remain intact
        assert os.path.exists(src_path)


@pytest.mark.anyio
async def test_async_archive_to_s3_success():
    """Verify non-blocking async archive to S3 creates pointer and removes local file atomically."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "video_clip.mp4")
        test_data = b"X" * (6 * 1024 * 1024)  # 6 MiB to meet MIN_S3_PART_SIZE
        with open(src_path, "wb") as f:
            f.write(test_data)

        mock_s3 = MockAsyncS3Client()
        res = await storage_tier.archive_to_s3_async(
            source_path=src_path,
            bucket="my-archive",
            key="media/video_clip.mp4",
            client=mock_s3,
            part_size=5 * 1024 * 1024,
        )

        assert res["ok"] is True
        assert res["action"] == "archived_to_s3_async"
        assert res["bytes_moved"] == len(test_data)

        # Local source file unlinked
        assert not os.path.exists(src_path)
        # Pointer file written
        pointer_path = f"{src_path}{storage_tier.S3_POINTER_SUFFIX}"
        assert os.path.exists(pointer_path)

        with open(pointer_path, "r", encoding="utf-8") as f:
            pointer = json.load(f)
        assert pointer["bucket"] == "my-archive"
        assert pointer["key"] == "media/video_clip.mp4"
        assert pointer["sha256"] == hashlib.sha256(test_data).hexdigest()


@pytest.mark.anyio
async def test_async_archive_immutable_refusal():
    """Verify immutability contract: refuses to overwrite existing remote object with different content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "doc.pdf")
        with open(src_path, "wb") as f:
            f.write(b"original content" + b"0" * (5 * 1024 * 1024))

        mock_s3 = MockAsyncS3Client()
        # Seed different remote object at same key
        mock_s3.store[("my-bucket", "docs/doc.pdf")] = {
            "bytes": b"different content",
            "etag": '"abc"',
            "metadata": {"sha256": "different_sha256"},
        }

        res = await storage_tier.archive_to_s3_async(
            source_path=src_path,
            bucket="my-bucket",
            key="docs/doc.pdf",
            client=mock_s3,
            part_size=5 * 1024 * 1024,
        )

        assert res["ok"] is False
        assert "refusing to overwrite" in res["error"]
        assert os.path.exists(src_path)


@pytest.mark.anyio
async def test_async_archive_failure_aborts_multipart():
    """Verify that failure during upload aborts the multipart upload and preserves local file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "broken.bin")
        with open(src_path, "wb") as f:
            f.write(b"Z" * (6 * 1024 * 1024))

        mock_s3 = MockAsyncS3Client()

        async def failing_upload_part(*args, **kwargs):
            raise IOError("Network connection reset during part upload")

        mock_s3.upload_part = failing_upload_part

        res = await storage_tier.archive_to_s3_async(
            source_path=src_path,
            bucket="my-bucket",
            key="broken.bin",
            client=mock_s3,
            part_size=5 * 1024 * 1024,
        )

        assert res["ok"] is False
        assert "upload failed" in res["error"]
        assert len(mock_s3.aborted) == 1
        assert os.path.exists(src_path)


@pytest.mark.anyio
async def test_stat_object_non_404_raises_fail_closed():
    """Verify F5 fix: stat_object must raise on non-404 errors, only returning None for confirmed 404."""
    from bulk_downloader.async_object_storage import AsyncObjectStorageClient

    mock_s3 = MockAsyncS3Client()
    client = AsyncObjectStorageClient(raw_client=mock_s3)

    # 1. Confirmed NoSuchKey -> returns None
    meta = await client.stat_object("my-bucket", "missing-key.bin")
    assert meta is None

    # 2. 500 error -> raises, does NOT return None
    async def head_500(Bucket: str, Key: str):
        raise RuntimeError("500 Internal Server Error")

    mock_s3.head_object = head_500
    with pytest.raises(RuntimeError) as exc_info:
        await client.stat_object("my-bucket", "missing-key.bin")
    assert "500" in str(exc_info.value)


@pytest.mark.anyio
async def test_async_archive_part_etag_mismatch_aborts():
    """Verify M3 kill: part etag mismatch aborts multipart upload."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "corrupt_part.bin")
        with open(src_path, "wb") as f:
            f.write(b"C" * (6 * 1024 * 1024))

        mock_s3 = MockAsyncS3Client()

        async def corrupted_upload_part(Bucket: str, Key: str, UploadId: str, PartNumber: int, Body=None):
            return {"ETag": '"00000000000000000000000000000000"'}

        mock_s3.upload_part = corrupted_upload_part

        res = await storage_tier.archive_to_s3_async(
            source_path=src_path,
            bucket="my-bucket",
            key="corrupt_part.bin",
            client=mock_s3,
            part_size=5 * 1024 * 1024,
        )

        assert res["ok"] is False
        assert "stored bytes differ" in res["error"]
        assert len(mock_s3.aborted) == 1
        assert os.path.exists(src_path)


@pytest.mark.anyio
async def test_async_archive_remote_checksum_mismatch_fails():
    """Verify M2 kill: remote checksum verification failure marks archive failed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "corrupt_remote.bin")
        with open(src_path, "wb") as f:
            f.write(b"D" * (6 * 1024 * 1024))

        mock_s3 = MockAsyncS3Client()
        orig_head = mock_s3.head_object

        async def corrupted_post_head(Bucket: str, Key: str):
            res = await orig_head(Bucket, Key)
            res["Metadata"]["sha256"] = "corrupted_remote_digest"
            return res

        async def dynamic_head(Bucket: str, Key: str):
            item = mock_s3.store.get((Bucket, Key))
            if item is None:
                raise RuntimeError(f"NoSuchKey: s3://{Bucket}/{Key}")
            return await corrupted_post_head(Bucket, Key)

        mock_s3.head_object = dynamic_head

        res = await storage_tier.archive_to_s3_async(
            source_path=src_path,
            bucket="my-bucket",
            key="corrupt_remote.bin",
            client=mock_s3,
            part_size=5 * 1024 * 1024,
        )

        assert res["ok"] is False
        assert "remote checksum verification failed" in res["error"]
        assert os.path.exists(src_path)


def test_ssrf_guard_blocks_private_and_loopback_endpoints():
    """Verify E1/F2 fix: SSRF guard rejects loopback, link-local, and private endpoint URLs."""
    from bulk_downloader.async_object_storage import AsyncObjectStorageClient, AsyncObjectStorageConfig
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked

    # 1. Async client: loopback rejected
    cfg_loop = AsyncObjectStorageConfig(endpoint_url="http://127.0.0.1:9000", bucket="test")
    client_loop = AsyncObjectStorageClient(cfg_loop)
    with pytest.raises(SSRFBlocked) as exc_info:
        _open(client_loop)
    assert "SSRF guard" in str(exc_info.value)

    # 2. Async client: link-local cloud metadata rejected
    cfg_meta = AsyncObjectStorageConfig(endpoint_url="http://169.254.169.254/latest/meta-data", bucket="test")
    client_meta = AsyncObjectStorageClient(cfg_meta)
    with pytest.raises(SSRFBlocked):
        _open(client_meta)

    # 3. Async client: private RFC1918 rejected
    cfg_priv = AsyncObjectStorageConfig(endpoint_url="http://10.0.0.1:9000", bucket="test")
    client_priv = AsyncObjectStorageClient(cfg_priv)
    with pytest.raises(SSRFBlocked):
        _open(client_priv)

    # 4. Sync client config: loopback rejected
    client_sync, err = storage_tier._s3_client_from_config({
        "storage_tier_s3_endpoint": "http://127.0.0.1:9000",
    })
    assert client_sync is None
    assert err is not None
    assert "SSRFBlocked" in err


def test_product_caller_run_site_migration_s3_async():
    """Verify E3 fix: run_site_migration wires s3_async mode end-to-end through storage tier scheduler."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filename = "media_item.mp4"
        file_path = os.path.join(tmpdir, filename)
        test_payload = b"P" * (6 * 1024 * 1024)
        with open(file_path, "wb") as f:
            f.write(test_payload)

        past_time = time.time() - (40 * 86400)
        os.utime(file_path, (past_time, past_time))

        mock_s3 = MockAsyncS3Client()
        from bulk_downloader.async_object_storage import AsyncObjectStorageClient, AsyncObjectStorageConfig
        async_storage_client = AsyncObjectStorageClient(
            config=AsyncObjectStorageConfig(bucket="migration-bucket"),
            raw_client=mock_s3,
        )

        site_cfg = {
            "storage_tier_enabled": True,
            "storage_tier_mode": "s3_async",
            "storage_tier_s3_bucket": "migration-bucket",
            "storage_tier_s3_prefix": "archive",
            "storage_tier_age_days": 30,
            "storage_tier_min_size_mb": 1,
            "storage_tier_async_client": async_storage_client,
        }

        orig_find = storage_tier.find_candidates
        orig_update = storage_tier._update_queue_filename
        storage_tier.find_candidates = lambda sid, age, min_sz: [{
            "url": "http://example.com/media1",
            "filename": filename,
            "file_size": len(test_payload),
            "ts_updated": "2026-01-01 00:00:00",
        }]
        storage_tier._update_queue_filename = lambda *args, **kwargs: None
        try:
            summary = storage_tier.run_site_migration("site1", site_cfg, tmpdir)
            assert summary["ok"] is True
            assert summary["migrated_count"] == 1
            assert summary["bytes_freed"] == len(test_payload)
            assert not os.path.exists(file_path)
            assert os.path.exists(f"{file_path}{storage_tier.S3_POINTER_SUFFIX}")

            # Also verify run_site_migration_async wrapper
            res_async = asyncio.run(
                storage_tier.run_site_migration_async("site1", site_cfg, tmpdir)
            )
            assert res_async["ok"] is True
        finally:
            storage_tier.find_candidates = orig_find
            storage_tier._update_queue_filename = orig_update


def test_async_storage_config_options():
    """Verify AsyncObjectStorageConfig defaults, options, and factory function."""
    from bulk_downloader.async_object_storage import (
        AsyncObjectStorageConfig,
        get_async_object_storage_client,
    )

    cfg = AsyncObjectStorageConfig(
        endpoint_url="https://s3.us-east-1.amazonaws.com",
        access_key_id="test_key",
        secret_access_key="test_secret",
        region_name="us-east-1",
        bucket="test-archive",
        part_size=10 * 1024 * 1024,
        backend="aioboto3",
        allow_private_hosts=False,
    )
    assert cfg.endpoint_url == "https://s3.us-east-1.amazonaws.com"
    assert cfg.access_key_id == "test_key"
    assert cfg.bucket == "test-archive"
    assert cfg.part_size == 10 * 1024 * 1024
    assert cfg.backend in ("aioboto3", "s3fs", "custom")
    assert cfg.allow_private_hosts is False

    client = get_async_object_storage_client({
        "storage_tier_s3_endpoint": "https://s3.us-east-1.amazonaws.com",
        "storage_tier_s3_bucket": "test-archive",
        "storage_tier_allow_private_hosts": True,
    })
    assert client.config.allow_private_hosts is True


# --- F2 (RULING-2258, close (a)): the aioboto3 client is guarded at CONNECT time ---
#
# The endpoint pre-check resolves the host once, at construction; the S3 client
# resolves it again when it opens a socket.  These tests stand in for the
# rebinding window by making the pre-check pass, then drive the resolver the
# product hands to aiobotocore through a real aiohttp connector against a real
# loopback listener, and assert what reached the socket.

import sys as _sys
import threading as _threading
import types as _types
from http.server import BaseHTTPRequestHandler as _Handler, HTTPServer as _HTTPServer


class _CountingHandler(_Handler):
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def _loopback_server():
    handler = type("_Counting", (_CountingHandler,), {"hits": 0})
    server = _HTTPServer(("127.0.0.1", 0), handler)
    _threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, handler


class _ClientContext:
    """What aioboto3 13.x Session.client() really returns (aiobotocore ClientCreatorContext):
    an async context manager with NO S3 methods of its own. The client exists only between
    __aenter__ and __aexit__ (N4-A E1)."""

    def __init__(self, peer, log):
        self._peer = peer
        self._log = log

    async def __aenter__(self):
        self._log.append(("enter", id(asyncio.get_running_loop())))
        return self._peer

    async def __aexit__(self, *exc):
        self._log.append(("exit", id(asyncio.get_running_loop())))
        return False


def _fake_aioboto3(monkeypatch, peer_factory=object):
    """Capture what the product hands aioboto3 without needing it installed."""
    captured = {"context_log": []}

    class _Session:
        def client(self, service, **kwargs):
            captured["service"] = service
            captured.update(kwargs)
            return _ClientContext(peer_factory(), captured["context_log"])

    class _AioConfig:
        def __init__(self, connector_args=None, **options):
            self.connector_args = dict(connector_args or {})
            self.options = options

    aioboto3 = _types.ModuleType("aioboto3")
    aioboto3.Session = _Session
    pkg = _types.ModuleType("aiobotocore")
    cfgmod = _types.ModuleType("aiobotocore.config")
    cfgmod.AioConfig = _AioConfig
    pkg.config = cfgmod
    monkeypatch.setitem(_sys.modules, "aioboto3", aioboto3)
    monkeypatch.setitem(_sys.modules, "aiobotocore", pkg)
    monkeypatch.setitem(_sys.modules, "aiobotocore.config", cfgmod)
    return captured


def _open(client):
    """Enter the product's per-operation client the way stat/delete/upload do."""
    async def run():
        async with client.open_client() as raw:
            return raw
    return asyncio.run(run())


def test_n4a_e1_stat_object_enters_the_aioboto3_client_context(monkeypatch):
    """N4-A E1: Session.client() is a context, not a client. The product must enter it for the
    operation and close it after; calling head_object on the context itself is AttributeError."""
    from bulk_downloader import async_object_storage as aos

    peer = MockAsyncS3Client()

    async def head(Bucket, Key):
        return {"ContentLength": 5, "ETag": '"e1"', "Metadata": {}}

    peer.head_object = head
    captured = _fake_aioboto3(monkeypatch, peer_factory=lambda: peer)
    client = aos.get_async_object_storage_client({"storage_tier_s3_bucket": "b"})
    meta = asyncio.run(client.stat_object("b", "k"))
    assert meta is not None and meta.content_length == 5 and meta.etag == "e1", (
        f"N4-A E1: stat_object did not reach the S3 peer through the aioboto3 client context: {meta!r}")
    assert [e for e, _ in captured["context_log"]] == ["enter", "exit"], captured["context_log"]


def test_n4a_e1_product_caller_s3_async_migrates_through_aioboto3(monkeypatch):
    """N4-A E1 through the product caller: run_site_migration(s3_async) with the client BUILT by
    the product (no injected raw client). Every upload runs on its own event loop
    (storage_tier._run_async), so each must open -- and close -- its client on that loop."""
    peers = []

    def new_peer():
        peers.append(MockAsyncS3Client())
        return peers[-1]

    captured = _fake_aioboto3(monkeypatch, peer_factory=new_peer)
    with tempfile.TemporaryDirectory() as tmpdir:
        names = ["a.mp4", "b.mp4"]
        payload = b"Q" * (6 * 1024 * 1024)
        past = time.time() - 40 * 86400
        for n in names:
            with open(os.path.join(tmpdir, n), "wb") as f:
                f.write(payload)
            os.utime(os.path.join(tmpdir, n), (past, past))
        monkeypatch.setattr(storage_tier, "find_candidates", lambda sid, age, mn: [
            {"url": f"http://example.com/{n}", "filename": n, "file_size": len(payload),
             "ts_updated": "2026-01-01 00:00:00"} for n in names])
        monkeypatch.setattr(storage_tier, "_update_queue_filename", lambda *a, **k: None)
        summary = storage_tier.run_site_migration("site1", {
            "storage_tier_enabled": True,
            "storage_tier_mode": "s3_async",
            "storage_tier_s3_bucket": "migration-bucket",
            "storage_tier_age_days": 30,
            "storage_tier_min_size_mb": 1,
        }, tmpdir)
    assert summary["migrated_count"] == 2, (
        f"N4-A E1: s3_async migrated {summary['migrated_count']} of 2 through the product-built "
        f"aioboto3 client; errors={summary['errors']}")
    log = captured["context_log"]
    assert [e for e, _ in log].count("enter") == [e for e, _ in log].count("exit") >= 2, log
    for i in range(0, len(log), 2):
        assert log[i][0] == "enter" and log[i + 1] == ("exit", log[i][1]), (
            f"a client context was not closed on the loop that opened it: {log}")


def _get_via_resolver(resolver, url):
    import aiohttp

    async def run():
        connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url) as resp:
                return resp.status

    return asyncio.run(run())


def _chain(exc):
    while exc is not None:
        yield exc
        exc = exc.__cause__ or exc.__context__


def test_f2_async_client_refuses_loopback_at_connect_after_precheck_passes(monkeypatch):
    from bulk_downloader import async_object_storage as aos
    from bulk_downloader.provider_resolve_impl import _common
    from bulk_downloader.ssrf_transport import GuardedTransportRefused

    captured = _fake_aioboto3(monkeypatch)
    # The rebinding window: the construction-time answer was public.
    monkeypatch.setattr(_common, "_is_safe_public_host", lambda host: (True, ""))
    server, handler = _loopback_server()
    try:
        port = server.server_address[1]
        client = aos.get_async_object_storage_client({
            "storage_tier_s3_endpoint": f"http://localhost:{port}",
            "storage_tier_s3_bucket": "b",
        })
        _open(client)
        assert captured["service"] == "s3"
        config = captured.get("config")
        assert config is not None, "aioboto3 client built without the guarded AioConfig"
        # Ambient HTTP(S)_PROXY must not carry the request past the resolver (row 439).
        assert config.options.get("proxies") == {}
        resolver = config.connector_args.get("resolver")
        assert resolver is not None, "aioboto3 client built without the guarded resolver"

        with pytest.raises(Exception) as exc_info:
            _get_via_resolver(resolver, f"http://localhost:{port}/")
        refused = [e for e in _chain(exc_info.value) if isinstance(e, GuardedTransportRefused)]
        assert refused, f"connect was not refused by the guard: {exc_info.value!r}"
        assert handler.hits == 0, "the loopback listener was reached"
    finally:
        server.shutdown()


def test_f2_positive_control_allow_private_hosts_admits_loopback_at_connect(monkeypatch):
    """Rule 7: the same probe says YES when the operator admits private hosts."""
    from bulk_downloader import async_object_storage as aos

    captured = _fake_aioboto3(monkeypatch)
    server, handler = _loopback_server()
    try:
        port = server.server_address[1]
        client = aos.get_async_object_storage_client({
            "storage_tier_s3_endpoint": f"http://localhost:{port}",
            "storage_tier_s3_bucket": "b",
            "storage_tier_allow_private_hosts": True,
        })
        _open(client)
        resolver = captured["config"].connector_args["resolver"]
        assert _get_via_resolver(resolver, f"http://localhost:{port}/") == 200
        assert handler.hits == 1
    finally:
        server.shutdown()


def test_f2_allow_private_hosts_still_refuses_metadata_literal(monkeypatch):
    """A literal IP never reaches aiohttp's resolver, so it is judged at construction."""
    from bulk_downloader import async_object_storage as aos
    from bulk_downloader.ssrf_transport import GuardedTransportRefused

    captured = _fake_aioboto3(monkeypatch)
    client = aos.get_async_object_storage_client({
        "storage_tier_s3_endpoint": "http://169.254.169.254/latest",
        "storage_tier_allow_private_hosts": True,
    })
    with pytest.raises(GuardedTransportRefused):
        _open(client)
    assert "service" not in captured
