"""Cockpit governance cleanup cannot escape the per-test owned root."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import test_v3_66_120_autonomy_policy_foundation as representative


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_POP_PROBE = "BD_COCKPIT_TASKS_POP_PROBE"


def _contains_tasks_root(node: ast.AST | None) -> bool:
    if node is None:
        return False
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == "tasks_root"
        for child in ast.walk(node)
    )


def _cleanup_call_population() -> tuple[list[str], list[str]]:
    unsafe: list[str] = []
    guarded: list[str] = []
    for path in sorted((_REPO / "tests").glob("test*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        task_root_names = {
            target.id
            for assignment in ast.walk(tree)
            if isinstance(assignment, (ast.Assign, ast.AnnAssign))
            and _contains_tasks_root(assignment.value)
            for target in (
                assignment.targets
                if isinstance(assignment, ast.Assign)
                else [assignment.target]
            )
            if isinstance(target, ast.Name)
        }
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            function_name = (
                call.func.id
                if isinstance(call.func, ast.Name)
                else call.func.attr
                if isinstance(call.func, ast.Attribute)
                else ""
            )
            rel = path.relative_to(_REPO).as_posix()
            location = f"{rel}:{call.lineno}"
            if function_name == "remove_test_governance":
                guarded.append(location)
            if function_name != "rmtree" or not call.args:
                continue
            argument = call.args[0]
            names = {
                child.id for child in ast.walk(argument) if isinstance(child, ast.Name)
            }
            if _contains_tasks_root(argument) or names.intersection(task_root_names):
                unsafe.append(location)
    return unsafe, guarded


def test_representative_fresh_refuses_and_preserves_a_caller_owned_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with tempfile.TemporaryDirectory(
        prefix="bd-caller-owned-", dir=tmp_path.parent
    ) as raw_caller_root:
        caller_root = Path(raw_caller_root)
        governance = caller_root / "governance"
        governance.mkdir()
        sentinel = governance / "caller-data"
        sentinel.write_text("must survive\n", encoding="ascii")
        assert governance.is_dir() and sentinel.is_file()
        assert not governance.resolve().is_relative_to(Path.cwd().resolve())
        monkeypatch.setenv("BD_COCKPIT_TASKS", str(caller_root))

        refusal = None
        try:
            representative._fresh()
        except RuntimeError as exc:
            refusal = str(exc)

        assert sentinel.is_file(), "representative _fresh deleted caller-owned data"
        assert refusal is not None and "outside test-owned root" in refusal


def test_inherited_cockpit_tasks_is_popped_before_test_code_runs(
    tmp_path: Path,
) -> None:
    if os.environ.get(_POP_PROBE) == "1":
        assert "BD_COCKPIT_TASKS" not in os.environ
        return

    inherited_root = tmp_path.parent / "inherited-caller-tasks"
    inherited_root.mkdir()
    sentinel = inherited_root / "caller-data"
    sentinel.write_text("must survive\n", encoding="ascii")
    environment = os.environ.copy()
    environment.pop("BD_INSTALL_DIR", None)
    environment["BD_COCKPIT_TASKS"] = str(inherited_root)
    environment[_POP_PROBE] = "1"
    nodeid = (
        "tests/test_v3_66_1257_cockpit_tasks_test_root_is_confined.py::"
        "test_inherited_cockpit_tasks_is_popped_before_test_code_runs"
    )
    run = subprocess.run(
        [sys.executable, "-m", "pytest", nodeid, "-q", "-p", "no:randomly"],
        cwd=_REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert sentinel.is_file(), "the inheritance probe touched caller-owned data"


def test_representative_fresh_removes_test_owned_governance(tmp_path: Path) -> None:
    assert "BD_COCKPIT_TASKS" not in os.environ
    task_root = representative.tasks_root()
    assert task_root == (tmp_path / "cockpit_tasks").resolve()
    governance = task_root / "governance"
    governance.mkdir(parents=True)
    sentinel = governance / "test-data"
    sentinel.write_text("remove me\n", encoding="ascii")
    assert sentinel.is_file()

    representative._fresh()

    assert not governance.exists()


def test_all_measured_cockpit_cleanup_sites_use_the_guard() -> None:
    unsafe, guarded = _cleanup_call_population()
    assert unsafe == [], unsafe
    assert len(guarded) == 18, guarded
    assert len({location.rsplit(":", 1)[0] for location in guarded}) == 18, guarded


def test_transform_control_imports_guard_without_running_cleanup() -> None:
    from _cockpit_tasks import remove_test_governance

    assert callable(remove_test_governance)
