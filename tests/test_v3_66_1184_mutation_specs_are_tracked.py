"""v3.66.1184 -- mutation evidence is tracked and directly re-runnable.

The repository had zero tracked ``bd-mutate`` JSON specs even though tests and
CHANGELOG entries claimed concrete mutation results.  These checks make the
artifact population non-empty, validate its executable anchors, and exercise
the production CLI rather than treating JSON text as evidence by itself.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_SCHEMA_V1 = "bd-mutate-spec/1"
_SCHEMA_V2 = "bd-mutation-spec/2"
_TOP_LEVEL_FIELDS = {"schema", "_comment", "subject", "band", "mutants"}
_BASE_MUTANT_FIELDS = {"label", "file", "old", "new", "direction"}
_REGRESSION_FIELDS = _BASE_MUTANT_FIELDS | {"catcher"}
_EXACT_REGRESSION_FIELDS = _REGRESSION_FIELDS | {"expected_failure", "preserves"}
_OVERCORRECTION_FIELDS = _BASE_MUTANT_FIELDS | {"control", "preserves"}


def _git_paths(*pathspecs: str) -> list[str]:
    run = subprocess.run(
        ["git", "ls-files", "--", *pathspecs],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in run.stdout.splitlines() if line]


def _tracked_specs() -> list[Path]:
    return [_REPO / rel for rel in _git_paths("tests/mutants/*.json")]


def _assert_anchor_counts(root: Path, document: dict) -> int:
    """Return the reconciled mutant count, refusing missing/ambiguous anchors."""
    mutants = document.get("mutants")
    assert isinstance(mutants, list) and mutants, "spec has zero mutants"
    checked = 0
    for mutant in mutants:
        rel = mutant.get("file")
        old = mutant.get("old")
        assert isinstance(rel, str) and rel, f"invalid mutant file: {mutant!r}"
        assert isinstance(old, str) and old, f"invalid old anchor: {mutant!r}"
        subject = root / rel
        assert subject.is_file(), f"mutant subject is absent: {rel}"
        count = subject.read_text(encoding="utf-8").count(old)
        assert count == 1, f"{rel}: old anchor occurs {count} times, expected exactly 1"
        checked += 1
    assert checked == len(mutants), (
        f"anchor reader checked {checked} of {len(mutants)} mutants"
    )
    return checked


def _defined_nodeids(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rel = path.relative_to(_REPO).as_posix()
    found: set[str] = set()

    def walk(body: list[ast.stmt], owners: tuple[str, ...] = ()) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                found.add("::".join((rel, *owners, node.name)))
            elif isinstance(node, ast.ClassDef):
                walk(node.body, (*owners, node.name))

    walk(tree.body)
    return found


def _write_synthetic_tree(tmp_path: Path) -> tuple[Path, str]:
    (tmp_path / "tests").mkdir()
    (tmp_path / "m.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_m.py").write_text(
        "import importlib\n"
        "import m\n"
        "def test_value():\n"
        "    importlib.reload(m)\n"
        "    assert m.value() == 1\n",
        encoding="utf-8",
    )
    return tmp_path, "tests/test_m.py"


def _run_tool(work: Path, spec: object, *extra: str) -> subprocess.CompletedProcess[str]:
    spec_path = work / "input-spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_TOOL), "--spec", str(spec_path), "--work", str(work), *extra],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _load_tool_module():
    loader = importlib.machinery.SourceFileLoader("bd_mutate_test", str(_TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_a_tracked_mutation_spec_exists_at_all():
    specs = _tracked_specs()
    assert specs, "tracked tests/mutants/*.json denominator is 0"


def test_every_tracked_spec_parses_and_declares_schema_band_and_mutants():
    specs = _tracked_specs()
    assert specs, "cannot validate a zero-spec population"
    tracked = set(_git_paths())
    checked = 0
    for path in specs:
        document = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(document, dict), f"{path}: tracked specs use object form"
        assert set(document) == _TOP_LEVEL_FIELDS, (
            f"{path}: fields {sorted(document)} != {sorted(_TOP_LEVEL_FIELDS)}"
        )
        schema = document["schema"]
        assert schema in {_SCHEMA_V1, _SCHEMA_V2}, path
        assert isinstance(document["_comment"], str) and document["_comment"].strip(), path
        assert isinstance(document["subject"], str) and document["subject"].strip(), path
        band = document["band"]
        assert isinstance(band, list) and band, f"{path}: band denominator is 0"
        assert len(band) == len(set(band)), f"{path}: duplicate band target"
        for target in band:
            assert isinstance(target, str) and target, f"{path}: invalid band target"
            rel = target.split("::", 1)[0]
            assert rel in tracked and (_REPO / rel).is_file(), (
                f"{path}: band target is absent or untracked: {target}"
            )
        mutants = document["mutants"]
        assert isinstance(mutants, list) and mutants, f"{path}: mutant denominator is 0"
        for mutant in mutants:
            assert isinstance(mutant, dict), f"{path}: mutant is not an object"
            direction = mutant.get("direction")
            if schema == _SCHEMA_V2:
                assert direction == "regression", (
                    f"{path}: v2 supports exact regression semantics only"
                )
            expected_fields = (
                _EXACT_REGRESSION_FIELDS
                if schema == _SCHEMA_V2 and direction == "regression"
                else _REGRESSION_FIELDS if direction == "regression"
                else _OVERCORRECTION_FIELDS if direction == "overcorrection"
                else set()
            )
            assert expected_fields, f"{path}: unsupported direction {direction!r}"
            assert set(mutant) == expected_fields, (
                f"{path}: mutant fields {sorted(mutant)} != {sorted(expected_fields)}"
            )
            for field in ("label", "file", "old", "new"):
                assert isinstance(mutant[field], str) and mutant[field], (
                    f"{path}: {field} must be a non-empty string"
                )
            assert mutant["file"] in tracked, f"{path}: untracked subject {mutant['file']}"
            if schema == _SCHEMA_V2:
                expected_failure = mutant["expected_failure"]
                assert set(expected_failure) == {"outcome", "signature"}, path
                assert expected_failure["outcome"] == "failed", path
                assert (isinstance(expected_failure["signature"], str)
                        and expected_failure["signature"]), path
                assert isinstance(mutant["preserves"], list) and mutant["preserves"], path
                assert len(mutant["preserves"]) == len(set(mutant["preserves"])), path
                assert mutant["catcher"] not in mutant["preserves"], path
            named = (
                [mutant["catcher"], *mutant.get("preserves", [])]
                if direction == "regression" else
                [mutant["control"], *mutant["preserves"]]
            )
            if direction == "overcorrection":
                assert isinstance(mutant["control"], str) and mutant["control"], path
                assert isinstance(mutant["preserves"], list) and mutant["preserves"], path
                assert len(mutant["preserves"]) == len(set(mutant["preserves"])), path
                assert mutant["control"] not in mutant["preserves"], path
            for nodeid in named:
                assert isinstance(nodeid, str) and nodeid, f"{path}: invalid nodeid"
                node_path = nodeid.split("::", 1)[0]
                assert node_path in tracked and (_REPO / node_path).is_file(), (
                    f"{path}: named test path is absent or untracked: {nodeid}"
                )
                assert nodeid in _defined_nodeids(_REPO / node_path), (
                    f"{path}: nodeid is not a defined test: {nodeid}"
                )
        checked += 1
    assert checked == len(specs), f"schema reader checked {checked} of {len(specs)} specs"


def test_every_tracked_mutant_anchor_occurs_exactly_once_in_its_file():
    specs = _tracked_specs()
    assert specs, "cannot judge anchors over a zero-spec population"
    checked = 0
    expected = 0
    for path in specs:
        document = json.loads(path.read_text(encoding="utf-8"))
        expected += len(document.get("mutants", []))
        checked += _assert_anchor_counts(_REPO, document)
    assert checked == expected and checked > 0, (
        f"anchor reader checked {checked} of {expected} declared mutants"
    )


def test_the_anchor_gate_rejects_zero_and_duplicate_matches(tmp_path):
    (tmp_path / "subject.py").write_text("anchor\nanchor\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="occurs 0 times"):
        _assert_anchor_counts(
            tmp_path,
            {"mutants": [{"file": "subject.py", "old": "absent"}]},
        )
    with pytest.raises(AssertionError, match="occurs 2 times"):
        _assert_anchor_counts(
            tmp_path,
            {"mutants": [{"file": "subject.py", "old": "anchor"}]},
        )


def test_object_form_uses_its_recorded_band_and_is_rerunnable(tmp_path):
    work, band = _write_synthetic_tree(tmp_path)
    spec = {
        "schema": _SCHEMA_V1,
        "_comment": "synthetic executable contract",
        "subject": "the recorded band catches a changed return value",
        "band": [band],
        "mutants": [{
            "label": "return 1 becomes 2",
            "file": "m.py",
            "old": "return 1",
            "new": "return 2",
            "direction": "regression",
            "catcher": "tests/test_m.py::test_value",
        }],
    }
    run = _run_tool(work, spec, "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    payload = json.loads(run.stdout[run.stdout.index("{"):])
    assert payload["rows"][0]["verdict"] == "CAUGHT", payload


def test_v2_regression_runs_only_its_catcher_and_declared_preserves(tmp_path):
    """V2 makes the validator's exact failure/control semantics executable."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "m.py").write_text(
        "VALUE = 1\nCONTROL = 7\n", encoding="utf-8"
    )
    test_path = tmp_path / "tests" / "test_m.py"
    test_path.write_text(
        "import importlib, m\n"
        "def fresh(): return importlib.reload(m)\n"
        "def test_catcher(): assert fresh().VALUE == 1\n"
        "def test_control(): assert fresh().CONTROL == 7\n"
        "def test_unselected_bandmate(): assert fresh().VALUE == 1\n",
        encoding="utf-8",
    )
    catcher = "tests/test_m.py::test_catcher"
    control = "tests/test_m.py::test_control"
    document = {
        "schema": "bd-mutation-spec/2",
        "_comment": "synthetic exact failure/control contract",
        "subject": "one named failure and one preserved control",
        "band": [catcher, control, "tests/test_m.py::test_unselected_bandmate"],
        "mutants": [{
            "label": "change value",
            "file": "m.py",
            "old": "VALUE = 1",
            "new": "VALUE = 2",
            "direction": "regression",
            "catcher": catcher,
            "expected_failure": {
                "outcome": "failed", "signature": "assert 2 == 1",
            },
            "preserves": [control],
        }],
    }
    run = _run_tool(tmp_path, document, "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    row = json.loads(run.stdout[run.stdout.index("{"):])["rows"][0]
    assert row["verdict"] == "CAUGHT", row
    assert row["executed_targets"] == [catcher, control]
    assert row["actual_failure"] == {
        "nodeid": catcher, "outcome": "failed", "signature": "assert 2 == 1",
    }


def test_v2_rejects_a_semantic_signature_not_observed_by_the_catcher(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "m.py").write_text(
        "VALUE = 1\nCONTROL = 7\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_m.py").write_text(
        "import importlib, m\n"
        "def test_value():\n"
        "    importlib.reload(m)\n"
        "    assert m.VALUE == 1\n"
        "def test_control():\n"
        "    assert m.CONTROL == 7\n",
        encoding="utf-8",
    )
    node = "tests/test_m.py::test_value"
    control = "tests/test_m.py::test_control"
    document = {
        "schema": "bd-mutation-spec/2",
        "_comment": "wrong-reason control",
        "subject": "a named catcher cannot fail for an unrelated reason",
        "band": [node, control],
        "mutants": [{
            "label": "change value", "file": "m.py",
            "old": "VALUE = 1", "new": "VALUE = 2",
            "direction": "regression", "catcher": node,
            "expected_failure": {
                "outcome": "failed", "signature": "unrelated signature",
            },
            "preserves": [control],
        }],
    }
    run = _run_tool(tmp_path, document, "--json")
    assert run.returncode == 1, run.stdout + run.stderr
    row = json.loads(run.stdout[run.stdout.index("{"):])["rows"][0]
    assert row["verdict"] == "INDISCRIMINATE", row
    assert "unrelated signature" in row["why"], row


def test_v2_rejects_signature_seen_only_in_a_passing_control(tmp_path):
    """Global pytest output may not authenticate the catcher's failure reason."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "m.py").write_text(
        "VALUE = 1\nCONTROL = 7\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_m.py").write_text(
        "import importlib, warnings, m\n"
        "def test_value():\n"
        "    importlib.reload(m)\n"
        "    assert m.VALUE == 1\n"
        "def test_control():\n"
        "    warnings.warn('CONTROL_ONLY_SIGNATURE')\n"
        "    assert m.CONTROL == 7\n",
        encoding="utf-8",
    )
    catcher = "tests/test_m.py::test_value"
    control = "tests/test_m.py::test_control"
    document = {
        "schema": "bd-mutation-spec/2",
        "_comment": "signature attribution negative control",
        "subject": "only the named catcher can supply its expected signature",
        "band": [catcher, control],
        "mutants": [{
            "label": "change value", "file": "m.py",
            "old": "VALUE = 1", "new": "VALUE = 2",
            "direction": "regression", "catcher": catcher,
            "expected_failure": {
                "outcome": "failed", "signature": "CONTROL_ONLY_SIGNATURE",
            },
            "preserves": [control],
        }],
    }
    run = _run_tool(tmp_path, document, "--json")
    assert run.returncode == 1, run.stdout + run.stderr
    row = json.loads(run.stdout[run.stdout.index("{"):])["rows"][0]
    assert row["verdict"] == "INDISCRIMINATE", row
    assert "CONTROL_ONLY_SIGNATURE" in row["why"], row


@pytest.mark.parametrize("forgery", [
    "missing failure",
    "wrong failure",
    "missing controls",
    "wrong controls",
])
def test_v2_publisher_refuses_unobserved_or_mismatched_results(tmp_path, forgery):
    """A CAUGHT label cannot reconstruct evidence the runner never observed."""
    tool = _load_tool_module()
    catcher = "tests/test_m.py::test_value"
    control = "tests/test_m.py::test_control"
    mutation_spec = [{
        "label": "change value", "file": "m.py",
        "old": "VALUE = 1", "new": "VALUE = 2",
        "direction": "regression", "catcher": catcher,
        "expected_failure": {
            "outcome": "failed", "signature": "assert 2 == 1",
        },
        "preserves": [control],
    }]
    row = {
        "label": "change value", "verdict": "CAUGHT",
        "subject_blob_sha256": "b" * 64,
        "mutated_blob_sha256": "c" * 64,
        "actual_failure": {
            "nodeid": catcher, "outcome": "failed", "signature": "assert 2 == 1",
        },
        "control_outcomes": [{"nodeid": control, "outcome": "passed"}],
    }
    if forgery == "missing failure":
        del row["actual_failure"]
    elif forgery == "wrong failure":
        row["actual_failure"]["nodeid"] = control
    elif forgery == "missing controls":
        del row["control_outcomes"]
    else:
        row["control_outcomes"][0]["outcome"] = "failed"

    with pytest.raises(ValueError, match="observed|GREEN"):
        tool._write_exact_mutation_evidence(
            tmp_path / "forged.json",
            candidate_sha="d" * 40,
            candidate_tree="e" * 40,
            spec_sha256="f" * 64,
            contract_sha256="a" * 64,
            environment_sha256="a" * 64,
            tool_sha256="a" * 64,
            spec=mutation_spec,
            rows=[row],
        )


def _observed_v2_row(*, label="change value", subject="b", mutated="c"):
    catcher = "tests/test_m.py::test_value"
    control = "tests/test_m.py::test_control"
    mutant = {
        "label": label, "file": "m.py", "old": "VALUE = 1", "new": "VALUE = 2",
        "direction": "regression", "catcher": catcher,
        "expected_failure": {"outcome": "failed", "signature": "assert 2 == 1"},
        "preserves": [control],
    }
    row = {
        "label": label, "verdict": "CAUGHT",
        "subject_blob_sha256": subject * 64,
        "mutated_blob_sha256": mutated * 64,
        "actual_failure": {
            "nodeid": catcher, "outcome": "failed", "signature": "assert 2 == 1",
        },
        "control_outcomes": [{"nodeid": control, "outcome": "passed"}],
    }
    return mutant, row


def _publish_v2(tool, tmp_path: Path, spec, rows):
    tool._write_exact_mutation_evidence(
        tmp_path / "result.json", candidate_sha="d" * 40,
        candidate_tree="e" * 40, spec_sha256="f" * 64,
        contract_sha256="a" * 64, environment_sha256="a" * 64,
        tool_sha256="a" * 64, spec=spec, rows=rows,
    )
    return json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("fault", ["missing", "duplicate", "reordered"])
def test_v2_publisher_rejects_missing_duplicate_or_reordered_labels(tmp_path, fault):
    tool = _load_tool_module()
    first_mutant, first_row = _observed_v2_row(label="first")
    second_mutant, second_row = _observed_v2_row(label="second", mutated="d")
    spec = [first_mutant, second_mutant]
    rows = [first_row, second_row]
    if fault == "missing":
        rows.pop()
    elif fault == "duplicate":
        second_mutant = dict(first_mutant)
        spec[1] = second_mutant
        rows[1] = dict(rows[0])
    else:
        rows.reverse()
    with pytest.raises(ValueError, match="label accounting"):
        _publish_v2(tool, tmp_path, spec, rows)


def test_v2_publisher_preserves_observed_mutated_blob(tmp_path):
    tool = _load_tool_module()
    mutant, row = _observed_v2_row(mutated="c")
    result = _publish_v2(tool, tmp_path, [mutant], [row])
    assert result["mutants"][0]["mutated_blob_sha256"] == "c" * 64
    assert result["mutants"][0]["mutated_blob_sha256"] != result["subject_blob_sha256"]


def test_v2_publisher_rejects_duplicate_declared_and_observed_labels(tmp_path):
    tool = _load_tool_module()
    first_mutant, first_row = _observed_v2_row(label="same", mutated="c")
    second_mutant, second_row = _observed_v2_row(label="same", mutated="d")
    with pytest.raises(ValueError, match="label accounting"):
        _publish_v2(
            tool, tmp_path,
            [first_mutant, second_mutant], [first_row, second_row],
        )


def test_v2_publisher_rejects_missing_observed_label(tmp_path):
    tool = _load_tool_module()
    first_mutant, first_row = _observed_v2_row(label="first", mutated="c")
    second_mutant, _second_row = _observed_v2_row(label="second", mutated="d")
    with pytest.raises(ValueError, match="label accounting"):
        _publish_v2(tool, tmp_path, [first_mutant, second_mutant], [first_row])


def test_v2_rejects_ambiguous_junit_identity(tmp_path):
    tool = _load_tool_module()
    junit = tmp_path / "ambiguous.xml"
    junit.write_text(
        '<testsuite><testcase classname="tests.test_m" name="test_value"/>'
        '<testcase classname="tests.test_m" name="test_value"/></testsuite>',
        encoding="utf-8",
    )
    _outcomes, _records, error = tool._read_junit(
        junit, ["tests/test_m.py::test_value", "tests/test_m.py::test_value"],
    )
    assert error and "maps to" in error and "2 collected nodeids" in error


def test_v2_rejects_a_red_preserved_control():
    tool = _load_tool_module()
    mutant, _row = _observed_v2_row()
    catcher = mutant["catcher"]
    control = mutant["preserves"][0]
    result = {
        "rc": 1, "collection_error": False, "measurement_error": None,
        "outcomes": {catcher: ["failed"], control: ["failed"]},
        "records": {catcher: [{
            "outcome": "failed", "failure_text": "assert 2 == 1",
            "failure_text_sha256": "a" * 64,
        }]},
    }
    verdict, why = tool._grade_mutant(mutant, result, exact_semantics=True)
    assert verdict == "INDISCRIMINATE"
    assert "preserved controls were not green" in why


def test_v2_refuses_a_red_baseline(tmp_path, monkeypatch):
    tool = _load_tool_module()
    mutant, _row = _observed_v2_row()
    monkeypatch.setattr(tool, "journal_preflight", lambda work: (0, []))
    monkeypatch.setattr(tool, "_purge_pycache", lambda work: 0)
    monkeypatch.setattr(tool, "_run_band", lambda *args, **kwargs: {
        "rc": 1, "tail": "baseline failed", "collected": [mutant["catcher"]],
        "outcomes": {mutant["catcher"]: ["failed"]},
        "records": {}, "measurement_error": None, "collection_error": False,
        "junit_evidence": None,
    })
    rc, rows = tool.run_battery(
        [mutant], [mutant["catcher"]], tmp_path, verbose=False,
        exact_semantics=True,
    )
    assert rc == 2 and rows == []


def test_v2_refuses_candidate_or_spec_change_during_battery(
        tmp_path, monkeypatch, capsys):
    tool = _load_tool_module()
    work = tmp_path / "work"
    work.mkdir()
    spec_path = work / "spec.json"
    mutant, row = _observed_v2_row()
    spec_path.write_text(json.dumps({
        "schema": "bd-mutation-spec/2", "_comment": "identity check",
        "subject": "one subject", "band": [mutant["catcher"], *mutant["preserves"]],
        "mutants": [mutant],
    }), encoding="utf-8")
    identities = iter([("a" * 40, "b" * 40, "c" * 64),
                       ("d" * 40, "b" * 40, "c" * 64)])
    monkeypatch.setattr(tool, "_evidence_candidate", lambda *args: next(identities))
    monkeypatch.setattr(tool, "run_battery", lambda *args, **kwargs: (0, [row]))
    evidence = tmp_path / "evidence" / "result.json"
    evidence.parent.mkdir()
    monkeypatch.setattr(sys, "argv", [
        "bd-mutate", "--spec", str(spec_path), "--work", str(work),
        "--evidence-out", str(evidence),
        "--contract-sha256", "a" * 64, "--environment-sha256", "a" * 64,
        "--tool-sha256", "a" * 64,
    ])
    assert tool.main() == 2
    assert "identity changed during the mutation battery" in capsys.readouterr().err
    assert not evidence.exists()


def test_v2_result_binds_exact_contract_environment_and_tool_hashes(tmp_path):
    tool = _load_tool_module()
    mutant, row = _observed_v2_row()
    result = _publish_v2(tool, tmp_path, [mutant], [row])
    assert result["contract_sha256"] == "a" * 64
    assert result["environment_sha256"] == "a" * 64
    assert result["tool_sha256"] == "a" * 64


@pytest.mark.parametrize(("verdict", "expected"), [
    ("CAUGHT", 0), ("ESCAPED", 1), ("INDISCRIMINATE", 1),
    ("UNKNOWN", 2), ("INVALID", 2), ("ERROR", 2),
])
def test_battery_exit_accepts_only_all_caught(verdict, expected):
    tool = _load_tool_module()
    assert tool._battery_exit([{"verdict": verdict}]) == expected


def test_v2_publisher_refuses_multiple_subject_blobs(tmp_path):
    tool = _load_tool_module()
    first_mutant, first_row = _observed_v2_row(label="first", subject="b")
    second_mutant, second_row = _observed_v2_row(
        label="second", subject="c", mutated="d",
    )
    with pytest.raises(ValueError, match="one exact subject blob"):
        _publish_v2(
            tool, tmp_path, [first_mutant, second_mutant], [first_row, second_row],
        )


def test_v2_publisher_refuses_wrong_actual_failure(tmp_path):
    tool = _load_tool_module()
    mutant, row = _observed_v2_row()
    row["actual_failure"]["nodeid"] = mutant["preserves"][0]
    with pytest.raises(ValueError, match="observed catcher failure"):
        _publish_v2(tool, tmp_path, [mutant], [row])


def test_v2_publisher_refuses_wrong_control_outcome(tmp_path):
    tool = _load_tool_module()
    mutant, row = _observed_v2_row()
    row["control_outcomes"][0]["outcome"] = "failed"
    with pytest.raises(ValueError, match="observed GREEN controls"):
        _publish_v2(tool, tmp_path, [mutant], [row])


def test_battery_exit_refuses_escaped():
    tool = _load_tool_module()
    assert tool._battery_exit([{"verdict": "ESCAPED"}]) == 1


def test_v2_emits_candidate_bound_cut_mutation_result(tmp_path):
    work = tmp_path / "repo"
    (work / "tests" / "mutants").mkdir(parents=True)
    (work / "m.py").write_text("VALUE = 1\nCONTROL = 7\n", encoding="utf-8")
    (work / "tests" / "test_m.py").write_text(
        "import importlib, m\n"
        "def test_value():\n"
        "    importlib.reload(m)\n"
        "    assert m.VALUE == 1\n"
        "def test_control():\n"
        "    assert m.CONTROL == 7\n",
        encoding="utf-8",
    )
    catcher = "tests/test_m.py::test_value"
    control = "tests/test_m.py::test_control"
    spec_path = work / "tests" / "mutants" / "v3_66_9999_exact.json"
    spec_path.write_text(json.dumps({
        "schema": "bd-mutation-spec/2", "_comment": "evidence contract",
        "subject": "one candidate-bound result", "band": [catcher, control],
        "mutants": [{
            "label": "change value", "file": "m.py",
            "old": "VALUE = 1", "new": "VALUE = 2",
            "direction": "regression", "catcher": catcher,
            "expected_failure": {
                "outcome": "failed", "signature": "assert 2 == 1",
            },
            "preserves": [control],
        }],
    }), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "mutation@example.invalid"],
                   cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "Mutation Test"],
                   cwd=work, check=True)
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=work, check=True)
    evidence = tmp_path / "mutation-result.json"
    digest = "a" * 64
    run = subprocess.run([
        sys.executable, str(_TOOL), "--spec", str(spec_path),
        "--work", str(work), "--evidence-out", str(evidence),
        "--contract-sha256", digest, "--environment-sha256", digest,
        "--tool-sha256", digest, "--json",
    ], cwd=_REPO, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    value = json.loads(evidence.read_text(encoding="utf-8"))
    assert value["schema"] == "cut-mutation-result/2"
    assert value["candidate_sha"] == subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, capture_output=True,
        text=True, check=True,
    ).stdout.strip()
    assert value["declared_labels"] == ["change value"]
    assert value["selected_labels"] == value["executed_labels"] == ["change value"]
    assert value["mutants"][0]["actual_failures"] == [{
        "nodeid": catcher, "outcome": "failed", "signature": "assert 2 == 1",
    }]
    assert value["mutants"][0]["controls"] == [{
        "nodeid": control, "outcome": "passed",
    }]
    node_evidence = Path(str(evidence) + ".nodes.json")
    node_value = json.loads(node_evidence.read_text(encoding="utf-8"))
    assert node_value["schema"] == "cut-mutation-node-evidence/1"
    assert node_value["candidate_sha"] == value["candidate_sha"]
    assert node_value["candidate_tree"] == value["candidate_tree"]
    assert node_value["spec_sha256"] == value["spec_sha256"]
    assert [row["kind"] for row in node_value["runs"]] == ["baseline", "mutant"]
    for row in node_value["runs"]:
        artifact = Path(row["artifact"])
        assert artifact.is_file()
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == row["sha256"]
    mutant_run = node_value["runs"][1]
    assert mutant_run["label"] == "change value"
    assert mutant_run["actual_failure"] == value["mutants"][0]["actual_failures"][0]


def test_the_legacy_bare_list_form_stays_runnable(tmp_path):
    work, band = _write_synthetic_tree(tmp_path)
    spec = [{
        "label": "return 1 becomes 2",
        "file": "m.py",
        "old": "return 1",
        "new": "return 2",
    }]
    run = _run_tool(work, spec, "--band", band, "--json")
    assert run.returncode == 0, run.stdout + run.stderr


def test_an_invalid_mutant_does_not_exit_one(tmp_path):
    work, band = _write_synthetic_tree(tmp_path)
    spec = [{
        "label": "make the module unimportable",
        "file": "m.py",
        "old": "    return 1",
        "new": "    return (",
    }]
    run = _run_tool(work, spec, "--band", band, "--json")
    assert run.returncode == 2, run.stdout + run.stderr
    payload = json.loads(run.stdout[run.stdout.index("{"):])
    assert payload["rows"][0]["verdict"] == "INVALID", payload


def test_a_genuine_escape_still_exits_one(tmp_path):
    work, band = _write_synthetic_tree(tmp_path)
    spec = [{
        "label": "unobserved extra binding",
        "file": "m.py",
        "old": "def value():",
        "new": "UNOBSERVED = 1\ndef value():",
    }]
    run = _run_tool(work, spec, "--band", band, "--json")
    assert run.returncode == 1, run.stdout + run.stderr
    payload = json.loads(run.stdout[run.stdout.index("{"):])
    assert payload["rows"][0]["verdict"] == "ESCAPED", payload
