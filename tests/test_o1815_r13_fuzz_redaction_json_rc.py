"""O1815 R13 (P2-6): bd-fuzz-redaction --json must not exit 0 when the scrubber
failed to load -- the text path already exits 1; JSON mode returned 0."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "toolchain/bin/bd-fuzz-redaction"

SCRUBBERS = {
    "missing": None,
    "broken": 'raise RuntimeError("boom")\n',
    "redacting": ('def scrub_string(t, m, n):\n    return "[R]"\n'
                  'def scrub_url(t, m, n):\n    return "[R]"\n'),
    "leaky": ('def scrub_string(t, m, n):\n    return t\n'
              'def scrub_url(t, m, n):\n    return t\n'),
}


def run(tmp_path, scrubber, *extra):
    (tmp_path / "bulk_downloader").mkdir()
    (tmp_path / "bulk_downloader" / "m.py").write_text("x = 1\n")
    if SCRUBBERS[scrubber] is not None:
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "capture_scrub.py").write_text(SCRUBBERS[scrubber])
    return subprocess.run([sys.executable, str(TOOL), "--work", str(tmp_path), *extra],
                          capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("scrubber", ["missing", "broken"])
def test_json_unloadable_scrubber_exits_nonzero(tmp_path, scrubber):
    p = run(tmp_path, scrubber, "--json")
    summary = json.loads(p.stdout)["summary"]
    assert summary["error"], p.stdout
    assert summary["tested"] == 0, p.stdout
    assert p.returncode == 1, f"BD-FUZZ-REDACT-JSON-RC0 rc={p.returncode} {summary}"


@pytest.mark.parametrize("scrubber", ["missing", "broken"])
def test_text_unloadable_scrubber_exits_nonzero(tmp_path, scrubber):
    assert run(tmp_path, scrubber).returncode == 1


@pytest.mark.parametrize("scrubber,rc", [("redacting", 0), ("leaky", 1)])
def test_json_loaded_scrubber_rc_tracks_leaks(tmp_path, scrubber, rc):
    p = run(tmp_path, scrubber, "--json")
    summary = json.loads(p.stdout)["summary"]
    assert summary["error"] is None, p.stdout
    assert summary["tested"] > 0, p.stdout
    assert p.returncode == rc, p.stdout
