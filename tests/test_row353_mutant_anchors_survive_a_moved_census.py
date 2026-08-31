"""Row 353: mutation anchors may describe shape without pinning mutable text."""
from __future__ import annotations

import hashlib
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_ROW348_SPEC = (
    _REPO / "tests" / "mutants" / "row348_raw_unicode_gate_ci_reachability.json"
)
_CENSUS_GATE = _REPO / "tests" / "test_v3_66_939_ci_gate_shards_cover_every_gate.py"
_BAND = "tests/test_subject.py"
_CATCHER = f"{_BAND}::test_subject_is_unchanged"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_text_tree(tmp_path: Path, original: bytes) -> tuple[Path, Path]:
    tests = tmp_path / "tests"
    tests.mkdir()
    subject = tmp_path / "subject.txt"
    subject.write_bytes(original)
    (tmp_path / _BAND).write_text(
        "from pathlib import Path\n"
        f"EXPECTED = {original!r}\n"
        "def test_subject_is_unchanged():\n"
        "    observed = Path('subject.txt').read_bytes()\n"
        "    Path('observed.bin').write_bytes(observed)\n"
        "    assert observed == EXPECTED\n",
        encoding="utf-8",
    )
    assert subject.is_file()
    assert (tmp_path / _BAND).is_file()
    return tmp_path, subject


def _make_python_tree(tmp_path: Path) -> tuple[Path, Path]:
    tests = tmp_path / "tests"
    tests.mkdir()
    subject = tmp_path / "subject.py"
    subject.write_text("def value():\n    return 217\n", encoding="utf-8")
    (tmp_path / _BAND).write_text(
        "import importlib\n"
        "import subject\n"
        "def test_subject_is_unchanged():\n"
        "    assert importlib.reload(subject).value() == 217\n",
        encoding="utf-8",
    )
    assert subject.is_file()
    assert (tmp_path / _BAND).is_file()
    return tmp_path, subject


def _spec(mutant: dict[str, str]) -> dict[str, object]:
    return {
        "schema": "bd-mutate-spec/1",
        "_comment": "row 353 synthetic regex-anchor contract",
        "subject": "one synthetic mutation anchor",
        "band": [_BAND],
        "mutants": [{
            "label": "synthetic regex mutant",
            "file": "subject.txt",
            "new": "CENSUS = 1",
            "direction": "regression",
            "catcher": _CATCHER,
            **mutant,
        }],
    }


