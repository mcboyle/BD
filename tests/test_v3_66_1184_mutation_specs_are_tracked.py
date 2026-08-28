"""v3.66.1184 -- mutation evidence is tracked and directly re-runnable.

The repository had zero tracked ``bd-mutate`` JSON specs even though tests and
CHANGELOG entries claimed concrete mutation results.  These checks make the
artifact population non-empty, validate its executable anchors, and exercise
the production CLI rather than treating JSON text as evidence by itself.
"""
from __future__ import annotations

import ast
import concurrent.futures
import json
import os
import re
import subprocess
import sys
from itertools import repeat
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_SCHEMA = "bd-mutate-spec/1"
_TOP_LEVEL_FIELDS = {"schema", "_comment", "subject", "band", "mutants"}
_COMMON_MUTANT_FIELDS = {"label", "file", "new", "direction"}
_REGRESSION_FIELDS = _COMMON_MUTANT_FIELDS | {"catcher"}
_OVERCORRECTION_FIELDS = _COMMON_MUTANT_FIELDS | {"control", "preserves"}


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
        assert isinstance(rel, str) and rel, f"invalid mutant file: {mutant!r}"
        anchor_fields = {"old", "old_regex"} & set(mutant)
        assert len(anchor_fields) == 1, (
            f"mutant must carry exactly one anchor field: {mutant!r}"
        )
        anchor_field = next(iter(anchor_fields))
        anchor = mutant[anchor_field]
        assert isinstance(anchor, str) and anchor, (
            f"invalid {anchor_field} anchor: {mutant!r}"
        )
        subject = root / rel
        assert subject.is_file(), f"mutant subject is absent: {rel}"
        source = subject.read_text(encoding="utf-8")
        if anchor_field == "old_regex":
            try:
                count = len(list(re.finditer(anchor, source)))
            except re.error as exc:
                raise AssertionError(f"{rel}: invalid regex anchor: {exc}") from exc
        else:
            count = source.count(anchor)
        assert count == 1, (
            f"{rel}: {anchor_field} anchor occurs {count} times, expected exactly 1"
        )
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


def _defined_base_nodeid(nodeid: str) -> str | None:
    head, sep, leaf = nodeid.rpartition("::")
    if not sep or not leaf:
        return None
    if "[" not in leaf:
        return nodeid
    base, bracket, parameter = leaf.partition("[")
    if (not bracket or not base or not parameter.endswith("]")
            or parameter == "]" or "[" in parameter[:-1]):
        return None
    return f"{head}::{base}"


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


