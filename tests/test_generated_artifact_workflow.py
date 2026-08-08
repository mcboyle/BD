"""Contract tests for the canonical generated-artifact regeneration command."""
from __future__ import annotations

import ast
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
REACHABILITY_LEDGER = REPO_ROOT / "reports" / "endpoint_reachability.json"

# Both copies of the cut driver. toolchain/bin is the OPERATIONAL copy; asserting
# only over project-knowledge is a denominator that structurally excludes the file
# that actually runs a release (CLAUDE.md 0). The idiom is the one already used at
# tests/test_pytest_runner_boundary.py:208.
# @943: one copy. The mirror is retired, so the tuple that existed to prove
# both copies agreed now names the only copy there is.
BD_CUT_COPIES = ("toolchain/bin/bd-cut",)


def _bd_cut_source(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _load_bd_cut(relative: str):
    """Import a bd-cut copy for behavioural assertions (not source scraping)."""
    path = REPO_ROOT / relative
    name = "bd_cut_" + relative.replace("/", "_").replace("-", "_")
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    saved_path, saved_argv = list(sys.path), list(sys.argv)
    sys.argv = ["bd-cut", "--plan"]
    try:
        loader.exec_module(module)
    finally:
        sys.path[:] = saved_path
        sys.argv[:] = saved_argv
    return module


def _bare_python3_sites(relative: str):
    """AST, not grep: every literal "python3" the driver could hand to subprocess.

    Grep is not a denominator -- the shebang and the usage docstring both contain
    the substring and neither is an argv. An exact-value Constant match fixes the
    predicate; walking the parsed module fixes the denominator.
    """
    tree = ast.parse(_bd_cut_source(relative))
    return sorted(
        f"{relative}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == "python3"
    )


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
        # @947 (register item 35). LAST by contract: it hashes
        # project-knowledge/, so nothing that could write there may follow it.
        # tests/test_v3_66_947_* asserts that position independently.
        "STATIC_KB",
    ]
    assert regen.repo_root(str(TOOL_PATH)) == str(REPO_ROOT)
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


@pytest.mark.parametrize("relative", BD_CUT_COPIES)
def test_operator_release_cut_uses_repository_regenerator_and_fails_closed(relative):
    release = _bd_cut_source(relative)

    assert 'os.path.join(work, "toolchain", "bin", "bd-regen-order")' in release, relative
    assert "shutil.which(\"bd-regen-order\")" not in release, relative
    assert "/home/claude/bin/bd-regen-order" not in release, relative
    assert "bd-regen-order missing from release work tree" in release, relative
    assert "derived docs may be stale" not in release, relative


@pytest.mark.parametrize("relative", BD_CUT_COPIES)
def test_operator_release_cut_never_spawns_a_bare_interpreter(relative):
    """Bare `python3` is 3.11-without-flask here; the parity regen then falls back."""
    assert _bare_python3_sites(relative) == [], (
        "bd-cut must resolve the work-tree interpreter, not argv[0]='python3'"
    )


@pytest.mark.parametrize("relative", BD_CUT_COPIES)
def test_operator_release_cut_resolves_the_work_tree_interpreter(relative, tmp_path):
    """$BD_PYTHON > <work>/venv > <work>/.venv > sys.executable, and only if executable."""
    cut = _load_bd_cut(relative)
    assert hasattr(cut, "python_for"), f"{relative} has no interpreter resolver"

    work = tmp_path / "work"
    work.mkdir()
    assert cut.python_for(str(work)) == sys.executable, relative

    dot_venv = work / ".venv" / "bin" / "python"
    dot_venv.parent.mkdir(parents=True)
    dot_venv.touch()
    dot_venv.chmod(0o644)
    # present but NOT executable -> must be skipped, not selected.
    assert cut.python_for(str(work)) == sys.executable, relative
    dot_venv.chmod(0o755)
    assert cut.python_for(str(work)) == str(dot_venv), relative

    venv = work / "venv" / "bin" / "python"
    venv.parent.mkdir(parents=True)
    venv.touch()
    venv.chmod(0o755)
    assert cut.python_for(str(work)) == str(venv), relative

    override = tmp_path / "bd-python"
    override.touch()
    override.chmod(0o755)
    os.environ["BD_PYTHON"] = str(override)
    try:
        assert cut.python_for(str(work)) == str(override), relative
    finally:
        os.environ.pop("BD_PYTHON", None)


@pytest.mark.parametrize("relative", BD_CUT_COPIES)
def test_operator_release_cut_rejects_a_degraded_parity_inventory(relative, tmp_path):
    """A parity inventory built off the ENDPOINT_CATALOG fallback must NO-CUT.

    Observed: a 1248-route inventory overwritten with 252 routes because the
    regen ran under an interpreter without flask, so url_map was unavailable and
    gui_parity_inventory silently degraded to the catalog. Reading the file back
    is the only way the driver can see which happened.
    """
    cut = _load_bd_cut(relative)
    assert hasattr(cut, "assert_live_inventory"), (
        f"{relative} does not read the regenerated inventory back"
    )

    reports = tmp_path / "reports"
    reports.mkdir()
    inventory = reports / "gui_parity_inventory.json"

    inventory.write_text(json.dumps({"route_source": "live url_map"}), encoding="utf-8")
    cut.assert_live_inventory(str(tmp_path))  # live -> proceeds

    for degraded in ("ENDPOINT_CATALOG.md (fallback)", "", None):
        inventory.write_text(json.dumps({"route_source": degraded}), encoding="utf-8")
        with pytest.raises(SystemExit) as excinfo:
            cut.assert_live_inventory(str(tmp_path))
        assert excinfo.value.code != 0, (relative, degraded)

    # Unknown is a THIRD STATE and it fails: unreadable/absent must not pass.
    inventory.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        cut.assert_live_inventory(str(tmp_path))
    inventory.unlink()
    with pytest.raises(SystemExit):
        cut.assert_live_inventory(str(tmp_path))


def test_policy_requires_canonical_regeneration_before_review():
    """Review packaging must name the canonical regeneration command.

    The interpreter is `venv/`, not `.venv/`. CLAUDE.md said `.venv` while the
    cloud environment builds `venv`, so the documented command exited 127 and
    the caller fell back to bare `python3` -- which is 3.11 without the project
    dependencies. A whole test band was measured on the wrong interpreter as a
    result. (CODEX_HANDOFF.md's `.venv` is a DIFFERENT machine, the WSL Codex
    box, and is correct there.)

    The absence assertion is load-bearing: `venv/bin/python` is a SUBSTRING of
    `.venv/bin/python`, so a presence check alone would still pass if the
    leading dot came back -- a gate whose predicate cannot exclude the very
    thing it exists to forbid.
    """
    policy = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert 'venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"' in policy
    assert ".venv/bin/python" not in policy, (
        "CLAUDE.md names `.venv/bin/python`, which does not exist in the cloud "
        "environment; the canonical interpreter is `venv/bin/python`"
    )


# @943: retired with the project-knowledge mirrors. There is no second copy
# of the executable toolchain left to compare against; tests/
# test_pk_mirrors_stay_retired.py asserts the stronger property that no such
# duplicate exists at all.
