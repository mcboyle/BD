"""v3.66.1187 -- a wedged mutation measurement is UNKNOWN, never unbounded."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_BAND = "tests/test_m.py"


def _tree(tmp_path: Path, phase: str) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "m.py").write_text(
        "COLLECTION_STALL = False\n"
        "EXECUTION_STALL = False\n",
        encoding="utf-8",
    )
    collection_stall = (
        "if m.COLLECTION_STALL:\n"
        "    time.sleep(8)\n"
        if phase == "collection"
        else ""
    )
    (tmp_path / _BAND).write_text(
        "import time\n"
        "import m\n"
        + collection_stall
        + "def test_behavior():\n"
        "    if m.EXECUTION_STALL:\n"
        "        time.sleep(8)\n"
        "    assert True\n",
        encoding="utf-8",
    )
    return tmp_path


def _run(work: Path, phase: str) -> tuple[subprocess.CompletedProcess[str], float]:
    anchor = "COLLECTION_STALL" if phase == "collection" else "EXECUTION_STALL"
    spec = work / "spec.json"
    spec.write_text(
        json.dumps({
            "schema": "bd-mutate-spec/1",
            "subject": f"bounded {phase}",
            "band": [_BAND],
            "mutants": [{
                "label": f"wedge pytest {phase}",
                "file": "m.py",
                "old": f"{anchor} = False",
                "new": f"{anchor} = True",
                "direction": "regression",
                "catcher": f"{_BAND}::test_behavior",
            }],
        }),
        encoding="utf-8",
    )
    before = time.monotonic()
    run = subprocess.run(
        [
            sys.executable,
            str(_TOOL),
            "--spec",
            str(spec),
            "--work",
            str(work),
            "--timeout",
            "1",
            "--json",
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return run, time.monotonic() - before


def _row(run: subprocess.CompletedProcess[str]) -> dict:
    start = run.stdout.find("{")
    assert start >= 0, run.stdout + run.stderr
    return json.loads(run.stdout[start:])["rows"][0]


def test_a_collection_timeout_is_UNKNOWN_exit_2_and_restores_the_subject(tmp_path):
    work = _tree(tmp_path, "collection")
    run, elapsed = _run(work, "collection")
    assert run.returncode == 2, run.stdout + run.stderr
    row = _row(run)
    assert row["verdict"] == "UNKNOWN", row
    assert "collection exceeded 1s" in row["why"], row
    assert elapsed < 6, f"the 1s bound took {elapsed:.2f}s"
    assert "COLLECTION_STALL = False" in (work / "m.py").read_text(encoding="utf-8")


def test_an_execution_timeout_is_UNKNOWN_exit_2_and_leaves_no_junit(tmp_path):
    work = _tree(tmp_path, "execution")
    run, elapsed = _run(work, "execution")
    assert run.returncode == 2, run.stdout + run.stderr
    row = _row(run)
    assert row["verdict"] == "UNKNOWN", row
    assert "execution exceeded 1s" in row["why"], row
    assert elapsed < 6, f"the 1s bound took {elapsed:.2f}s"
    assert "EXECUTION_STALL = False" in (work / "m.py").read_text(encoding="utf-8")
    residue = [
        p.relative_to(work).as_posix()
        for p in work.rglob("*")
        if "junit" in p.name.lower()
    ]
    assert residue == [], residue
