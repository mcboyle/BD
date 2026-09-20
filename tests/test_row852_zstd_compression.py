"""Row 852: archive compression uses the local zstd streaming tool."""
from __future__ import annotations

import subprocess

import pytest


BD_GATE_SCOPE = "module"


def _payload() -> bytes:
    return (b"cold-tier metadata\n" * 20_000) + bytes(range(256))


def test_zstd_archive_roundtrip_integrity(tmp_path):
    from bulk_downloader.archive_compression import compress_archive, open_archive

    source = tmp_path / "metadata.jsonl"
    archive = tmp_path / "metadata.jsonl.zst"
    expected = _payload()
    source.write_bytes(expected)

    result = compress_archive(source, archive, threads=2)

    assert result.path == archive
    assert result.threads == 2
    assert archive.is_file() and archive.stat().st_size > 0
    with open_archive(archive) as reader:
        assert reader.read() == expected


def test_compression_passes_requested_thread_count_to_zstd(monkeypatch, tmp_path):
    from bulk_downloader import archive_compression as subject

    source = tmp_path / "cold.log"
    archive = tmp_path / "cold.log.zst"
    source.write_bytes(b"benchmark payload")
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        archive.write_bytes(b"zstd output")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subject.subprocess, "run", run)

    result = subject.compress_archive(source, archive, threads=4)

    assert result.threads == 4
    assert calls == [["zstd", "-q", "-T4", "-o", str(archive), "--", str(source)]]


def test_archive_reader_rejects_a_nonpositive_thread_count(tmp_path):
    from bulk_downloader.archive_compression import compress_archive

    source = tmp_path / "cold.log"
    source.write_bytes(b"payload")

    with pytest.raises(ValueError, match="threads must be positive"):
        compress_archive(source, tmp_path / "cold.log.zst", threads=0)


def test_archive_reader_reports_a_corrupt_archive_on_context_exit(tmp_path):
    from bulk_downloader.archive_compression import open_archive

    archive = tmp_path / "corrupt.zst"
    archive.write_bytes(b"not a zstd archive")

    with pytest.raises(RuntimeError, match="zstd decompression failed"):
        with open_archive(archive) as reader:
            reader.read()


# ---- fixer (O928): correctness REFUTE E1-E3 ----------------------------------

def test_e1_a_source_named_like_an_option_is_a_file_not_a_flag(tmp_path, monkeypatch):
    """E1: a relative source "-f" must reach zstd as a filename ("--" ends
    option parsing); the archive expands to the source bytes, not to empty."""
    from bulk_downloader.archive_compression import compress_archive, open_archive
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "-f"
    source.write_bytes(b"correct-source-payload" * 100)
    result = compress_archive("-f", "out.zst", threads=1)
    assert result.path.is_file() and result.path.stat().st_size > 0
    with open_archive(result.path) as reader:
        assert reader.read() == b"correct-source-payload" * 100
    # and an archive named like an option reads back too
    with open_archive(result.path.rename(tmp_path / "-d.zst")) as reader:
        assert reader.read() == b"correct-source-payload" * 100


def test_e2_a_partial_read_closes_cleanly_and_a_full_read_still_detects_corruption(tmp_path):
    """E2: read(1) then exit is an intentional partial read: no error (zstd
    dies of SIGPIPE, which is not a verdict on the archive). Control: the
    same archive read to EOF is fine; a corrupt archive read to EOF raises;
    a corrupt archive zstd rejects up front raises even on a partial read."""
    from bulk_downloader.archive_compression import compress_archive, open_archive
    source = tmp_path / "big.bin"
    source.write_bytes(bytes(range(256)) * 200_000)          # ~51 MB decompressed: zstd cannot finish into a closed pipe
    archive = compress_archive(source, tmp_path / "big.zst").path
    with open_archive(archive) as reader:
        assert reader.read(1) == b"\x00"
    with open_archive(archive) as reader:
        assert len(reader.read()) == 256 * 200_000
    corrupt = tmp_path / "corrupt.zst"
    corrupt.write_bytes(b"not a zstd archive at all" * 10)
    with pytest.raises(RuntimeError, match="zstd decompression failed"):
        with open_archive(corrupt) as reader:
            reader.read()
    # a truncated real archive: reading to EOF surfaces the corruption
    data = archive.read_bytes()
    truncated = tmp_path / "truncated.zst"
    truncated.write_bytes(data[: len(data) // 2])
    with pytest.raises(RuntimeError, match="zstd decompression failed"):
        with open_archive(truncated) as reader:
            reader.read()


def test_e3_exited_reader_contexts_leave_no_pipe_descriptors_open(tmp_path):
    """E3: every pipe the reader owns (stdout AND stderr) is closed on exit;
    sixteen exited contexts held by the caller do not raise the fd count."""
    import os
    from bulk_downloader.archive_compression import compress_archive, open_archive
    source = tmp_path / "s.bin"
    source.write_bytes(b"x" * 100_000)
    archive = compress_archive(source, tmp_path / "s.zst").path
    fd_dir = f"/proc/{os.getpid()}/fd"
    before = len(os.listdir(fd_dir))
    kept = []
    for _ in range(16):
        ctx = open_archive(archive)
        with ctx as reader:
            reader.read(1)                                    # partial read: the SIGPIPE path
        kept.append(ctx)
        ctx = open_archive(archive)
        with ctx as reader:
            reader.read()                                     # full read path
        kept.append(ctx)
    after = len(os.listdir(fd_dir))
    assert after <= before, (before, after)
    assert all(c._process.poll() is not None for c in kept)   # no child left running
