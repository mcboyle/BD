"""Row 243's toolchain half: owned pytest starts registered, tools stay single-file.

The seven recorded test5 identities were terminated by an authorised fleet
clean, not observed to exit.  This module therefore proves only the durable
toolchain half and pins the row state OPEN.
"""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "toolchain" / "bin"
OWNERSHIP = REPO / "toolchain" / "pytest_process_ownership.json"
MUTANT_SPEC = REPO / "tests" / "mutants" / "row243_inline_registration.json"
CONTROL_SPEC = (
    REPO / "tests" / "mutants" / "row243_inline_registration_transform_control.json"
)
REGISTRY_ANCHOR = 'JOBS_DIR = pathlib.Path("/tmp/bd-jobs")'

REGISTRATION_TOOLS = (
    "bd-ab",
    "bd-band",
    "bd-chromium-race",
    "bd-ladder",
    "bd-leakprobe",
    "bd-modwatch",
    "bd-mutate",
    "bd-parband",
    "bd-precut",
)

EXPECTED_TRACKED_LAUNCHES = {
    "toolchain/bin/bd-ab::sample::1",
    "toolchain/bin/bd-band::main::1",
    "toolchain/bin/bd-chromium-race::run_sample::1",
    "toolchain/bin/bd-ladder::probe::1",
    "toolchain/bin/bd-leakprobe::_run::1",
    "toolchain/bin/bd-modwatch::watch::1",
    "toolchain/bin/bd-mutate::_run_band::1",
    "toolchain/bin/bd-mutate::_run_band::2",
    "toolchain/bin/bd-parband::run_one::1",
    "toolchain/bin/bd-precut::main::1",
    "toolchain/bin/bd-sweep-run::section5_tokens::1",
    "toolchain/bin/bd-wedge-hunt::<module>::1",
}


def _function_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return "<module>"


def _is_direct_pytest_list(node: ast.AST) -> bool:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return False
    values = [
        part.value
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
        else None
        for part in node.elts
    ]
    return any(
        values[index] == "-m" and values[index + 1] == "pytest"
        for index in range(len(values) - 1)
    )


def _is_registered_pytest_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    function = node.func
    return (
        isinstance(function, ast.Name)
        and function.id == "_registered_pytest_argv"
    ) or (
        isinstance(function, ast.Attribute)
        and function.attr == "_registered_pytest_argv"
    )


def _is_command_metadata(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    parent = parents.get(node)
    return (
        isinstance(parent, ast.Call)
        and isinstance(parent.func, ast.Attribute)
        and isinstance(parent.func.value, ast.Name)
        and parent.func.value.id == "shlex"
        and parent.func.attr == "join"
    )


def _tracked_launch_population() -> dict[str, str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "toolchain/bin", "tools"],
        cwd=REPO,
        capture_output=True,
        check=True,
    )
    paths = [Path(item.decode()) for item in listed.stdout.split(b"\0") if item]
    assert paths, "tracked toolchain/tools denominator collapsed to zero"

    found: dict[str, str] = {}
    for relative in paths:
        try:
            tree = ast.parse((REPO / relative).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        candidates: list[tuple[int, str, str]] = []
        for node in ast.walk(tree):
            function = _function_name(node, parents)
            if function == "selftest":
                continue
            if _is_direct_pytest_list(node) and not _is_command_metadata(node, parents):
                candidates.append((node.lineno, function, "direct"))
            elif _is_registered_pytest_call(node):
                candidates.append((node.lineno, function, "registered"))

        per_function: defaultdict[str, int] = defaultdict(int)
        for _line, function, mode in sorted(candidates):
            per_function[function] += 1
            identity = (
                f"{relative.as_posix()}::{function}::{per_function[function]}"
            )
            assert identity not in found, f"duplicate launch identity {identity}"
            found[identity] = mode
    return found


def _load_extensionless(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copy_jobs_with_registry(source: Path, destination: Path, registry: Path) -> None:
    text = source.read_text(encoding="utf-8")
    pattern = r'^JOBS_DIR = pathlib\.Path\([^\n]+\)$'
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    assert len(matches) == 1, matches
    shutil.copy2(source, destination)
    replaced, count = re.subn(
        pattern,
        "JOBS_DIR = pathlib.Path(%r)" % str(registry),
        text,
        flags=re.MULTILINE,
    )
    assert count == 1
    destination.write_text(replaced, encoding="utf-8")


@pytest.mark.parametrize("tool_name", REGISTRATION_TOOLS)
def test_each_registration_tool_runs_from_its_bytes_alone(tmp_path, tool_name):
    isolated = tmp_path / tool_name
    isolated.mkdir()
    tool = isolated / tool_name
    shutil.copy2(BIN / tool_name, tool)
    assert [path.name for path in isolated.iterdir()] == [tool_name]

    env = dict(os.environ)
    for name in ("BD_ROOT", "BD_REPO", "BD_WORK_TREE", "PYTHONPATH"):
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, str(tool), "--help"],
        cwd=isolated,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ModuleNotFoundError" not in result.stdout + result.stderr
    assert [path.name for path in isolated.iterdir()] == [tool_name]


def test_nine_inline_tools_have_no_registration_sibling_import():
    observed = {}
    for name in REGISTRATION_TOOLS:
        source = (BIN / name).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=name)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        observed[name] = source.count("def _registered_pytest_argv(")
        assert "bdtools_sec" not in imports, (name, imports)
        assert not any(module.startswith("bdtools_") for module in imports), (
            name,
            imports,
        )
    assert observed == {name: 1 for name in REGISTRATION_TOOLS}


