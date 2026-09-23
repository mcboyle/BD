"""v3.43.42: Storage tier migration.

Auto-move completed download files older than N days from the
primary `download_dir` (typically a fast/expensive SSD or NVMe)
to a cold storage tier (slow HDD, NAS share, S3-mounted FUSE).
Frees up the fast tier for new downloads without losing the
files.

Distinct from `spillover_dirs` which is WRITE-side overflow
("primary full → write new downloads here instead"). This
module is READ-side cleanup ("downloads completed >30 days
ago → relocate to cold tier").

## Configuration

Per-site fields:

  - `storage_tier_enabled` (bool, default False) — opt-in
  - `storage_tier_dir` (str) — destination root path. Must
    exist and be writable, but tested lazily at migration time
    rather than at config save.
  - `storage_tier_age_days` (int, default 30) — files completed
    more than this many days ago are eligible
  - `storage_tier_min_size_mb` (int, default 0) — only migrate
    files at-or-above this size. Useful when you have lots of
    small thumbnails / preview images you'd rather keep on the
    fast tier even when old.
  - `storage_tier_mode` ("move" | "symlink_after_move" | "dry_run")
    - "move": straight shutil.move (default)
    - "symlink_after_move": move to cold tier, leave a symlink
      at the original location so any downstream tool that
      indexes the fast tier (Plex, Stash, Jellyfin) keeps
      working without a rescan
    - "dry_run": log what would happen but don't actually move

## Scheduling

A daemon thread (StorageTierScheduler) wakes up once an hour,
scans every site with the feature enabled, and migrates eligible
files. Hourly is the right cadence — disk churn under load is
real and we don't want to fight workers for I/O.

Migration runs are bounded:
  - **MAX_FILES_PER_RUN = 500** so a huge backlog doesn't lock
    up the I/O subsystem for hours; subsequent runs pick up
    where the last one stopped
  - Per-file timeout enforced via shutil.move's natural blocking
    (Python doesn't expose move-timeout natively, so we rely on
    the OS layer)

## What counts as "completed"

We pull from the SQLite queue table: status='done' AND ts_updated
older than the cutoff. We do NOT walk the filesystem looking for
old files — that would pick up files the user moved there manually
(e.g. backup imports) or files outside our queue's awareness.

## Safety

- **Atomic moves** within the same filesystem are atomic; cross-
  filesystem moves go through copy-then-delete. Either way we
  verify the destination exists before unlinking the source.
- **Preserves directory structure** under the destination root.
  If the file was at ``E:/Wow/Episodes/ep001.mp4`` and
  storage_tier_dir is ``Z:/Cold/Wow``, it lands at
  ``Z:/Cold/Wow/Episodes/ep001.mp4``.
- **Updates the queue row** to point at the new path. The runner
  serves history queries from this column so Plex/Stash links
  stay valid.
- **Dry-run mode** for the user to validate the policy without
  committing.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import threading
import time
import hashlib
import io
import json
from pathlib import Path
from typing import Optional


# Hourly run cadence
DEFAULT_INTERVAL_S = 3600

# Cap files per run to avoid I/O storms
MAX_FILES_PER_RUN = 500

# S3 requires all multipart parts except the final one to be at least 5 MiB.
MIN_S3_PART_SIZE = 5 * 1024 * 1024
DEFAULT_S3_PART_SIZE = 8 * 1024 * 1024


S3_POINTER_SUFFIX = ".s3_pointer.json"


def _is_s3_not_found(exc: Exception) -> bool:
    """True iff the exception represents a confirmed 404 / NoSuchKey / NotFound from S3.

    Non-404 errors (500 InternalServerError, 403 AccessDenied, network timeouts, throttles)
    mean existence is UNVERIFIABLE and must fail closed to protect immutable archives.
    """
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        err = resp.get("Error", {})
        code = str(err.get("Code", ""))
        status = resp.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("404", "NoSuchKey", "NotFound") or status == 404:
            return True
        if code in ("403", "500", "AccessDenied", "InternalError", "SlowDown"):
            return False
    msg = str(exc)
    msg_lower = msg.lower()
    if any(err_tok in msg_lower for err_tok in ("500", "403", "accessdenied", "internalerror")):
        return False
    return any(tok in msg_lower for tok in ("nosuchkey", "404", "notfound", "not found"))


def archive_to_s3(source_path: str, bucket: str, key: str, client,
                  *, part_size: int = DEFAULT_S3_PART_SIZE) -> dict:
    """Stream a file to an S3-compatible store and replace it with a pointer.

    The local media remains authoritative until the completed object reports
    the exact length, ETag, and SHA-256 recorded during upload.
    """
    if not all(isinstance(value, str) and value for value in
               (source_path, bucket, key)):
        return {"ok": False, "error": "missing source, bucket, or key"}
    if not isinstance(part_size, int) or part_size < MIN_S3_PART_SIZE:
        return {"ok": False, "error": "part size must be at least 5 MiB"}
    pointer_path = f"{source_path}{S3_POINTER_SUFFIX}"
    if os.path.exists(pointer_path):
        # E2: an archive already exists for this media. The remote object it
        # names is IMMUTABLE -- never re-upload over it (an unversioned store
        # would lose the original archive); the caller resolves the stub instead.
        return {"ok": False, "error": f"already archived: pointer exists at {pointer_path}"}
    try:
        # E1: hold ONE open handle for the whole operation; identity is the
        # inode/size/mtime of THAT handle. The unlink at the end happens only
        # if the path still names this very file, unchanged.
        handle = open(source_path, "rb")
    except OSError as exc:
        return {"ok": False, "error": f"source unavailable: {exc}"}
    with handle:
        try:
            identity = _file_identity(os.fstat(handle.fileno()))
            source_size = identity[1]
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
            handle.seek(0)
        except OSError as exc:
            return {"ok": False, "error": f"source unavailable: {exc}"}

        # E2: a remote object already at this key is an archive that must not
        # be overwritten (unless it IS this content, byte for byte).
        try:
            existing = client.head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            if _is_s3_not_found(exc):
                existing = None
            else:
                return {
                    "ok": False,
                    "error": f"remote object existence unverifiable for s3://{bucket}/{key}: {exc}; "
                             "archives are immutable, refusing to overwrite",
                }
        if existing:
            existing_digest = (existing.get("Metadata") or {}).get("sha256")
            if existing_digest != digest or existing.get("ContentLength") != source_size:
                return {"ok": False,
                        "error": f"remote object exists at s3://{bucket}/{key} with different content; "
                                 "archives are immutable, refusing to overwrite"}

        upload_id = None
        try:
            created = client.create_multipart_upload(
                Bucket=bucket, Key=key, Metadata={"sha256": digest})
            upload_id = created["UploadId"]
            parts = []
            part_md5s = []
            uploaded_digest = hashlib.sha256()
            part_number = 1
            while payload := handle.read(part_size):
                uploaded_digest.update(payload)
                local_md5 = hashlib.md5(payload)
                uploaded = client.upload_part(
                    Bucket=bucket, Key=key, UploadId=upload_id,
                    PartNumber=part_number, Body=io.BytesIO(payload))
                # E3: S3's contract -- a part's ETag IS the MD5 of the bytes it
                # stored. A store that kept different bytes cannot return this.
                part_etag = str(uploaded.get("ETag", "")).strip('"')
                if part_etag != local_md5.hexdigest():
                    raise RuntimeError(
                        f"part {part_number} stored bytes differ from source "
                        f"(etag {part_etag!r} != md5 {local_md5.hexdigest()!r})")
                part_md5s.append(local_md5.digest())
                parts.append({"PartNumber": part_number, "ETag": uploaded["ETag"]})
                part_number += 1
            if uploaded_digest.hexdigest() != digest:
                raise RuntimeError("source changed while uploading")
            expected_etag = _multipart_etag(part_md5s)
            completed = client.complete_multipart_upload(
                Bucket=bucket, Key=key, UploadId=upload_id,
                MultipartUpload={"Parts": parts})
            completed_etag = str(completed.get("ETag", "")).strip('"')
            remote = client.head_object(Bucket=bucket, Key=key)
            remote_etag = str(remote.get("ETag", "")).strip('"')
            remote_digest = (remote.get("Metadata") or {}).get("sha256")
            # E3: the assembled object's ETag is derived from the part MD5s we
            # computed locally -- a real integrity check of what the store holds,
            # not an echo of the metadata we supplied (kept as a secondary check).
            if (remote.get("ContentLength") != source_size
                    or completed_etag != expected_etag or remote_etag != expected_etag
                    or remote_digest != digest):
                return {"ok": False, "error": "remote checksum verification failed"}
        except Exception as exc:
            abort_error = None
            if upload_id is not None:
                try:
                    client.abort_multipart_upload(
                        Bucket=bucket, Key=key, UploadId=upload_id)
                except Exception as abort_exc:
                    abort_error = f"{type(abort_exc).__name__}: {abort_exc}"
            error_msg = f"upload failed: {type(exc).__name__}: {exc}"
            if abort_error:
                error_msg += f" (abort failed: {abort_error})"
            return {"ok": False, "error": error_msg}

        # E1: the path must still name the SAME unchanged file we uploaded
        # (inode, size, mtime) -- a replacement or rewrite that landed after the
        # upload keeps its bytes; the archive stays, the pointer is not written.
        try:
            now_fd = _file_identity(os.fstat(handle.fileno()))
            now_path = _file_identity(os.stat(source_path))
        except OSError as exc:
            return {"ok": False, "error": f"source changed after upload: {exc}"}
        if now_fd != identity or now_path != identity:
            return {"ok": False,
                    "error": "source changed after upload (identity mismatch); local file kept, "
                             f"archive at s3://{bucket}/{key} left in place"}
        pointer = {"bucket": bucket, "key": key, "etag": expected_etag,
                   "sha256": digest}
        try:
            with open(pointer_path, "x", encoding="utf-8") as pointer_handle:
                json.dump(pointer, pointer_handle, sort_keys=True)
        except OSError as exc:
            return {"ok": False, "error": f"pointer replacement failed: {exc}"}
        try:
            if _file_identity(os.stat(source_path)) != identity:
                os.unlink(pointer_path)
                return {"ok": False, "error": "source changed after upload (identity mismatch); local file kept"}
            os.unlink(source_path)
        except OSError as exc:
            return {"ok": False, "error": f"pointer replacement failed: {exc}"}
    return {"ok": True, "action": "archived_to_s3", "bytes_moved": source_size,
            "dest_path": f"s3://{bucket}/{key}", "etag": expected_etag}


async def archive_to_s3_async(source_path: str, bucket: str, key: str, client,
                              *, part_size: int = DEFAULT_S3_PART_SIZE) -> dict:
    """Asynchronously stream a file to an S3-compatible store and replace it with a pointer.

    The local media remains authoritative until the completed object reports
    the exact length, ETag, and SHA-256 recorded during upload.
    """
    if not all(isinstance(value, str) and value for value in
               (source_path, bucket, key)):
        return {"ok": False, "error": "missing source, bucket, or key"}
    if not isinstance(part_size, int) or part_size < MIN_S3_PART_SIZE:
        return {"ok": False, "error": "part size must be at least 5 MiB"}
    pointer_path = f"{source_path}{S3_POINTER_SUFFIX}"
    if os.path.exists(pointer_path):
        return {"ok": False, "error": f"already archived: pointer exists at {pointer_path}"}
    try:
        handle = open(source_path, "rb")
    except OSError as exc:
        return {"ok": False, "error": f"source unavailable: {exc}"}
    with handle:
        try:
            identity = _file_identity(os.fstat(handle.fileno()))
            source_size = identity[1]
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
            handle.seek(0)
        except OSError as exc:
            return {"ok": False, "error": f"source unavailable: {exc}"}

        try:
            existing = await client.head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            if _is_s3_not_found(exc):
                existing = None
            else:
                return {
                    "ok": False,
                    "error": f"remote object existence unverifiable for s3://{bucket}/{key}: {exc}; "
                             "archives are immutable, refusing to overwrite",
                }
        if existing:
            existing_digest = (existing.get("Metadata") or {}).get("sha256")
            if existing_digest != digest or existing.get("ContentLength") != source_size:
                return {"ok": False,
                        "error": f"remote object exists at s3://{bucket}/{key} with different content; "
                                 "archives are immutable, refusing to overwrite"}

        upload_id = None
        try:
            created = await client.create_multipart_upload(
                Bucket=bucket, Key=key, Metadata={"sha256": digest})
            upload_id = created["UploadId"]
            parts = []
            part_md5s = []
            uploaded_digest = hashlib.sha256()
            part_number = 1
            while payload := handle.read(part_size):
                uploaded_digest.update(payload)
                local_md5 = hashlib.md5(payload)
                uploaded = await client.upload_part(
                    Bucket=bucket, Key=key, UploadId=upload_id,
                    PartNumber=part_number, Body=io.BytesIO(payload))
                part_etag = str(uploaded.get("ETag", "")).strip('"')
                if part_etag != local_md5.hexdigest():
                    raise RuntimeError(
                        f"part {part_number} stored bytes differ from source "
                        f"(etag {part_etag!r} != md5 {local_md5.hexdigest()!r})")
                part_md5s.append(local_md5.digest())
                parts.append({"PartNumber": part_number, "ETag": uploaded["ETag"]})
                part_number += 1
            if uploaded_digest.hexdigest() != digest:
                raise RuntimeError("source changed while uploading")
            expected_etag = _multipart_etag(part_md5s)
            completed = await client.complete_multipart_upload(
                Bucket=bucket, Key=key, UploadId=upload_id,
                MultipartUpload={"Parts": parts})
            completed_etag = str(completed.get("ETag", "")).strip('"')
            remote = await client.head_object(Bucket=bucket, Key=key)
            remote_etag = str(remote.get("ETag", "")).strip('"')
            remote_digest = (remote.get("Metadata") or {}).get("sha256")
            if (remote.get("ContentLength") != source_size
                    or completed_etag != expected_etag or remote_etag != expected_etag
                    or remote_digest != digest):
                return {"ok": False, "error": "remote checksum verification failed"}
        except Exception as exc:
            abort_error = None
            if upload_id is not None:
                try:
                    await client.abort_multipart_upload(
                        Bucket=bucket, Key=key, UploadId=upload_id)
                except Exception as abort_exc:
                    abort_error = f"{type(abort_exc).__name__}: {abort_exc}"
            error_msg = f"upload failed: {type(exc).__name__}: {exc}"
            if abort_error:
                error_msg += f" (abort failed: {abort_error})"
            return {"ok": False, "error": error_msg}

        try:
            now_fd = _file_identity(os.fstat(handle.fileno()))
            now_path = _file_identity(os.stat(source_path))
        except OSError as exc:
            return {"ok": False, "error": f"source changed after upload: {exc}"}
        if now_fd != identity or now_path != identity:
            return {"ok": False,
                    "error": "source changed after upload (identity mismatch); local file kept, "
                             f"archive at s3://{bucket}/{key} left in place"}
        pointer = {"bucket": bucket, "key": key, "etag": expected_etag,
                   "sha256": digest}
        try:
            with open(pointer_path, "x", encoding="utf-8") as pointer_handle:
                json.dump(pointer, pointer_handle, sort_keys=True)
        except OSError as exc:
            return {"ok": False, "error": f"pointer replacement failed: {exc}"}
        try:
            if _file_identity(os.stat(source_path)) != identity:
                os.unlink(pointer_path)
                return {"ok": False, "error": "source changed after upload (identity mismatch); local file kept"}
            os.unlink(source_path)
        except OSError as exc:
            return {"ok": False, "error": f"pointer replacement failed: {exc}"}
    return {"ok": True, "action": "archived_to_s3_async", "bytes_moved": source_size,
            "dest_path": f"s3://{bucket}/{key}", "etag": expected_etag}


def _file_identity(st: os.stat_result) -> tuple:
    return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_dev)


def _multipart_etag(part_md5_digests: list) -> str:
    """S3/MinIO multipart ETag: MD5 over the concatenated raw part MD5s, then
    '-<part count>'."""
    return hashlib.md5(b"".join(part_md5_digests)).hexdigest() + f"-{len(part_md5_digests)}"


def _run_async(coro):
    """Execute coroutine safely whether an event loop is active or not."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _s3_client_from_config(cfg: dict):
    """Return the configured S3 client without importing an optional client at boot."""
    client = cfg.get("storage_tier_s3_client")
    if client is not None:
        return client, None
    endpoint = cfg.get("storage_tier_s3_endpoint")
    if endpoint and not cfg.get("storage_tier_allow_private_hosts", False):
        from urllib.parse import urlparse
        from bulk_downloader.provider_resolve_impl._common import _is_safe_public_host, SSRFBlocked

        parsed = urlparse(endpoint)
        host = parsed.hostname or ""
        ok, reason = _is_safe_public_host(host)
        if not ok:
            return None, f"SSRFBlocked: endpoint_url '{endpoint}' rejected by SSRF guard: {reason}"
    try:
        import boto3
    except ImportError:
        return None, "S3 client unavailable (install boto3 or configure a client)"
    options = {}
    for config_name, option_name in (
        ("storage_tier_s3_endpoint", "endpoint_url"),
        ("storage_tier_s3_access_key", "aws_access_key_id"),
        ("storage_tier_s3_secret_key", "aws_secret_access_key"),
        ("storage_tier_s3_region", "region_name"),
    ):
        value = cfg.get(config_name)
        if isinstance(value, str) and value:
            options[option_name] = value
    return boto3.client("s3", **options), None


