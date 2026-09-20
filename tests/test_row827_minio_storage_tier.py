"""Cut 827: S3 cold-storage archival contract."""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import hashlib
import json
import os
import time


MiB = 1024 * 1024


class _MemoryMultipartClient:
    """Small S3 boundary fake that verifies the uploaded object, not calls."""

    def __init__(self, *, reported_sha256: str | None = None,
                 fail_part: int | None = None, corrupt_part: int | None = None,
                 existing: dict | None = None):
        self.reported_sha256 = reported_sha256
        self.fail_part = fail_part
        self.corrupt_part = corrupt_part      # store different bytes for this part (honest ETag of what was stored)
        self.parts: list[bytes] = []
        self.aborted = False
        self.completed = False
        self.metadata: dict[str, str] = {}
        self.objects: dict[str, dict] = dict(existing or {})   # key -> {"parts": [...], "metadata": {...}}
        self.uploads = 0

    def create_multipart_upload(self, **_kwargs):
        self.metadata = _kwargs["Metadata"]
        self.parts = []
        self.uploads += 1
        return {"UploadId": "row827-upload"}

    def upload_part(self, *, PartNumber, Body, **_kwargs):
        if PartNumber == self.fail_part:
            raise RuntimeError("network unavailable")
        payload = Body.read()
        if PartNumber == self.corrupt_part:
            payload = bytes(b ^ 0xFF for b in payload[:1]) + payload[1:]   # same length, one byte flipped
        self.parts.append(payload)
        return {"ETag": hashlib.md5(payload).hexdigest()}     # S3 contract: MD5 of the STORED bytes

    def _etag(self, parts):
        return hashlib.md5(b"".join(hashlib.md5(p).digest() for p in parts)).hexdigest() + f"-{len(parts)}"

    def complete_multipart_upload(self, *, Key, MultipartUpload, **_kwargs):
        assert MultipartUpload["Parts"]
        self.completed = True
        self.objects[Key] = {"parts": list(self.parts), "metadata": dict(self.metadata)}
        return {"ETag": self._etag(self.parts)}

    def head_object(self, *, Key, **_kwargs):
        obj = self.objects.get(Key)
        if obj is None:
            raise KeyError("NoSuchKey")
        meta = dict(obj["metadata"])
        if self.reported_sha256:
            meta["sha256"] = self.reported_sha256
        return {"ContentLength": sum(map(len, obj["parts"])), "ETag": self._etag(obj["parts"]), "Metadata": meta}

    def abort_multipart_upload(self, **_kwargs):
        self.aborted = True


def _s3_archive():
    from bulk_downloader import storage_tier

    archive = getattr(storage_tier, "archive_to_s3", None)
    assert archive is not None, "S3 archival entry point is missing"
    return archive


def test_s3_archive_streams_large_file_then_replaces_it_with_verified_pointer(tmp_path):
    """Removing stream/checksum/pointer behavior must break this contract."""
    source = tmp_path / "recording.mp4"
    with source.open("wb") as handle:
        handle.truncate(100 * MiB + 1)
    client = _MemoryMultipartClient()

    result = _s3_archive()(str(source), "cold", "archive/recording.mp4", client,
                           part_size=8 * MiB)

    assert result["ok"] is True
    assert len(client.parts) == 13
    assert sum(map(len, client.parts)) == 100 * MiB + 1
    assert not source.exists()
    pointer = json.loads((tmp_path / "recording.mp4.s3_pointer.json").read_text())
    assert pointer == {
        "bucket": "cold",
        "etag": client._etag(client.parts),
        "key": "archive/recording.mp4",
        "sha256": hashlib.sha256(b"\0" * (100 * MiB + 1)).hexdigest(),
    }


def test_s3_archive_keeps_local_file_when_remote_checksum_disagrees(tmp_path):
    """A checksum mismatch must never exchange media for a pointer."""
    source = tmp_path / "recording.mp4"
    source.write_bytes(b"verified-content")
    client = _MemoryMultipartClient(reported_sha256="not-the-local-checksum")

    result = _s3_archive()(str(source), "cold", "archive/recording.mp4", client,
                           part_size=5 * MiB)

    assert result["ok"] is False
    assert "checksum" in result["error"]
    assert source.read_bytes() == b"verified-content"
    assert not (tmp_path / "recording.mp4.s3_pointer.json").exists()


