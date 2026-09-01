"""v3.43.27: per-file resume checkpoint.

Tracks chunk-level completion state for parallel HTTP downloads so a
crash or restart picks up exactly where it left off. Without this, a
stop mid-download of a 5GB 8K file means redownloading from byte 0.

Disk layout per in-progress file:
  <final_path>.part           — the partial bytes (sparse-allocated at
                                 total size; chunks written at offsets)
  <final_path>.bdseg.json     — the checkpoint sidecar (this module)
  <final_path>.part.meta      — Phase 62 sequential-resume validators
                                 (kept for compatibility; ignored when
                                 a .bdseg.json is present and valid)

The sidecar JSON shape:
  {
    "format_version": 1,
    "url": "https://...",
    "total": 1234567890,
    "etag": "...",                    optional, from HEAD
    "last_modified": "...",           optional, from HEAD
    "started_at": 1715515200.0,
    "updated_at": 1715515300.0,
    "chunks": [
      {"start": 0,        "end": 123456789, "done_bytes": 123456789},
      {"start": 123456790, "end": 246913579, "done_bytes": 50000000},
      ...
    ]
  }

`done_bytes` is the count of bytes successfully written FROM THIS CHUNK,
not the absolute file position. A chunk is complete when
`done_bytes == (end - start + 1)`.

Atomic writes via `.tmp + replace` (same invariant as v3.43.19).

Resume eligibility checks (in order):
  1. .bdseg.json exists and parses
  2. format_version matches what this code supports
  3. url matches (different URL = different file, abandon)
  4. total matches what HEAD now reports
  5. etag matches (when both sides have it)
  6. last_modified matches (when both sides have it)

If any check fails, the resume is abandoned: both .part and .bdseg.json
are deleted and the download restarts from byte 0. Better to redownload
than to corrupt a file.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


FORMAT_VERSION = 1


def sidecar_path(final_path: str | Path) -> Path:
    """Compute the sidecar location from the final-file path. Sidecar
    sits next to the .part so it travels with the in-progress data."""
    final_path = Path(final_path)
    return final_path.with_suffix(final_path.suffix + ".part.bdseg.json")


def part_path(final_path: str | Path) -> Path:
    """Companion to sidecar_path() — the partial-bytes file."""
    final_path = Path(final_path)
    return final_path.with_suffix(final_path.suffix + ".part")


# ── Read ──────────────────────────────────────────────────────────────

def load(final_path: str | Path) -> dict | None:
    """Read the sidecar. Returns None on any error (missing, malformed,
    permission denied) — callers treat None as 'no checkpoint, start
    fresh'. Errors are silent because they're expected on the first
    download of each file."""
    p = sidecar_path(final_path)
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        if data.get("format_version") != FORMAT_VERSION:
            return None
        # Minimal structural validation; defer detailed checks to
        # is_resumable() so callers can log specific reasons.
        if not isinstance(data.get("chunks"), list):
            return None
        return data
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def is_resumable(checkpoint: dict, url: str, total: int,
                  etag: str | None = None,
                  last_modified: str | None = None) -> tuple[bool, str]:
    """Decide whether `checkpoint` can be resumed against the current
    request. Returns (ok, reason). reason is a human-readable string
    for the event log when ok=False.

    Checks (most-likely-mismatch first):
      - total bytes match (different size = different file)
      - url matches
      - etag matches when both sides have one
      - last_modified matches when both sides have one
    """
    if not isinstance(checkpoint, dict):
        return False, "checkpoint not a dict"
    if checkpoint.get("total") != total:
        return False, (
            f"size mismatch: checkpoint={checkpoint.get('total')} "
            f"current={total}"
        )
    if checkpoint.get("url") and checkpoint["url"] != url:
        return False, "url mismatch"
    # Validators — only compared when BOTH sides have them. A missing
    # validator on either side is not a failure (some CDNs don't send
    # ETag at all; some clients don't capture it).
    cp_etag = checkpoint.get("etag")
    if cp_etag and etag and cp_etag != etag:
        return False, f"etag mismatch: {cp_etag} vs {etag}"
    cp_lm = checkpoint.get("last_modified")
    if cp_lm and last_modified and cp_lm != last_modified:
        return False, "last-modified mismatch"
    return True, "ok"


def bytes_already_downloaded(checkpoint: dict) -> int:
    """Sum of done_bytes across all chunks. Used for progress reporting
    immediately after resume so the UI shows the correct percentage."""
    if not isinstance(checkpoint, dict):
        return 0
    chunks = checkpoint.get("chunks") or []
    return sum(int(c.get("done_bytes", 0)) for c in chunks
                if isinstance(c, dict))


def incomplete_chunks(checkpoint: dict) -> list[dict]:
    """Return the chunks that still need work. Caller submits these to
    the worker pool; chunks already at done_bytes == (end-start+1)
    are skipped entirely."""
    if not isinstance(checkpoint, dict):
        return []
    out = []
    for c in checkpoint.get("chunks") or []:
        if not isinstance(c, dict):
            continue
        try:
            start = int(c["start"])
            end = int(c["end"])
            done = int(c.get("done_bytes", 0))
        except (KeyError, ValueError, TypeError):
            continue
        expected = end - start + 1
        if done < expected:
            out.append(c)
    return out


# ── Write ─────────────────────────────────────────────────────────────

def initialize(final_path: str | Path, url: str, total: int,
                n_chunks: int,
                etag: str | None = None,
                last_modified: str | None = None) -> dict:
    """Create a fresh checkpoint and persist it. Used when starting a
    new download (no resumable checkpoint). Returns the in-memory
    dict that the caller mutates as chunks progress; call save() to
    persist updates."""
    if n_chunks <= 0:
        n_chunks = 1
    if total <= 0:
        # Caller shouldn't reach this path, but be defensive — a single
        # zero-length chunk is at least internally consistent.
        chunks = [{"start": 0, "end": -1, "done_bytes": 0}]
    else:
        slice_size = total // n_chunks
        chunks = []
        for i in range(n_chunks):
            start = i * slice_size
            end = (total - 1) if i == n_chunks - 1 else (start + slice_size - 1)
            chunks.append({"start": start, "end": end, "done_bytes": 0})
    now = time.time()
    cp = {
        "format_version": FORMAT_VERSION,
        "url": url,
        "total": total,
        "etag": etag,
        "last_modified": last_modified,
        "started_at": now,
        "updated_at": now,
        "chunks": chunks,
    }
    save(final_path, cp)
    return cp


def update_chunk_progress(checkpoint: dict, chunk_idx: int,
                           done_bytes: int):
    """Mutate the in-memory checkpoint to record progress for one chunk.
    Doesn't auto-persist — the caller batches a save() periodically
    (e.g. every 5MB) to avoid hammering the disk. The chunk completion
    flag is implicit: done_bytes == (end - start + 1)."""
    if not isinstance(checkpoint, dict):
        return
    chunks = checkpoint.get("chunks") or []
    if 0 <= chunk_idx < len(chunks):
        chunks[chunk_idx]["done_bytes"] = int(done_bytes)
        checkpoint["updated_at"] = time.time()


def save(final_path: str | Path, checkpoint: dict) -> bool:
    """Persist the checkpoint atomically. Returns True on success.
    Failures are logged-and-suppressed by the caller — a failed save
    means the resume point is slightly out of date but the download
    can still complete (worst case: a few MB redownloaded on restart).
    """
    if not isinstance(checkpoint, dict):
        return False
    p = sidecar_path(final_path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(checkpoint, f)
        # On Windows, replace() handles the atomic rename-over-existing
        # case (which os.rename historically didn't).
        tmp.replace(p)
        return True
    except (OSError, TypeError, ValueError):
        return False


def cleanup(final_path: str | Path) -> bool:
    """Remove the sidecar and report whether this call removed a file.

    A caller that presents an operator with a cleanup count needs an effect,
    not merely an attempted unlink. Existing completion callers deliberately
    ignore the return value, preserving their best-effort behaviour.
    """
    target = sidecar_path(final_path)
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


# ── HEAD probe helper ────────────────────────────────────────────────

def head_probe(client, url: str, headers: dict | None = None,
                cookies: dict | None = None,
                timeout: float = 15.0) -> dict:
    """Run a HEAD request to learn:
      - Content-Length (for chunk sizing)
      - Accept-Ranges (capability check — Range support required for
        parallel)
      - ETag and/or Last-Modified (for resume validation)

    Returns: {
      "total": int,
      "supports_range": bool,
      "etag": str | None,
      "last_modified": str | None,
      "status": int,
      "error": str | None,
    }

    If HEAD fails (some CDNs respond 405), the caller falls back to a
    single-stream download — no parallelism, no resume.
    """
    result = {
        "total": 0, "supports_range": False,
        "etag": None, "last_modified": None,
        "status": 0, "error": None,
    }
    try:
        r = client.head(url, headers=headers or {}, cookies=cookies or {},
                         timeout=timeout, follow_redirects=True)
        result["status"] = r.status_code
        if r.status_code in (200, 206):
            try:
                result["total"] = int(r.headers.get("content-length") or 0)
            except (ValueError, TypeError):
                result["total"] = 0
            accept_ranges = (r.headers.get("accept-ranges") or "").lower()
            result["supports_range"] = (accept_ranges == "bytes")
            # ETag often quoted ("abc123") — preserve as-is so equality
            # checks work without normalization
            result["etag"] = r.headers.get("etag")
            result["last_modified"] = r.headers.get("last-modified")
        else:
            result["error"] = f"HEAD HTTP {r.status_code}"
    except Exception as e:
        result["error"] = f"HEAD failed: {type(e).__name__}: {e}"
    return result


# ── Sanity: byte-count vs disk reality ────────────────────────────────

def reconcile_with_disk(checkpoint: dict, final_path: str | Path) -> bool:
    """After loading a checkpoint on restart, verify the .part file
    on disk is consistent with what the checkpoint says. Returns True
    if reconciliation succeeded (resume is safe to proceed); False if
    not (caller should abandon and restart).

    Sanity checks:
      - .part file exists
      - .part file size >= total (it's pre-allocated sparse, so this
        is the expected case; smaller means corrupt or different file)
      - Sum of done_bytes <= total

    We DON'T verify chunk content (no hash check on partial data) —
    too expensive, and Range requests are byte-precise so corruption
    within a completed chunk would be a Range bug, not a checkpoint bug.
    """
    if not isinstance(checkpoint, dict):
        return False
    pp = part_path(final_path)
    if not pp.exists():
        return False
    try:
        size = pp.stat().st_size
    except OSError:
        return False
    total = int(checkpoint.get("total") or 0)
    if size < total:
        # Pre-allocation was incomplete — could be a crash during init.
        # Safer to restart than to assume the chunks line up correctly.
        return False
    done = bytes_already_downloaded(checkpoint)
    if done > total:
        # Impossible math — checkpoint is corrupt
        return False
    return True