def _s3_object_key(source_path: str, source_root: str, prefix: str) -> str:
    """Derive an object key without allowing a source path to escape its root."""
    try:
        relative = Path(source_path).resolve().relative_to(
            Path(source_root).resolve()).as_posix()
    except ValueError:
        relative = Path(source_path).name
    return "/".join(piece for piece in (prefix.strip("/"), relative) if piece)


def is_eligible(file_path: str, age_days: int,
                  min_size_bytes: int = 0) -> bool:
    """True if the file at `file_path` exists, is at least
    `age_days` old (by mtime), and meets the min-size threshold.

    Defensive: any error checking the file (permission, disappeared
    between calls, weird filesystem) returns False — we'd rather
    skip than crash."""
    if not file_path or not isinstance(file_path, str):
        return False
    try:
        st = os.stat(file_path)
    except (FileNotFoundError, PermissionError, OSError):
        return False
    if min_size_bytes > 0 and st.st_size < min_size_bytes:
        return False
    age_s = time.time() - st.st_mtime
    return age_s >= (age_days * 86400)


def plan_destination(source_path: str, source_root: str,
                       dest_root: str) -> Optional[str]:
    """Compute the destination path for a file being migrated.

    Mirrors the relative subpath of `source_path` (relative to
    `source_root`) under `dest_root`. If the source isn't under
    the configured source_root, returns None (we don't want to
    accidentally relocate a file from somewhere unexpected).

    Example:
      source_path = "E:\\Wow\\Episodes\\ep01.mp4"
      source_root = "E:\\Wow"
      dest_root   = "Z:\\Cold\\Wow"
      → "Z:\\Cold\\Wow\\Episodes\\ep01.mp4"
    """
    if not (source_path and source_root and dest_root):
        return None
    if not all(isinstance(s, str) for s in (source_path, source_root, dest_root)):
        return None
    try:
        src = Path(source_path).resolve()
        root = Path(source_root).resolve()
        try:
            rel = src.relative_to(root)
        except ValueError:
            # AUDIT v3.43.46: source_path is not under source_root.
            # Previously the fallback was `rel = src.name`, which
            # would happily relocate /etc/passwd to dest_root/passwd
            # given a corrupted config. Now we refuse the fallback
            # for absolute or special paths. The legitimate case
            # (user moved files manually) still works because the
            # filename is the basename of the actual on-disk file.
            name = src.name
            # Reject obviously dangerous filenames
            if (not name
                  or name in (".", "..")
                  or "/" in name
                  or "\\" in name
                  or "\x00" in name
                  or len(name) > 255):
                return None
            rel = name
        return str(Path(dest_root) / rel)
    except (OSError, ValueError):
        return None


