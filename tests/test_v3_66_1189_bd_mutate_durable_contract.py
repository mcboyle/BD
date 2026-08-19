"""v3.66.1189 -- durable specs describe exactly the battery that was measured."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_BAND = "tests/test_m.py"
_CATCHER = f"{_BAND}::test_value"


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "m.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / _BAND).write_text(
        "import m\n"
        "def test_value():\n"
        "    assert m.VALUE == 1\n",
        encoding="utf-8",
    )
    return tmp_path


def _run(work: Path, document: object, *extra: str) -> subprocess.CompletedProcess[str]:
    spec = work / "spec.json"
    spec.write_text(json.dumps(document), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(_TOOL),
            "--spec",
            str(spec),
            "--work",
            str(work),
            "--json",
            *extra,
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _regression(*, catcher: bool = True) -> dict:
    mutant = {
        "label": "change the measured value",
        "file": "m.py",
        "old": "VALUE = 1",
        "new": "VALUE = 2",
        "direction": "regression",
    }
    if catcher:
        mutant["catcher"] = _CATCHER
    return mutant


def test_a_durable_regression_requires_a_nonempty_named_catcher(tmp_path):
    work = _tree(tmp_path)
    document = {
        "schema": "bd-mutate-spec/1",
        "subject": "missing durable catcher",
        "band": [_BAND],
        "mutants": [_regression(catcher=False)],
    }
    run = _run(work, document)
    assert run.returncode == 2, run.stdout + run.stderr
    assert "durable spec requires a named catcher" in run.stderr, run.stderr
    assert "baseline GREEN" not in run.stdout, run.stdout


def test_emit_spec_refuses_a_filtered_direction_before_running_or_publishing(tmp_path):
    work = _tree(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "--", "m.py", _BAND], cwd=work, check=True)
    destination = work / "tests" / "mutants" / "v3_66_9999_filtered.json"
    run = _run(
        work,
        [_regression()],
        "--band",
        _BAND,
        "--emit-spec",
        destination.name,
        "--subject",
        "filtered emission is not complete evidence",
        "--direction",
        "regression",
    )
    assert run.returncode == 2, run.stdout + run.stderr
    assert "--emit-spec requires --direction all" in run.stderr, run.stderr
    assert "baseline GREEN" not in run.stdout, run.stdout
    assert not destination.exists()
