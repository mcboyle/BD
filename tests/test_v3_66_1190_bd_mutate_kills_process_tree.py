"""v3.66.1190 -- timed-out mutation bands cannot escape descendant work."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_BAND = "tests/test_m.py"


def _tree(tmp_path: Path, phase: str) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "m.py").write_text(
        "COLLECTION_ESCAPE = False\n"
        "EXECUTION_ESCAPE = False\n",
        encoding="utf-8",
    )
    collection_escape = (
        "if m.COLLECTION_ESCAPE:\n"
        "    _spawn_escape()\n"
        "    time.sleep(8)\n"
        if phase == "collection" else ""
    )
    execution_escape = (
        "    if m.EXECUTION_ESCAPE:\n"
        "        _spawn_escape()\n"
        "        time.sleep(8)\n"
        if phase == "execution" else ""
    )
    (tmp_path / _BAND).write_text(
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "import m\n\n"
        "def _spawn_escape():\n"
        "    code = (\n"
        "        'import signal, time\\n'\n"
        "        'from pathlib import Path\\n'\n"
        "        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\\n'\n"
        "        'time.sleep(3)\\n'\n"
        "        'Path(\\\"post_restore.txt\\\").write_text(Path(\\\"m.py\\\").read_text())\\n'\n"
        "    )\n"
        "    child = subprocess.Popen(\n"
        "        [sys.executable, '-c', code],\n"
        "        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "    )\n"
        "    Path('descendant.pid').write_text(str(child.pid))\n\n"
        + collection_escape
        + "def test_behavior():\n"
        + execution_escape
        + "    assert True\n",
        encoding="utf-8",
    )
    return tmp_path


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _run(work: Path, phase: str) -> subprocess.CompletedProcess[str]:
    anchor = "COLLECTION_ESCAPE" if phase == "collection" else "EXECUTION_ESCAPE"
    (work / "spec.json").write_text(
        json.dumps({
            "schema": "bd-mutate-spec/1",
            "subject": f"owned {phase} process tree",
            "band": [_BAND],
            "mutants": [{
                "label": f"spawn descendant during {phase}",
                "file": "m.py",
                "old": f"{anchor} = False",
                "new": f"{anchor} = True",
                "direction": "regression",
                "catcher": f"{_BAND}::test_behavior",
            }],
        }),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            sys.executable, str(_TOOL), "--spec", str(work / "spec.json"),
            "--work", str(work), "--timeout", "1", "--json",
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.mark.parametrize("phase", ["collection", "execution"])
def test_timeout_kills_descendants_before_restoring_subject(tmp_path, phase):
    work = _tree(tmp_path, phase)
    anchor = "COLLECTION_ESCAPE" if phase == "collection" else "EXECUTION_ESCAPE"
    pid = None
    try:
        run = _run(work, phase)
        pid = int((work / "descendant.pid").read_text(encoding="utf-8"))
        alive_after_return = _alive(pid)
        time.sleep(3.25)
        post_restore_activity = (work / "post_restore.txt").exists()
    finally:
        if pid is not None and _alive(pid):
            os.kill(pid, signal.SIGKILL)

    assert run.returncode == 2, run.stdout + run.stderr
    payload = json.loads(run.stdout[run.stdout.find("{"):])
    row = payload["rows"][0]
    assert row["verdict"] == "UNKNOWN", row
    assert f"{phase} exceeded 1s" in row["why"], row
    assert not alive_after_return, f"descendant {pid} survived tool return"
    assert not post_restore_activity, "descendant ran after the subject was restored"
    assert f"{anchor} = False" in (work / "m.py").read_text(encoding="utf-8")
