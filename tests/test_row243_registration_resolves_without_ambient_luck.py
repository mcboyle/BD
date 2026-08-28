"""Row 243: owned pytest registration resolves from explicit repository truth.

The CI regression shape is deliberate: the pytest interpreter is invoked
through a path outside the checkout, ``bd-jobs`` is absent from ``PATH``, and
the work tree is a synthetic Git repository.  No interpreter-parent or PATH
search is allowed to rescue the launch.
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
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "toolchain" / "bin"
MUTANT_SPEC = REPO / "tests" / "mutants" / "row243_registration_resolution.json"
CONTROL_SPEC = (
    REPO / "tests" / "mutants"
    / "row243_registration_resolution_transform_control.json"
)
SCOPED_TOOLS = (
    "bd-ab",
    "bd-band",
    "bd-chromium-race",
    "bd-ladder",
    "bd-leakprobe",
    "bd-modwatch",
)
OWNING_TOOLS = (
    *SCOPED_TOOLS,
    "bd-mutate",
)
REGISTERED = "REGISTERED"
REGISTRATION_FAILED = "REGISTRATION FAILED"
NO_REGISTRAR = "NO REGISTRAR IN THIS TREE"
STATE_PREFIX = "BD-PYTEST REGISTRATION STATE: "


def _load_extensionless(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_init(root: Path, *tracked: str) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    if tracked:
        subprocess.run(
            ["git", "-C", str(root), "add", "--", *tracked], check=True
        )


def _outside_python(tmp_path: Path, ambient_registry: Path | None = None) -> Path:
    external_venv = tmp_path / "setup-python" / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--copies", str(external_venv)],
        check=True,
    )
    external = external_venv / "bin" / "python"
    site = subprocess.run(
        [str(external), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    pytest_site = Path(pytest.__file__).resolve().parents[1]
    Path(site, "row243-repository-dependencies.pth").write_text(
        str(pytest_site) + "\n", encoding="utf-8"
    )
    imported = subprocess.run(
        [str(external), "-c", "import pytest; print(pytest.__version__)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert not external.is_relative_to(REPO)
    assert not external.resolve().is_relative_to(REPO)
    assert external.is_file()
    assert imported.stdout.strip() == pytest.__version__
    if ambient_registry is not None:
        _copy_jobs_with_registry(
            external_venv.parent / "toolchain" / "bin" / "bd-jobs",
            ambient_registry,
        )
    return external


def _copy_jobs_with_registry(destination: Path, registry: Path) -> None:
    source = (BIN / "bd-jobs").read_text(encoding="utf-8")
    pattern = r'^JOBS_DIR = pathlib\.Path\([^\n]+\)$'
    matches = re.findall(pattern, source, flags=re.MULTILINE)
    assert len(matches) == 1, matches
    replaced, count = re.subn(
        pattern,
        "JOBS_DIR = pathlib.Path(%r)" % str(registry),
        source,
        flags=re.MULTILINE,
    )
    assert count == 1 and replaced != source
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(replaced, encoding="utf-8")
    destination.chmod(0o755)


def _probe_source() -> str:
    return '''\
import json
import os
from pathlib import Path


def _starttime(pid):
    raw = Path("/proc", str(pid), "stat").read_text()
    return int(raw[raw.rindex(")") + 1:].split()[19])


def test_registration_state_precedes_work():
    fired = Path(os.environ["ROW243_FIRED"])
    fired.write_text(str(int(fired.read_text()) + 1) if fired.exists() else "1")
    registry_name = os.environ.get("ROW243_REGISTRY")
    if registry_name:
        registry = Path(registry_name)
        records = sorted(registry.glob("*.json")) if registry.is_dir() else []
        assert len(records) == 1, (
            "registration precondition: expected exactly one live bd-jobs "
            f"entry before pytest work; observed {len(records)}"
        )
        row = json.loads(records[0].read_text())
        assert row["pid"] == os.getpid()
        assert row["starttime"] == _starttime(os.getpid())
        Path(os.environ["ROW243_SNAPSHOT"]).write_text(json.dumps(row))
'''


def _synthetic_work(tmp_path: Path) -> tuple[Path, Path, Path]:
    work = tmp_path / "synthetic-work"
    work.mkdir(parents=True)
    (work / "feature.txt").write_text("synthetic\n", encoding="utf-8")
    (work / "gate.py").write_text(_probe_source(), encoding="utf-8")
    _git_init(work, "feature.txt", "gate.py")
    fired = tmp_path / "probe-fired"
    snapshot = tmp_path / "registered-snapshot.json"
    assert not (work / "toolchain").exists()
    return work, fired, snapshot


def _child_env(fired: Path, snapshot: Path, registry: Path | None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    env.pop("PYTHONPATH", None)
    env.update(
        PATH="/usr/bin:/bin",
        BD_DISABLE_KEEPALIVE="1",
        ROW243_FIRED=str(fired),
        ROW243_SNAPSHOT=str(snapshot),
    )
    if registry is None:
        env.pop("ROW243_REGISTRY", None)
    else:
        env["ROW243_REGISTRY"] = str(registry)
    assert shutil.which("bd-jobs", path=env["PATH"]) is None
    return env


def _run_registered_command(
    module,
    python: Path,
    anchor: Path,
    work: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess:
    argv = module._registered_pytest_argv(
        python,
        "row243 CI-shape probe",
        "gate.py",
        "-q",
        "-p",
        "no:randomly",
        repository_root=anchor,
    )
    return subprocess.run(
        argv,
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _state_counts(*processes: subprocess.CompletedProcess) -> dict[str, int]:
    text = "\n".join(process.stdout + process.stderr for process in processes)
    return {
        state: text.count(STATE_PREFIX + state)
        for state in (REGISTERED, REGISTRATION_FAILED, NO_REGISTRAR)
    }


def test_transform_control_imports_launcher_without_asserting_registration():
    _load_extensionless("row243_transform_control", BIN / "bd-ladder")


def test_ci_shape_registers_from_explicit_anchor_with_no_ambient_rescue(tmp_path):
    """Reintroducing interpreter-parent/PATH search must break this test."""
    python = _outside_python(tmp_path)
    work, fired, snapshot = _synthetic_work(tmp_path)
    runner_repo = tmp_path / "runner-repository"
    registrar = runner_repo / "toolchain" / "bin" / "bd-jobs"
    registry = tmp_path / "registry"
    _copy_jobs_with_registry(registrar, registry)
    _git_init(runner_repo, "toolchain/bin/bd-jobs")
    module = _load_extensionless("row243_ladder_registered", BIN / "bd-ladder")

    process = _run_registered_command(
        module,
        python,
        runner_repo,
        work,
        _child_env(fired, snapshot, registry),
    )

    assert process.returncode == 0, process.stdout + process.stderr
    assert fired.read_text(encoding="utf-8") == "1"
    row = json.loads(snapshot.read_text(encoding="utf-8"))
    assert row["purpose"] == "row243 CI-shape probe"
    assert row["pid"] > 0 and row["starttime"] > 0
    assert _state_counts(process) == {
        REGISTERED: 1,
        REGISTRATION_FAILED: 0,
        NO_REGISTRAR: 0,
    }


def test_three_named_registration_outcomes_have_exact_nonzero_counts(tmp_path):
    python = _outside_python(tmp_path, tmp_path / "ambient-rescue-registry")
    module = _load_extensionless("row243_ladder_states", BIN / "bd-ladder")

    registered_work, registered_fired, registered_snapshot = _synthetic_work(
        tmp_path / "registered"
    )
    registered_anchor = tmp_path / "registered-anchor"
    registered_registry = tmp_path / "registered-registry"
    _copy_jobs_with_registry(
        registered_anchor / "toolchain" / "bin" / "bd-jobs",
        registered_registry,
    )
    _git_init(registered_anchor, "toolchain/bin/bd-jobs")
    registered = _run_registered_command(
        module,
        python,
        registered_anchor,
        registered_work,
        _child_env(
            registered_fired, registered_snapshot, registered_registry
        ),
    )

    absent_work, absent_fired, absent_snapshot = _synthetic_work(
        tmp_path / "absent"
    )
    absent_anchor = tmp_path / "absent-anchor"
    absent_anchor.mkdir()
    (absent_anchor / "README").write_text("not BD\n", encoding="utf-8")
    _git_init(absent_anchor, "README")
    absent = _run_registered_command(
        module,
        python,
        absent_anchor,
        absent_work,
        _child_env(absent_fired, absent_snapshot, None),
    )

    failed_work, failed_fired, failed_snapshot = _synthetic_work(
        tmp_path / "failed"
    )
    failed_anchor = tmp_path / "failed-anchor"
    failed_jobs = failed_anchor / "toolchain" / "bin" / "bd-jobs"
    failed_jobs.parent.mkdir(parents=True)
    failed_jobs.write_text(
        "import sys\n"
        "print('sentinel registrar refusal', file=sys.stderr)\n"
        "raise SystemExit(41)\n",
        encoding="utf-8",
    )
    _git_init(failed_anchor, "toolchain/bin/bd-jobs")
    failed = _run_registered_command(
        module,
        python,
        failed_anchor,
        failed_work,
        _child_env(failed_fired, failed_snapshot, tmp_path / "unused"),
    )

    assert registered.returncode == absent.returncode == 0
    assert failed.returncode == 3
    assert registered_fired.read_text(encoding="utf-8") == "1"
    assert absent_fired.read_text(encoding="utf-8") == "1"
    assert not failed_fired.exists(), (
        "registration-failed arm became permissive and pytest work ran"
    )
    assert "sentinel registrar refusal" in failed.stderr
    assert str(absent_anchor.resolve()) in absent.stderr
    assert _state_counts(registered, absent, failed) == {
        REGISTERED: 1,
        REGISTRATION_FAILED: 1,
        NO_REGISTRAR: 1,
    }


@pytest.mark.parametrize("tool_name", OWNING_TOOLS)
def test_real_bd_checkout_cannot_reach_no_registrar(tool_name):
    module = _load_extensionless(
        "row243_real_anchor_" + tool_name.replace("-", "_"), BIN / tool_name
    )
    state, registrar, detail = module._resolve_pytest_registrar(REPO)
    assert state == REGISTERED, (tool_name, state, registrar, detail)
    assert registrar == BIN / "bd-jobs"
    assert registrar.is_file()


@pytest.mark.parametrize("tool_name", OWNING_TOOLS)
def test_no_registrar_proof_is_consistent_across_every_launcher(tmp_path, tool_name):
    anchor = tmp_path / "non-bd-anchor"
    anchor.mkdir()
    (anchor / "README").write_text("synthetic\n", encoding="utf-8")
    _git_init(anchor, "README")
    module = _load_extensionless(
        "row243_absent_anchor_" + tool_name.replace("-", "_"), BIN / tool_name
    )

    state, registrar, detail = module._resolve_pytest_registrar(anchor)

    assert state == NO_REGISTRAR
    assert registrar is None
    assert detail == (
        "Git proves toolchain/bin/bd-jobs is not tracked under this anchor"
    )


def test_registration_unknown_refuses_before_a_child_can_start(tmp_path):
    module = _load_extensionless("row243_ladder_unknown", BIN / "bd-ladder")
    anchor = tmp_path / "damaged-bd-anchor"
    registrar = anchor / "toolchain" / "bin" / "bd-jobs"
    registrar.parent.mkdir(parents=True)
    registrar.write_text("raise SystemExit(0)\n", encoding="utf-8")
    _git_init(anchor, "toolchain/bin/bd-jobs")
    registrar.unlink()
    fired = tmp_path / "unknown-fired"

    with pytest.raises(
        RuntimeError,
        match=r"REGISTRATION UNKNOWN:.*tracked toolchain/bin/bd-jobs is absent.*nothing ran",
    ):
        module._registered_pytest_argv(
            sys.executable,
            "unknown must refuse",
            "gate.py",
            repository_root=anchor,
        )
    assert not fired.exists()


@pytest.mark.parametrize("tool_name", OWNING_TOOLS)
def test_tracked_registrar_symlink_is_unknown_for_every_launcher(
    tmp_path, tool_name
):
    anchor = tmp_path / tool_name
    registrar = anchor / "toolchain" / "bin" / "bd-jobs"
    registrar.parent.mkdir(parents=True)
    outside = tmp_path / (tool_name + "-outside-jobs")
    outside.write_text("raise SystemExit(0)\n", encoding="utf-8")
    registrar.symlink_to(outside)
    _git_init(anchor, "toolchain/bin/bd-jobs")
    module = _load_extensionless(
        "row243_symlink_" + tool_name.replace("-", "_"), BIN / tool_name
    )

    with pytest.raises(
        RuntimeError,
        match=r"REGISTRATION UNKNOWN:.*registrar.*symlink.*nothing ran",
    ):
        module._resolve_pytest_registrar(anchor)


@pytest.mark.parametrize("tool_name", OWNING_TOOLS)
def test_unavailable_registrar_metadata_is_unknown_for_every_launcher(
    tmp_path, tool_name
):
    anchor = tmp_path / tool_name
    registrar = anchor / "toolchain" / "bin" / "bd-jobs"
    registrar.parent.mkdir(parents=True)
    registrar.write_text("raise SystemExit(0)\n", encoding="utf-8")
    _git_init(anchor, "toolchain/bin/bd-jobs")
    module = _load_extensionless(
        "row243_unavailable_" + tool_name.replace("-", "_"), BIN / tool_name
    )

    registrar.parent.chmod(0)
    try:
        with pytest.raises(
            RuntimeError,
            match=(r"REGISTRATION UNKNOWN:.*registrar metadata is unavailable"
                   r".*nothing ran"),
        ):
            module._resolve_pytest_registrar(anchor)
    finally:
        registrar.parent.chmod(0o755)


def _copied_tool_with_failed_registrar(tmp_path: Path, tool_name: str) -> Path:
    anchor = tmp_path / (tool_name + "-runner")
    private_bin = anchor / "toolchain" / "bin"
    private_bin.mkdir(parents=True)
    runner = private_bin / tool_name
    shutil.copy2(BIN / tool_name, runner)
    jobs = private_bin / "bd-jobs"
    jobs.write_text(
        "import sys\n"
        "print('row243 owner-boundary refusal', file=sys.stderr)\n"
        "raise SystemExit(41)\n",
        encoding="utf-8",
    )
    tracked = ["toolchain/bin/" + tool_name, "toolchain/bin/bd-jobs"]
    if tool_name == "bd-band":
        shutil.copy2(BIN / "bdtools_sec.py", private_bin / "bdtools_sec.py")
        tracked.append("toolchain/bin/bdtools_sec.py")
    _git_init(anchor, *tracked)
    return runner


def test_bd_ab_sample_refuses_instead_of_counting_registration_failure(tmp_path):
    runner = _copied_tool_with_failed_registrar(tmp_path, "bd-ab")
    module = _load_extensionless("row243_ab_owner", runner)
    gate = tmp_path / "test_gate.py"
    gate.write_text("def test_gate():\n    assert True\n", encoding="utf-8")

    with pytest.raises(
        module.PytestRegistrationRefused,
        match=r"REGISTRATION FAILED:.*owner-boundary refusal",
    ):
        module.sample([str(gate), "-q", "-p", "no:randomly"], tmp_path / "ab.log")


def test_bd_band_verdict_refuses_for_registration_failure(tmp_path):
    runner = _copied_tool_with_failed_registrar(tmp_path, "bd-band")
    work = tmp_path / "band-work"
    tests = work / "tests"
    tests.mkdir(parents=True)
    fired = tmp_path / "band-fired"
    (tests / "test_gate.py").write_text(
        "import os\nfrom pathlib import Path\n\n"
        "def test_gate():\n"
        "    Path(os.environ['ROW243_BAND_FIRED']).write_text('ran')\n",
        encoding="utf-8",
    )
    (work / "venv").symlink_to(REPO / "venv", target_is_directory=True)
    _git_init(work, "tests/test_gate.py")
    env = dict(os.environ)
    env["ROW243_BAND_FIRED"] = str(fired)
    env.pop("BD_INSTALL_DIR", None)

    process = subprocess.run(
        [sys.executable, str(runner), "--work", str(work), "--skip-bandcheck",
         "tests/test_gate.py"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = process.stdout + process.stderr

    assert process.returncode == 2, output
    assert "BD-BAND UNEVALUABLE" in output
    assert STATE_PREFIX + REGISTRATION_FAILED in output
    assert "row243 owner-boundary refusal" in output
    assert not fired.exists()


def test_chromium_sample_refuses_instead_of_recording_empty_measurement(tmp_path):
    runner = _copied_tool_with_failed_registrar(tmp_path, "bd-chromium-race")
    module = _load_extensionless("row243_chromium_owner", runner)
    work = tmp_path / "chromium-work"
    target = work / module.TARGET_FILE
    target.parent.mkdir(parents=True)
    target.write_text(
        "def %s():\n    assert True\n" % module.TARGET_TEST,
        encoding="utf-8",
    )
    outdir = tmp_path / "chromium-out"
    outdir.mkdir()
    module.REPO = work
    module.PY = Path(sys.executable)
    module.SAMPLE_CAP = 60
    module.time.sleep = lambda _seconds: None

    with pytest.raises(
        module.PytestRegistrationRefused,
        match=r"REGISTRATION FAILED:.*owner-boundary refusal",
    ):
        module.run_sample(0, False, outdir, "registration-refusal")


def test_scoped_launch_sites_all_use_the_registered_helper():
    expected = {name: 1 for name in SCOPED_TOOLS}
    expected["bd-mutate"] = 2
    observed = {}
    direct = {}
    for tool_name in OWNING_TOOLS:
        tree = ast.parse((BIN / tool_name).read_text(encoding="utf-8"))
        observed[tool_name] = sum(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_registered_pytest_argv"
            for node in ast.walk(tree)
        )
        direct[tool_name] = sum(
            isinstance(node, (ast.List, ast.Tuple))
            and any(
                isinstance(left, ast.Constant)
                and left.value == "-m"
                and isinstance(right, ast.Constant)
                and right.value == "pytest"
                for left, right in zip(node.elts, node.elts[1:])
            )
            for node in ast.walk(tree)
        )
    assert observed == expected
    assert direct == {name: 0 for name in OWNING_TOOLS}


def test_copied_bd_mutate_runs_a_synthetic_tree_without_ambient_luck(tmp_path):
    """This is the five-failure CI topology from the preserved prior patch."""
    python = _outside_python(tmp_path)
    runner_repo = tmp_path / "runner-repository"
    runner = runner_repo / "toolchain" / "bin" / "bd-mutate"
    runner.parent.mkdir(parents=True)
    shutil.copy2(BIN / "bd-mutate", runner)
    _git_init(runner_repo, "toolchain/bin/bd-mutate")
    assert not (runner_repo / "toolchain" / "bin" / "bd-jobs").exists()

    work = tmp_path / "synthetic-mutation-work"
    tests = work / "tests"
    tests.mkdir(parents=True)
    feature = work / "feature.py"
    feature.write_text("VALUE = 1\n", encoding="utf-8")
    (tests / "test_gate.py").write_text(
        "from feature import VALUE\n\n"
        "def test_value():\n"
        "    assert VALUE == 1\n",
        encoding="utf-8",
    )
    _git_init(work, "feature.py", "tests/test_gate.py")
    spec = tmp_path / "synthetic-spec.json"
    spec.write_text(
        json.dumps({
            "schema": "bd-mutate-spec/1",
            "subject": "CI-shaped copied runner",
            "band": ["tests/test_gate.py::test_value"],
            "mutants": [{
                "label": "change value",
                "file": "feature.py",
                "old": "VALUE = 1",
                "new": "VALUE = 2",
                "direction": "regression",
                "catcher": "tests/test_gate.py::test_value",
            }],
        }),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    env.pop("PYTHONPATH", None)
    env.update(PATH="/usr/bin:/bin", BD_DISABLE_KEEPALIVE="1")
    assert shutil.which("bd-jobs", path=env["PATH"]) is None

    process = subprocess.run(
        [str(python), str(runner), "--spec", str(spec), "--work", str(work),
         "--json", "--timeout", "60"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert process.returncode == 0, process.stdout + process.stderr
    payload = json.loads(process.stdout)
    assert payload["selected"] == payload["total"] == 1
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["verdict"] == "CAUGHT"
    assert feature.read_text(encoding="utf-8") == "VALUE = 1\n"


def _mutation_work(tmp_path: Path) -> Path:
    work = tmp_path / "mutation-work"
    private_bin = work / "toolchain" / "bin"
    tests = work / "tests"
    private_bin.mkdir(parents=True)
    tests.mkdir()
    shutil.copy2(BIN / "bd-ladder", private_bin / "bd-ladder")
    _copy_jobs_with_registry(
        private_bin / "bd-jobs", tmp_path / "mutation-work-registry"
    )
    shutil.copy2(Path(__file__), tests / Path(__file__).name)
    _git_init(
        work,
        "toolchain/bin/bd-ladder",
        "toolchain/bin/bd-jobs",
        "tests/" + Path(__file__).name,
    )
    return work


def _mutation_runner(tmp_path: Path) -> Path:
    root = tmp_path / "mutation-runner"
    private_bin = root / "toolchain" / "bin"
    private_bin.mkdir(parents=True)
    runner = private_bin / "bd-mutate"
    shutil.copy2(BIN / "bd-mutate", runner)
    _copy_jobs_with_registry(
        private_bin / "bd-jobs", tmp_path / "mutation-runner-registry"
    )
    _git_init(root, "toolchain/bin/bd-mutate", "toolchain/bin/bd-jobs")
    return runner


def _mutation_payload(
    runner: Path, spec: Path, work: Path
) -> tuple[subprocess.CompletedProcess, dict]:
    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    env.update(PATH="/usr/bin:/bin", BD_DISABLE_KEEPALIVE="1")
    process = subprocess.run(
        [sys.executable, str(runner), "--spec", str(spec), "--work", str(work),
         "--json", "--timeout", "90"],
        cwd=work.parent,
        env=env,
        capture_output=True,
        text=True,
        # MEASURED, AND BELOW THE 240s ITEM BOUND. 300 was above it, so the
        # budget could never fire: its error path was dead code and a hang
        # would have killed the xdist worker instead of failing this test.
        # This call measured 46.28s serially on test5 at v3.66.1307; 150s is
        # roughly triple that, which covers the band's -n 24 contention while
        # staying subordinate to the bound governing the item.
        timeout=150,
    )
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            "bd-mutate emitted no complete JSON (%s):\nexit=%s\nstdout=%s\nstderr=%s"
            % (exc, process.returncode, process.stdout, process.stderr)
        )
    return process, payload


def test_bd_mutate_catches_three_regressions_and_control_escapes(tmp_path):
    work = _mutation_work(tmp_path)
    runner = _mutation_runner(tmp_path)

    caught_process, caught = _mutation_payload(runner, MUTANT_SPEC, work)
    control_process, control = _mutation_payload(runner, CONTROL_SPEC, work)

    assert caught_process.returncode == 0, (
        caught_process.stdout + caught_process.stderr
    )
    assert caught["selected"] == caught["total"] == 3
    assert len(caught["rows"]) == 3
    assert [row["verdict"] for row in caught["rows"]] == ["CAUGHT"] * 3
    assert {row["label"].split()[0] for row in caught["rows"]} == {
        "M1", "M2", "M3"
    }

    assert control_process.returncode == 1, (
        control_process.stdout + control_process.stderr
    )
    assert control["selected"] == control["total"] == 1
    assert len(control["rows"]) == 1
    assert control["rows"][0]["verdict"] == "ESCAPED"
