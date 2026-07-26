"""Contract tests for the canonical generated-artifact regeneration command."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "toolchain" / "bin" / "bd-regen-order"
DOCUMENTED_TOOL_PATH = REPO_ROOT / "project-knowledge" / "bd-regen-order"
REACHABILITY_LEDGER = REPO_ROOT / "reports" / "endpoint_reachability.json"


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
    python.chmod(0o755)
    assert regen.python_for(str(tmp_path)) == str(python)

    python.unlink()
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    venv_python.chmod(0o755)
    assert regen.python_for(str(tmp_path)) == str(venv_python)

    venv_python.unlink()
    assert regen.python_for(str(tmp_path)) == regen.sys.executable


def test_python_selection_skips_nonexecutable_environment(tmp_path):
    regen = _load_regen_tool()
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    python.chmod(0o644)

    assert not os.access(python, os.X_OK)
    assert regen.python_for(str(tmp_path)) == regen.sys.executable


@pytest.mark.parametrize("missing_kind", ["generator", "verifier", "reachability"])
def test_regeneration_fails_closed_when_required_subject_is_missing(
    tmp_path, monkeypatch, missing_kind
):
    regen = _load_regen_tool()
    monkeypatch.setattr(sys, "argv", ["bd-regen-order", "--work", str(tmp_path)])
    monkeypatch.setattr(regen, "CHAIN", [])
    monkeypatch.setattr(regen, "VERIFY", [])
    monkeypatch.setattr(regen, "check_reach", lambda _work: (True, "in sync"))

    if missing_kind == "generator":
        regen.CHAIN = [("missing generator", ["tools/missing-generator.py"], "required")]
    elif missing_kind == "verifier":
        regen.VERIFY = [("missing verifier", ["tools/missing-verifier.py"], "required")]
    else:
        monkeypatch.setattr(
            regen,
            "check_reach",
            lambda _work: (None, "endpoint_reachability not present"),
        )

    assert regen.main() == 1


def test_regeneration_rejects_missing_worktree(tmp_path, monkeypatch, capsys):
    regen = _load_regen_tool()
    missing = tmp_path / "missing-worktree"
    monkeypatch.setattr(sys, "argv", ["bd-regen-order", "--work", str(missing)])

    assert regen.main() == 1
    assert "work tree not found" in capsys.readouterr().out


def test_reachability_ledger_is_tracked_for_clean_checkout_regeneration():
    tracked = subprocess.run(
        [
            "git",
            "ls-files",
            "--error-unmatch",
            "--",
            str(REACHABILITY_LEDGER.relative_to(REPO_ROOT)),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    payload = json.loads(REACHABILITY_LEDGER.read_text(encoding="utf-8"))

    assert tracked.returncode == 0, tracked.stderr
    assert type(payload["dark_count"]) is int
    assert set(payload["dark"]).issubset(payload["classified"])


def test_ci_installs_runtime_dependencies_before_canonical_regeneration():
    """A clean runner must satisfy imports before it regenerates artifacts."""
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    install = "python -m pip install -r requirements.txt"
    regenerate = 'python toolchain/bin/bd-regen-order --work "$GITHUB_WORKSPACE"'
    assert install in ci
    assert regenerate in ci
    assert ci.index(install) < ci.index(regenerate)
    assert "git ls-files --error-unmatch" in ci
    assert "git status --porcelain --untracked-files=all" in ci
    assert 'test -f "$artifact"' in ci
    assert "ROUTE_INDEX.json" in ci
    assert "ENDPOINT_CATALOG.md" in ci
    assert "DEPENDENCY_GRAPH.json" in ci


def test_release_preserves_legacy_packaging_before_canonical_regeneration():
    """Regeneration must not replace the legacy release packaging contract."""
    release = (REPO_ROOT / "scripts" / "build_release.sh").read_text(encoding="utf-8")

    assert "python toolchain/bin/bd-regen-order --work \"$PWD\"" in release
    assert 'Z=/mnt/user-data/uploads/BulkDownloader_v3_66_137.zip' in release
    assert "STAGE=/home/claude/release_148" in release
    assert release.index("bd-regen-order") < release.index("Z=")


def test_operator_release_cut_uses_repository_regenerator_and_fails_closed():
    release = (REPO_ROOT / "project-knowledge" / "bd-cut").read_text(
        encoding="utf-8"
    )

    assert 'os.path.join(work, "toolchain", "bin", "bd-regen-order")' in release
    assert "shutil.which(\"bd-regen-order\")" not in release
    assert "bd-regen-order missing from release work tree" in release
    assert "derived docs may be stale" not in release


def test_policy_requires_canonical_regeneration_before_review():
    """Review packaging must use the same canonical command as CI and release."""
    policy = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert ".venv/bin/python toolchain/bin/bd-regen-order --work \"$PWD\"" in policy


def test_documented_toolchain_copy_is_byte_identical_to_canonical_tool():
    """The operator-facing copy must execute the exact canonical contract."""
    documented = REPO_ROOT / "project-knowledge" / "bd-regen-order"
    assert documented.read_bytes() == TOOL_PATH.read_bytes()
