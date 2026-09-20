"""Streaming zstd helpers for cold-tier archive files."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import BinaryIO


@dataclass(frozen=True)
class ArchiveCompressionResult:
    """The destination and zstd worker count used for one archive."""

    path: Path
    threads: int


class _EofTrackingReader:
    """The zstd stdout pipe, remembering whether the caller read to EOF."""

    def __init__(self, raw: BinaryIO):
        self._raw = raw
        self.eof = False

    def read(self, size: int = -1) -> bytes:
        data = self._raw.read(size)
        if size is None or size < 0 or (size > 0 and not data):
            self.eof = True
        return data

    def read1(self, size: int = -1) -> bytes:
        data = self._raw.read1(size) if size is not None and size >= 0 else self._raw.read1()
        if size is not None and size > 0 and not data:
            self.eof = True
        return data

    def readinto(self, b) -> int:
        n = self._raw.readinto(b)
        if not n and len(b):
            self.eof = True
        return n

    def readline(self, size: int = -1) -> bytes:
        data = self._raw.readline(size)
        if not data:
            self.eof = True
        return data

    def __iter__(self):
        for line in self._raw:
            yield line
        self.eof = True

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        self._raw.close()

    @property
    def closed(self) -> bool:
        return self._raw.closed

    def fileno(self) -> int:
        return self._raw.fileno()


class _ArchiveReader:
    """A context manager that exposes zstd's decompressed stdout as a reader.

    Leaving the context before EOF is an intentional partial read: the
    child is stopped and its exit status is not a verdict on the archive
    (a closed pipe kills zstd with SIGPIPE). Reading to EOF makes the exit
    status the verdict: a corrupt archive raises. Either way every pipe the
    reader owns is closed on exit."""

    def __init__(self, archive: Path):
        self._archive = archive
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: _EofTrackingReader | None = None

    def __enter__(self) -> BinaryIO:
        try:
            self._process = subprocess.Popen(
                ["zstd", "-q", "-d", "-c", "--", str(self._archive)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("zstd executable is required for archive reads") from exc
        assert self._process.stdout is not None
        self._reader = _EofTrackingReader(self._process.stdout)
        return self._reader  # type: ignore[return-value]

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        proc, reader = self._process, self._reader
        assert proc is not None and reader is not None
        assert proc.stdout is not None and proc.stderr is not None
        try:
            already_exited = proc.poll()
            if reader.eof or already_exited is not None:
                # the stream was consumed (or zstd finished on its own): its
                # exit status is the verdict on the archive
                stderr = proc.stderr.read().decode("utf-8", "replace").strip()
                proc.stdout.close()
                returncode = proc.wait()
                if exc_type is None and returncode:
                    raise RuntimeError(f"zstd decompression failed: {stderr or returncode}")
            else:
                # intentional partial read: stop the child; SIGPIPE/SIGTERM is
                # not a corruption finding
                proc.stdout.close()
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        finally:
            for pipe in (proc.stdout, proc.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass
        return False


def _as_file(value: str | Path, *, name: str) -> Path:
    path = Path(value)
    if not path.is_file():
        raise FileNotFoundError(f"{name} is not a regular file: {path}")
    return path


def compress_archive(
    source: str | Path,
    destination: str | Path,
    *,
    threads: int = 1,
) -> ArchiveCompressionResult:
    """Compress ``source`` into a new zstd archive with ``threads`` workers."""
    source_path = _as_file(source, name="source")
    destination_path = Path(destination)
    if threads <= 0:
        raise ValueError("threads must be positive")
    if destination_path.exists():
        raise FileExistsError(f"archive destination already exists: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # "--" ends option parsing: a source named "-f" is a file, never a flag
        subprocess.run(
            ["zstd", "-q", f"-T{threads}", "-o", str(destination_path), "--", str(source_path)],
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("zstd executable is required for archive compression") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else str(exc.returncode)
        raise RuntimeError(f"zstd compression failed: {detail}") from exc
    return ArchiveCompressionResult(path=destination_path, threads=threads)


def open_archive(archive: str | Path) -> _ArchiveReader:
    """Return a context manager yielding a transparent binary decompression reader."""
    return _ArchiveReader(_as_file(archive, name="archive"))
