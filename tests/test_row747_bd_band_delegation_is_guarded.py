"""Row 747: bd-band preserves bd-bandcheck refusals at its subprocess seam."""
from __future__ import annotations
import re, shutil, subprocess, sys
import importlib.machinery, importlib.util
from pathlib import Path
BD_GATE_SCOPE = "repo-wide"
ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "toolchain/bin"
def invoke(tool, *args):
    return subprocess.run([sys.executable, str(tool), *args], cwd=ROOT, text=True, capture_output=True, timeout=30)
def test_delegated_unsafe_and_missing_targets_refuse_once():
    perf = "tests/test_perf_lab.py"; missing = "tests/test_row747_missing_xyz.py"
    assert (ROOT / perf).is_file(); assert not (ROOT / missing).exists()
    for target, needle in ((perf, "UNSAFE  test_perf_lab.py"), (missing, "MISSING 'tests/test_row747_missing_xyz.py'")):
        r = invoke(BIN / "bd-band", target); out = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout + r.stderr)
        assert r.returncode == 2 and out.count("refusing this band -- bd-bandcheck flagged it:") == 1 and out.count(needle) == 1, out
def test_missing_loader_refuses_once(tmp_path):
    copied = tmp_path / "bin"; shutil.copytree(BIN, copied)
    (copied / "bd-bandcheck").unlink()
    assert (copied / "bd-band").is_file(); assert not (copied / "bd-bandcheck").exists()
    r = invoke(copied / "bd-band", "tests/test_row747_missing_xyz.py"); out = r.stdout + r.stderr
    assert r.returncode == 2 and out.count("bd-bandcheck is not loadable") == 1, out
def test_row747_transform_control_imports_without_judging_refusal():
    loader = importlib.machinery.SourceFileLoader("row747_band", str(BIN / "bd-band"))
    spec = importlib.util.spec_from_loader(loader.name, loader); module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)