def _run(work: Path, document: dict[str, object]) -> subprocess.CompletedProcess[str]:
    spec = work / "spec.json"
    spec.write_text(json.dumps(document), encoding="utf-8")
    environment = os.environ.copy()
    environment.pop("BD_INSTALL_DIR", None)
    environment["BD_DISABLE_KEEPALIVE"] = "1"
    return subprocess.run(
        [
            sys.executable,
            str(_TOOL),
            "--spec",
            str(spec),
            "--work",
            str(work),
            "--timeout",
            "60",
            "--json",
        ],
        cwd=_REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _payload(run: subprocess.CompletedProcess[str]) -> dict[str, object]:
    start = run.stdout.find("{")
    assert start >= 0, run.stdout + run.stderr
    payload = json.loads(run.stdout[start:])
    assert payload["selected"] == 1
    assert payload["total"] == 1
    assert len(payload["rows"]) == 1
    return payload


def test_regex_mutant_replaces_one_span_verbatim_with_exact_length_arithmetic(
        tmp_path: Path) -> None:
    original = b"CENSUS = 217\n"
    work, subject = _make_text_tree(tmp_path, original)
    pattern = r"CENSUS = ([0-9]+)"
    replacement = r"CENSUS = \1"
    matches = list(re.finditer(pattern, original.decode("utf-8")))
    assert len(matches) == 1
    matched = matches[0].group(0).encode("utf-8")
    expected_after = b"CENSUS = \\1\n"
    assert expected_after != original
    assert len(expected_after) == len(original) - len(matched) + len(
        replacement.encode("utf-8")
    )
    before = _digest(subject)
    document = _spec({"old_regex": pattern})
    document["mutants"][0]["new"] = replacement
    assert len(document["mutants"]) == 1

    run = _run(work, document)

    payload = _payload(run)
    assert run.returncode == 0, run.stdout + run.stderr
    assert payload["exit"] == 0
    assert payload["rows"][0]["verdict"] == "CAUGHT"
    assert (work / "observed.bin").read_bytes() == expected_after
    assert _digest(subject) == before


def test_regex_anchor_matching_zero_times_refuses_with_its_own_diagnostic(
        tmp_path: Path) -> None:
    work, subject = _make_text_tree(tmp_path, b"CENSUS = 217\n")
    pattern = r"MISSING = [0-9]+"
    assert len(re.findall(pattern, subject.read_text(encoding="utf-8"))) == 0
    before = _digest(subject)
    document = _spec({"old_regex": pattern})
    assert len(document["mutants"]) == 1

    run = _run(work, document)

    payload = _payload(run)
    assert run.returncode == 2, run.stdout + run.stderr
    assert payload["rows"][0]["verdict"] == "ERROR"
    assert payload["rows"][0]["why"] == (
        "apply: regex anchor occurs 0 times, must be exactly 1"
    )
    assert _digest(subject) == before


def test_regex_anchor_matching_twice_refuses_distinctly_from_zero(
        tmp_path: Path) -> None:
    work, subject = _make_text_tree(tmp_path, b"CENSUS = 217\nCENSUS = 218\n")
    pattern = r"CENSUS = [0-9]+"
    assert len(re.findall(pattern, subject.read_text(encoding="utf-8"))) == 2
    before = _digest(subject)
    document = _spec({"old_regex": pattern})
    assert len(document["mutants"]) == 1

    run = _run(work, document)

    payload = _payload(run)
    assert run.returncode == 2, run.stdout + run.stderr
    assert payload["rows"][0]["verdict"] == "ERROR"
    assert payload["rows"][0]["why"] == (
        "apply: regex anchor occurs 2 times, must be exactly 1"
    )
    assert payload["rows"][0]["why"] != (
        "apply: regex anchor occurs 0 times, must be exactly 1"
    )
    assert _digest(subject) == before


def test_mutant_with_both_anchor_fields_is_a_spec_error_and_writes_nothing(
        tmp_path: Path) -> None:
    work, subject = _make_text_tree(tmp_path, b"CENSUS = 217\n")
    assert subject.read_text(encoding="utf-8").count("CENSUS = 217") == 1
    assert len(re.findall(r"CENSUS = [0-9]+", subject.read_text(encoding="utf-8"))) == 1
    before = _digest(subject)
    document = _spec({
        "old": "CENSUS = 217",
        "old_regex": r"CENSUS = [0-9]+",
    })
    assert len(document["mutants"]) == 1

    run = _run(work, document)

    assert run.returncode == 2, run.stdout + run.stderr
    assert (
        "mutant 1 must supply exactly one of 'old' or 'old_regex'; got both"
        in run.stderr
    )
    assert _digest(subject) == before


def test_mutant_with_neither_anchor_field_is_a_spec_error_and_writes_nothing(
        tmp_path: Path) -> None:
    work, subject = _make_text_tree(tmp_path, b"CENSUS = 217\n")
    assert subject.read_text(encoding="utf-8").count("CENSUS = 217") == 1
    before = _digest(subject)
    document = _spec({})
    assert len(document["mutants"]) == 1

    run = _run(work, document)

    assert run.returncode == 2, run.stdout + run.stderr
    assert (
        "mutant 1 must supply exactly one of 'old' or 'old_regex'; got neither"
        in run.stderr
    )
    assert _digest(subject) == before


def test_invalid_regex_is_a_spec_error_and_writes_nothing(tmp_path: Path) -> None:
    work, subject = _make_text_tree(tmp_path, b"CENSUS = 217\n")
    before = _digest(subject)
    document = _spec({"old_regex": "["})
    assert len(document["mutants"]) == 1

    run = _run(work, document)

    assert run.returncode == 2, run.stdout + run.stderr
    assert "mutant 1 field 'old_regex' is not a valid Python regular expression" in run.stderr
    assert _digest(subject) == before


def test_regex_mutant_with_malformed_output_is_invalid_not_caught(
        tmp_path: Path) -> None:
    work, subject = _make_python_tree(tmp_path)
    text = subject.read_text(encoding="utf-8")
    pattern = r"return [0-9]+"
    assert len(re.findall(pattern, text)) == 1
    before = _digest(subject)
    document = _spec({"old_regex": pattern})
    document["mutants"][0]["file"] = "subject.py"
    document["mutants"][0]["new"] = "return ("
    assert len(document["mutants"]) == 1

    run = _run(work, document)

    payload = _payload(run)
    assert run.returncode == 2, run.stdout + run.stderr
    assert payload["rows"][0]["verdict"] == "INVALID"
    assert payload["rows"][0]["why"].startswith("ast.parse:")
    assert "never closed" in payload["rows"][0]["why"]
    assert _digest(subject) == before


def _load_census_gate():
    spec = importlib.util.spec_from_file_location("row353_census_gate", _CENSUS_GATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_literal_exactly_once(text: str, anchor: str) -> None:
    count = text.count(anchor)
    assert count == 1, f"literal anchor occurs {count} times, expected exactly 1"


def test_row348_m4_was_retired_with_the_literal_it_severed() -> None:
    """M4 is gone, and this proves the removal was honest rather than quiet.

    Row 353 wrote a regex anchor for M4 so a moving census could not invalidate
    the spec. Row 531 removed the census literal itself: the declared-gate
    expectation is now derived from the declared set, so there is no number left
    that can be stale. A mutant aimed at the derivation would leave a consistent
    tree consistent and ESCAPE -- a false negative dressed as coverage -- so the
    spec stops claiming it.

    Deleting a mutant is exactly the move that should be hard to do quietly, so
    the assertions below are the ones that would catch it being done for the
    wrong reason: the literal really is absent from the gate, the six mutants
    that sever the TREE are still there, and the property M4 stood in front of
    is still enforced -- by a live floor that refuses a population below it, and
    by a derivation that needs no literal at all.
    """
    assert _ROW348_SPEC.is_file()
    assert _CENSUS_GATE.is_file()
    document = json.loads(_ROW348_SPEC.read_text(encoding="utf-8"))

    labels = [mutant["label"] for mutant in document["mutants"]]
    assert not [label for label in labels if label.startswith("M4 ")], labels
    assert len(labels) == 6, labels
    # The three that make the TREE inconsistent are what row348 is really for.
    for prefix in ("M1 ", "M2 ", "M3 "):
        assert len([label for label in labels if label.startswith(prefix)]) == 1, labels
    assert "M4 SEVERED A DEFECT THAT NO LONGER EXISTS" in document["_comment"]

    # PARSED, not grepped. The gate module explains the retirement in a comment
    # that necessarily NAMES the retired constant, and a text scan reports its
    # own explanation -- A7's "if a gate scans source text, remember its comments
    # and examples are inside that denominator". Caught here by this very
    # assertion failing on its first run.
    current = _CENSUS_GATE.read_text(encoding="utf-8")
    bindings = {
        target.id
        for node in ast.parse(current, filename=str(_CENSUS_GATE)).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "_EXPECTED_DECLARED_GATE_COUNT" not in bindings, (
        "the retired literal came back as a real binding; M4 was removed on the "
        "premise that it cannot be stale because it no longer exists"
    )
    assert "_EXPECTED_CONFIRMED_SAFETY_GATE_COUNT" not in bindings
    assert "_DECLARED_GATE_FLOOR" in bindings, (
        "the population floor that replaced the literal is not bound at module "
        "scope, so nothing carries the removal tripwire"
    )

    gate = _load_census_gate()
    declared_count = len(gate._DECLARED)
    assert declared_count > 1
    shards = gate._shard_lists()
    assert len(shards) > 0

    # NEGATIVE CONTROL: a floor above the real population is refused, and the
    # refusal names both numbers rather than saying only that something is wrong.
    with pytest.raises(
        AssertionError,
        match=r"the declared gate population fell from at least 999999 to [0-9]+",
    ):
        gate._assert_exact_gate_coverage(gate._DECLARED, shards, floor=999999)

    # POSITIVE CONTROL: the same call at the true population passes, with no
    # literal anywhere -- which is the whole reason M4 has nothing left to sever.
    gate._assert_exact_gate_coverage(gate._DECLARED, shards, floor=declared_count)

    # And the derivation still refuses the failure the spec exists for: a gate
    # that is declared and that no shard runs.
    with pytest.raises(AssertionError, match="missing from CI"):
        gate._assert_exact_gate_coverage(
            gate._DECLARED, {"one": sorted(gate._DECLARED)[:-1]},
            floor=declared_count)


def test_transform_control_only_confirms_the_mutation_tool_exists() -> None:
    """Import-only control for row353's replacement-transform mutant."""
    assert _TOOL.is_file()