def migrate_file(source_path: str, dest_path: str,
                   mode: str = "move",
                   dry_run: bool = False) -> dict:
    """Move a single file from source_path to dest_path.

    Returns: {ok, bytes_moved, dest_path, error?, action}

    `mode`: one of
      - "move"               — straight shutil.move
      - "symlink_after_move" — after move, create symlink at old path
      - "dry_run"            — log intent, return ok=True with action='would_move'

    If `dry_run=True` (or mode='dry_run'), no filesystem change.
    """
    if not (source_path and dest_path):
        return {"ok": False, "error": "missing source or dest"}
    if mode == "dry_run":
        dry_run = True
    if dry_run:
        try:
            size = os.path.getsize(source_path) if os.path.exists(source_path) else 0
        except OSError:
            size = 0
        return {
            "ok": True,
            "action": "would_move",
            "bytes_moved": size,
            "dest_path": dest_path,
        }
    try:
        size = os.path.getsize(source_path)
    except OSError as e:
        return {"ok": False, "error": f"source vanished: {e}"}
    # Create parent dirs
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"mkdir failed: {e}"}
    # If destination already exists, refuse — don't silently overwrite
    if os.path.exists(dest_path):
        return {"ok": False, "error": "destination already exists",
                "dest_path": dest_path}
    # AUDIT v3.43.46: between the exists() check above and the move
    # below there's a small TOCTOU window where another process could
    # create dest_path; shutil.move would then silently overwrite.
    # Mitigation: pre-create dest_path as an exclusive lockfile via
    # O_EXCL, then retain that directory entry until shutil.move replaces
    # it or writes through it during a cross-volume copy fallback.
    # The exclusive open is the audit defense: if another process
    # races us, the open() raises FileExistsError and we abort.
    try:
        fd = os.open(dest_path,
                       os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    except FileExistsError:
        return {"ok": False,
                "error": "destination raced (another writer); skipped",
                "dest_path": dest_path}
    except OSError as e:
        # On Windows the path may already be reserved; treat as a
        # standard create error and fall through to letting move
        # raise the real problem
        return {"ok": False,
                "error": f"dest pre-create failed: {e}",
                "dest_path": dest_path}
    # Perform the move while the exclusive placeholder still owns the
    # destination pathname. shutil.move replaces it on a same-filesystem
    # rename and writes through it on its cross-filesystem copy fallback.
    # Removing it first would reopen the TOCTOU window guarded above.
    try:
        shutil.move(source_path, dest_path)
    except (OSError, shutil.Error) as e:
        # Best-effort cleanup of placeholder if move failed
        try:
            if os.path.exists(dest_path) and os.path.getsize(dest_path) == 0:
                os.remove(dest_path)
        except OSError: pass
        return {"ok": False, "error": f"move failed: {type(e).__name__}: {e}"}
    # Optional: leave a symlink so Plex/Stash/Jellyfin don't have
    # to re-scan
    if mode == "symlink_after_move":
        try:
            os.symlink(dest_path, source_path)
            return {"ok": True, "action": "moved_with_symlink",
                    "bytes_moved": size, "dest_path": dest_path}
        except OSError as e:
            # Symlink failed — file was already moved successfully.
            # Report as moved (not symlinked) rather than failing
            # the whole operation.
            return {"ok": True, "action": "moved_no_symlink",
                    "bytes_moved": size, "dest_path": dest_path,
                    "warning": f"symlink failed: {e}"}
    return {"ok": True, "action": "moved",
            "bytes_moved": size, "dest_path": dest_path}


# ── Per-site migration runner ───────────────────────────────────────

def find_candidates(site_id: str, age_days: int,
                      min_size_mb: int = 0,
                      limit: int = MAX_FILES_PER_RUN) -> list:
    """Query the SQLite queue for completed downloads older than
    `age_days` days. Returns list of {url, filename, file_size,
    ts_updated} sorted oldest first.

    Filters at the SQL level by status='done' and ts_updated
    older than cutoff. Filename emptiness check is done in
    Python (the DB has empty-string for filename in some legacy
    rows).

    Defensive: non-numeric age_days / min_size_mb default rather
    than crash — a corrupted cfg shouldn't break the sweep for
    other sites.
    """
    try:
        from .db import db_conn
    except Exception:
        return []
    if not isinstance(site_id, str) or not site_id:
        return []
    try:
        age_int = int(age_days)
        if age_int < 1:
            age_int = 1
    except (TypeError, ValueError):
        age_int = 30
    try:
        min_size_int = int(min_size_mb)
        if min_size_int < 0:
            min_size_int = 0
    except (TypeError, ValueError):
        min_size_int = 0
    cutoff_ts = time.time() - (age_int * 86400)
    # ts_updated is a TEXT column (UTC ISO format — written via
    # SQLite strftime('%Y-%m-%dT%H:%M:%S','now') which is UTC),
    # so we must build the cutoff in UTC too. Using time.localtime
    # here was a bug: on a non-UTC host the comparison was off by
    # the host's UTC offset, causing files to be missed or
    # extra-included by the migration sweep. See LESSONS_LEARNED A2.
    cutoff_iso = time.strftime("%Y-%m-%dT%H:%M:%S",
                                  time.gmtime(cutoff_ts))
    min_size_bytes = min_size_int * 1024 * 1024
    out = []
    try:
        with db_conn() as cx:
            cur = cx.execute("""
                SELECT url, filename, file_size, ts_updated
                FROM queue
                WHERE site_id = ?
                  AND status = 'done'
                  AND ts_updated < ?
                ORDER BY ts_updated ASC
                LIMIT ?
            """, (site_id, cutoff_iso, limit))
            for row in cur.fetchall():
                fn = row[1] or ""
                fs = int(row[2] or 0)
                if not fn:
                    continue  # legacy row, no filename to migrate
                if min_size_bytes > 0 and fs < min_size_bytes:
                    continue
                out.append({
                    "url": row[0],
                    "filename": fn,
                    "file_size": fs,
                    "ts_updated": row[3],
                })
    except Exception:
        return []
    return out


def run_site_migration(site_id: str, cfg: dict,
                         download_dir: str) -> dict:
    """Run one migration pass for a site. Returns summary:

      {
        ok, migrated_count, bytes_freed, skipped_count,
        errors: [...], dry_run: bool
      }
    """
    if not isinstance(cfg, dict):
        return {"ok": False, "error": "bad config"}
    if not cfg.get("storage_tier_enabled"):
        return {"ok": False, "error": "feature disabled",
                "migrated_count": 0}
    mode = (cfg.get("storage_tier_mode") or "move").strip().lower()
    if mode not in ("move", "symlink_after_move", "dry_run", "s3", "s3_async"):
        mode = "move"
    is_s3 = mode in ("s3", "s3_async")
    is_async_s3 = (mode == "s3_async" or (mode == "s3" and bool(cfg.get("storage_tier_s3_async"))))
    dest_root = (cfg.get("storage_tier_dir") or "").strip()
    if not is_s3 and not dest_root:
        return {"ok": False, "error": "storage_tier_dir not set",
                "migrated_count": 0}
    if not is_s3 and not os.path.isdir(dest_root):
        return {"ok": False, "error": f"dest not a directory: {dest_root}",
                "migrated_count": 0}
    s3_bucket = (cfg.get("storage_tier_s3_bucket") or "").strip()
    s3_client = None
    async_s3_client = None
    if is_s3:
        if not s3_bucket:
            return {"ok": False, "error": "storage_tier_s3_bucket not set",
                    "migrated_count": 0}
        if is_async_s3:
            from .async_object_storage import get_async_object_storage_client
            async_s3_client = cfg.get("storage_tier_async_client") or get_async_object_storage_client(cfg)
        else:
            s3_client, s3_error = _s3_client_from_config(cfg)
            if s3_error:
                return {"ok": False, "error": s3_error, "migrated_count": 0}
    try:
        age_days = max(1, int(cfg.get("storage_tier_age_days") or 30))
    except (TypeError, ValueError):
        age_days = 30
    try:
        min_size_mb = max(0, int(cfg.get("storage_tier_min_size_mb") or 0))
    except (TypeError, ValueError):
        min_size_mb = 0
    candidates = find_candidates(site_id, age_days, min_size_mb)
    summary = {
        "ok": True,
        "migrated_count": 0,
        "bytes_freed": 0,
        "skipped_count": 0,
        "errors": [],
        "dry_run": (mode == "dry_run"),
        "mode": mode,
        "age_days": age_days,
        "min_size_mb": min_size_mb,
    }
    for cand in candidates:
        # Reconstruct the on-disk path from download_dir + filename
        source = os.path.join(download_dir, cand["filename"])
        # E4: an archived stub (the pointer itself, or media that already has a
        # pointer beside it) is never a migration candidate again.
        if source.endswith(S3_POINTER_SUFFIX) or os.path.exists(source + S3_POINTER_SUFFIX):
            summary["skipped_count"] += 1
            continue
        if not os.path.exists(source):
            summary["skipped_count"] += 1
            continue
        # mtime-based eligibility check (the SQL filter is a coarse
        # pre-filter on the DB row's ts_updated; the actual file's
        # mtime is the ground truth)
        if not is_eligible(source, age_days,
                             min_size_bytes=min_size_mb * 1024 * 1024):
            summary["skipped_count"] += 1
            continue
        if is_s3:
            key = _s3_object_key(source, download_dir,
                                 cfg.get("storage_tier_s3_prefix") or "")
            if is_async_s3:
                res = _run_async(async_s3_client.upload_file(source, s3_bucket, key))
                result = {
                    "ok": res.ok,
                    "action": res.action,
                    "bytes_moved": res.bytes_moved,
                    "dest_path": res.dest_path,
                    "etag": res.etag,
                    "error": res.error,
                }
            else:
                result = archive_to_s3(source, s3_bucket, key, s3_client)
            dest = f"{source}{S3_POINTER_SUFFIX}"
        else:
            dest = plan_destination(source, download_dir, dest_root)
            if not dest:
                summary["errors"].append(
                    f"could not plan destination for {cand['filename']}")
                continue
            result = migrate_file(source, dest, mode=mode,
                                     dry_run=(mode == "dry_run"))
        if result["ok"]:
            summary["migrated_count"] += 1
            summary["bytes_freed"] += result.get("bytes_moved", 0)
            # Update the queue row with the new path (when not dry-run)
            if mode != "dry_run":
                _update_queue_filename(site_id, cand["url"],
                                          dest, download_dir)
        else:
            summary["errors"].append(
                f"{cand['filename']}: {result.get('error')}")
            summary["skipped_count"] += 1
    return summary


async def run_site_migration_async(site_id: str, cfg: dict, download_dir: str) -> dict:
    """Async variant of run_site_migration leveraging non-blocking object storage operations."""
    async_cfg = dict(cfg)
    async_cfg["storage_tier_mode"] = "s3_async"
    async_cfg["storage_tier_s3_async"] = True
    return run_site_migration(site_id, async_cfg, download_dir)


def _update_queue_filename(site_id: str, url: str,
                              new_path: str, old_root: str):
    """After a successful migration, update the queue row's
    filename column to point at the new location. Stored as a
    path relative to a logical root would be cleaner, but the
    existing schema has filename as just the basename — so we
    store the FULL new path so the downstream consumer can
    find it. The runner's existing `_resolve_existing_file`
    will handle both absolute and relative filenames."""
    try:
        from .db import db_conn
        with db_conn() as cx:
            cx.execute("""
                UPDATE queue SET filename = ?
                WHERE site_id = ? AND url = ?
            """, (new_path, site_id, url))
            cx.commit()
    except Exception:
        # Migration succeeded on disk; if the DB update fails the
        # file is still in the cold tier and findable via the
        # original filename's basename in either dir. Acceptable
        # degradation.
        pass


# ── Scheduler ─────────────────────────────────────────────────────────

class StorageTierScheduler:
    """Hourly daemon: walks every site with the feature enabled
    and runs a migration pass.

    Same BD_DISABLE_KEEPALIVE gate as session_keeper and
    download_window so tests opt-out cleanly."""

    def __init__(self, interval_s: int = DEFAULT_INTERVAL_S):
        self.interval_s = interval_s
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # Per-site last-run timestamps for visibility / cooldown
        self._last_runs: dict = {}
        self._last_summaries: dict = {}
        self._lock = threading.Lock()

    def start(self):
        if os.environ.get("BD_DISABLE_KEEPALIVE"):
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True,
            name="bd-storage-tier")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        """Main loop: every interval_s, walk every configured site."""
        import sys
        try:
            from . import app as _app
        except Exception as e:
            sys.stderr.write(f"  storage_tier: app import failed: {e}\n")
            return
        # On startup, wait a few seconds so the rest of the app is
        # initialized before the first sweep
        self._stop.wait(30)
        while not self._stop.is_set():
            try:
                cfg_snapshot = list(_app.s_cfg.items())
            except Exception:
                self._stop.wait(self.interval_s)
                continue
            for sid, cfg in cfg_snapshot:
                if self._stop.is_set():
                    break
                if not isinstance(cfg, dict):
                    continue
                if not cfg.get("storage_tier_enabled"):
                    continue
                dl_dir = (cfg.get("download_dir") or "").strip()
                if not dl_dir:
                    continue
                try:
                    summary = run_site_migration(sid, cfg, dl_dir)
                except Exception as e:
                    summary = {"ok": False,
                                "error": f"{type(e).__name__}: {e}",
                                "migrated_count": 0}
                with self._lock:
                    self._last_runs[sid] = time.time()
                    self._last_summaries[sid] = summary
                # Log a summary event to the runner if it's
                # active, so the user sees migration activity in
                # their event stream
                runner = _app.runners.get(sid)
                if runner and summary.get("ok"):
                    try:
                        moved = summary.get("migrated_count", 0)
                        freed_gb = summary.get("bytes_freed", 0) / (1024**3)
                        if moved > 0 or summary.get("errors"):
                            runner.log_event(
                                "storage_tier",
                                f"Tier sweep: moved {moved} file(s), "
                                f"freed {freed_gb:.2f} GB"
                                + (f", {len(summary['errors'])} errors"
                                    if summary.get("errors") else ""))
                    except Exception:
                        pass
            self._stop.wait(self.interval_s)

    def get_status(self) -> dict:
        """Snapshot of last-run state across all sites."""
        with self._lock:
            return {
                "ok": True,
                "running": self._thread is not None and self._thread.is_alive(),
                "interval_seconds": self.interval_s,
                "per_site": {
                    sid: {
                        "last_run_ts": ts,
                        "summary": self._last_summaries.get(sid, {}),
                    }
                    for sid, ts in self._last_runs.items()
                },
            }


# Module singleton
_SCHEDULER: Optional[StorageTierScheduler] = None
_SCHEDULER_LOCK = threading.Lock()


def get_scheduler() -> StorageTierScheduler:
    global _SCHEDULER
    if _SCHEDULER is None:
        with _SCHEDULER_LOCK:
            if _SCHEDULER is None:
                _SCHEDULER = StorageTierScheduler()
    return _SCHEDULER
