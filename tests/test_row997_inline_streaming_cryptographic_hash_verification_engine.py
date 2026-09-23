"""Row 997 -- Inline Streaming Cryptographic Hash Verification Engine.

WHY THIS GATE EXISTS (Row 997, PLAN-2040):
Validating download integrity after file completion requires expensive secondary
disk reads, introducing I/O bottlenecks on high-throughput networks and NVMe/SSD wear.
The inline streaming cryptographic hash verification engine computes one or more
cryptographic checksums (SHA-256, SHA-512, MD5, BLAKE2b) incrementally as bytes flow
through reader/writer streams, enforcing constant-time integrity verification with
zero secondary I/O passes.

WHAT THIS GATE ASSERTS:
1. Positive control: existing integrity verification formats and magic numbers pass at base.
2. Capability premise: integrity module provides verify_stream_hash and verify_file_hash_streaming.
3. Module bulk_downloader.streaming_hash exists and exports engine and stream wrappers.
4. StreamingHashEngine incrementally accumulates digests for multiple algorithms simultaneously.
5. Verification results report validity, digests, throughput, and matched/mismatched algorithms.
6. StreamingHashWriter transparently streams bytes to target while computing checksums inline.
7. StreamingHashReader transparently reads from source while computing checksums inline.
8. Mismatch detection catches single-bit corruptions and flags mismatched algorithms.
9. Empty stream edge case correctly evaluates standard empty digests without panic.
10. verify_file_streaming and verify_stream_inline convenience utilities operate end-to-end.
"""
from __future__ import annotations

import hashlib
import io
import os
import tempfile

BD_GATE_SCOPE = "module"


def test_positive_control_existing_integrity_probe():
    """Positive control: existing integrity module formats evaluate cleanly at base."""
    from bulk_downloader import integrity

    assert hasattr(integrity, "_IMAGE_MAGIC")
    assert ".jpg" in integrity._IMAGE_MAGIC
    assert hasattr(integrity, "verify_media_integrity")


def test_inline_streaming_hash_capability_premise():
    """Capability premise: integrity module exposes inline streaming verification helpers."""
    from bulk_downloader import integrity

    assert hasattr(integrity, "verify_stream_hash"), (
        "Row 997 capability missing: integrity module has no verify_stream_hash"
    )
    assert hasattr(integrity, "verify_file_hash_streaming"), (
        "Row 997 capability missing: integrity module has no verify_file_hash_streaming"
    )


def test_streaming_hash_exports():
    """Export assertion: bulk_downloader.streaming_hash exports required classes and functions."""
    from bulk_downloader import streaming_hash

    assert hasattr(streaming_hash, "StreamingHashEngine")
    assert hasattr(streaming_hash, "StreamingHashWriter")
    assert hasattr(streaming_hash, "StreamingHashReader")
    assert hasattr(streaming_hash, "HashVerificationResult")
    assert hasattr(streaming_hash, "verify_stream_inline")
    assert hasattr(streaming_hash, "verify_file_streaming")


def test_multi_algorithm_incremental_hashing():
    """Assertion 4: StreamingHashEngine computes multiple digests simultaneously."""
    from bulk_downloader.streaming_hash import StreamingHashEngine

    engine = StreamingHashEngine(algorithms=["sha256", "md5", "sha512"])
    payload = b"Row 997 inline streaming verification payload data block * 1024" * 100

    chunk_size = 128
    for i in range(0, len(payload), chunk_size):
        engine.update(payload[i : i + chunk_size])

    result = engine.finalize()
    assert result.bytes_processed == len(payload)
    assert result.digests["sha256"] == hashlib.sha256(payload).hexdigest()
    assert result.digests["md5"] == hashlib.md5(payload).hexdigest()
    assert result.digests["sha512"] == hashlib.sha512(payload).hexdigest()
    assert result.valid is True


