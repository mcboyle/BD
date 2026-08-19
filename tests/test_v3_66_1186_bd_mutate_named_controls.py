"""v3.66.1186 -- executable mutation directions prove named controls."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_BAND = "tests/test_m.py"
_FIX = f"{_BAND}::test_fix"
_CONTROL = f"{_BAND}::test_control"
_BANDMATE = f"{_BAND}::test_bandmate"


def _tree(tmp_path: Path, *, decorative_control: bool = False) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "m.py").write_text(
        "MARKER = 1\n"
        "def render(value):\n"
        "    if value is None:\n"
        "        return 'unknown'\n"
        "    return str(value)\n",
        encoding="utf-8",
    )
    control = (
        "    assert _fresh().render(None) == 'unknown'\n"
        if decorative_control
        else "    assert _fresh().render(7) == '7'\n"
    )
    bandmate = (
        "    assert _fresh().render(7) == '7'\n"
        if decorative_control
        else "    assert _fresh().render('ok') == 'ok'\n"
    )
    (tmp_path / _BAND).write_text(
        "import importlib\n"
        "import m\n"
        "def _fresh():\n"
        "    return importlib.reload(m)\n"
        "def test_fix():\n"
        "    assert _fresh().render(None) == 'unknown'\n"
        "def test_control():\n"
        + control
        + "def test_bandmate():\n"
        + bandmate,
        encoding="utf-8",
    )
    return tmp_path


def _overcorrection(*, old: str = "    return str(value)", new: str = "    return 'unknown'", control: str | None = _CONTROL) -> dict:
    mutant = {
        "label": "apply the plausible over-correction",
        "file": "m.py",
        "old": old,
        "new": new,
        "direction": "overcorrection",
        "preserves": [_FIX],
    }
    if control is not None:
        mutant["control"] = control
    return mutant


def _regression(*, catcher: str = _FIX) -> dict:
    return {
        "label": "remove the fixed behavior",
        "file": "m.py",
        "old": "        return 'unknown'",
        "new": "        return 'broken'",
        "direction": "regression",
        "catcher": catcher,
    }


def _run(work: Path, mutants: list[dict], *extra: str) -> subprocess.CompletedProcess[str]:
    spec = work / "spec.json"
    spec.write_text(
        json.dumps({
            "schema": "bd-mutate-spec/1",
            "_comment": "synthetic direction contract",
            "subject": "named-control semantics",
            "band": [_BAND],
            "mutants": mutants,
        }),
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, str(_TOOL), "--spec", str(spec), "--work", str(work), "--json", *extra],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _payload(run: subprocess.CompletedProcess[str]) -> dict:
    start = run.stdout.find("{")
    assert start >= 0, run.stdout + run.stderr
    return json.loads(run.stdout[start:])


def test_an_overcorrection_caught_only_by_a_bandmate_is_ESCAPED(tmp_path):
    work = _tree(tmp_path, decorative_control=True)
    run = _run(work, [_overcorrection()])
    assert run.returncode == 1, run.stdout + run.stderr
    row = _payload(run)["rows"][0]
    assert row["verdict"] == "ESCAPED", row
    assert _CONTROL in row["why"] and "passed" in row["why"].lower(), row


def test_an_overcorrection_the_control_really_sees_stays_CAUGHT(tmp_path):
    work = _tree(tmp_path)
    run = _run(work, [_overcorrection()])
    assert run.returncode == 0, run.stdout + run.stderr
    assert _payload(run)["rows"][0]["verdict"] == "CAUGHT"


def test_a_control_that_is_not_collected_is_UNKNOWN_and_exits_2(tmp_path):
    work = _tree(tmp_path)
    missing = f"{_BAND}::test_renamed_control"
    run = _run(work, [_overcorrection(control=missing)])
    assert run.returncode == 2, run.stdout + run.stderr
    row = _payload(run)["rows"][0]
    assert row["verdict"] == "UNKNOWN", row
    assert missing in row["why"], row


def test_an_import_breaking_mutant_is_INVALID_not_CAUGHT(tmp_path):
    work = _tree(tmp_path)
    mutant = {
        "label": "parseable import break",
        "file": "m.py",
        "old": "MARKER = 1",
        "new": "import zzz_definitely_not_a_module",
        "direction": "regression",
        "catcher": _FIX,
    }
    run = _run(work, [mutant])
    assert run.returncode == 2, run.stdout + run.stderr
    row = _payload(run)["rows"][0]
    assert row["verdict"] == "INVALID", row
    assert "error" in row["why"].lower(), row


def test_control_failed_but_a_preserved_test_also_failed_is_INDISCRIMINATE(tmp_path):
    work = _tree(tmp_path)
    mutant = _overcorrection(
        old="    if value is None:\n        return 'unknown'\n    return str(value)",
        new="    return 'broken'",
    )
    run = _run(work, [mutant])
    assert run.returncode == 1, run.stdout + run.stderr
    row = _payload(run)["rows"][0]
    assert row["verdict"] == "INDISCRIMINATE", row
    assert _FIX in row["why"], row


def test_an_overcorrection_with_no_named_control_is_UNRUNNABLE(tmp_path):
    work = _tree(tmp_path)
    run = _run(work, [_overcorrection(control=None)])
    assert run.returncode == 2, run.stdout + run.stderr
    assert "overcorrection requires one named control" in run.stderr, run.stderr


def test_direction_filtering_to_zero_mutants_exits_2_not_0(tmp_path):
    work = _tree(tmp_path)
    run = _run(work, [_regression()], "--direction", "overcorrection")
    assert run.returncode == 2, run.stdout + run.stderr
    assert "0 of 1 mutants after --direction overcorrection" in run.stderr, run.stderr


def test_the_regression_direction_is_unchanged_by_default(tmp_path):
    work = _tree(tmp_path)
    run = _run(work, [_regression()])
    assert run.returncode == 0, run.stdout + run.stderr
    assert _payload(run)["rows"][0]["verdict"] == "CAUGHT"


def test_a_regression_caught_only_by_a_bandmate_is_ESCAPED(tmp_path):
    work = _tree(tmp_path, decorative_control=True)
    mutant = {
        **_overcorrection(),
        "direction": "regression",
        "catcher": _CONTROL,
    }
    mutant.pop("control")
    mutant.pop("preserves")
    run = _run(work, [mutant])
    assert run.returncode == 1, run.stdout + run.stderr
    row = _payload(run)["rows"][0]
    assert row["verdict"] == "ESCAPED", row
    assert _CONTROL in row["why"], row


def test_junit_xml_never_becomes_worktree_residue(tmp_path):
    work = _tree(tmp_path)
    run = _run(work, [_overcorrection()])
    assert run.returncode == 0, run.stdout + run.stderr
    residue = [p.relative_to(work).as_posix() for p in work.rglob("*.xml")]
    assert residue == [], residue