def _registration_probe_source() -> str:
    return '''\
import json
import os
from pathlib import Path


def _starttime(pid):
    raw = Path("/proc", str(pid), "stat").read_text()
    return int(raw[raw.rindex(")") + 1:].split()[19])


def test_registration_precedes_work():
    fired = Path(os.environ["ROW243_FIRED"])
    fired.write_text(str(int(fired.read_text()) + 1) if fired.exists() else "1")
    registry = Path(os.environ["ROW243_REGISTRY"])
    records = sorted(registry.glob("*.json")) if registry.is_dir() else []
    assert len(records) == 1, (
        "registration precondition: expected exactly one live bd-jobs entry "
        f"before pytest work; observed {len(records)}"
    )
    row = json.loads(records[0].read_text())
    assert row["pid"] == os.getpid()
    assert row["starttime"] == _starttime(os.getpid())
    Path(os.environ["ROW243_SNAPSHOT"]).write_text(json.dumps(row))
'''


def test_bd_ladder_registers_exactly_once_before_work(tmp_path):
    work = tmp_path / "work"
    tests = work / "tests"
    private_bin = work / "toolchain" / "bin"
    runner_dir = tmp_path / "runner"
    tests.mkdir(parents=True)
    private_bin.mkdir(parents=True)
    runner_dir.mkdir()

    ladder = runner_dir / "bd-ladder"
    shutil.copy2(BIN / "bd-ladder", ladder)
    assert [path.name for path in runner_dir.iterdir()] == ["bd-ladder"]

    registry = tmp_path / "registry"
    _copy_jobs_with_registry(BIN / "bd-jobs", private_bin / "bd-jobs", registry)
    probe = tests / "test_registration_probe.py"
    probe.write_text(_registration_probe_source(), encoding="utf-8")
    chain = work / "chain.txt"
    chain.write_text("tests/test_registration_probe.py\n", encoding="utf-8")

    negative_fired = tmp_path / "negative.fired"
    negative_snapshot = tmp_path / "negative.snapshot.json"
    negative_env = dict(os.environ)
    negative_env.update(
        ROW243_FIRED=str(negative_fired),
        ROW243_REGISTRY=str(registry),
        ROW243_SNAPSHOT=str(negative_snapshot),
    )
    negative = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_registration_probe.py",
         "-q", "-p", "no:randomly"],
        cwd=work,
        env=negative_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert negative_fired.read_text(encoding="utf-8") == "1"
    assert negative.returncode == 1, negative.stdout + negative.stderr
    assert "expected exactly one live bd-jobs entry before pytest work; observed 0" in (
        negative.stdout + negative.stderr
    )
    assert not negative_snapshot.exists()

    registered_fired = tmp_path / "registered.fired"
    snapshot = tmp_path / "registered.snapshot.json"
    registered_env = dict(os.environ)
    registered_env.update(
        ROW243_FIRED=str(registered_fired),
        ROW243_REGISTRY=str(registry),
        ROW243_SNAPSHOT=str(snapshot),
    )
    registered = subprocess.run(
        [sys.executable, str(ladder), "--chain", "chain.txt", "--guard",
         "tests/test_registration_probe.py", "--rungs", "0", "--jobs", "1",
         "--timeout", "30", "--logdir", str(tmp_path / "logs")],
        cwd=work,
        env=registered_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert registered_fired.read_text(encoding="utf-8") == "1"
    assert snapshot.is_file(), (
        "registered work reached the probe without publishing its exact entry:\n"
        + registered.stdout
        + registered.stderr
    )
    row = json.loads(snapshot.read_text(encoding="utf-8"))
    assert row["purpose"] == "bd-ladder rung 0"
    assert row["pid"] > 0 and row["starttime"] > 0
    assert " -m pytest " in " " + row["cmd"] + " "


def test_tracked_pytest_population_is_exact_and_registered():
    found = _tracked_launch_population()
    assert len(found) == len(EXPECTED_TRACKED_LAUNCHES) == 12
    assert set(found) == EXPECTED_TRACKED_LAUNCHES
    assert sum(mode == "registered" for mode in found.values()) == 10

    manifest = json.loads(OWNERSHIP.read_text(encoding="utf-8"))
    declared = {row["id"]: row for row in manifest["tracked_launches"]}
    assert set(declared) == EXPECTED_TRACKED_LAUNCHES
    unregistered = [
        identity
        for identity, mode in found.items()
        if mode == "direct"
        and declared[identity]["registration"] not in {
            "gated-exact-pid-before-release",
            "registered-ancestor-before-pytest",
        }
    ]
    assert unregistered == []


def test_non_owning_exemption_is_in_the_tool_and_reachable():
    precut = _load_extensionless("row243_pre_cut", BIN / "bd-precut")
    manifest = json.loads(OWNERSHIP.read_text(encoding="utf-8"))
    exemptions = manifest["non_owning_exemptions"]
    assert len(exemptions) == 1
    row = exemptions[0]
    assert row["subject"] == "bd-precut::_run_insync"
    assert precut.NON_OWNING_EXEMPTIONS == {row["subject"]: row["reason"]}
    assert "not a real pytest master" in row["reason"]


def test_row_243_remains_open_because_recorded_processes_did_not_end_naturally():
    manifest = json.loads(OWNERSHIP.read_text(encoding="utf-8"))
    disposition = manifest["recorded_pid_disposition"]
    assert manifest["row"] == 243 and manifest["row_state"] == "OPEN"
    assert disposition["satisfies_census_half"] is False
    assert disposition["pids"] == [
        1587184,
        1619109,
        1655881,
        1656718,
        1656837,
        1665966,
        1667312,
    ]
    assert "authorised fleet clean" in disposition["disposition"]
    assert "not observed to exit naturally" in disposition["disposition"]


def _mutation_work(tmp_path: Path) -> Path:
    work = tmp_path / "mutant-work"
    private_bin = work / "toolchain" / "bin"
    tests = work / "tests"
    private_bin.mkdir(parents=True)
    tests.mkdir()
    shutil.copy2(BIN / "bd-ladder", private_bin / "bd-ladder")
    _copy_jobs_with_registry(
        BIN / "bd-jobs", private_bin / "bd-jobs", tmp_path / "mutant-registry"
    )
    shutil.copy2(Path(__file__), tests / Path(__file__).name)
    os.symlink(REPO / "venv", work / "venv", target_is_directory=True)
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    return work


def _mutation_payload(spec: Path, work: Path) -> tuple[subprocess.CompletedProcess, dict]:
    result = subprocess.run(
        [sys.executable, str(BIN / "bd-mutate"), "--spec", str(spec),
         "--work", str(work), "--json", "--timeout", "90"],
        cwd=work.parent,
        capture_output=True,
        text=True,
        # DERIVED, NOT ROUND. The inner `bd-mutate --timeout 90` bounds the work
        # this subprocess actually does; 180s is 2x that, leaving headroom for
        # process start and JSON capture. It must also sit BELOW the 240s pytest
        # bound governing this item: at 240 the pytest timeout fires first, so
        # this budget could NEVER trigger and its TimeoutExpired path was dead
        # code -- and a hang would kill the xdist worker instead of failing the
        # test by name. tests/test_v3_66_1222_every_budget_is_subordinate_to_its_bound
        # refuses exactly that shape.
        timeout=180,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"bd-mutate emitted no complete JSON ({exc}):\n"
            f"exit={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result, payload


def test_bd_mutate_catches_removed_registration_and_control_escapes(tmp_path):
    work = _mutation_work(tmp_path)
    caught_process, caught = _mutation_payload(MUTANT_SPEC, work)
    assert caught_process.returncode == 0, caught_process.stdout + caught_process.stderr
    assert caught["total"] == caught["selected"] == 1
    assert len(caught["rows"]) == 1
    assert caught["rows"][0]["verdict"] == "CAUGHT"

    control_process, control = _mutation_payload(CONTROL_SPEC, work)
    assert control_process.returncode == 1, control_process.stdout + control_process.stderr
    assert control["total"] == control["selected"] == 1
    assert len(control["rows"]) == 1
    assert control["rows"][0]["verdict"] == "ESCAPED"
