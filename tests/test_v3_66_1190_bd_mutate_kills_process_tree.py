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


def _install_private_registrar(work: Path) -> None:
    """Publish exact register calls without global-lock or compile contention."""
    private_jobs = work / "toolchain" / "bin" / "bd-jobs"
    private_jobs.parent.mkdir(parents=True)
    private_jobs.write_text(
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import sys\n\n"
        f"registry = pathlib.Path({str(work / '.bd-jobs')!r})\n"
        "args = sys.argv[1:]\n"
        "if len(args) < 7 or args[0] != 'register' or args[1] != '--pid':\n"
        "    raise SystemExit(64)\n"
        "pid = int(args[2])\n"
        "if pid <= 0 or pid != os.getppid():\n"
        "    raise SystemExit(65)\n"
        "raw = pathlib.Path('/proc', str(pid), 'stat').read_text()\n"
        "starttime = int(raw[raw.rindex(')') + 1:].split()[19])\n"
        "registry.mkdir(parents=True, exist_ok=True)\n"
        "final = registry / f'{pid}-{starttime}.json'\n"
        "stage = registry / f'.{pid}-{starttime}.tmp'\n"
        "stage.write_text(json.dumps({\n"
        "    'pid': pid, 'pgid': os.getpgid(pid), 'starttime': starttime,\n"
        "}))\n"
        "stage.replace(final)\n",
        encoding="utf-8",
    )


def _tree(tmp_path: Path, phase: str) -> Path:
    (tmp_path / "tests").mkdir()
    _install_private_registrar(tmp_path)
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
        "    pid_path = Path('descendant.pid')\n"
        "    pid_temp = pid_path.with_name(f'{pid_path.name}.tmp.{child.pid}')\n"
        "    pid_temp.write_text(str(child.pid))\n"
        "    pid_temp.replace(pid_path)\n"
        "    with Path('spawned_pids.txt').open('a', encoding='utf-8') as stream:\n"
        "        stream.write(f'{child.pid}\\n')\n\n"
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


def _run(
    work: Path, phase: str
) -> tuple[subprocess.CompletedProcess[str], int | None, bool]:
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
    command = [
        sys.executable, str(_TOOL), "--spec", str(work / "spec.json"),
        "--work", str(work), "--timeout", "1", "--json",
    ]
    proc = subprocess.Popen(
        command,
        cwd=_REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    pid = None
    created_alive_before_timeout = False
    pid_path = work / "descendant.pid"
    observation_deadline = time.monotonic() + 20
    while time.monotonic() < observation_deadline:
        if pid_path.is_file():
            pid = int(pid_path.read_text(encoding="utf-8"))
            created_alive_before_timeout = proc.poll() is None and _alive(pid)
            break
        if proc.poll() is not None:
            break
        time.sleep(0.01)
    stdout, stderr = proc.communicate(timeout=20)
    return (
        subprocess.CompletedProcess(command, proc.returncode, stdout, stderr),
        pid,
        created_alive_before_timeout,
    )


@pytest.mark.parametrize("phase", ["collection", "execution"])
def test_timeout_kills_descendants_before_restoring_subject(tmp_path, phase):
    work = _tree(tmp_path, phase)
    anchor = "COLLECTION_ESCAPE" if phase == "collection" else "EXECUTION_ESCAPE"
    pid = None
    try:
        run, pid, created_alive_before_timeout = _run(work, phase)
        assert pid is not None, run.stdout + run.stderr
        assert created_alive_before_timeout, (
            f"descendant {pid} was not observed alive before the timeout")
        spawned_pids = [
            int(raw)
            for raw in (work / "spawned_pids.txt").read_text(
                encoding="utf-8").splitlines()
        ]
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
    assert spawned_pids == [pid], (
        f"expected exactly one descendant in {phase}, observed {spawned_pids}")
    registrations = list((work / ".bd-jobs").glob("*.json"))
    expected_registrations = 3 if phase == "collection" else 4
    assert len(registrations) == expected_registrations, (
        f"expected {expected_registrations} private registrations before the "
        f"{phase} timeout, observed {len(registrations)}")
    receipts = [json.loads(path.read_text()) for path in registrations]
    assert len({receipt["pid"] for receipt in receipts}) == expected_registrations
    assert all(receipt["pid"] == receipt["pgid"] for receipt in receipts), (
        "each runpy bootstrap must remain the leader of bd-mutate's private "
        f"process group: {receipts}")
    assert not alive_after_return, f"descendant {pid} survived tool return"
    assert not post_restore_activity, "descendant ran after the subject was restored"
    assert f"{anchor} = False" in (work / "m.py").read_text(encoding="utf-8")


def test_transform_control_only_observes_the_runner_identity():
    """The cleanup mutant loads while this non-behavioural control stays green."""
    assert _TOOL.is_file()
    assert _TOOL.name == "bd-mutate"
