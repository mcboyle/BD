"""v3.43.27 regression tests — multi-segment resume.

Tests the checkpoint module (resume.py) in isolation, plus the runner
integration that wires it into the parallel HTTP downloader.

Coverage:
  - Sidecar I/O: initialize, save, load, cleanup
  - Atomic write pattern (matches v3.43.19 invariant)
  - Resume eligibility: total/url/etag/last-modified validators
  - Reconcile with disk: missing .part, smaller-than-expected, etc.
  - bytes_already_downloaded + incomplete_chunks helpers
  - Format-version mismatch handling
  - Runner integration: parallel downloader honors checkpoint
"""
from __future__ import annotations

import json
import tempfile
# [SAST 3:13pm 13 may] removed unused: import time
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_RUNNER_PY = _REPO_ROOT / "bulk_downloader" / "runner.py"


def _bd_runner_src():
    """v3.66.404: runner.py decomposed into runner_*.py mixins; aggregate the
    package so moved SiteRunner method bodies stay visible to source checks."""
    from pathlib import Path as _P
    from bulk_downloader import runner as _R
    _pd = _P(_R.__file__).parent
    return "\n".join(q.read_text(encoding="utf-8")
                     for q in [_pd / "runner.py"] + sorted(_pd.glob("runner_*.py")))
_RESUME_PY = _REPO_ROOT / "bulk_downloader" / "resume.py"


# ── Module exists + path helpers ──────────────────────────────────────

def test_resume_module_importable():
    import bulk_downloader.resume as r  # noqa: F401


def test_sidecar_path_format():
    """The sidecar must sit next to the .part file with a predictable
    name so external tools (rsync, backup scripts) can find both.

    Compares against Path-built expected values rather than literal
    "/"-style strings so the test is correct on Windows too (where
    Path renders backslashes)."""
    from bulk_downloader.resume import sidecar_path, part_path
    p = Path("/data/foo.mp4")
    assert part_path(p) == p.with_suffix(".mp4.part")
    assert sidecar_path(p) == p.with_suffix(".mp4.part.bdseg.json")
    # And the part file's name is always the final name + ".part"
    assert part_path(p).name == "foo.mp4.part"
    assert sidecar_path(p).name == "foo.mp4.part.bdseg.json"


# ── Initialize + save + load round-trip ──────────────────────────────

def test_initialize_creates_n_equal_chunks():
    """Without a HEAD result we still need to split the file into N
    chunks. Last chunk absorbs any remainder so all bytes are covered."""
    from bulk_downloader.resume import initialize, sidecar_path
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.mp4"
        cp = initialize(p, "https://example.com/test", 1000, 4)
        assert cp["format_version"] == 1
        assert cp["total"] == 1000
        assert cp["url"] == "https://example.com/test"
        # Four chunks, no gaps, no overlaps
        chunks = cp["chunks"]
        assert len(chunks) == 4
        assert chunks[0]["start"] == 0
        assert chunks[3]["end"] == 999, "last chunk must absorb remainder"
        for i, c in enumerate(chunks):
            assert c["done_bytes"] == 0
            if i > 0:
                assert c["start"] == chunks[i-1]["end"] + 1, (
                    f"gap between chunks {i-1} and {i}"
                )
        # Sidecar file was persisted
        assert sidecar_path(p).exists()


def test_save_load_round_trip():
    """A checkpoint persisted and reloaded must return the same data."""
    from bulk_downloader.resume import initialize, load
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.mp4"
        original = initialize(p, "https://example.com/test", 5000, 2,
                                etag='"abc123"',
                                last_modified="Mon, 1 Jan 2024 12:00:00 GMT")
        loaded = load(p)
        assert loaded is not None
        assert loaded["total"] == original["total"]
        assert loaded["url"] == original["url"]
        assert loaded["etag"] == '"abc123"'
        assert len(loaded["chunks"]) == len(original["chunks"])


def test_save_is_atomic():
    """v3.43.19 invariant: every state file uses .tmp + replace.
    Verify by reading the source for the pattern."""
    src = _RESUME_PY.read_text(encoding="utf-8")
    save_start = src.find("def save(")
    assert save_start > 0
    save_body = src[save_start:save_start + 1500]
    assert ".tmp" in save_body
    assert "replace" in save_body


def test_load_returns_none_for_missing():
    """Missing sidecar is the common case (fresh download). Must NOT
    raise — just return None."""
    from bulk_downloader.resume import load
    with tempfile.TemporaryDirectory() as td:
        result = load(Path(td) / "nonexistent.mp4")
        assert result is None


def test_load_returns_none_for_malformed_json():
    """A corrupt sidecar must return None, not raise."""
    from bulk_downloader.resume import load, sidecar_path
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.mp4"
        sidecar_path(p).write_text("not valid json {{{", encoding="utf-8")
        assert load(p) is None


