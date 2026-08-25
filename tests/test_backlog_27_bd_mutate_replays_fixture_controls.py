"""Backlog 27 -- a fixture control must turn green again after restoration.

``bd-mutate`` already proves the recorded band is green before the first
mutant and proves an over-correction makes its named control fail while the
named preserves pass.  A fixture can nevertheless leave state outside the
mutated source file.  Without an immediate pristine replay, that residue can
make the control stay red and the tool still calls the mutant CAUGHT.
"""
from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_BAND = "tests/test_m.py"
_FIX = f"{_BAND}::test_fix"
_CONTROL = f"{_BAND}::test_control"
_IMPORT_ONLY = f"{_BAND}::test_import_only"


def _write_subject(work: Path) -> None:
    (work / "m.py").write_text(
        "FIXTURE_POISONS = False\n"
        "def render(value):\n"
        "    if value is None:\n"
        "        return 'unknown'\n"
        "    return str(value)\n",
        encoding="utf-8",
    )


def _run(
    work: Path,
    *,
    control: str = _CONTROL,
) -> subprocess.CompletedProcess[str]:
    spec = work / "spec.json"
    spec.write_text(
        json.dumps({
            "schema": "bd-mutate-spec/1",
            "_comment": "synthetic fixture-residue direction contract",
            "subject": "a fixture control flips red only while the mutant exists",
            "band": [_BAND],
            "mutants": [{
                "label": "make the fixture poison every control",
                "file": "m.py",
                "old": "FIXTURE_POISONS = False",
                "new": "FIXTURE_POISONS = True",
                "direction": "overcorrection",
                "control": control,
                "preserves": [_FIX],
            }],
        }),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            sys.executable,
            str(_TOOL),
            "--spec",
            str(spec),
            "--work",
            str(work),
            "--json",
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _payload(run: subprocess.CompletedProcess[str]) -> dict:
    start = run.stdout.find("{")
    assert start >= 0, run.stdout + run.stderr
    return json.loads(run.stdout[start:])


def _fixture_residue_tree(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    _write_subject(tmp_path)
    (tmp_path / _BAND).write_text(
        "import importlib\n"
        "from pathlib import Path\n"
        "import pytest\n"
        "import m\n"
        "EVENTS = Path('fixture-events.txt')\n"
        "POISON = Path('fixture-poisoned')\n"
        "def _event(line):\n"
        "    with EVENTS.open('a', encoding='utf-8') as stream:\n"
        "        stream.write(line + '\\n')\n"
        "@pytest.fixture\n"
        "def observed():\n"
        "    current = importlib.reload(m)\n"
        "    _event(f'fixture mutant={int(current.FIXTURE_POISONS)} "
        "poison={int(POISON.exists())}')\n"
        "    if current.FIXTURE_POISONS:\n"
        "        POISON.write_text('left by mutant fixture\\n', encoding='utf-8')\n"
        "    return current\n"
        "def test_fix():\n"
        "    assert importlib.reload(m).render(None) == 'unknown'\n"
        "def test_control(observed):\n"
        "    assert not POISON.exists(), 'fixture residue reached the control'\n"
        "    assert observed.render(7) == '7'\n"
        "def test_import_only():\n"
        "    importlib.reload(m)\n",
        encoding="utf-8",
    )
    return tmp_path


def _bounded_replay_tree(tmp_path: Path) -> Path:
    """A third-run-only bandmate proves the replay denominator stays narrow."""
    (tmp_path / "tests").mkdir()
    _write_subject(tmp_path)
    (tmp_path / _BAND).write_text(
        "import importlib\n"
        "from pathlib import Path\n"
        "import pytest\n"
        "import m\n"
        "COUNT = Path('bandmate-count.txt')\n"
        "@pytest.fixture\n"
        "def observed():\n"
        "    return importlib.reload(m)\n"
        "def test_fix():\n"
        "    assert importlib.reload(m).render(None) == 'unknown'\n"
        "def test_control(observed):\n"
        "    assert not observed.FIXTURE_POISONS\n"
        "def test_import_only():\n"
        "    importlib.reload(m)\n"
        "def test_bandmate_fails_only_on_a_third_execution():\n"
        "    count = int(COUNT.read_text()) if COUNT.exists() else 0\n"
        "    count += 1\n"
        "    COUNT.write_text(str(count), encoding='utf-8')\n"
        "    assert count != 3, 'unrelated third execution'\n",
        encoding="utf-8",
    )
    return tmp_path


def test_fixture_residue_makes_a_caught_control_UNKNOWN(tmp_path):
    work = _fixture_residue_tree(tmp_path)
    run = _run(work)

    events = (work / "fixture-events.txt").read_text(encoding="utf-8").splitlines()
    assert events[:2] == [
        "fixture mutant=0 poison=0",
        "fixture mutant=1 poison=0",
    ], events
    assert len(events) == 3, (
        f"the named fixture ran {len(events)} times, expected baseline, mutant, "
        f"and restored-source replay: {events}"
    )
    assert events[2] == "fixture mutant=0 poison=1", events
    assert run.returncode == 2, run.stdout + run.stderr
    row = _payload(run)["rows"][0]
    assert row["verdict"] == "UNKNOWN", row
    assert "restored-source replay" in row["why"], row
    assert _CONTROL in row["why"], row
    assert (work / "m.py").read_text(encoding="utf-8").startswith(
        "FIXTURE_POISONS = False"
    )


def test_restored_replay_judges_only_the_named_nonzero_population(tmp_path):
    work = _bounded_replay_tree(tmp_path)
    run = _run(work)

    assert run.returncode == 0, run.stdout + run.stderr
    row = _payload(run)["rows"][0]
    assert row["verdict"] == "CAUGHT", row
    assert (work / "bandmate-count.txt").read_text(encoding="utf-8") == "2"


def test_an_import_only_named_control_ESCAPES(tmp_path):
    work = _bounded_replay_tree(tmp_path)
    run = _run(work, control=_IMPORT_ONLY)

    assert run.returncode == 1, run.stdout + run.stderr
    row = _payload(run)["rows"][0]
    assert row["verdict"] == "ESCAPED", row
    assert _IMPORT_ONLY in row["why"], row


def test_transform_control_only_loads_the_tool_without_asserting_replay_behavior():
    """Battery transform control: loading the mutant is not a behavior check."""
    runpy.run_path(str(_TOOL), run_name="bd_mutate_transform_control")
