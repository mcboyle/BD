"""H668: CI "Version pin coherence" checks all FIVE version carriers and reports every mismatch in one run.

A bump touches the five paths toolchain/bin/bd-land-trio stamps. The step used to check three of them and exit on the
first mismatch, so a partial bump cost one CI cycle per missing file (run 35758385351 job 106850420125). The step's
own Python is extracted from .github/workflows/ci.yml and run against scratch trees; nothing in the checkout is written.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
V = "3.66.9001"


def _step_script() -> str:
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    m = re.search(r"- name: Version pin coherence\n\s+run: \|\n\s+python - <<'PY'\n(.*?)\n\s+PY\n", text, re.S)
    assert m, "ci.yml has no 'Version pin coherence' step with a python heredoc"
    return textwrap.dedent(m.group(1))


def _tree(tmp: Path, *, changelog=V, test_pin=V, pin_index=V, manifest="v" + V) -> Path:
    (tmp / "bulk_downloader").mkdir()
    (tmp / "bulk_downloader/__init__.py").write_text(f'__version__ = "{V}"\n')
    (tmp / "CHANGELOG.md").write_text(f"# Changelog\n\n## v{changelog} -- fixture\n")
    (tmp / "tests").mkdir()
    (tmp / "tests/test_settings_center_slice4.py").write_text(f'assert __version__ == "{test_pin}"\n')
    pins = [{"form": "version", "file": "tests/test_settings_center_slice4.py", "line": 1, "value": pin_index}]
    (tmp / "PIN_INDEX.json").write_text(json.dumps({"pins": pins}))
    (tmp / "project-knowledge").mkdir()
    (tmp / "project-knowledge/STATIC_KB_MANIFEST.json").write_text(json.dumps({"version_context": manifest}))
    return tmp


def _run(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", _step_script()], cwd=tree, capture_output=True, text=True, timeout=60)


def test_coherent_tree_passes(tmp_path):
    r = _run(_tree(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "coherent across all five carriers" in r.stdout


@pytest.mark.parametrize(
    "field, path",
    [
        ("changelog", "CHANGELOG.md"),
        ("test_pin", "tests/"),
        ("pin_index", "PIN_INDEX.json"),
        ("manifest", "project-knowledge/STATIC_KB_MANIFEST.json"),
    ],
)
def test_each_carrier_is_checked(tmp_path, field, path):
    r = _run(_tree(tmp_path, **{field: "3.66.1" if field != "manifest" else "v3.66.1"}))
    assert r.returncode != 0
    assert f"::error::{path}" in r.stdout, r.stdout + r.stderr


def test_every_mismatch_is_reported_in_one_run(tmp_path):
    r = _run(_tree(tmp_path, pin_index="3.66.1", manifest="v3.66.1", test_pin="3.66.1"))
    assert r.returncode != 0
    errors = [line for line in r.stdout.splitlines() if line.startswith("::error::")]
    assert len(errors) == 3, r.stdout
    assert "3 version carrier mismatch(es)" in r.stderr


def test_step_covers_the_carriers_bd_land_trio_stamps():
    trio = (ROOT / "toolchain/bin/bd-land-trio").read_text(encoding="utf-8")
    paths = re.search(r"PATHS = \((.*?)\)", trio, re.S).group(1)
    script = _step_script()
    for p in re.findall(r'"([^"]+)"', paths):
        needle = "tests/" if p.startswith("tests/") else p
        assert needle in script, f"bd-land-trio stamps {p} but the CI step never reads it"