def test_load_rejects_wrong_format_version():
    """Future-proofing: if we bump FORMAT_VERSION, old checkpoints
    must be silently abandoned (caller starts fresh) rather than
    misinterpreted."""
    from bulk_downloader.resume import load, sidecar_path
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.mp4"
        # Hand-craft an "old" checkpoint with format_version=0
        sidecar_path(p).write_text(
            json.dumps({"format_version": 0, "chunks": []}),
            encoding="utf-8")
        assert load(p) is None


# ── Resume eligibility ───────────────────────────────────────────────

def test_is_resumable_happy_path():
    """All validators match → resume."""
    from bulk_downloader.resume import is_resumable
    cp = {"format_version": 1, "total": 1000, "url": "x",
          "etag": '"abc"', "last_modified": "yesterday",
          "chunks": []}
    ok, why = is_resumable(cp, url="x", total=1000,
                            etag='"abc"', last_modified="yesterday")
    assert ok, why


def test_is_resumable_rejects_size_mismatch():
    """Different total bytes → different file → no resume."""
    from bulk_downloader.resume import is_resumable
    cp = {"format_version": 1, "total": 1000, "url": "x", "chunks": []}
    ok, why = is_resumable(cp, url="x", total=2000)
    assert not ok
    assert "size mismatch" in why.lower()


def test_is_resumable_rejects_url_mismatch():
    """Same size, different URL → don't risk it."""
    from bulk_downloader.resume import is_resumable
    cp = {"format_version": 1, "total": 1000, "url": "x", "chunks": []}
    ok, why = is_resumable(cp, url="y", total=1000)
    assert not ok
    assert "url" in why.lower()


def test_is_resumable_rejects_etag_mismatch():
    """ETag mismatch = file changed server-side. Must restart."""
    from bulk_downloader.resume import is_resumable
    cp = {"format_version": 1, "total": 1000, "url": "x",
          "etag": '"old"', "chunks": []}
    ok, why = is_resumable(cp, url="x", total=1000, etag='"new"')
    assert not ok
    assert "etag" in why.lower()


def test_is_resumable_tolerates_missing_validator():
    """If one side has ETag and the other doesn't, resume is allowed
    (we don't have enough info to detect a change, but we don't
    pessimistically refuse)."""
    from bulk_downloader.resume import is_resumable
    cp = {"format_version": 1, "total": 1000, "url": "x",
          "etag": None, "chunks": []}
    ok, _ = is_resumable(cp, url="x", total=1000, etag='"abc"')
    assert ok
    cp2 = {"format_version": 1, "total": 1000, "url": "x",
           "etag": '"abc"', "chunks": []}
    ok2, _ = is_resumable(cp2, url="x", total=1000, etag=None)
    assert ok2


# ── Helper functions ──────────────────────────────────────────────────

def test_bytes_already_downloaded():
    from bulk_downloader.resume import bytes_already_downloaded
    cp = {"chunks": [
        {"start": 0, "end": 99, "done_bytes": 100},   # complete
        {"start": 100, "end": 199, "done_bytes": 50},  # half
        {"start": 200, "end": 299, "done_bytes": 0},   # untouched
    ]}
    assert bytes_already_downloaded(cp) == 150


def test_incomplete_chunks_skips_complete():
    """Chunks where done_bytes == (end-start+1) shouldn't appear in
    the resume list — they're already done."""
    from bulk_downloader.resume import incomplete_chunks
    cp = {"chunks": [
        {"start": 0, "end": 99, "done_bytes": 100},      # complete
        {"start": 100, "end": 199, "done_bytes": 50},     # partial
        {"start": 200, "end": 299, "done_bytes": 0},      # untouched
        {"start": 300, "end": 399, "done_bytes": 100},    # complete
    ]}
    incomplete = incomplete_chunks(cp)
    assert len(incomplete) == 2
    assert incomplete[0]["start"] == 100
    assert incomplete[1]["start"] == 200


# ── update_chunk_progress + save batching ─────────────────────────────

def test_update_chunk_progress_mutates_in_place():
    """update_chunk_progress is hot-path — it must mutate the dict in
    place rather than rebuild it (cheap; no allocations)."""
    from bulk_downloader.resume import initialize, update_chunk_progress
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.mp4"
        cp = initialize(p, "x", 1000, 4)
        original_id = id(cp["chunks"][0])
        update_chunk_progress(cp, 0, 200)
        assert cp["chunks"][0]["done_bytes"] == 200
        assert id(cp["chunks"][0]) == original_id, "mutated, not replaced"


# ── reconcile_with_disk ──────────────────────────────────────────────

def test_reconcile_with_disk_rejects_missing_part():
    """If .part is gone (user manually deleted, drive wiped), we
    can't resume from a stale checkpoint."""
    from bulk_downloader.resume import reconcile_with_disk
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.mp4"
        cp = {"total": 1000, "chunks": []}
        # No .part file exists
        assert reconcile_with_disk(cp, p) is False


def test_reconcile_with_disk_rejects_short_part():
    """If .part is smaller than total, pre-allocation was incomplete
    (crash during init). Safer to restart."""
    from bulk_downloader.resume import reconcile_with_disk, part_path
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.mp4"
        pp = part_path(p)
        pp.write_bytes(b"\x00" * 500)  # less than total=1000
        cp = {"total": 1000, "chunks": []}
        assert reconcile_with_disk(cp, p) is False