def test_s3_archive_aborts_failed_upload_and_keeps_local_file(tmp_path):
    """An interrupted upload is recoverable and leaves no destructive side effect."""
    source = tmp_path / "recording.mp4"
    source.write_bytes(b"uninterrupted-local-media")
    client = _MemoryMultipartClient(fail_part=1)

    result = _s3_archive()(str(source), "cold", "archive/recording.mp4", client,
                           part_size=5 * MiB)

    assert result["ok"] is False
    assert "upload failed" in result["error"]
    assert client.aborted is True
    assert source.read_bytes() == b"uninterrupted-local-media"


def test_s3_mode_archives_candidates_without_a_local_destination(tmp_path, monkeypatch):
    """Changing the S3 mode branch must stop the tier sweep from archiving."""
    from bulk_downloader import storage_tier

    source = tmp_path / "recording.mp4"
    source.write_bytes(b"aged-media")
    old = time.time() - 2 * 86400
    os.utime(source, (old, old))
    client = _MemoryMultipartClient()
    monkeypatch.setattr(storage_tier, "find_candidates", lambda *_args: [{
        "url": "https://example.test/media",
        "filename": source.name,
        "file_size": source.stat().st_size,
        "ts_updated": "2000-01-01T00:00:00",
    }])

    summary = storage_tier.run_site_migration("site", {
        "storage_tier_enabled": True,
        "storage_tier_mode": "s3",
        "storage_tier_s3_bucket": "cold",
        "storage_tier_s3_client": client,
        "storage_tier_age_days": 1,
    }, str(tmp_path))

    assert summary["migrated_count"] == 1
    assert summary["bytes_freed"] == len(b"aged-media")
    assert not source.exists()
    assert (tmp_path / "recording.mp4.s3_pointer.json").exists()


def test_storage_tier_scheduler_loop_executes_migration(monkeypatch):
    """Verify scheduler loop executes migration and records summary."""
    import sys
    import types
    from bulk_downloader import storage_tier

    scheduler = storage_tier.StorageTierScheduler(interval_s=1)
    fake_app = types.SimpleNamespace(
        s_cfg={"s1": {"storage_tier_enabled": True, "download_dir": "/tmp"}},
        runners={},
    )
    # `from . import app` returns the package ATTRIBUTE when a real
    # bulk_downloader.app was imported earlier in this worker, so the
    # sys.modules stub alone leaves the loop walking the real s_cfg forever.
    import bulk_downloader
    monkeypatch.setitem(sys.modules, "bulk_downloader.app", fake_app)
    monkeypatch.setattr(bulk_downloader, "app", fake_app, raising=False)

    called = []

    def _fake_migrate(sid, cfg, dl):
        called.append(sid)
        scheduler._stop.set()
        return {"ok": True, "migrated_count": 1}

    monkeypatch.setattr(storage_tier, "run_site_migration", _fake_migrate)
    monkeypatch.setattr(scheduler._stop, "wait", lambda timeout=None: None)

    scheduler._loop()
    assert "s1" in called
    status = scheduler.get_status()
    assert status["per_site"]["s1"]["summary"] == {"ok": True, "migrated_count": 1}


# ---- fixer (O928) controls for the correctness REFUTE E1-E4 (HIGH) ---------

def test_e1_source_replaced_after_upload_is_never_deleted(tmp_path):
    """E1: the media changes between the upload and the unlink (during HEAD);
    the new bytes must stay, no pointer is written."""
    source = tmp_path / "recording.mp4"
    source.write_bytes(b"original-content")
    client = _MemoryMultipartClient()
    real_head = client.head_object

    def head_then_replace(**kw):
        res = real_head(**kw)
        tmp = tmp_path / "replacement.tmp"
        tmp.write_bytes(b"replacement-bytes")
        os.replace(tmp, source)          # atomic replace: new inode at the same path
        return res

    client.head_object = head_then_replace
    result = _s3_archive()(str(source), "cold", "archive/recording.mp4", client, part_size=5 * MiB)
    assert result["ok"] is False and "changed after upload" in result["error"], result
    assert source.read_bytes() == b"replacement-bytes"
    assert not (tmp_path / "recording.mp4.s3_pointer.json").exists()


