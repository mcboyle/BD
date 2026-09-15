"""v3.66.1184 -- mutation evidence is tracked and directly re-runnable.

The repository had zero tracked ``bd-mutate`` JSON specs even though tests and
CHANGELOG entries claimed concrete mutation results.  These checks make the
artifact population non-empty, validate its executable anchors, and exercise
the production CLI rather than treating JSON text as evidence by itself.
"""
from __future__ import annotations

import ast
import concurrent.futures
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import runpy
import subprocess
import sys
from itertools import repeat
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_SCHEMA_V1 = "bd-mutate-spec/1"
_SCHEMA_V2 = "bd-mutation-spec/2"
_TOP_LEVEL_FIELDS = {"schema", "_comment", "subject", "band", "mutants"}
# H89: the one optional top-level field. A spec that is a transform control
# declares it, and the only value a tracked spec may declare is a literal true.
_OPTIONAL_TOP_LEVEL_FIELDS = {"control_spec"}
_COMMON_MUTANT_FIELDS = {"label", "file", "new", "direction"}
_REGRESSION_FIELDS = _COMMON_MUTANT_FIELDS | {"catcher"}
_EXACT_REGRESSION_FIELDS = _REGRESSION_FIELDS | {"expected_failure", "preserves"}
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


def _load_tool_module():
    loader = importlib.machinery.SourceFileLoader("bd_mutate_test", str(_TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


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
    assert _TOP_LEVEL_FIELDS <= set(document) <= (
            _TOP_LEVEL_FIELDS | _OPTIONAL_TOP_LEVEL_FIELDS), (
        f"{path}: fields {sorted(document)} are not {sorted(_TOP_LEVEL_FIELDS)} "
        f"plus optional {sorted(_OPTIONAL_TOP_LEVEL_FIELDS)}"
    )
    if "control_spec" in document:
        assert document["control_spec"] is True, (
            f"{path}: control_spec must be true when present, got "
            f"{document['control_spec']!r}"
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
    named_references = []
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
        anchor_fields = {"old", "old_regex"} & set(mutant)
        assert len(anchor_fields) == 1, (
            f"{path}: mutant must carry exactly one of old/old_regex"
        )
        anchor_field = next(iter(anchor_fields))
        expected_fields = expected_fields | {anchor_field}
        assert set(mutant) == expected_fields, (
            f"{path}: mutant fields {sorted(mutant)} != {sorted(expected_fields)}"
        )
        for field in ("label", "file", anchor_field):
            assert isinstance(mutant[field], str) and mutant[field], (
                f"{path}: {field} must be a non-empty string"
            )
        # ROW1184: `new` MAY be the empty string -- that is how a spec expresses a
        # DELETION mutant, and bd-mutate has planted one since h377. It is checked
        # apart from the three above because empty and absent are different things:
        # ABSENT is still refused, by the exact-field-set assertion above, which
        # is what guarantees the subscript below cannot raise KeyError.
        assert isinstance(mutant["new"], str), (
            f"{path}: new must be a string (it may be empty = delete the span)"
        )
        if anchor_field == "old_regex":
            try:
                re.compile(mutant[anchor_field])
            except re.error as exc:
                raise AssertionError(f"{path}: invalid regex anchor: {exc}") from exc
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
            named = [mutant["catcher"], *mutant["preserves"]]
        elif direction == "overcorrection":
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


def test_a_tracked_spec_may_declare_control_spec_true_and_nothing_else(tmp_path):
    """H89: the only optional top-level field is a literal true control declaration."""
    template = _tracked_specs()[0]
    tracked = set(_git_paths())
    document = json.loads(template.read_text(encoding="utf-8"))
    assert "control_spec" not in document, template
    _validate_one_tracked_spec(template, tracked)

    declared = tmp_path / "declared_control.json"
    declared.write_text(
        json.dumps({**document, "control_spec": True}), encoding="utf-8")
    _validate_one_tracked_spec(declared, tracked)

    for bad in (False, "yes", 1):
        path = tmp_path / "bad_control.json"
        path.write_text(
            json.dumps({**document, "control_spec": bad}), encoding="utf-8")
        with pytest.raises(AssertionError, match="control_spec must be true"):
            _validate_one_tracked_spec(path, tracked)

    stray = tmp_path / "stray_field.json"
    stray.write_text(
        json.dumps({**document, "controls": True}), encoding="utf-8")
    with pytest.raises(AssertionError, match="plus optional"):
        _validate_one_tracked_spec(stray, tracked)


def test_a_deletion_mutant_declares_an_EMPTY_new_and_the_validator_admits_it(tmp_path):
    """ROW1184: `"new": ""` is how a spec says DELETE THE SPAN, so this gate must admit it.

    bd-mutate accepts an empty ``new`` as of h377; until this row the tracked-spec
    validator did not, so a deletion battery could be RUN and EMITTED but never
    CHECKED IN. The relaxation is only a measurement if the controls still refuse,
    so the limbs are: EMPTY passes, ABSENT refuses, NON-STRING refuses.
    """
    template = _tracked_specs()[0]
    tracked = set(_git_paths())
    document = json.loads(template.read_text(encoding="utf-8"))
    first = document["mutants"][0]
    assert isinstance(first.get("new"), str) and first["new"], (
        f"{template}: fixture precondition -- this test rewrites the template's "
        f"first `new`, which must start out a non-empty string, got {first.get('new')!r}"
    )

    def _spec_whose_first_mutant_is(mutant: object, name: str) -> Path:
        path = tmp_path / name
        path.write_text(
            json.dumps({**document, "mutants": [mutant, *document["mutants"][1:]]}),
            encoding="utf-8",
        )
        return path

    # POSITIVE -- the deletion mutant. `new` is the only field that differs from a
    # spec this gate already accepts, so a failure here can only be the empty string.
    deletion = {**first, "new": ""}
    _validate_one_tracked_spec(
        _spec_whose_first_mutant_is(deletion, "deletion_mutant.json"), tracked)

    # CONTROL 1 -- ABSENT IS NOT EMPTY. A missing key is a spec its author did not
    # finish writing. The exact-field-set assertion refuses it before the field
    # loop is reached, so the message matched is deliberately that one: naming the
    # per-field message here would assert a refusal that does not happen.
    absent = {field: value for field, value in first.items() if field != "new"}
    with pytest.raises(AssertionError, match=r"mutant fields \[.*\] != \[.*'new'.*\]"):
        _validate_one_tracked_spec(
            _spec_whose_first_mutant_is(absent, "absent_new.json"), tracked)

    # CONTROL 2 -- present but not a string. This is the check this row rewrites,
    # and the one a careless fix (deleting "new" from the tuple and stopping) drops.
    for not_a_string in (None, 0, 1, [], {}, ["", ""]):
        with pytest.raises(AssertionError, match="new must be a string"):
            _validate_one_tracked_spec(
                _spec_whose_first_mutant_is(
                    {**first, "new": not_a_string}, "non_string_new.json"),
                tracked,
            )


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
# Row 810: test_every_tracked_spec_parses_and_declares_schema_band_and_mutants
# -- the whole-corpus collection walk, one `pytest --collect-only` per spec --
# was the whole of the mutation-tools shard's wall time (measured 2026-09-14 on
# test5: 100 s of the file's 137 s at 12 workers; 729 s of 765 s pinned to 4
# CPUs, i.e. the ONE worker a 4-vCPU CI runner gets from _collection_worker_count).
# It now runs as interleaved slices of the tracked population (specs[k::of]),
# one file per slice (tests/test_row810_spec_collection_slice_<k>.py), each in
# its own CI shard. The slice files import the helpers above; the partition test
# below proves the slice files on disk cover the population exactly.
_SPEC_SLICE_GLOB = "tests/test_row810_spec_collection_slice_*.py"
_SPEC_SLICE_BINDING = "_SPEC_SLICE"


def _tracked_spec_slice(index: int, of: int) -> list[Path]:
    """Every `of`-th tracked spec starting at `index`, in tracked order."""
    assert 0 <= index < of, f"slice {index} of {of} is not a slice"
    return _tracked_specs()[index::of]


def _spec_slice_declarations() -> dict[str, tuple[int, int]]:
    """path -> (index, of) as each slice file on disk binds _SPEC_SLICE."""
    declared = {}
    for rel in _git_paths(_SPEC_SLICE_GLOB):
        tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"), filename=rel)
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == _SPEC_SLICE_BINDING
                    for t in node.targets):
                declared[rel] = tuple(ast.literal_eval(node.value))
    return declared


def test_the_spec_slice_files_partition_the_tracked_population_exactly():
    """Row 810: the slices on disk are 0..of-1 of ONE `of`, and re-assemble to the
    whole population -- a deleted or mis-numbered slice file shrinks the walk
    silently otherwise, and the 939 gate cannot see inside a file."""
    declared = _spec_slice_declarations()
    assert declared, f"no {_SPEC_SLICE_BINDING} binding under {_SPEC_SLICE_GLOB}"
    counts = {of for _index, of in declared.values()}
    assert len(counts) == 1, f"slice files disagree on the slice count: {declared}"
    of = counts.pop()
    indexes = sorted(index for index, _of in declared.values())
    assert indexes == list(range(of)), (
        f"slice files declare indexes {indexes}, expected every one of 0..{of - 1} once"
    )
    specs = _tracked_specs()
    assembled = [spec
                 for k in range(of)
                 for spec in _tracked_spec_slice(k, of)]
    assert len(assembled) == len(specs) > 0
    assert sorted(assembled) == sorted(specs), "slices do not re-assemble the population"
    assert all(_tracked_spec_slice(k, of) for k in range(of)), "an empty slice"


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
        "schema": _SCHEMA_V1,
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


# Backlog row 27, omission slice.  A behavior-changing cut does not expose a
# reliable syntactic bit saying that it needed a mutation battery.  A cut that
# publishes a quantitative mutation receipt does expose a narrower, objective
# fact: durable evidence must accompany the receipt.  These immutable releases
# are the RED case, a legitimate regression-only case, and the executable
# over-correction case respectively.
_MUTATION_SPEC_ADOPTION = "d2b0ad1e91e94ec840dfb821f12c99babd3b21e3"
_LIVE_RECEIPT_WITHOUT_SPEC = "4d8c85570456a825a04ce26ecf46e4689082b41c"
_LATER_LIVE_RECEIPT_WITHOUT_SPEC = "f993f654fab2977aed196645a609a7235d29de26"
_KNOWN_RECEIPT_EVIDENCE_OMISSIONS = frozenset({
    _LIVE_RECEIPT_WITHOUT_SPEC,
    _LATER_LIVE_RECEIPT_WITHOUT_SPEC,
})
_LEGITIMATE_NO_RECEIPT = "f65712e4bd26387caa94b5dcde99064bf85e8751"
_LEGITIMATE_REGRESSION_ONLY_RECEIPT = "c7b207f8de079f29ed47b2d09ab5ec19a88149f7"
_EXECUTABLE_OVERCORRECTION_RECEIPT = "9514266bfc1c433e162045a0925e47ccfd0b5c8d"


def _mutation_evidence_auditor():
    namespace = runpy.run_path(
        str(_TOOL), run_name="bd_mutate_release_evidence_contract"
    )
    auditor = namespace.get("_audit_release_mutation_evidence")
    assert callable(auditor), (
        "bd-mutate has no release mutation-evidence auditor; quantitative "
        "receipts cannot be checked for omitted durable specs"
    )
    return auditor


def _synthetic_worktree_receipt(
    tmp_path: Path, *, explicit_control: bool, with_spec: bool = True
) -> dict:
    (tmp_path / "tests" / "mutants").mkdir(parents=True)
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## v1.0.0 - baseline\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "add", "--", "CHANGELOG.md"], cwd=tmp_path, check=True
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Row 27 Test",
            "-c",
            "user.email=row27@example.invalid",
            "commit",
            "-qm",
            "baseline",
        ],
        cwd=tmp_path,
        check=True,
    )
    control_claim = (
        " M1 is an explicit OVERCORRECTION mutant."
        if explicit_control else ""
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "## v1.0.1 - candidate\n\n"
        f"- MUTATION: 1 CAUGHT, 0 ESCAPED.{control_claim}\n\n"
        "## v1.0.0 - baseline\n",
        encoding="utf-8",
    )
    if with_spec:
        (tmp_path / "tests" / "mutants" / "v3_66_1_synthetic.json").write_text(
            json.dumps({
                "schema": _SCHEMA_V1,
                "_comment": "synthetic regression-only receipt",
                "subject": "a regression-only battery is legitimate unless OC is claimed",
                "band": ["tests/test_m.py"],
                "mutants": [{
                    "label": "regression only",
                    "file": "m.py",
                    "old": "VALUE = 1",
                    "new": "VALUE = 2",
                    "direction": "regression",
                    "catcher": "tests/test_m.py::test_value",
                }],
            }),
            encoding="utf-8",
        )
    return _mutation_evidence_auditor()(tmp_path, None)


