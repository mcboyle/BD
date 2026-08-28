"""Process boundary shared by every subprocess-backed plugin bridge.

This module is intentionally the only place the node, Python, and generic
interpreter bridges may spawn plugin code.  The boundary is enforced by the
API rather than repeated as six optional keyword lists:

* the child receives an explicit allowlist of non-secret runtime variables;
* its cwd and synthetic HOME are the directory that owns the plugin file;
* stdout and stderr are drained concurrently and capped per stream;
* timeout or output overflow kills the isolated plugin process group.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import BinaryIO, Sequence


MAX_OUTPUT_BYTES = 256 * 1024

# Values needed to locate a runtime and preserve ordinary text/temp behavior.
# Credential/config surfaces (HOME from the parent, PYTHONPATH, cloud tokens,
# proxy credentials, loader overrides, and arbitrary BD_* values) are absent.
_ENV_ALLOWLIST = frozenset(
    {
        "COMSPEC",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "TZ",
        "WINDIR",
    }
)


class PluginOutputLimitExceeded(subprocess.SubprocessError):
    """A plugin exceeded the fixed stdout/stderr capture budget."""


def _plugin_environment(plugin_dir: Path) -> dict[str, str]:
    """Build the complete child environment without copying ambient secrets."""
    child_env = {
        name: os.environ[name]
        for name in _ENV_ALLOWLIST
        if name in os.environ
    }
    child_env.setdefault("PATH", os.defpath)
    # Runtimes commonly consult one or the other.  Both point at plugin-owned
    # storage rather than exposing the operator's profile directory.
    child_env["HOME"] = str(plugin_dir)
    child_env["USERPROFILE"] = str(plugin_dir)
    child_env["PYTHONIOENCODING"] = "utf-8"
    return child_env


def _kill_plugin_group(proc: subprocess.Popen[bytes]) -> None:
    """Best-effort kill of the plugin and descendants sharing its new group."""
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _drain_limited(
    stream: BinaryIO,
    proc: subprocess.Popen[bytes],
    chunks: list[bytes],
    overflow: threading.Event,
) -> None:
    """Drain one pipe while retaining at most :data:`MAX_OUTPUT_BYTES`."""
    retained = 0
    while True:
        try:
            chunk = stream.read(8192)
        except (OSError, ValueError):
            return
        if not chunk:
            return
        room = MAX_OUTPUT_BYTES - retained
        if room > 0:
            kept = chunk[:room]
            chunks.append(kept)
            retained += len(kept)
        if len(chunk) > room:
            overflow.set()
            _kill_plugin_group(proc)
            # Continue draining until the killed process group closes the pipe.


def _write_input(stream: BinaryIO, payload: bytes) -> None:
    try:
        stream.write(payload)
        stream.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _join_io_threads(
    proc: subprocess.Popen[bytes],
    threads: Sequence[threading.Thread],
) -> None:
    """Bound pipe cleanup even if a plugin descendant retained a descriptor."""
    for thread in threads:
        thread.join(timeout=1.0)
    if any(thread.is_alive() for thread in threads):
        _kill_plugin_group(proc)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        for thread in threads:
            thread.join(timeout=1.0)


def run_plugin_process(
    argv: Sequence[str],
    *,
    plugin_path: Path,
    timeout: float,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one plugin invocation inside the enforced process boundary."""
    plugin_file = Path(plugin_path).resolve(strict=True)
    plugin_dir = plugin_file.parent
    if not plugin_dir.is_dir():
        raise OSError(f"plugin directory is unavailable: {plugin_dir}")
    child_env = _plugin_environment(plugin_dir)
    encoded_input = None if input_text is None else input_text.encode("utf-8")

    proc = subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL if encoded_input is None else subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        cwd=str(plugin_dir),
        env=child_env,
        close_fds=True,
        start_new_session=(os.name == "posix"),
    )
    assert proc.stdout is not None and proc.stderr is not None
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    overflow = threading.Event()
    readers = [
        threading.Thread(
            target=_drain_limited,
            args=(proc.stdout, proc, stdout_chunks, overflow),
            daemon=True,
        ),
        threading.Thread(
            target=_drain_limited,
            args=(proc.stderr, proc, stderr_chunks, overflow),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    writer = None
    if encoded_input is not None:
        assert proc.stdin is not None
        writer = threading.Thread(
            target=_write_input,
            args=(proc.stdin, encoded_input),
            daemon=True,
        )
        writer.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_plugin_group(proc)
        proc.wait()
        _join_io_threads(proc, readers)
        if writer is not None:
            writer.join(timeout=1.0)
        stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        raise subprocess.TimeoutExpired(
            list(argv), timeout, output=stdout, stderr=stderr
        ) from exc

    _join_io_threads(proc, readers)
    if writer is not None:
        writer.join(timeout=1.0)
    stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    if overflow.is_set():
        raise PluginOutputLimitExceeded(
            f"plugin output limit exceeded ({MAX_OUTPUT_BYTES} bytes per stream)"
        )
    return subprocess.CompletedProcess(list(argv), proc.returncode, stdout, stderr)
