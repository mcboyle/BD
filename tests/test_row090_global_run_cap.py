"""Row 90: repository-owned POSIX test launches need an outer lifetime cap.

``pytest-timeout`` is inside pytest, therefore it cannot end a run whose worker
or master has stopped making progress.  ``bd-run`` owns that cap for its callers
and capture owns a separate stage cap; this test owns the remaining tracked
POSIX launchers that can start a long test process directly.

The assertions deliberately inspect launch construction rather than treating
elapsed time as a test assertion. Host load changes elapsed time; whether the
launcher puts an outer cap in front of the test command does not.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
import subprocess

import pytest


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_CAP_ENV = "TEST_RUN_CAP_SECONDS"
_SHELL_LAUNCHERS = (
    Path("run_test.sh"),
    Path("run_all_tests.sh"),
    Path("slowest_tests.sh"),
)


def _calls(source: str, name: str) -> list[ast.Call]:
    tree = ast.parse(source)
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and node.func.attr == name
    ]


def _has_keyword(call: ast.Call, name: str) -> bool:
    return any(keyword.arg == name for keyword in call.keywords)


def _runs_test_runner(call: ast.Call) -> bool:
    return "run_tests.py" in ast.unparse(call)


def _shell_outer_cap(source: str) -> bool:
    """Recognise the cap shape without depending on its numeric value."""
    return (
        _CAP_ENV in source
        and 'timeout --kill-after=10 "$CAP_SECONDS"' in source
        and 'TEST-RUN-CAPPED' in source
    )


def test_outer_cap_detector_rejects_an_unbounded_launcher() -> None:
    unbounded = 'env -u BD_INSTALL_DIR "$PY" run_tests.py tests/\n'
    assert not _shell_outer_cap(unbounded), (
        "the detector accepted a direct long test launch with no outer cap"
    )


def test_every_repository_owned_posix_long_test_launcher_is_outer_capped() -> None:
    """Complete current POSIX denominator outside bd-run and capture.

    The three shell launchers are user-facing wrappers around ``run_tests.py``.
    ``dev_tools.start_run`` is the only application path that can launch that
    runner in the background.  Build verification and the old round helper are
    positive controls: they use their existing, independent timeout mechanisms
    and must not be rejected merely because they do not share this env variable.
    """
    assert len(_SHELL_LAUNCHERS) == 3
    shell_results = {
        path.as_posix(): _shell_outer_cap(
            (_REPO / path).read_text(encoding="utf-8")
        )
        for path in _SHELL_LAUNCHERS
    }

    dev_source = (_REPO / "bulk_downloader/dev_tools.py").read_text(
        encoding="utf-8"
    )
    dev_calls = _calls(dev_source, "Popen")
    dev_capped = (
        len(dev_calls) == 1
        and "_outer_capped_command(cmd)" in dev_source
        and _CAP_ENV in dev_source
    )

    build_source = (_REPO / "tools/build_release.py").read_text(encoding="utf-8")
    verify_source = (_REPO / "tools/verify_release.py").read_text(encoding="utf-8")
    round_source = (_REPO / "project-knowledge/round.sh").read_text(encoding="utf-8")
    build_caps = [
        call for call in _calls(build_source, "run")
        if _runs_test_runner(call) and _has_keyword(call, "timeout")
    ]
    verify_caps = [
        call for call in _calls(verify_source, "run")
        if _runs_test_runner(call) and _has_keyword(call, "timeout")
    ]
    controls = {
        "build-release": len(build_caps) == 1,
        "verify-release": len(verify_caps) == 1,
        "round": "timeout 110 env -u BD_INSTALL_DIR" in round_source,
    }

    assert len(shell_results) + 1 + len(controls) == 7
    assert all(controls.values()), f"existing independently capped controls regressed: {controls}"
    assert all(shell_results.values()) and dev_capped, (
        "long test launcher(s) can still run outside bd-run/capture with no "
        f"outer cap: shell={shell_results}, dev-tools={dev_capped}"
    )


@pytest.mark.parametrize(
    ("script_name", "arguments"),
    (("run_test.sh", ("tests/test_one.py",)), ("run_all_tests.sh", ()),
     ("slowest_tests.sh", ())),
)
def test_shell_launchers_report_a_real_outer_cap(
    tmp_path: Path, script_name: str, arguments: tuple[str, ...],
) -> None:
    """Exercise the wrapper's cap without asserting a scheduler-sensitive age."""
    script = tmp_path / script_name
    shutil.copy2(_REPO / script_name, script)
    python = tmp_path / "venv/bin/python"
    python.parent.mkdir(parents=True)
    marker = tmp_path / "subject-started"
    python.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"${1:-}\" = '-c' ]; then exit 0; fi\n"
        "printf launched > \"${ROW090_MARKER:?}\"\n"
        "sleep 30\n",
        encoding="utf-8",
    )
    python.chmod(python.stat().st_mode | 0o111)
    (tmp_path / "run_tests.py").write_text("# wrapper fixture\n", encoding="utf-8")
    env = os.environ | {
        _CAP_ENV: "3",
        "ROW090_MARKER": str(marker),
    }

    result = subprocess.run(
        ["bash", str(script), *arguments], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=15,
    )

    assert marker.read_text(encoding="utf-8") == "launched", (
        f"{script_name} never reached its subject; its cap verdict would be "
        f"proving the fixture rather than the launcher: {result.stdout}{result.stderr}"
    )
    assert result.returncode == 124, result.stdout + result.stderr
    assert "TEST-RUN-CAPPED" in result.stdout + result.stderr


def test_shell_cap_leaves_a_completed_subject_exit_code_alone(tmp_path: Path) -> None:
    script = tmp_path / "run_all_tests.sh"
    shutil.copy2(_REPO / "run_all_tests.sh", script)
    python = tmp_path / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
    python.chmod(python.stat().st_mode | 0o111)
    (tmp_path / "run_tests.py").write_text("# wrapper fixture\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(script)], cwd=tmp_path,
        env=os.environ | {_CAP_ENV: "60"}, capture_output=True, text=True,
        timeout=10,
    )

    assert result.returncode == 7, result.stdout + result.stderr
    assert "TEST-RUN-CAPPED" not in result.stdout + result.stderr
