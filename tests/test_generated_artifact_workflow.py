"""Contract tests for the canonical generated-artifact regeneration command."""
from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "toolchain" / "bin" / "bd-regen-order"
DOCUMENTED_TOOL_PATH = REPO_ROOT / "project-knowledge" / "bd-regen-order"


def _load_regen_tool():
    loader = importlib.machinery.SourceFileLoader("bd_regen_order", str(TOOL_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_canonical_chain_and_location_interfaces_select_the_real_tree(tmp_path):
    """A copied command must derive its own root and prefer that tree's .venv."""
    regen = _load_regen_tool()
    labels = [label for label, _argv, _why in regen.CHAIN]
    assert labels == [
        "gui_parity",
        "ROUTE_INDEX",
        "ENDPOINT_CATALOG",
        "DEPENDENCY_GRAPH",
        "FUNCTION_INDEX",
        "PIN_INDEX",
    ]
    assert regen.repo_root(str(TOOL_PATH)) == str(REPO_ROOT)
    assert regen.repo_root(str(DOCUMENTED_TOOL_PATH)) == str(REPO_ROOT)
    with pytest.raises(RuntimeError, match="could not find repository root"):
        regen.repo_root(str(tmp_path / "not-a-repo" / "bd-regen-order"))

    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    assert regen.python_for(str(tmp_path)) == str(python)

    python.unlink()
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    assert regen.python_for(str(tmp_path)) == str(venv_python)

    venv_python.unlink()
    assert regen.python_for(str(tmp_path)) == regen.sys.executable


def test_ci_installs_runtime_dependencies_before_canonical_regeneration():
    """A clean runner must satisfy imports before it regenerates artifacts."""
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    install = "python -m pip install -r requirements.txt"
    regenerate = 'python toolchain/bin/bd-regen-order --work "$GITHUB_WORKSPACE"'
    assert install in ci
    assert regenerate in ci
    assert ci.index(install) < ci.index(regenerate)
    assert "git diff --exit-code --" in ci
    assert "ROUTE_INDEX.json ENDPOINT_CATALOG.md DEPENDENCY_GRAPH.json" in ci


def test_release_preserves_legacy_packaging_before_canonical_regeneration():
    """Regeneration must not replace the legacy release packaging contract."""
    release = (REPO_ROOT / "scripts" / "build_release.sh").read_text(encoding="utf-8")

    assert "python toolchain/bin/bd-regen-order --work \"$PWD\"" in release
    assert 'Z=/mnt/user-data/uploads/BulkDownloader_v3_66_137.zip' in release
    assert "STAGE=/home/claude/release_148" in release
    assert release.index("bd-regen-order") < release.index("Z=")


def test_policy_requires_canonical_regeneration_before_review():
    """Review packaging must use the same canonical command as CI and release."""
    policy = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert ".venv/bin/python toolchain/bin/bd-regen-order --work \"$PWD\"" in policy


def test_documented_toolchain_copy_is_byte_identical_to_canonical_tool():
    """The operator-facing copy must execute the exact canonical contract."""
    documented = REPO_ROOT / "project-knowledge" / "bd-regen-order"
    assert documented.read_bytes() == TOOL_PATH.read_bytes()