def test_reconcile_with_disk_accepts_correct_part():
    from bulk_downloader.resume import reconcile_with_disk, part_path
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.mp4"
        pp = part_path(p)
        pp.write_bytes(b"\x00" * 1000)  # exactly total
        cp = {"total": 1000,
              "chunks": [{"start": 0, "end": 999, "done_bytes": 500}]}
        assert reconcile_with_disk(cp, p) is True


# ── cleanup ───────────────────────────────────────────────────────────

def test_cleanup_removes_sidecar():
    """After successful completion, the sidecar must be removed so
    the next download of this filename isn't confused by stale data."""
    from bulk_downloader.resume import initialize, cleanup, sidecar_path
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.mp4"
        initialize(p, "x", 1000, 4)
        assert sidecar_path(p).exists()
        cleanup(p)
        assert not sidecar_path(p).exists()


def test_cleanup_silent_when_already_gone():
    """Cleanup must not raise on a missing sidecar — it's a no-op."""
    from bulk_downloader.resume import cleanup
    with tempfile.TemporaryDirectory() as td:
        # No sidecar present
        cleanup(Path(td) / "nothing.mp4")  # should not raise


# ── Runner integration ────────────────────────────────────────────────

def test_parallel_downloader_imports_resume_module():
    """The retrofit in _http_download_parallel must import from
    .resume. Without this import the resume logic doesn't run."""
    src = _bd_runner_src()
    parallel_pos = src.find("def _http_download_parallel(")
    assert parallel_pos > 0
    body = src[parallel_pos:parallel_pos + 10000]
    assert "from . import resume as _resume" in body


def test_parallel_downloader_uses_head_probe():
    """Resume validation requires ETag/Last-Modified from a HEAD probe
    before starting the download."""
    src = _bd_runner_src()
    parallel_pos = src.find("def _http_download_parallel(")
    body = src[parallel_pos:parallel_pos + 10000]
    assert "head_probe" in body


def test_parallel_downloader_checks_is_resumable():
    """The validator check must run before the download starts.
    Without this, a stale checkpoint would corrupt the download."""
    src = _bd_runner_src()
    parallel_pos = src.find("def _http_download_parallel(")
    body = src[parallel_pos:parallel_pos + 10000]
    assert "is_resumable" in body
    assert "reconcile_with_disk" in body


def test_parallel_downloader_saves_checkpoint_on_failure():
    """On worker error, the checkpoint MUST be saved before raising —
    otherwise partial progress is lost. This is the core resume
    guarantee."""
    src = _bd_runner_src()
    parallel_pos = src.find("def _http_download_parallel(")
    body = src[parallel_pos:parallel_pos + 15000]
    # Find the error-handling block specifically
    err_block_pos = body.find("worker_errors")
    assert err_block_pos > 0
    # After the worker error detection, we should see a save() call
    rest = body[err_block_pos:]
    assert "_resume.save(final_path, checkpoint)" in rest, (
        "Checkpoint must be saved before raising on worker error"
    )


def test_parallel_downloader_cleans_up_checkpoint_on_success():
    """The sidecar must be removed after successful rename, otherwise
    the next download of the same filename would see a stale
    'complete' checkpoint."""
    src = _bd_runner_src()
    parallel_pos = src.find("def _http_download_parallel(")
    body = src[parallel_pos:parallel_pos + 15000]
    # cleanup must happen after the atomic rename, before the return
    assert "_resume.cleanup(final_path)" in body


def test_parallel_downloader_worker_seeks_past_resumed_bytes():
    """The worker's seek position must include resume_offset[idx],
    otherwise resumed chunks overwrite their own completed bytes."""
    src = _bd_runner_src()
    parallel_pos = src.find("def _http_download_parallel(")
    body = src[parallel_pos:parallel_pos + 15000]
    # The retrofit defines effective_start = byte_start + resume_offset[idx]
    assert "effective_start = byte_start + resume_offset[idx]" in body
    # And uses it in the Range header
    assert "bytes={effective_start}-" in body


def test_parallel_downloader_periodic_checkpoint_save():
    """Every 5MB of progress, a save() must fire so a crash loses
    bounded work."""
    src = _bd_runner_src()
    parallel_pos = src.find("def _http_download_parallel(")
    body = src[parallel_pos:parallel_pos + 15000]
    assert "CHECKPOINT_SAVE_EVERY_BYTES" in body
    # 5MB = 5 * 1024 * 1024
    assert "5 * 1024 * 1024" in body


def test_parallel_downloader_resume_offset_added_to_total():
    """The user-visible progress in the UI must include the resumed
    bytes, not just the bytes downloaded in this run. Otherwise the
    user thinks the download is starting fresh when it isn't."""
    src = _bd_runner_src()
    parallel_pos = src.find("def _http_download_parallel(")
    body = src[parallel_pos:parallel_pos + 15000]
    assert "pre_resume_total" in body
    # The variable is summed into total_bytes
    assert "total_bytes = pre_resume_total + this_run" in body
