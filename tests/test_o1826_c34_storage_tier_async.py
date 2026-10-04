"""O1826 BRIEF-34: storage_tier async paths must not block the event loop.

M175: run_site_migration_async called the blocking sync migration on the
event loop, so no other task could run until the whole pass finished.
M174: archive_to_s3_async duplicated archive_to_s3 verbatim; both now share
one helper, so their client call sequences and results must stay identical.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import threading
import time

from bulk_downloader import storage_tier

BD_GATE_SCOPE = "module"

_PART = storage_tier.MIN_S3_PART_SIZE


def _migration_cfg(client) -> dict:
    return {
        "storage_tier_enabled": True,
        "storage_tier_mode": "s3_async",
        "storage_tier_s3_bucket": "tier-bucket",
        "storage_tier_s3_prefix": "archive",
        "storage_tier_age_days": 30,
        "storage_tier_min_size_mb": 1,
        "storage_tier_async_client": client,
    }


def test_async_migration_does_not_block_event_loop(monkeypatch, tmp_path):
    """A concurrent ticker must advance while run_site_migration_async runs."""
    ticked = threading.Event()
    seen = {}

    def blocking_find_candidates(site_id, age_days, min_size_mb):
        # The candidate query is blocking I/O. Wait for the ticker: it can only
        # run if the migration is NOT occupying the event loop thread.
        seen["released"] = ticked.wait(timeout=2.0)
        seen["thread"] = threading.current_thread()
        return []

    async def ticker(counter):
        while True:
            counter["n"] += 1
            if counter["n"] >= 3:
                ticked.set()
            await asyncio.sleep(0.001)

    async def scenario():
        counter = {"n": 0}
        task = asyncio.create_task(ticker(counter))
        try:
            summary = await storage_tier.run_site_migration_async(
                "site1", _migration_cfg(object()), str(tmp_path))
        finally:
            task.cancel()
        return summary, counter["n"], threading.current_thread()

    with monkeypatch.context() as m:
        m.setattr(storage_tier, "find_candidates", blocking_find_candidates)
        summary, ticks, loop_thread = asyncio.run(scenario())

    assert summary["ok"] is True and summary["migrated_count"] == 0, summary
    assert seen.get("released") is True, (
        "M175 EVENT LOOP BLOCKED: run_site_migration_async ran the sync migration "
        f"on the loop thread; ticker advanced {ticks} times while it ran")
    assert seen["thread"] is not loop_thread


class _RecordingSyncClient:
    """Minimal S3 multipart store that records every call it receives."""

    def __init__(self):
        self.calls = []
        self.objects = {}
        self.pending = {}

    def _record(self, name, kwargs):
        self.calls.append((name, sorted(k for k in kwargs if k != "Body")))

    def head_object(self, **kw):
        self._record("head_object", kw)
        obj = self.objects.get(kw["Key"])
        if obj is None:
            raise RuntimeError(f"NoSuchKey: {kw['Key']}")
        return dict(obj)

    def create_multipart_upload(self, **kw):
        self._record("create_multipart_upload", kw)
        self.pending = {"metadata": dict(kw.get("Metadata") or {}), "parts": []}
        return {"UploadId": "u1"}

    def upload_part(self, **kw):
        self._record("upload_part", kw)
        data = kw["Body"].read()
        self.pending["parts"].append(data)
        return {"ETag": f'"{hashlib.md5(data).hexdigest()}"'}

    def complete_multipart_upload(self, **kw):
        self._record("complete_multipart_upload", kw)
        parts = self.pending["parts"]
        etag = storage_tier._multipart_etag([hashlib.md5(p).digest() for p in parts])
        self.objects[kw["Key"]] = {
            "ContentLength": sum(len(p) for p in parts),
            "ETag": f'"{etag}"',
            "Metadata": self.pending["metadata"],
        }
        return {"ETag": f'"{etag}"'}

    def abort_multipart_upload(self, **kw):
        self._record("abort_multipart_upload", kw)
        return {}


class _RecordingAsyncClient:
    def __init__(self):
        self.sync = _RecordingSyncClient()

    def __getattr__(self, name):
        method = getattr(self.sync, name)

        async def call(**kw):
            await asyncio.sleep(0)
            return method(**kw)
        return call


class _CorruptingSyncClient(_RecordingSyncClient):
    def upload_part(self, **kw):
        super().upload_part(**kw)
        return {"ETag": '"0000"'}


def _write_media(path, size):
    path.write_bytes(bytes(i % 251 for i in range(size)))
    return str(path)


def _archive_both(tmp_path, sync_client, async_client, size):
    sync_src = _write_media(tmp_path / "sync.bin", size)
    async_src = _write_media(tmp_path / "async.bin", size)
    sync_res = storage_tier.archive_to_s3(
        sync_src, "b", "k", sync_client, part_size=_PART)
    async_res = asyncio.run(storage_tier.archive_to_s3_async(
        async_src, "b", "k", async_client, part_size=_PART))
    return sync_src, sync_res, async_src, async_res


def test_archive_sync_and_async_share_behaviour(tmp_path):
    """Same fixture -> same client call sequence, same result, same pointer."""
    sync_client = _RecordingSyncClient()
    async_client = _RecordingAsyncClient()
    sync_src, sync_res, async_src, async_res = _archive_both(
        tmp_path, sync_client, async_client, 2 * _PART + 17)

    assert sync_res["ok"] is True, sync_res
    assert sync_res["action"] == "archived_to_s3"
    assert async_res["action"] == "archived_to_s3_async"
    assert {k: v for k, v in sync_res.items() if k != "action"} == \
        {k: v for k, v in async_res.items() if k != "action"}
    assert sync_client.calls == async_client.sync.calls
    assert [c[0] for c in sync_client.calls].count("upload_part") == 3
    assert not os.path.exists(sync_src) and not os.path.exists(async_src)
    assert (tmp_path / f"sync.bin{storage_tier.S3_POINTER_SUFFIX}").read_bytes() == \
        (tmp_path / f"async.bin{storage_tier.S3_POINTER_SUFFIX}").read_bytes()


def test_archive_failure_paths_match_between_sync_and_async(tmp_path):
    """A corrupted part aborts the upload and keeps the source on both paths."""
    sync_client = _CorruptingSyncClient()
    async_client = _RecordingAsyncClient()
    async_client.sync = _CorruptingSyncClient()
    sync_src, sync_res, async_src, async_res = _archive_both(
        tmp_path, sync_client, async_client, _PART + 1)

    assert sync_res == async_res
    assert sync_res["ok"] is False
    assert "part 1 stored bytes differ from source" in sync_res["error"]
    assert sync_client.calls[-1][0] == "abort_multipart_upload"
    assert sync_client.calls == async_client.sync.calls
    assert os.path.exists(sync_src) and os.path.exists(async_src)


def test_async_migration_results_match_sync_migration(monkeypatch, tmp_path):
    """Negative control: offloading changes where it runs, not what it returns."""
    media = _write_media(tmp_path / "old.mp4", _PART + 5)
    past = time.time() - 40 * 86400
    os.utime(media, (past, past))

    def candidates(site_id, age_days, min_size_mb):
        return [{"url": "http://example.invalid/m", "filename": "old.mp4"}]

    class Upload:
        def __init__(self):
            self.calls = []

        async def upload_file(self, source, bucket, key):
            self.calls.append((source, bucket, key))
            from types import SimpleNamespace
            return SimpleNamespace(ok=False, action="archived_to_s3_async",
                                   bytes_moved=0, dest_path="", etag="",
                                   error="refused by fixture")

    with monkeypatch.context() as m:
        m.setattr(storage_tier, "find_candidates", candidates)
        m.setattr(storage_tier, "_update_queue_filename", lambda *a, **k: None)
        sync_uploader, async_uploader = Upload(), Upload()
        sync_summary = storage_tier.run_site_migration(
            "site1", _migration_cfg(sync_uploader), str(tmp_path))
        async_summary = asyncio.run(storage_tier.run_site_migration_async(
            "site1", _migration_cfg(async_uploader), str(tmp_path)))

    assert async_summary == sync_summary
    assert sync_summary["errors"] == ["old.mp4: refused by fixture"]
    assert sync_uploader.calls == async_uploader.calls == [
        (media, "tier-bucket", "archive/old.mp4")]
