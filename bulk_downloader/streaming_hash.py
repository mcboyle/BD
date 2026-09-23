"""Inline Streaming Cryptographic Hash Verification Engine (Row 997).

Provides transparent, zero-copy, multi-algorithm checksum calculation and integrity
verification as byte streams are ingested, streamed, or written to disk.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self


@dataclass
class HashVerificationResult:
    """Outcome of inline streaming cryptographic verification."""
    valid: bool
    digests: dict[str, str] = field(default_factory=dict)
    bytes_processed: int = 0
    matched_algorithms: list[str] = field(default_factory=list)
    mismatched_algorithms: list[str] = field(default_factory=list)
    unchecked_algorithms: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    throughput_mb_s: float = 0.0


class StreamingHashEngine:
    """Incremental, multi-algorithm cryptographic hashing engine."""

    def __init__(
        self,
        algorithms: Iterable[str] | None = None,
        expected_hashes: dict[str, str] | None = None,
    ) -> None:
        self.algorithms = [a.lower() for a in (algorithms or ["sha256"])]
        self.expected_hashes = {k.lower(): v.lower() for k, v in (expected_hashes or {}).items()}
        self._hashers: dict[str, hashlib._Hash] = {}
        self.bytes_processed: int = 0
        self._start_time = time.monotonic()
        self._end_time: float | None = None
        self._result: HashVerificationResult | None = None
        self.reset()

    def reset(self) -> None:
        """Resets the state of all hash accumulators."""
        self._hashers = {algo: hashlib.new(algo) for algo in self.algorithms}
        self.bytes_processed = 0
        self._start_time = time.monotonic()
        self._end_time = None
        self._result = None

    def update(self, data: bytes | bytearray | memoryview) -> None:
        """Feeds a block of bytes into all active hashers."""
        if not data:
            return
        data_len = len(data)
        self.bytes_processed += data_len
        for hasher in self._hashers.values():
            hasher.update(data)
        # A verdict covers exactly the bytes seen so far; new bytes void it.
        self._result = None

    def finalize(self) -> HashVerificationResult:
        """Finalizes digests and verifies against expected hashes."""
        if self._result is not None:
            return self._result

        self._end_time = time.monotonic()
        elapsed = max(1e-6, self._end_time - self._start_time)
        throughput = (self.bytes_processed / (1024 * 1024)) / elapsed

        digests: dict[str, str] = {}
        matched: list[str] = []
        mismatched: list[str] = []
        unchecked: list[str] = []
        all_valid = True

        for algo, hasher in self._hashers.items():
            digest = hasher.hexdigest().lower()
            digests[algo] = digest

            expected = self.expected_hashes.get(algo)
            if expected is not None:
                if hmac.compare_digest(digest, expected):
                    matched.append(algo)
                else:
                    mismatched.append(algo)
                    all_valid = False

        # Any expected algorithm that was not computed is unchecked and invalidates verification (O1224)
        for exp_algo in self.expected_hashes:
            if exp_algo not in self._hashers:
                unchecked.append(exp_algo)
                all_valid = False

        self._result = HashVerificationResult(
            valid=all_valid,
            digests=digests,
            bytes_processed=self.bytes_processed,
            matched_algorithms=matched,
            mismatched_algorithms=mismatched,
            unchecked_algorithms=unchecked,
            elapsed_seconds=elapsed,
            throughput_mb_s=throughput,
        )
        return self._result


class StreamingHashWriter:
    """Wraps a writable stream, updating hash digest on every write."""

    def __init__(
        self,
        target_stream: BinaryIO,
        algorithms: Iterable[str] | None = None,
        expected_hashes: dict[str, str] | None = None,
    ) -> None:
        self.target_stream = target_stream
        self.engine = StreamingHashEngine(algorithms=algorithms, expected_hashes=expected_hashes)

    def write(self, b: bytes | bytearray | memoryview) -> int | None:
        # Hash only what the sink accepted: a raw stream may take a prefix, or
        # None when a non-blocking write took nothing (io.RawIOBase.write).
        written = self.target_stream.write(b)
        with memoryview(b) as view:
            self.engine.update(view.cast("B")[: written or 0])
        return written

    def flush(self) -> None:
        self.target_stream.flush()

    def get_result(self) -> HashVerificationResult:
        return self.engine.finalize()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.flush()
        self.engine.finalize()


class StreamingHashReader:
    """Wraps a readable stream, updating hash digest on every read."""

    def __init__(
        self,
        source_stream: BinaryIO,
        algorithms: Iterable[str] | None = None,
        expected_hashes: dict[str, str] | None = None,
    ) -> None:
        self.source_stream = source_stream
        self.engine = StreamingHashEngine(algorithms=algorithms, expected_hashes=expected_hashes)

    def read(self, size: int = -1) -> bytes:
        data = self.source_stream.read(size)
        if data:
            self.engine.update(data)
        return data

    def get_result(self) -> HashVerificationResult:
        return self.engine.finalize()


def verify_stream_inline(
    stream: BinaryIO,
    expected_hash: str,
    algorithm: str = "sha256",
    chunk_size: int = 65536,
) -> HashVerificationResult:
    """Calculates hash incrementally from a readable binary stream and validates."""
    engine = StreamingHashEngine(algorithms=[algorithm], expected_hashes={algorithm: expected_hash})
    while chunk := stream.read(chunk_size):
        engine.update(chunk)
    return engine.finalize()


def verify_file_streaming(
    file_path: str | Path,
    expected_hash: str,
    algorithm: str = "sha256",
    chunk_size: int = 65536,
) -> HashVerificationResult:
    """Calculates file checksum using chunked streaming reads without full memory buffering."""
    with open(file_path, "rb") as f:
        return verify_stream_inline(f, expected_hash=expected_hash, algorithm=algorithm, chunk_size=chunk_size)
