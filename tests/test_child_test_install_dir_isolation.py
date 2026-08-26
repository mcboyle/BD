"""Child-test launchers must not inherit an operator install directory."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_THIS_TEST = "tests/test_child_test_install_dir_isolation.py"
_MODE = "BD_INSTALL_DIR_CHILD_PROBE"
_RECEIPT = "BD_INSTALL_DIR_CHILD_RECEIPT"
_PROCESS_START_INSTALL_DIR = os.environ.get("BD_INSTALL_DIR")

# The regression probe must exercise the collection-time window as well as the
# test body.  If bd-band is mutated to forward the ambient value, importing
# this module reaches the same database resolver before an autouse fixture can
# run.  The target is always the outer test's sacrificial temp directory.
if os.environ.get(_MODE) == "inherited-probe" and _PROCESS_START_INSTALL_DIR:
    from bulk_downloader import db as _collection_probe_db

    _collection_probe_db.db_init()

# Re-derived from every tracked non-test Python/toolchain source containing a
# real pytest/run_tests launch, then traced through its environment builder.
# The hunt's 10 paths / 9 entry points omitted the final three entries here.
# Each tuple is one formerly-inheriting logical launch path and every function
# that must pop before that path reaches its child.
_PYTHON_LAUNCH_PATHS = {
    "dev-tools": ("bulk_downloader/dev_tools.py", (("start_run", "subprocess.Popen"),)),
    "bd-band": ("toolchain/bin/bd-band", (("band_env", "return"),)),
    "bd-cut": ("toolchain/bin/bd-cut", (("band", "run"),)),
    "bd-fullsuite-spawn": (
        "toolchain/bin/bd-fullsuite",
        (("run_one", "subprocess.run"),),
    ),
    "bd-fullsuite-fork": (
        "toolchain/bin/bd-fullsuite",
        (("_run_fork", "subprocess.Popen"), ("_fork_worker", "runpy.run_path")),
    ),
    "bd-mutation-test": (
        "toolchain/bin/bd-mutation-test",
        (("_run", "subprocess.run"),),
    ),
    "bd-precut": ("toolchain/bin/bd-precut", (("_run_insync", "subprocess.run"),)),
    "bd-retest": ("toolchain/bin/bd-retest", (("run_one", "subprocess.run"),)),
    "bd-parband": ("toolchain/bin/bd-parband", (("run_one", "subprocess.run"),)),
    "bd-ab": ("toolchain/bin/bd-ab", (("plain_env", "return"),)),
    "bd-mutate": ("toolchain/bin/bd-mutate", (("_child_env", "return"),)),
    "build-release": (
        "tools/build_release.py",
        (("_run_extracted_suite", "subprocess.run"),),
    ),
    "verify-release": (
        "tools/verify_release.py",
        (("_run_one", "subprocess.run"),),
    ),
}

# The original hunt did not include root/capture/Windows launch scripts.  The
# same tracked-tree search found nine more launch paths across eight entry
# points; exact counts keep the two capture lanes from collapsing into one.
_SHELL_LAUNCH_PATHS = {
    "capture.sh": (
        "env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 "
        "venv/bin/python -m pytest",
        2,
    ),
    "run_test.sh": (
        'env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 "$PYEXE" run_tests.py',
        1,
    ),
    "run_all_tests.sh": (
        'env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 "$PYEXE" run_tests.py',
        1,
    ),
    "slowest_tests.sh": (
        'env -u BD_INSTALL_DIR "$PY" run_tests.py',
        1,
    ),
    "project-knowledge/round.sh": (
        "timeout 110 env -u BD_INSTALL_DIR BD_HOME=",
        1,
    ),
    "run_test.bat": ('set "BD_INSTALL_DIR="', 1),
    "run_all_tests.bat": ('set "BD_INSTALL_DIR="', 1),
    "install_dev.bat": ('echo set "BD_INSTALL_DIR="', 1),
}


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _call_name(call: ast.Call) -> str:
    parts: list[str] = []
    node: ast.AST = call.func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1, f"expected one function {name!r}, found {len(matches)}"
    return matches[0]


def _pop_lines(function: ast.FunctionDef) -> list[int]:
    return [
        call.lineno
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "pop"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "BD_INSTALL_DIR"
    ]


def _boundary_lines(function: ast.FunctionDef, boundary: str) -> list[int]:
    if boundary == "return":
        return [node.lineno for node in ast.walk(function) if isinstance(node, ast.Return)]
    return [
        call.lineno
        for call in ast.walk(function)
        if isinstance(call, ast.Call) and _call_name(call) == boundary
    ]


def test_all_twenty_measured_entry_points_pop_all_twenty_two_child_paths() -> None:
    assert len(_PYTHON_LAUNCH_PATHS) == 13
    assert len({path for path, _ in _PYTHON_LAUNCH_PATHS.values()}) == 12
    assert sum(count for _, count in _SHELL_LAUNCH_PATHS.values()) == 9
    assert len(_SHELL_LAUNCH_PATHS) == 8
    assert len(_PYTHON_LAUNCH_PATHS) + sum(
        count for _, count in _SHELL_LAUNCH_PATHS.values()
    ) == 22
    assert len({path for path, _ in _PYTHON_LAUNCH_PATHS.values()}) + len(
        _SHELL_LAUNCH_PATHS
    ) == 20

    for label, (relative, requirements) in _PYTHON_LAUNCH_PATHS.items():
        source = (_REPO / relative).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
        for function_name, boundary in requirements:
            function = _function(tree, function_name)
            pops = _pop_lines(function)
            boundaries = _boundary_lines(function, boundary)
            assert len(pops) == 1, f"{label}: {function_name} pop lines={pops}"
            assert boundaries, f"{label}: {function_name} has no {boundary} boundary"
            assert pops[0] < max(boundaries), (
                f"{label}: BD_INSTALL_DIR is not popped before {boundary}"
            )

    for relative, (anchor, expected) in _SHELL_LAUNCH_PATHS.items():
        source = (_REPO / relative).read_text(encoding="utf-8")
        assert source.count(anchor) == expected, (
            f"{relative}: expected {expected} isolated child launch(es), "
            f"found {source.count(anchor)}"
        )


def test_bd_band_child_drops_inherited_install_dir_before_database_use(
    tmp_path: Path,
) -> None:
    if os.environ.get(_MODE) == "inherited-probe":
        inherited = _PROCESS_START_INSTALL_DIR
        receipt = Path(os.environ[_RECEIPT])
        receipt.write_text(
            "absent\n" if inherited is None else f"inherited={inherited}\n",
            encoding="ascii",
        )

        from bulk_downloader import db

        db.db_init()
        assert "BD_INSTALL_DIR" not in os.environ
        assert inherited is None, (
            "the nested real pytest process started with BD_INSTALL_DIR="
            f"{inherited!r}"
        )
        return

    sacrificial = tmp_path / "sacrificial-install"
    sacrificial.mkdir()
    sentinel = sacrificial / "operator-data"
    sentinel.write_bytes(b"must survive byte-for-byte\n")
    before = _snapshot(sacrificial)
    assert before == {"operator-data": b"must survive byte-for-byte\n"}

    receipt = tmp_path / "child-receipt"
    environment = os.environ.copy()
    environment["BD_INSTALL_DIR"] = str(sacrificial)
    environment[_MODE] = "inherited-probe"
    environment[_RECEIPT] = str(receipt)
    run = subprocess.run(
        [
            sys.executable,
            "toolchain/bin/bd-band",
            _THIS_TEST,
            "--work",
            ".",
            "--timeout",
            "120",
            "--skip-bandcheck",
        ],
        cwd=_REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert receipt.is_file(), "the nested pytest probe never ran"
    assert _snapshot(sacrificial) == before, (
        "the child test touched its caller's sacrificial install directory: "
        f"{sorted(_snapshot(sacrificial))}"
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert receipt.read_text(encoding="ascii") == "absent\n"


def test_autouse_boundary_pops_inheritance_inside_nested_real_pytest(
    tmp_path: Path,
) -> None:
    if os.environ.get(_MODE) == "fixture-probe":
        assert _PROCESS_START_INSTALL_DIR is not None
        assert "BD_INSTALL_DIR" not in os.environ
        Path(os.environ[_RECEIPT]).write_text("fixture-popped\n", encoding="ascii")
        return

    if os.environ.get(_MODE):
        pytest.skip("this boundary has its own nested pytest process")

    sacrificial = tmp_path / "fixture-inherited-install"
    sacrificial.mkdir()
    receipt = tmp_path / "fixture-child-receipt"
    environment = os.environ.copy()
    environment["BD_INSTALL_DIR"] = str(sacrificial)
    environment[_MODE] = "fixture-probe"
    environment[_RECEIPT] = str(receipt)
    nodeid = (
        f"{_THIS_TEST}::"
        "test_autouse_boundary_pops_inheritance_inside_nested_real_pytest"
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
    assert receipt.read_text(encoding="ascii") == "fixture-popped\n"
    assert not any(sacrificial.iterdir())


def test_test_owned_install_dir_still_supports_a_nested_real_pytest(
    clean_workdir: Path,
    tmp_path: Path,
) -> None:
    if os.environ.get(_MODE) == "owned-probe":
        assert os.environ.get("BD_INSTALL_DIR") == str(clean_workdir)

        from bulk_downloader import db

        expected = clean_workdir / "downloader_history.db"
        assert Path(db._resolve_db_path()) == expected
        db.db_init()
        assert expected.is_file()
        Path(os.environ[_RECEIPT]).write_text("owned\n", encoding="ascii")
        return

    if os.environ.get(_MODE) == "inherited-probe":
        pytest.skip("negative control runs in its own nested pytest process")

    receipt = tmp_path / "owned-child-receipt"
    environment = os.environ.copy()
    environment.pop("BD_INSTALL_DIR", None)
    environment[_MODE] = "owned-probe"
    environment[_RECEIPT] = str(receipt)
    nodeid = f"{_THIS_TEST}::test_test_owned_install_dir_still_supports_a_nested_real_pytest"
    run = subprocess.run(
        [sys.executable, "-m", "pytest", nodeid, "-q", "-p", "no:randomly"],
        cwd=_REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    assert receipt.read_text(encoding="ascii") == "owned\n"