def test_verification_match_and_mismatch():
    """Assertion 5: Constant-time validation detects authentic and corrupted streams."""
    from bulk_downloader.streaming_hash import StreamingHashEngine

    payload = b"Authentic network streaming payload bytes"
    expected_sha256 = hashlib.sha256(payload).hexdigest()
    expected_md5 = hashlib.md5(payload).hexdigest()

    # Success case: matching expected hashes
    engine = StreamingHashEngine(
        algorithms=["sha256", "md5"],
        expected_hashes={"sha256": expected_sha256, "md5": expected_md5},
    )
    engine.update(payload)
    res = engine.finalize()
    assert res.valid is True
    assert "sha256" in res.matched_algorithms
    assert "md5" in res.matched_algorithms
    assert len(res.mismatched_algorithms) == 0

    # Failure case: corrupted payload
    engine_corrupt = StreamingHashEngine(
        algorithms=["sha256", "md5"],
        expected_hashes={"sha256": expected_sha256, "md5": expected_md5},
    )
    engine_corrupt.update(b"Corrupted network streaming payload bytes")
    res_corrupt = engine_corrupt.finalize()
    assert res_corrupt.valid is False
    assert "sha256" in res_corrupt.mismatched_algorithms
    assert "md5" in res_corrupt.mismatched_algorithms


def test_streaming_hash_writer():
    """Assertion 6: StreamingHashWriter computes hashes while writing to underlying sink."""
    from bulk_downloader.streaming_hash import StreamingHashWriter

    buffer = io.BytesIO()
    payload = b"Streaming download chunk 1, chunk 2, chunk 3, chunk 4"
    expected_hash = hashlib.sha256(payload).hexdigest()

    with StreamingHashWriter(buffer, algorithms=["sha256"], expected_hashes={"sha256": expected_hash}) as writer:
        writer.write(payload[:20])
        writer.write(payload[20:])

    assert buffer.getvalue() == payload
    result = writer.get_result()
    assert result.valid is True
    assert result.digests["sha256"] == expected_hash
    assert result.bytes_processed == len(payload)


def test_streaming_hash_reader():
    """Assertion 7: StreamingHashReader computes hashes while reading from underlying source."""
    from bulk_downloader.streaming_hash import StreamingHashReader

    payload = b"Streaming upload source buffer to read in variable blocks"
    expected_hash = hashlib.sha256(payload).hexdigest()
    buffer = io.BytesIO(payload)

    reader = StreamingHashReader(buffer, algorithms=["sha256"], expected_hashes={"sha256": expected_hash})
    read_data = bytearray()
    while chunk := reader.read(16):
        read_data.extend(chunk)

    assert bytes(read_data) == payload
    result = reader.get_result()
    assert result.valid is True
    assert result.digests["sha256"] == expected_hash
    assert result.bytes_processed == len(payload)


def test_empty_stream_handling():
    """Assertion 8: Empty streams evaluate cleanly without errors."""
    from bulk_downloader.streaming_hash import StreamingHashEngine

    engine = StreamingHashEngine(algorithms=["sha256", "md5"])
    res = engine.finalize()

    empty_sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    empty_md5 = "d41d8cd98f00b204e9800998ecf8427e"
    assert res.bytes_processed == 0
    assert res.digests["sha256"] == empty_sha256
    assert res.digests["md5"] == empty_md5
    assert res.valid is True


def test_verify_stream_inline_helper():
    """Assertion 9: verify_stream_inline verifies an arbitrary readable stream."""
    from bulk_downloader.streaming_hash import verify_stream_inline

    data = b"Testing inline stream verification helper function"
    expected = hashlib.sha256(data).hexdigest()

    stream = io.BytesIO(data)
    res = verify_stream_inline(stream, expected_hash=expected, algorithm="sha256", chunk_size=10)
    assert res.valid is True
    assert res.bytes_processed == len(data)

    # Test mismatch with helper
    stream_mismatch = io.BytesIO(data)
    res_bad = verify_stream_inline(stream_mismatch, expected_hash="0" * 64, algorithm="sha256")
    assert res_bad.valid is False


