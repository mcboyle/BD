"""Row 130: fixed-window contents, not only their count, are ratcheted."""
import subprocess
import sys
import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BD_GATE_SCOPE = "repo-wide"


def test_every_fixed_source_window_has_a_current_content_hash():
    result = subprocess.run(
        [sys.executable, "tools/build_source_window_hashes.py", "--check"],
        cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_source_window_generator_is_in_the_canonical_regen_chain():
    regen = (ROOT / "toolchain/bin/bd-regen-order").read_text()
    command = '["tools/build_source_window_hashes.py"]'
    assert command in regen
    assert regen.index(command) < regen.index('("PIN_INDEX"')


def test_source_window_generator_refuses_an_empty_denominator(monkeypatch):
    path = ROOT / "tools/build_source_window_hashes.py"
    loader = importlib.machinery.SourceFileLoader("source_windows_1183", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    monkeypatch.setattr(module, "_tracked_tests", lambda: [])
    monkeypatch.setattr(module, "_source_corpora", lambda: [])
    with pytest.raises(RuntimeError, match="zero fixed source windows"):
        module.build()
