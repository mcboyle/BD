import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "vnc_display_sync.py"


def _launch(script, env):
    try:
        return subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=5, check=False)
    except OSError as exc:
        pytest.fail(f"SHEBANG_NOT_PATH_RESOLVED: {exc}")


def _relocate(tmp_path, name):
    script = tmp_path / name
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(SCRIPT.read_bytes())
    script.chmod(0o755)
    return script


def _env(tmp_path):
    env = os.environ.copy()
    env.update(PATH=str(tmp_path), PYTHONPATH=str(tmp_path), DISPLAY=":99", XDG_SESSION_TYPE="x11")
    (tmp_path / "pyautogui.py").write_text('print("ABSOLUTE_INTERPRETER_BYPASSED_PATH")\nraise SystemExit(0)\n')
    return env


@pytest.mark.parametrize("name", ["vnc_display_sync.py", "other-checkout/scripts/vnc_display_sync.py"])
def test_relocated_script_selects_python3_from_active_path(tmp_path, name):
    env = _env(tmp_path)
    python = tmp_path / "python3"
    python.write_text('#!/bin/sh\nprintf "PATH_PYTHON3_SELECTED:%s\\n" "$1"\n')
    python.chmod(0o755)
    script = _relocate(tmp_path, name)
    result = _launch(script, env)
    assert result.returncode == 0 and result.stdout.strip() == f"PATH_PYTHON3_SELECTED:{script}", "SHEBANG_NOT_PATH_RESOLVED"


def test_portable_launcher_positive_control(tmp_path):
    env = _env(tmp_path)
    python = tmp_path / "python3"
    python.write_text('#!/bin/sh\nprintf "PATH_PYTHON3_SELECTED:%s\\n" "$1"\n')
    python.chmod(0o755)
    control = tmp_path / "control.py"
    control.write_text("#!/usr/bin/env python3\n")
    control.chmod(0o755)
    result = _launch(control, env)
    assert result.returncode == 0 and result.stdout.strip() == f"PATH_PYTHON3_SELECTED:{control}"


def test_real_active_python_loads_dependency_from_environment(tmp_path):
    env = _env(tmp_path)
    python = tmp_path / "python3"
    python.symlink_to(sys.executable)
    (tmp_path / "pyautogui.py").write_text(
        'import json, os, sys\n'
        'print(json.dumps([sys.executable, os.environ["DISPLAY"], os.environ["XDG_SESSION_TYPE"]]))\n'
        'raise SystemExit(0)\n'
    )
    result = _launch(_relocate(tmp_path, "vnc_display_sync.py"), env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [str(python), ":99", "x11"], "ACTIVE_PYTHON_ENV_NOT_SELECTED"
