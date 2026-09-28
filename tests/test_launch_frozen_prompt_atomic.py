"""Deterministic reader/producer handoff for the real launcher publication block."""
from __future__ import annotations

import os
import select
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_LAUNCH_FROZEN_PROMPT_RACE_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
OLD = b"complete previous role prompt\n"
NEW = b"complete replacement role prompt\n"


def _program() -> str:
    source = Path(CANDIDATE).read_text()
    start = source.index("# Load frozen system prompt if available;")
    end = source.index('\nif [ "$POOL" = codex ]; then', start)
    return '\n'.join([
        'set -u',
        'die(){ c=$1; shift; printf "%s\\n" "$*" >&2; exit "$c"; }',
        'FROZEN_PROMPT=$1; PROMPT=$2; SPFILE=$3',
        source[start:end],
    ])


def _start(tmp_path: Path, mode: str, outcome: str = "ok", existing: bool = True):
    frozen = tmp_path / "frozen"
    prompt = tmp_path / "source"
    shared = tmp_path / "published"
    prompt.write_bytes(NEW)
    if mode == "frozen":
        frozen.write_bytes(NEW)
    if existing:
        shared.write_bytes(OLD)
    binary = tmp_path / "bin"
    binary.mkdir()
    # Pause after the destination is opened, before any bytes are produced.
    # stderr handshake + stdin release avoids timing assumptions or sleeps.
    shim = r"""#!/usr/bin/python3
import os, pathlib, sys
source = pathlib.Path(sys.argv[1]).read_bytes()
target = open(sys.argv[2], 'wb') if pathlib.Path(sys.argv[0]).name == 'cp' else sys.stdout.buffer
sys.stderr.write('PRODUCER_OPENED\n'); sys.stderr.flush()
if sys.stdin.readline() != 'publish\n':
    sys.exit(90)
outcome = os.environ['PRODUCER_OUTCOME']
if outcome == 'fail':
    target.write(b'partial'); target.flush(); sys.exit(7)
if outcome != 'empty':
    target.write(source); target.flush()
"""
    for name in ("cat", "cp"):
        script = binary / name
        script.write_text(shim)
        script.chmod(0o755)
    proc = subprocess.Popen(
        ["bash", "-c", _program(), "publication-probe", str(frozen), str(prompt), str(shared)],
        env={"PATH": str(binary) + ":/usr/bin:/bin", "PRODUCER_OUTCOME": outcome},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.stderr is not None
    ready, _, _ = select.select([proc.stderr], [], [], 10)
    assert ready, "PRODUCER_HANDSHAKE_TIMEOUT"
    marker = proc.stderr.readline()
    assert marker == b"PRODUCER_OPENED\n", f"PRODUCER_SETUP_INVALID: {marker!r}"
    return proc, shared


@pytest.mark.parametrize("mode", ["frozen", "fallback"])
def test_reader_observes_complete_prompt_while_writer_open(tmp_path, mode):
    proc, shared = _start(tmp_path, mode)
    try:
        observed = shared.read_bytes()
    finally:
        _, error = proc.communicate(b"publish\n", timeout=10)
    assert observed == OLD, f"PROMPT_PUBLICATION_RACE: reader observed {observed!r}"
    assert proc.returncode == 0, f"PUBLICATION_FAILED: {error!r}"
    assert shared.read_bytes() == NEW
    assert list(tmp_path.glob("published.tmp.*")) == []


@pytest.mark.parametrize("mode", ["frozen", "fallback"])
@pytest.mark.parametrize("outcome", ["fail", "empty"])
def test_failed_producer_preserves_previous_prompt(tmp_path, mode, outcome):
    proc, shared = _start(tmp_path, mode, outcome)
    _, error = proc.communicate(b"publish\n", timeout=10)
    assert proc.returncode == 4, f"PRODUCER_FAILURE_NOT_REFUSED: {error!r}"
    assert shared.read_bytes() == OLD, "FAILED_PRODUCER_REPLACED_VALID_PROMPT"
    assert list(tmp_path.glob("published.tmp.*")) == []


@pytest.mark.parametrize("mode", ["frozen", "fallback"])
def test_first_publication_creates_complete_prompt(tmp_path, mode):
    proc, shared = _start(tmp_path, mode, existing=False)
    _, error = proc.communicate(b"publish\n", timeout=10)
    assert proc.returncode == 0, f"FIRST_PUBLICATION_FAILED: {error!r}"
    assert shared.read_bytes() == NEW