def test_e2_retry_with_existing_pointer_never_overwrites_the_archive(tmp_path):
    source = tmp_path / "recording.mp4"
    source.write_bytes(b"first-archive")
    client = _MemoryMultipartClient()
    assert _s3_archive()(str(source), "cold", "archive/recording.mp4", client, part_size=5 * MiB)["ok"]
    archived = list(client.objects["archive/recording.mp4"]["parts"])
    # a new file appears at the same path (re-download) and a retry sweeps it
    source.write_bytes(b"second-different-content")
    result = _s3_archive()(str(source), "cold", "archive/recording.mp4", client, part_size=5 * MiB)
    assert result["ok"] is False and "already archived" in result["error"]
    assert client.objects["archive/recording.mp4"]["parts"] == archived
    assert client.uploads == 1
    assert source.read_bytes() == b"second-different-content"
    # no pointer, but the remote key is taken by different content: refused before any upload
    (tmp_path / "recording.mp4.s3_pointer.json").unlink()
    result = _s3_archive()(str(source), "cold", "archive/recording.mp4", client, part_size=5 * MiB)
    assert result["ok"] is False and "immutable" in result["error"]
    assert client.uploads == 1 and client.objects["archive/recording.mp4"]["parts"] == archived


def test_e3_corrupted_stored_bytes_with_honest_etags_keep_the_local_file(tmp_path):
    """E3: the store keeps one flipped byte (same length) and reports honest
    ETags for what it stored; the metadata still echoes our sha256. Local media
    must not be deleted."""
    source = tmp_path / "recording.mp4"
    source.write_bytes(b"verified-content-" * 1000)
    client = _MemoryMultipartClient(corrupt_part=1)
    result = _s3_archive()(str(source), "cold", "archive/recording.mp4", client, part_size=5 * MiB)
    assert result["ok"] is False and "stored bytes differ" in result["error"], result
    assert client.aborted
    assert source.read_bytes() == b"verified-content-" * 1000
    assert not (tmp_path / "recording.mp4.s3_pointer.json").exists()


def test_e4_next_sweep_skips_archived_stubs(tmp_path, monkeypatch):
    from bulk_downloader import storage_tier
    media = tmp_path / "old.mp4"
    media.write_bytes(b"media-bytes")
    old = time.time() - 90 * 86400
    os.utime(media, (old, old))
    client = _MemoryMultipartClient()
    cfg = {"download_dir": str(tmp_path), "storage_tier_enabled": True, "storage_tier_mode": "s3", "storage_tier_s3_bucket": "cold",
           "storage_tier_s3_client": client, "storage_tier_age_days": 30}
    rows = [{"url": "u1", "filename": "old.mp4", "file_size": 11, "ts_updated": "2000-01-01T00:00:00"}]
    monkeypatch.setattr(storage_tier, "find_candidates", lambda *a, **k: list(rows))
    updates = []
    monkeypatch.setattr(storage_tier, "_update_queue_filename", lambda *a: updates.append(a))
    first = storage_tier.run_site_migration("site", cfg, str(tmp_path))
    assert first["migrated_count"] == 1 and client.uploads == 1
    pointer = tmp_path / "old.mp4.s3_pointer.json"
    assert pointer.exists() and not media.exists()
    assert updates and updates[0][2] == str(pointer)
    # the DB row now names the pointer (what _update_queue_filename wrote); age it and sweep again
    os.utime(pointer, (old, old))
    rows[:] = [{"url": "u1", "filename": "old.mp4.s3_pointer.json", "file_size": 100, "ts_updated": "2000-01-01T00:00:00"}]
    second = storage_tier.run_site_migration("site", cfg, str(tmp_path))
    assert second["migrated_count"] == 0 and second["skipped_count"] == 1
    assert client.uploads == 1
    assert pointer.exists() and not (tmp_path / "old.mp4.s3_pointer.json.s3_pointer.json").exists()
    # media re-downloaded beside an existing pointer is skipped too (E2 at the sweep level)
    media.write_bytes(b"again"); os.utime(media, (old, old))
    rows[:] = [{"url": "u1", "filename": "old.mp4", "file_size": 5, "ts_updated": "2000-01-01T00:00:00"}]
    third = storage_tier.run_site_migration("site", cfg, str(tmp_path))
    assert third["migrated_count"] == 0 and client.uploads == 1 and media.exists()