def _collect_spec_band(path: Path, band: list[str]) -> subprocess.CompletedProcess[str]:
    """Collect one tracked band under a CI-realistic, path-attributed bound."""
    command = [
        sys.executable, "-m", "pytest", "--collect-only", "-q",
        "-p", "no:randomly", *band,
    ]
    try:
        return subprocess.run(
            command,
            cwd=_REPO,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        partial = "".join(
            value.decode("utf-8", "replace") if isinstance(value, bytes) else value or ""
            for value in (exc.stdout, exc.stderr)
        )
        raise AssertionError(
            f"{path}: recorded band collection exceeded 600 seconds:\n"
            f"{partial[-2000:]}"
        ) from None


def _collection_worker_count(spec_count: int) -> int:
    """Use at most one quarter of the CPUs available to this process."""
    assert spec_count > 0, "cannot size a worker pool for zero specs"
    try:
        available_cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        available_cpus = os.cpu_count() or 1
    return min(spec_count, max(1, available_cpus // 4))


def _validate_one_tracked_spec(path: Path, tracked: set[str]) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"{path}: tracked specs use object form"
    assert set(document) == _TOP_LEVEL_FIELDS, (
        f"{path}: fields {sorted(document)} != {sorted(_TOP_LEVEL_FIELDS)}"
    )
    assert document["schema"] == _SCHEMA, path
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
    named_references = []
    mutants = document["mutants"]
    assert isinstance(mutants, list) and mutants, f"{path}: mutant denominator is 0"
    for mutant in mutants:
        assert isinstance(mutant, dict), f"{path}: mutant is not an object"
        direction = mutant.get("direction")
        expected_fields = (
            _REGRESSION_FIELDS if direction == "regression"
            else _OVERCORRECTION_FIELDS if direction == "overcorrection"
            else set()
        )
        assert expected_fields, f"{path}: unsupported direction {direction!r}"
        anchor_fields = {"old", "old_regex"} & set(mutant)
        assert len(anchor_fields) == 1, (
            f"{path}: mutant must carry exactly one of old/old_regex"
        )
        anchor_field = next(iter(anchor_fields))
        expected_fields = expected_fields | {anchor_field}
        assert set(mutant) == expected_fields, (
            f"{path}: mutant fields {sorted(mutant)} != {sorted(expected_fields)}"
        )
        for field in ("label", "file", anchor_field, "new"):
            assert isinstance(mutant[field], str) and mutant[field], (
                f"{path}: {field} must be a non-empty string"
            )
        if anchor_field == "old_regex":
            try:
                re.compile(mutant[anchor_field])
            except re.error as exc:
                raise AssertionError(f"{path}: invalid regex anchor: {exc}") from exc
        assert mutant["file"] in tracked, f"{path}: untracked subject {mutant['file']}"
        if direction == "overcorrection":
            assert isinstance(mutant["control"], str) and mutant["control"], path
            assert isinstance(mutant["preserves"], list) and mutant["preserves"], path
            assert len(mutant["preserves"]) == len(set(mutant["preserves"])), path
            assert mutant["control"] not in mutant["preserves"], path
            named = [mutant["control"], *mutant["preserves"]]
        else:
            named = [mutant["catcher"]]
        named_references.extend(named)
        for nodeid in named:
            assert isinstance(nodeid, str) and nodeid, f"{path}: invalid nodeid"
            node_path = nodeid.split("::", 1)[0]
            assert node_path in tracked and (_REPO / node_path).is_file(), (
                f"{path}: named test path is absent or untracked: {nodeid}"
            )
            base_nodeid = _defined_base_nodeid(nodeid)
            assert (base_nodeid is not None
                    and base_nodeid in _defined_nodeids(_REPO / node_path)), (
                f"{path}: nodeid is not a defined test: {nodeid}"
            )
    collected = _collect_spec_band(path, band)
    assert collected.returncode == 0, (
        f"{path}: recorded band did not collect cleanly:\n"
        f"{(collected.stdout + collected.stderr)[-2000:]}"
    )
    assert collected.stdout.strip(), f"{path}: recorded band returned no output"
    collected_nodeids = []
    for raw_line in collected.stdout.splitlines():
        nodeid = raw_line.strip()
        node_path = nodeid.split("::", 1)[0]
        if "::" in nodeid and node_path in tracked and (_REPO / node_path).is_file():
            collected_nodeids.append(nodeid)
    for nodeid in named_references:
        assert collected_nodeids.count(nodeid) == 1, (
            f"{path}: named nodeid must identify one exact collected test; "
            f"{nodeid!r} appeared {collected_nodeids.count(nodeid)} times"
        )


def _tracked_spec_outcome(path: Path, tracked: set[str]) -> tuple[Path, str | None]:
    """Return a named failure so every dispatched spec can still be counted."""
    try:
        _validate_one_tracked_spec(path, tracked)
    except Exception as exc:
        return path, f"{type(exc).__name__}: {exc}"
    return path, None


def _validate_tracked_specs_concurrently(specs: list[Path], tracked: set[str]) -> int:
    """Validate every spec concurrently and fail only after reconciling the set."""
    assert specs, "cannot validate a zero-spec population"
    workers = _collection_worker_count(len(specs))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        # Executor.map preserves input order, making the failure verdict stable
        # even though subprocess completion order is deliberately nondeterministic.
        outcomes = list(pool.map(_tracked_spec_outcome, specs, repeat(tracked)))

    processed_paths = [path for path, _error in outcomes]
    processed = len(processed_paths)
    assert processed > 0 and processed == len(specs), (
        f"schema reader processed {processed} of {len(specs)} tracked specs"
    )
    assert processed_paths == specs, (
        "schema reader did not preserve the tracked spec population"
    )
    failures = [(path, error) for path, error in outcomes if error is not None]
    assert not failures, (
        f"processed {processed} of {len(specs)} tracked specs; "
        f"{len(failures)} failed:\n"
        + "\n".join(f"{path}: {error}" for path, error in failures)
    )
    return processed


def test_a_tracked_mutation_spec_exists_at_all():
    specs = _tracked_specs()
    assert specs, "tracked tests/mutants/*.json denominator is 0"


def test_spec_collection_timeout_is_reported_with_its_path(monkeypatch, tmp_path):
    """A slow collector is a named spec failure, not an uncaught traceback."""
    spec_path = tmp_path / "slow-spec.json"

    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"], output="partial")

    monkeypatch.setattr(subprocess, "run", time_out)
    with pytest.raises(AssertionError, match="slow-spec.json.*600 seconds"):
        _collect_spec_band(spec_path, ["tests/test_slow.py"])


def test_collection_worker_count_scales_with_available_cpus_and_population(monkeypatch):
    monkeypatch.setattr(
        os, "sched_getaffinity", lambda _pid: set(range(48)), raising=False
    )
    assert _collection_worker_count(139) == 12
    assert _collection_worker_count(5) == 5


def test_parallel_gate_fails_closed_and_names_a_malformed_spec(tmp_path):
    specs = _tracked_specs()
    assert specs, "negative control needs one valid tracked spec"
    malformed = tmp_path / "malformed-mutation-spec.json"
    malformed.write_text("{not valid JSON", encoding="utf-8")

    population = [specs[0], malformed]
    with pytest.raises(AssertionError) as failure:
        _validate_tracked_specs_concurrently(population, set(_git_paths()))

    message = str(failure.value)
    assert "processed 2 of 2 tracked specs" in message
    assert str(malformed) in message
    assert "JSONDecodeError" in message


def test_parallel_gate_rejects_an_empty_collection(monkeypatch):
    path = _tracked_specs()[0]
    tracked = set(_git_paths())
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )
    with pytest.raises(AssertionError) as failure:
        _validate_tracked_specs_concurrently([path], tracked)
    message = str(failure.value)
    assert "processed 1 of 1 tracked specs" in message
    assert str(path) in message
    assert "recorded band returned no output" in message


# Row 317, 48 CPUs, 139 specs: 270.00s serial; 77.30s with the 12-worker
# quarter-CPU pool. The assertions and denominator remain per-spec and ordered
# outcomes are reconciled before failures surface.
@pytest.mark.timeout(900)
def test_every_tracked_spec_parses_and_declares_schema_band_and_mutants():
    specs = _tracked_specs()
    assert specs, "cannot validate a zero-spec population"
    tracked = set(_git_paths())
    checked = _validate_tracked_specs_concurrently(specs, tracked)
    assert checked > 0 and checked == len(specs), (
        f"schema reader processed {checked} of {len(specs)} tracked specs"
    )


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
    with pytest.raises(AssertionError, match="old_regex anchor occurs 2 times"):
        _assert_anchor_counts(
            tmp_path,
            {"mutants": [{"file": "subject.py", "old_regex": "anchor"}]},
        )


def test_object_form_uses_its_recorded_band_and_is_rerunnable(tmp_path):
    work, band = _write_synthetic_tree(tmp_path)
    spec = {
        "schema": _SCHEMA,
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


def test_named_reference_must_resolve_to_one_exact_collected_case(tmp_path):
    """A parameterized base name cannot provide one attributable verdict."""
    work, band = _write_synthetic_tree(tmp_path)
    (work / "tests" / "test_m.py").write_text(
        "import importlib\n"
        "import pytest\n"
        "import m\n"
        "@pytest.mark.parametrize('expected', [1, 1])\n"
        "def test_value(expected):\n"
        "    importlib.reload(m)\n"
        "    assert m.value() == expected\n",
        encoding="utf-8",
    )
    spec = {
        "schema": _SCHEMA,
        "_comment": "a base node cannot identify one parameter case",
        "subject": "named verdicts are attributable to exactly one case",
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
    before = (work / "m.py").read_bytes()
    run = _run_tool(work, spec, "--json")
    assert run.returncode == 2
    assert "named nodeid is not one exact collected test" in run.stderr
    payload = json.loads(run.stdout[run.stdout.index("{"):])
    assert payload["rows"] == []
    assert (work / "m.py").read_bytes() == before


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
