"""Row 170: INV_TAGS is a generated view of current source, not authority."""
import subprocess
import sys
import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BD_GATE_SCOPE = "repo-wide"


def test_inv_tags_is_generated_from_current_source():
    result = subprocess.run(
        [sys.executable, "tools/build_inv_tags.py", "--check"],
        cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    text = (ROOT / "INV_TAGS.md").read_text()
    assert "GENERATED CURRENT-SOURCE VIEW" in text
    assert "DANGER_MAP entry:" not in text
    assert "VERSION: 3.64.4" not in text


def test_inv_tags_generator_is_in_the_canonical_regen_chain():
    regen = (ROOT / "toolchain/bin/bd-regen-order").read_text()
    command = '["tools/build_inv_tags.py"]'
    assert command in regen
    assert regen.index(command) < regen.index('("PIN_INDEX"')


def test_inv_tags_refuses_an_empty_denominator(monkeypatch):
    path = ROOT / "tools/build_inv_tags.py"
    loader = importlib.machinery.SourceFileLoader("inv_tags_1183", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    monkeypatch.setattr(module, "tracked_python", lambda: [])
    with pytest.raises(RuntimeError, match="zero current-source"):
        module.render()
