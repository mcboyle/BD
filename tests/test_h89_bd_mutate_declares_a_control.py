"""H89 -- a bd-mutate spec can declare itself a control.

HARNESS_BACKLOG.md heading "H89 -- bd-mutate CANNOT DECLARE A CONTROL, SO A
CORRECT CONTROL IS BYTE-IDENTICAL IN SHAPE TO A REAL ESCAPE".

Before this change a transform control's JSON row carried ``direction:
regression``, a named catcher, ``verdict: ESCAPED`` and ``exit: 1`` -- the same
fields with the same values as a genuine uncaught mutant. The only
discriminators were the ``_transform_control`` filename suffix and a
"CONTROL " prefix inside a free-text label, and two readers misread a correct
control as a real escape inside one hour.

The contract pinned here:
  * a spec declares itself a control by the ``_transform_control.json`` suffix
    or by a top-level ``"control_spec": true`` field;
  * a declared control whose mutant ESCAPES is ``CONTROL-ESCAPED`` and the run
    exits 0 -- that is the expected result of a control;
  * NEGATIVE CONTROL: a declared control whose mutant is CAUGHT is
    ``CONTROL-CAUGHT``, a failure, exit 1 -- a control that stops controlling
    must not go unseen;
  * controls are outside the regression numerator and denominator: the report
    counts them apart from caught/escaped;
  * an undeclared spec keeps the old contract;
  * the ``why`` of a passed catcher names the other band members that failed,
    says none failed, or says the catcher was the only member that ran --
    never a fixed "another band member may have failed".
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_BAND = "tests/test_m.py"
_CATCHER = f"{_BAND}::test_catcher"
_BANDMATE = f"{_BAND}::test_bandmate"

_IMPORT_ONLY = "    assert fresh() is not None\n"
_SEES_BEHAVIOUR = "    assert fresh().render(None) == 'unknown'\n"
_SEES_OTHER_BEHAVIOUR = "    assert fresh().render(7) == '7'\n"
_SUFFIX = "h89_transform_control.json"
_PLAIN = "h89_plain.json"
_ABSENT = object()


def _tree(root: Path, *, catcher: str, bandmate: str | None = None) -> Path:
    work = root / "work"
    (work / "tests").mkdir(parents=True)
    (work / "m.py").write_text(
        "def render(value):\n"
        "    if value is None:\n"
        "        return 'unknown'\n"
        "    return str(value)\n",
        encoding="utf-8",
    )
    body = (
        "import importlib, m\n"
        "def fresh(): return importlib.reload(m)\n"
        "def test_catcher():\n" + catcher
    )
    if bandmate is not None:
        body += "def test_bandmate():\n" + bandmate
    (work / _BAND).write_text(body, encoding="utf-8")
    return work


def _git_tree(work: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "--", "m.py", _BAND], cwd=work, check=True)


def _mutant(label: str = "flip the None sentinel") -> dict:
    return {
        "label": label,
        "file": "m.py",
        "old": "        return 'unknown'",
        "new": "        return 'UNKNOWN'",
        "direction": "regression",
        "catcher": _CATCHER,
    }


def _spec(work: Path, name: str, *, control_spec: object = _ABSENT,
          mutants: list[dict] | None = None) -> Path:
    document = {
        "schema": "bd-mutate-spec/1",
        "_comment": "H89 synthetic control-declaration contract",
        "subject": "a declared control escapes by design",
        "band": [_BAND],
        "mutants": mutants or [_mutant()],
    }
    if control_spec is not _ABSENT:
        document["control_spec"] = control_spec
    path = work / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _run(spec: Path, work: Path, *extra: str,
         json_out: bool = True) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(_TOOL), "--spec", str(spec),
            "--work", str(work), *extra]
    if json_out:
        argv.append("--json")
    return subprocess.run(
        argv, cwd=_REPO, capture_output=True, text=True, timeout=120)


def _payload(run: subprocess.CompletedProcess[str]) -> dict:
    stdout = run.stdout
    assert "{" in stdout, run.stdout + run.stderr
    return json.loads(stdout[stdout.index("{"):])


def _declared(work: Path, declaration: str) -> Path:
    if declaration == "suffix":
        return _spec(work, _SUFFIX)
    return _spec(work, _PLAIN, control_spec=True)


@pytest.mark.parametrize("declaration", ["suffix", "field"])
def test_a_declared_control_that_escapes_is_CONTROL_ESCAPED_and_exits_0(
        tmp_path, declaration):
    work = _tree(tmp_path, catcher=_IMPORT_ONLY)
    run = _run(_declared(work, declaration), work)
    payload = _payload(run)
    assert run.returncode == 0, run.stdout + run.stderr
    assert payload["exit"] == 0, payload
    assert payload["control_spec"] is True, payload
    [row] = payload["rows"]
    assert row["verdict"] == "CONTROL-ESCAPED", row
    assert row["direction"] == "regression", row
    assert "only band member that ran" in row["why"], row
    assert "may have failed" not in row["why"], row


@pytest.mark.parametrize("declaration", ["suffix", "field"])
def test_NEGATIVE_a_declared_control_that_is_caught_is_a_failure_and_exits_1(
        tmp_path, declaration):
    work = _tree(tmp_path, catcher=_SEES_BEHAVIOUR)
    run = _run(_declared(work, declaration), work)
    payload = _payload(run)
    assert run.returncode == 1, run.stdout + run.stderr
    assert payload["exit"] == 1, payload
    assert payload["control_spec"] is True, payload
    [row] = payload["rows"]
    assert row["verdict"] == "CONTROL-CAUGHT", row
    assert _CATCHER in row["why"], row


def test_an_undeclared_spec_keeps_the_regression_contract(tmp_path):
    escapes = _tree(tmp_path / "escapes", catcher=_IMPORT_ONLY)
    run = _run(_spec(escapes, _PLAIN), escapes)
    payload = _payload(run)
    assert run.returncode == 1, run.stdout + run.stderr
    assert payload["control_spec"] is False, payload
    assert payload["rows"][0]["verdict"] == "ESCAPED", payload

    caught = _tree(tmp_path / "caught", catcher=_SEES_BEHAVIOUR)
    run = _run(_spec(caught, _PLAIN), caught)
    payload = _payload(run)
    assert run.returncode == 0, run.stdout + run.stderr
    assert payload["control_spec"] is False, payload
    assert payload["rows"][0]["verdict"] == "CAUGHT", payload

    explicit = _tree(tmp_path / "explicit", catcher=_IMPORT_ONLY)
    run = _run(_spec(explicit, _PLAIN, control_spec=False), explicit)
    payload = _payload(run)
    assert run.returncode == 1, run.stdout + run.stderr
    assert payload["control_spec"] is False, payload
    assert payload["rows"][0]["verdict"] == "ESCAPED", payload


@pytest.mark.parametrize("name, value, message", [
    (_PLAIN, "yes", "control_spec must be true or false"),
    (_PLAIN, 1, "control_spec must be true or false"),
    (_SUFFIX, False, 'declares "control_spec": false'),
])
def test_a_malformed_or_contradictory_declaration_is_UNRUNNABLE(
        tmp_path, name, value, message):
    work = _tree(tmp_path, catcher=_IMPORT_ONLY)
    before = (work / "m.py").read_bytes()
    run = _run(_spec(work, name, control_spec=value), work)
    assert run.returncode == 2, run.stdout + run.stderr
    assert "BD-MUTATE-UNRUNNABLE" in run.stderr, run.stderr
    assert message in run.stderr, run.stderr
    assert (work / "m.py").read_bytes() == before


def test_the_why_of_a_passed_catcher_reports_the_band_members_it_measured(tmp_path):
    alone = _tree(tmp_path / "alone", catcher=_IMPORT_ONLY)
    row = _payload(_run(_spec(alone, _PLAIN), alone))["rows"][0]
    assert row["verdict"] == "ESCAPED", row
    assert "only band member that ran" in row["why"], row
    assert "may have failed" not in row["why"], row

    red = _tree(tmp_path / "red", catcher=_IMPORT_ONLY, bandmate=_SEES_BEHAVIOUR)
    row = _payload(_run(_spec(red, _PLAIN), red))["rows"][0]
    assert row["verdict"] == "ESCAPED", row
    assert "other band member(s) failed" in row["why"], row
    assert _BANDMATE in row["why"], row
    assert "may have failed" not in row["why"], row

    green = _tree(tmp_path / "green", catcher=_IMPORT_ONLY,
                  bandmate=_SEES_OTHER_BEHAVIOUR)
    row = _payload(_run(_spec(green, _PLAIN), green))["rows"][0]
    assert row["verdict"] == "ESCAPED", row
    assert "no other band member failed" in row["why"], row
    assert _BANDMATE not in row["why"], row
    assert "may have failed" not in row["why"], row


def test_the_report_prints_the_control_verdict_outside_the_regression_counts(tmp_path):
    work = _tree(tmp_path / "escapes", catcher=_IMPORT_ONLY)
    second = {**_mutant("CONTROL second transform"),
              "old": "    return str(value)", "new": "    return repr(value)"}
    spec = _spec(work, _SUFFIX, mutants=[_mutant("CONTROL first transform"), second])
    run = _run(spec, work, json_out=False)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "verdict: CONTROL-ESCAPED (expected)" in run.stdout, run.stdout
    assert "0 caught, 0 escaped" in run.stdout, run.stdout
    assert "2 control-escaped (expected), 0 control-caught" in run.stdout, run.stdout
    assert "An escape names a behaviour" not in run.stdout, run.stdout
    assert "[ESCAPED" not in run.stdout, run.stdout
    assert not [line for line in run.stdout.splitlines()
                if line.startswith("ESCAPED")], run.stdout
    assert run.stdout.count("CONTROL-ESCAPED (expected)") >= 3, run.stdout

    caught = _tree(tmp_path / "caught", catcher=_SEES_BEHAVIOUR)
    run = _run(_spec(caught, _SUFFIX), caught, json_out=False)
    assert run.returncode == 1, run.stdout + run.stderr
    assert "verdict: CONTROL-CAUGHT (FAILURE)" in run.stdout, run.stdout
    assert "0 caught, 0 escaped" in run.stdout, run.stdout
    assert "0 control-escaped (expected), 1 control-caught (FAILURE)" in run.stdout, run.stdout
    assert "[CAUGHT" not in run.stdout, run.stdout


def test_emit_spec_carries_a_control_declaration_into_the_emitted_document(tmp_path):
    work = _tree(tmp_path, catcher=_IMPORT_ONLY)
    _git_tree(work)
    name = "v3_66_9999_h89_control.json"
    run = _run(_spec(work, _PLAIN, control_spec=True), work,
               "--emit-spec", name, "--subject", "H89 control carry-through")
    assert run.returncode == 0, run.stdout + run.stderr
    assert _payload(run)["rows"][0]["verdict"] == "CONTROL-ESCAPED", run.stdout
    emitted = json.loads(
        (work / "tests" / "mutants" / name).read_text(encoding="utf-8"))
    assert emitted["control_spec"] is True, emitted
    assert emitted["schema"] == "bd-mutate-spec/1", emitted


def test_emit_spec_refuses_a_control_suffix_for_a_run_that_was_not_a_control(tmp_path):
    work = _tree(tmp_path, catcher=_IMPORT_ONLY)
    _git_tree(work)
    name = "v3_66_9999_h89_transform_control.json"
    before = (work / "m.py").read_bytes()
    run = _run(_spec(work, _PLAIN), work,
               "--emit-spec", name, "--subject", "H89 name/declaration mismatch")
    assert run.returncode == 2, run.stdout + run.stderr
    assert "BD-MUTATE-UNRUNNABLE" in run.stderr, run.stderr
    assert "declares a control by suffix" in run.stderr, run.stderr
    assert not (work / "tests" / "mutants" / name).exists()
    assert (work / "m.py").read_bytes() == before


def test_transform_control_only_loads_the_tool_without_grading_a_control():
    """Battery transform control: loading the tool is not a grading check.

    Named as the catcher of tests/mutants/h89_transform_control.json, whose
    mutant is byte-identical to the primary battery's verdict-mapping mutant.
    That control DECLARES itself by its suffix, so its escape is reported as
    CONTROL-ESCAPED (expected) and the run exits 0 -- the first run of the
    contract this file pins, on its own evidence.
    """
    import runpy
    runpy.run_path(str(_TOOL), run_name="bd_mutate_h89_transform_control")