def _changelog_revisions_since_adoption() -> list[str]:
    run = subprocess.run(
        [
            "git",
            "rev-list",
            "--first-parent",
            "--reverse",
            f"{_MUTATION_SPEC_ADOPTION}^..HEAD",
            "--",
            "CHANGELOG.md",
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    revisions = run.stdout.splitlines()
    assert revisions and revisions[0] == _MUTATION_SPEC_ADOPTION, (
        "mutation-spec adoption history is absent or truncated: "
        f"{revisions[:3]}"
    )
    assert len(revisions) == len(set(revisions)), revisions
    return revisions


def test_the_live_quantitative_receipt_without_a_spec_is_detected():
    """RED: v1218 said 3/3 caught but added no durable mutation spec."""
    record = _mutation_evidence_auditor()(
        _REPO, _LIVE_RECEIPT_WITHOUT_SPEC
    )

    assert record["quantitative_receipt"], record
    assert record["specs"] == [], record
    assert record["problems"] == [
        "quantitative mutation receipt has no same-cut durable mutation spec"
    ], record


def test_the_later_live_quantitative_receipt_without_a_spec_is_detected():
    """The stranded gate also exposes v1395's immutable missing evidence."""
    record = _mutation_evidence_auditor()(
        _REPO, _LATER_LIVE_RECEIPT_WITHOUT_SPEC
    )

    assert record["quantitative_receipt"], record
    assert record["specs"] == [], record
    assert record["problems"] == [
        "quantitative mutation receipt has no same-cut durable mutation spec"
    ], record


def test_a_quantified_regression_only_battery_legitimately_needs_no_control():
    """The slice must not turn every mutation receipt into an OC policy."""
    record = _mutation_evidence_auditor()(
        _REPO, _LEGITIMATE_REGRESSION_ONLY_RECEIPT
    )

    assert record["quantitative_receipt"], record
    assert record["specs"] == [
        "tests/mutants/v3_66_1225_spa_scanner_populations.json"
    ], record
    assert not record["explicit_overcorrection_mutant"], record
    assert record["overcorrection_controls"] == [], record
    assert record["problems"] == [], record


def test_a_cut_with_no_quantitative_receipt_needs_neither_spec_nor_control():
    """v1189 changed mutation machinery but claimed no executed battery."""
    record = _mutation_evidence_auditor()(_REPO, _LEGITIMATE_NO_RECEIPT)

    assert not record["quantitative_receipt"], record
    assert record["specs"] == [], record
    assert record["overcorrection_controls"] == [], record
    assert record["problems"] == [], record


def test_an_explicit_overcorrection_mutant_claim_has_an_executable_control():
    record = _mutation_evidence_auditor()(
        _REPO, _EXECUTABLE_OVERCORRECTION_RECEIPT
    )

    assert record["quantitative_receipt"], record
    assert record["explicit_overcorrection_mutant"], record
    assert record["overcorrection_controls"], record
    assert all(item["control"] and item["preserves"]
               for item in record["overcorrection_controls"]), record
    assert record["problems"] == [], record


def test_an_explicit_overcorrection_claim_without_a_control_is_detected(tmp_path):
    record = _synthetic_worktree_receipt(tmp_path, explicit_control=True)

    assert record["quantitative_receipt"], record
    assert record["explicit_overcorrection_mutant"], record
    assert record["overcorrection_controls"] == [], record
    assert record["problems"] == [
        "explicit overcorrection mutant claim has no executable named control"
    ], record


def test_an_uncommitted_quantitative_receipt_without_a_spec_is_detected(tmp_path):
    record = _synthetic_worktree_receipt(
        tmp_path, explicit_control=False, with_spec=False
    )

    assert record["quantitative_receipt"], record
    assert record["specs"] == [], record
    assert record["problems"] == [
        "quantitative mutation receipt has no same-cut durable mutation spec"
    ], record


def test_a_synthetic_regression_only_receipt_does_not_demand_a_control(tmp_path):
    record = _synthetic_worktree_receipt(tmp_path, explicit_control=False)

    assert record["quantitative_receipt"], record
    assert not record["explicit_overcorrection_mutant"], record
    assert record["specs"] == [
        "tests/mutants/v3_66_1_synthetic.json"
    ], record
    assert record["overcorrection_controls"] == [], record
    assert record["problems"] == [], record


def test_every_other_receipt_carries_its_durable_evidence():
    """Close the denominator while naming immutable historical omissions."""
    revisions = _changelog_revisions_since_adoption()
    auditor = _mutation_evidence_auditor()
    records = [auditor(_REPO, revision) for revision in revisions]
    receipts = [record for record in records if record["quantitative_receipt"]]
    overcorrections = [
        record for record in receipts
        if record["explicit_overcorrection_mutant"]
    ]
    failures = [record for record in records if record["problems"]]
    failures_by_revision = {
        record["revision"]: record["problems"] for record in failures
    }
    worktree = auditor(_REPO, None)

    assert receipts, "quantitative mutation-receipt denominator is 0"
    assert overcorrections, "explicit over-correction receipt denominator is 0"
    assert len(records) == len(revisions), (
        f"audited {len(records)} of {len(revisions)} CHANGELOG transitions"
    )
    assert failures_by_revision == {
        revision: [
            "quantitative mutation receipt has no same-cut durable mutation spec"
        ]
        for revision in _KNOWN_RECEIPT_EVIDENCE_OMISSIONS
    }, failures
    assert not worktree["problems"], worktree


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
    subject = tmp_path / "m.py"
    subject.write_text("VALUE = 1\nCONTROL = 7\n", encoding="utf-8")
    assert subject.is_file() and "VALUE = 1" in subject.read_text(encoding="utf-8")
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