def test_verify_file_streaming_helper():
    """Assertion 10: verify_file_streaming validates disk file in chunks without full preload."""
    from bulk_downloader.integrity import verify_file_hash_streaming

    data = b"On-disk file content for streaming checksum validation" * 64
    expected = hashlib.sha256(data).hexdigest()

    with tempfile.NamedTemporaryFile(delete=False) as tf:
        tf.write(data)
        temp_path = tf.name

    try:
        res = verify_file_hash_streaming(temp_path, expected_hash=expected, algorithm="sha256", chunk_size=256)
        assert res.valid is True
        assert res.bytes_processed == len(data)
        assert res.digests["sha256"] == expected
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_unchecked_expected_algorithm_fails_open_prevention():
    """PM Ruling 09:54Z / B6-B finding: expected algorithm not computed => valid=False with unchecked listed."""
    from bulk_downloader.streaming_hash import StreamingHashEngine

    engine = StreamingHashEngine(
        algorithms=["sha256"],
        expected_hashes={"sha512": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
    )
    payload = b"Testing unchecked expected algorithm fail-open prevention"
    engine.update(payload)
    res = engine.finalize()

    assert res.valid is False
    assert "sha512" in res.unchecked_algorithms
    assert len(res.matched_algorithms) == 0


def test_matched_plus_unchecked_expected_algorithm_is_invalid():
    """A matching digest must not mask an expected algorithm that was never computed."""
    from bulk_downloader.streaming_hash import StreamingHashEngine

    payload = b"matched plus unchecked"
    engine = StreamingHashEngine(
        algorithms=["sha256"],
        expected_hashes={"sha256": hashlib.sha256(payload).hexdigest(), "sha512": "0" * 128},
    )
    engine.update(payload)
    res = engine.finalize()

    assert res.valid is False
    assert res.matched_algorithms == ["sha256"]
    assert res.unchecked_algorithms == ["sha512"]


def test_verdict_after_result_covers_bytes_added_later():
    """get_result()/finalize() must not return a stale verdict once more bytes flow."""
    from bulk_downloader.streaming_hash import (
        StreamingHashEngine,
        StreamingHashReader,
        StreamingHashWriter,
    )

    payload = b"x" * 100
    expected = {"sha256": hashlib.sha256(payload).hexdigest()}

    writer = StreamingHashWriter(io.BytesIO(), expected_hashes=expected)
    writer.write(payload)
    assert writer.get_result().valid is True
    writer.write(b"TAMPER")
    res = writer.get_result()
    assert (res.valid, res.bytes_processed) == (False, 106)
    assert res.digests["sha256"] == hashlib.sha256(writer.target_stream.getvalue()).hexdigest()

    reader = StreamingHashReader(io.BytesIO(payload + b"TAIL"), expected_hashes=expected)
    reader.read(100)
    assert reader.get_result().valid is True
    reader.read()
    assert (reader.get_result().valid, reader.get_result().bytes_processed) == (False, 104)

    engine = StreamingHashEngine(expected_hashes=expected)
    engine.update(payload)
    assert engine.finalize().valid is True
    engine.update(b"y")
    assert (engine.finalize().valid, engine.finalize().bytes_processed) == (False, 101)


def test_writer_hashes_only_bytes_the_sink_accepted():
    """A short-writing raw sink: the digest must describe the bytes that landed."""
    from bulk_downloader.streaming_hash import StreamingHashWriter

    class ShortSink(io.RawIOBase):
        def __init__(self, accept):
            self.accept = accept
            self.buf = bytearray()

        def writable(self):
            return True

        def write(self, b):
            if self.accept is None:
                return None
            taken = bytes(b)[: self.accept]
            self.buf += taken
            return len(taken)

    sink = ShortSink(4)
    writer = StreamingHashWriter(sink)
    assert writer.write(b"0123456789") == 4
    res = writer.get_result()
    assert res.bytes_processed == 4
    assert res.digests["sha256"] == hashlib.sha256(bytes(sink.buf)).hexdigest()

    blocked = StreamingHashWriter(ShortSink(None))
    assert blocked.write(b"0123456789") is None
    assert blocked.get_result().bytes_processed == 0


def test_integrity_wrappers_pass_algorithm_and_chunk_size_through():
    """Both integrity wrappers honour a non-default algorithm and chunk size."""
    from bulk_downloader.integrity import verify_file_hash_streaming, verify_stream_hash

    payload = b"wrapper passthrough payload" * 3
    digest = hashlib.sha512(payload).hexdigest()
    reads = []

    class RecordingStream(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    res = verify_stream_hash(RecordingStream(payload), digest.upper(), algorithm="sha512", chunk_size=7)
    assert (res.valid, list(res.digests)) == (True, ["sha512"])
    assert set(reads) == {7}
    assert verify_stream_hash(io.BytesIO(payload), hashlib.sha256(payload).hexdigest(), algorithm="sha512").valid is False

    fd, temp_path = tempfile.mkstemp()
    os.close(fd)
    try:
        with open(temp_path, "wb") as f:
            f.write(payload)
        res = verify_file_hash_streaming(temp_path, digest, algorithm="sha512", chunk_size=5)
        assert (res.valid, list(res.digests), res.bytes_processed) == (True, ["sha512"], len(payload))
    finally:
        os.remove(temp_path)


def _present_corpus_archive(tmp_path):
    from bulk_downloader import backup as bk
    from bulk_downloader import dom_analyzer as da

    source = tmp_path / "source"
    for directory in da._CAPTURE_OUTPUT_DIRS:
        (source / directory / "nested").mkdir(parents=True)
        (source / directory / "nested" / f"{directory}.wacz").write_bytes(f"row997-{directory}".encode())
    archive = tmp_path / "corpus.zip"
    created = bk.create_capture_corpus_backup(archive, source_root=source)
    assert created["ok"] is True, created
    return bk, archive, created["files"]


def test_capture_corpus_restore_verifies_members_through_streaming_engine(tmp_path, monkeypatch):
    """E1 wiring: corpus restore checks every member digest via integrity.verify_stream_hash."""
    from bulk_downloader import integrity

    bk, archive, files = _present_corpus_archive(tmp_path)
    assert files >= 3
    real = integrity.verify_stream_hash
    calls = []

    def spy(stream, expected_hash, algorithm="sha256", chunk_size=65536):
        calls.append((algorithm, chunk_size))
        return real(stream, expected_hash, algorithm=algorithm, chunk_size=chunk_size)

    monkeypatch.setattr(integrity, "verify_stream_hash", spy)
    restored = bk.restore_capture_corpus_backup(archive, target_root=tmp_path / "dest", dry_run=True)
    assert restored["ok"] is True, restored
    assert calls == [("sha256", bk._COPY_BLOCK_BYTES)] * files


def test_capture_corpus_restore_refuses_when_streaming_engine_reports_mismatch(tmp_path, monkeypatch):
    """The engine's verdict is authoritative for restore: invalid -> checksum mismatch, nothing staged."""
    from bulk_downloader import integrity
    from bulk_downloader.streaming_hash import HashVerificationResult

    bk, archive, _ = _present_corpus_archive(tmp_path)
    monkeypatch.setattr(integrity, "verify_stream_hash", lambda *a, **k: HashVerificationResult(valid=False))
    destination = tmp_path / "dest"
    restored = bk.restore_capture_corpus_backup(archive, target_root=destination)
    assert restored["ok"] is False
    assert "checksum mismatch" in restored["error"]
    assert not destination.exists()
