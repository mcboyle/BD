"""Contract tests for the standalone semantic-diff frontend."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

import pytest

from tools.code_intelligence.results import ResultState
from tools.code_intelligence.schemas import validate_envelope
from tools.code_intelligence.semantic_service import (
    FunctionSemantics,
    classify_change,
    compare_semantics,
    run_semantic_diff,
    snapshot_tree,
)


ROOT = Path(__file__).resolve().parent.parent
SOURCE_FIX = ROOT / "tests" / "fixtures" / "code_intelligence" / "semantic"
_FIX_DIRECTORY = tempfile.TemporaryDirectory(prefix="semantic-diff-fixtures-")
FIX = Path(_FIX_DIRECTORY.name) / "semantic"
shutil.copytree(SOURCE_FIX, FIX)
HEX64 = "a" * 64


def _write_module(root: Path, source: str, name: str = "sample.py") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    module = root / name
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(source, encoding="utf-8")
    return module


def _semantic_snapshot(path: Path, functions: dict[str, FunctionSemantics]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_name": "bd.semantic-snapshot",
                "schema_version": 1,
                "source_sha": HEX64,
                "tool_version": "fixture-1",
                "input_hashes": {"tracked_tree": HEX64},
                "generated_at": "2026-07-23T00:00:00Z",
                "functions": {
                    key: asdict(value) for key, value in functions.items()
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _run(
    tmp_path: Path,
    *,
    before_tree: Path | None = None,
    after_tree: Path | None = None,
    before_snapshot: Path | None = None,
    after_snapshot: Path | None = None,
    check_path: Path | None = None,
    gate: bool = False,
    cst_adapter: str = "none",
):
    output = tmp_path / "semantic.json"
    result = run_semantic_diff(
        before_tree=before_tree,
        after_tree=after_tree,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        output_path=output,
        check_path=check_path,
        gate=gate,
        cst_adapter=cst_adapter,
    )
    return result, output


def test_snapshot_retains_full_signature_and_surfaces():
    functions = snapshot_tree(FIX / "after")
    fetch = functions["sample.py::fetch"]

    assert fetch.positional_only == ()
    assert fetch.positional == ("value",)
    assert fetch.keyword_only == ("strict",)
    assert fetch.vararg is None
    assert fetch.kwargs is None
    assert fetch.defaults == (("strict", "True"),)
    assert fetch.annotations == (("value", "str"), ("strict", "bool"))
    assert fetch.return_annotation == "str"
    assert fetch.return_shapes == ("name",)
    assert fetch.raises == ("ValueError",)
    assert fetch.decorators == ("require_login",)
    assert "require_login" in fetch.auth_gates
    assert "emit_metric" in fetch.metric_ops


def test_policy_distinguishes_breaking_risky_information_and_unknown():
    assert (
        classify_change("positional", ["value"], ["value", "required"])
        == "breaking"
    )
    assert classify_change("auth_gates", [], ["require_login"]) == "risky"
    assert classify_change("metric_ops", [], ["emit_metric"]) == "informational"
    assert classify_change("calls_unresolved", ["x"], ["y"]) == "unknown"
    assert classify_change("future_field", None, True) == "unknown"


def test_compare_reports_each_changed_contract_surface_as_json_values():
    changes = compare_semantics(
        snapshot_tree(FIX / "before"),
        snapshot_tree(FIX / "after"),
    )
    fields = {row["field"] for row in changes}

    assert {
        "keyword_only",
        "defaults",
        "annotations",
        "return_annotation",
        "raises",
        "decorators",
        "auth_gates",
        "metric_ops",
    } <= fields
    assert not any(
        row["field"] == "return_shapes" and row["before"] == row["after"]
        for row in changes
    )
    assert any(row["policy"] == "breaking" for row in changes)
    assert all(
        isinstance(row["before"], (bool, int, float, str, list, dict))
        or row["before"] is None
        for row in changes
    )
    assert changes == sorted(
        changes, key=lambda row: (str(row["function"]), str(row["field"]))
    )


def test_nested_duplicate_async_and_lambda_scopes_keep_distinct_local_facts(
    tmp_path,
):
    tree = tmp_path / "tree"
    _write_module(
        tree,
        """\
def helper():
    return 1

def outer(factory=make_default()):
    outer_call()
    callback = lambda: lambda_only()
    @decorate(register())
    async def inner(value: int = default_value()):
        inner_call()
        return await worker(value)
    return callback

if FLAG:
    def duplicate():
        first_only()
else:
    def duplicate():
        second_only()
""",
    )

    functions = snapshot_tree(tree)

    assert "sample.py::outer" in functions
    assert "sample.py::outer.inner" in functions
    duplicate_keys = sorted(
        key for key in functions if key.startswith("sample.py::duplicate")
    )
    assert len(duplicate_keys) == 2
    assert duplicate_keys[0] != duplicate_keys[1]
    outer = functions["sample.py::outer"]
    inner = functions["sample.py::outer.inner"]
    assert "outer_call" in outer.calls_unresolved
    assert "register" in outer.calls_unresolved
    assert "default_value" in outer.calls_unresolved
    assert "inner_call" not in outer.calls_unresolved
    assert "lambda_only" not in outer.calls_unresolved
    assert "inner_call" in inner.calls_unresolved
    assert "worker" in inner.calls_unresolved
    assert "helper" not in outer.calls_unresolved


def test_calls_are_resolved_only_to_unambiguous_local_declarations(tmp_path):
    tree = tmp_path / "tree"
    _write_module(
        tree,
        """\
def helper():
    return 1

class Worker:
    def run(self):
        helper()
        self.finish()
        external.call()

    def finish(self):
        return None
""",
    )

    functions = snapshot_tree(tree)
    run = functions["sample.py::Worker.run"]

    assert run.calls_resolved == (
        "sample.py::Worker.finish",
        "sample.py::helper",
    )
    assert run.calls_unresolved == ("external.call",)


def test_secret_literals_are_redacted_from_all_structural_expressions(tmp_path):
    tree = tmp_path / "tree"
    secret = "Bearer-raw-secret-value"
    _write_module(
        tree,
        f'''\
@authorize("{secret}")
def guarded(
    access_token: Annotated[str, "{secret}"] = "{secret}",
) -> Literal["{secret}"]:
    raise DomainError("{secret}")
''',
    )

    functions = snapshot_tree(tree)
    serialized = json.dumps(asdict(functions["sample.py::guarded"]), sort_keys=True)

    assert secret not in serialized
    assert "<redacted>" in serialized
    assert "DomainError" in serialized
    assert "authorize" in functions["sample.py::guarded"].auth_gates


def test_tree_rejects_symlinks_and_escaped_roots_without_reading_targets(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def leaked():\n    return 1\n", encoding="utf-8")
    link = tree / "link.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    with pytest.raises(ValueError, match="semantic tree invalid"):
        snapshot_tree(tree)


def test_run_emits_schema_source_hashes_locations_and_gate_policy(tmp_path):
    advisory, output = _run(
        tmp_path,
        before_tree=FIX / "before",
        after_tree=FIX / "after",
    )
    artifact = json.loads(output.read_text(encoding="utf-8"))

    assert advisory.state is ResultState.ADVISORY
    assert artifact["schema_name"] == "bd.semantic-diff"
    assert artifact["schema_version"] == 1
    assert artifact["engine"] == "ast"
    assert len(artifact["source_sha"]) == 64
    assert set(artifact["input_hashes"]) == {"after_tree", "before_tree"}
    assert all(len(value) == 64 for value in artifact["input_hashes"].values())
    assert artifact["summary"]["breaking"] > 0
    assert artifact["summary"]["total"] == len(artifact["changes"])
    assert any(
        location["function"] == "sample.py::fetch"
        and location["path"] == "sample.py"
        and location["line"] == 2
        for location in artifact["locations"]["after"]
    )

    gated, gated_output = _run(
        tmp_path / "gated",
        before_tree=FIX / "before",
        after_tree=FIX / "after",
        gate=True,
    )
    assert gated.state is ResultState.FAIL
    assert gated_output.is_file()


def test_unresolved_call_change_fails_closed_only_in_gate_mode(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    _write_module(before, "def work():\n    alpha()\n")
    _write_module(after, "def work():\n    beta()\n")

    advisory, _ = _run(
        tmp_path / "advisory",
        before_tree=before,
        after_tree=after,
    )
    gated, gated_output = _run(
        tmp_path / "gated",
        before_tree=before,
        after_tree=after,
        gate=True,
    )
    artifact = json.loads(gated_output.read_text(encoding="utf-8"))

    assert advisory.state is ResultState.ADVISORY
    assert gated.state is ResultState.FAIL
    assert any(
        row["field"] == "calls_unresolved" and row["policy"] == "unknown"
        for row in artifact["changes"]
    )


def test_semantic_snapshot_and_tree_frontends_have_ast_change_parity(tmp_path):
    before_snapshot = tmp_path / "before.json"
    after_snapshot = tmp_path / "after.json"
    _semantic_snapshot(before_snapshot, snapshot_tree(FIX / "before"))
    _semantic_snapshot(after_snapshot, snapshot_tree(FIX / "after"))

    tree_result, tree_output = _run(
        tmp_path / "tree-run",
        before_tree=FIX / "before",
        after_tree=FIX / "after",
    )
    snapshot_result, snapshot_output = _run(
        tmp_path / "snapshot-run",
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
    )
    tree_artifact = json.loads(tree_output.read_text(encoding="utf-8"))
    snapshot_artifact = json.loads(snapshot_output.read_text(encoding="utf-8"))

    assert snapshot_result.state is tree_result.state
    assert snapshot_artifact["changes"] == tree_artifact["changes"]
    assert snapshot_artifact["summary"] == tree_artifact["summary"]
    assert snapshot_artifact["input_hashes"]["before_snapshot"] == hashlib.sha256(
        before_snapshot.read_bytes()
    ).hexdigest()
    assert snapshot_artifact["input_hashes"]["after_snapshot"] == hashlib.sha256(
        after_snapshot.read_bytes()
    ).hexdigest()


@pytest.mark.parametrize(
    "raw",
    [
        '{"schema_name":"bd.semantic-snapshot","schema_name":"duplicate"}',
        '{"schema_name":"bd.semantic-snapshot","value":NaN}',
        "[]",
    ],
)
def test_malformed_duplicate_and_nonfinite_snapshots_are_controlled(
    tmp_path, raw
):
    before = tmp_path / "before.json"
    before.write_text(raw, encoding="utf-8")
    result, output = _run(
        tmp_path,
        before_snapshot=before,
        after_tree=FIX / "after",
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "before snapshot invalid"
    assert result.evidence == {"stage": "before_snapshot"}
    assert not output.exists()
    assert "duplicate" not in json.dumps(asdict(result), sort_keys=True)
    assert "NaN" not in json.dumps(asdict(result), sort_keys=True)


def test_check_is_validated_before_output_replacement(tmp_path):
    first, generated = _run(
        tmp_path / "first",
        before_tree=FIX / "before",
        after_tree=FIX / "after",
    )
    assert first.state is ResultState.ADVISORY
    check = tmp_path / "check.json"
    check.write_bytes(generated.read_bytes())
    output = tmp_path / "preserved.json"
    output.write_text('{"preserved":true}\n', encoding="utf-8")

    drift = run_semantic_diff(
        before_tree=FIX / "after",
        after_tree=FIX / "before",
        before_snapshot=None,
        after_snapshot=None,
        output_path=output,
        check_path=check,
        gate=False,
        cst_adapter="none",
    )

    assert drift.state is ResultState.FAIL
    assert drift.summary == "semantic artifact drift"
    assert output.read_text(encoding="utf-8") == '{"preserved":true}\n'


def test_check_accepts_valid_artifact_up_to_producer_size_limit(tmp_path):
    first, generated = _run(
        tmp_path / "first",
        before_tree=FIX / "before",
        after_tree=FIX / "after",
    )
    assert first.state is ResultState.ADVISORY
    artifact = json.loads(generated.read_text(encoding="utf-8"))
    artifact["cst"]["positions"] = ["x" * 16_000 for _ in range(1_050)]
    check = tmp_path / "large-check.json"
    check.write_text(json.dumps(artifact), encoding="utf-8")
    assert 16 * 1024 * 1024 < check.stat().st_size < 32 * 1024 * 1024

    result, _output = _run(
        tmp_path / "checked",
        before_tree=FIX / "before",
        after_tree=FIX / "after",
        check_path=check,
    )

    assert result.state is ResultState.FAIL
    assert result.summary == "semantic artifact drift"


def test_output_aliases_input_check_and_tree_are_rejected(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    _semantic_snapshot(snapshot, snapshot_tree(FIX / "before"))
    alias_result = run_semantic_diff(
        before_tree=None,
        after_tree=FIX / "after",
        before_snapshot=snapshot,
        after_snapshot=None,
        output_path=snapshot,
        check_path=None,
        gate=False,
        cst_adapter="none",
    )

    assert alias_result.state is ResultState.ERROR
    assert alias_result.summary == "semantic artifact path invalid"

    inside_tree = tmp_path / "tree"
    _write_module(inside_tree, "def unchanged():\n    return None\n")
    tree_result = run_semantic_diff(
        before_tree=inside_tree,
        after_tree=FIX / "after",
        before_snapshot=None,
        after_snapshot=None,
        output_path=inside_tree / "result.json",
        check_path=None,
        gate=False,
        cst_adapter="none",
    )
    assert tree_result.state is ResultState.ERROR
    assert tree_result.summary == "semantic artifact path invalid"


def test_optional_libcst_cannot_change_ast_verdict(tmp_path, monkeypatch):
    import tools.code_intelligence.semantic_service as service

    plain, plain_output = _run(
        tmp_path / "plain",
        before_tree=FIX / "before",
        after_tree=FIX / "after",
    )

    def unavailable(_name):
        raise ModuleNotFoundError("host-specific module message")

    monkeypatch.setattr(service.importlib, "import_module", unavailable)
    optional, optional_output = _run(
        tmp_path / "optional",
        before_tree=FIX / "before",
        after_tree=FIX / "after",
        cst_adapter="libcst",
    )
    plain_artifact = json.loads(plain_output.read_text(encoding="utf-8"))
    optional_artifact = json.loads(optional_output.read_text(encoding="utf-8"))

    assert optional.state is plain.state
    assert optional.summary == plain.summary
    assert optional_artifact["changes"] == plain_artifact["changes"]
    assert optional_artifact["summary"] == plain_artifact["summary"]
    assert optional_artifact["cst"] == {
        "adapter": "libcst",
        "positions": [],
        "status": "unavailable",
    }
    assert "host-specific" not in optional_output.read_text(encoding="utf-8")


def test_cli_requires_exactly_one_before_and_one_after_source():
    missing_after = subprocess.run(
        [
            sys.executable,
            "tools/semantic_diff.py",
            "--before-tree",
            str(FIX / "before"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    duplicate_before = subprocess.run(
        [
            sys.executable,
            "tools/semantic_diff.py",
            "--before-tree",
            str(FIX / "before"),
            "--before-snapshot",
            str(ROOT / "missing.json"),
            "--after-tree",
            str(FIX / "after"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert missing_after.returncode == 2
    assert "one after source is required" in missing_after.stderr
    assert duplicate_before.returncode == 2
    assert "one before source is required" in duplicate_before.stderr


def test_cli_help_is_lazy_and_portable_outside_repository(tmp_path):
    tool = ROOT / "tools" / "semantic_diff.py"
    run = subprocess.run(
        [sys.executable, str(tool), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert run.returncode == 0
    for option in (
        "--before-tree",
        "--after-tree",
        "--before-snapshot",
        "--after-snapshot",
        "--out",
        "--check",
        "--gate",
        "--cst-adapter",
        "--json",
    ):
        assert option in run.stdout


def test_cli_json_reports_enum_values_and_gate_exit(tmp_path):
    output = tmp_path / "result.json"
    run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "semantic_diff.py"),
            "--before-tree",
            str(FIX / "before"),
            "--after-tree",
            str(FIX / "after"),
            "--out",
            str(output),
            "--gate",
            "--json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert run.returncode == 1
    assert json.loads(run.stdout)["state"] == "fail"
    assert output.is_file()


def test_source_change_during_atomic_write_is_controlled_and_preserves_output(
    tmp_path, monkeypatch
):
    import tools.code_intelligence.semantic_service as service

    before = tmp_path / "before"
    after = tmp_path / "after"
    _write_module(before, "def work():\n    return 1\n")
    changed = _write_module(after, "def work():\n    return 2\n")
    output = tmp_path / "result.json"
    output.write_text('{"preserved":true}\n', encoding="utf-8")
    real_replace = service.os.replace

    def mutate_then_replace(source, destination, *args, **kwargs):
        changed.write_text("def work():\n    return 3\n", encoding="utf-8")
        real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(service.os, "replace", mutate_then_replace)
    result = run_semantic_diff(
        before_tree=before,
        after_tree=after,
        before_snapshot=None,
        after_snapshot=None,
        output_path=output,
        check_path=None,
        gate=False,
        cst_adapter="none",
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "after tree changed during analysis"
    assert output.read_text(encoding="utf-8") == '{"preserved":true}\n'
    assert list(tmp_path.glob(".result.json.*.tmp")) == []


def test_tree_path_rendering_is_posix_and_source_order_is_deterministic(tmp_path):
    tree = tmp_path / "tree"
    _write_module(tree, "def zed():\n    return None\n", "z/z.py")
    _write_module(tree, "def alpha():\n    return None\n", "a.py")

    functions = snapshot_tree(tree)

    assert list(functions) == ["a.py::alpha", "z/z.py::zed"]
    assert all("\\" not in key for key in functions)


@pytest.mark.skipif(os.name == "nt", reason="hard links differ on Windows")
def test_hard_link_output_alias_to_snapshot_is_rejected(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    _semantic_snapshot(snapshot, snapshot_tree(FIX / "before"))
    output = tmp_path / "alias.json"
    os.link(snapshot, output)

    result = run_semantic_diff(
        before_tree=None,
        after_tree=FIX / "after",
        before_snapshot=snapshot,
        after_snapshot=None,
        output_path=output,
        check_path=None,
        gate=False,
        cst_adapter="none",
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "semantic artifact path invalid"


def test_literals_are_secret_safe_but_losslessly_distinct_and_decorator_ordered(
    tmp_path,
):
    before = tmp_path / "before"
    after = tmp_path / "after"
    _write_module(
        before,
        '@zeta("Bearer-first-secret")\n@alpha\ndef work(value="Bearer-first-secret"):\n    return value\n',
    )
    _write_module(
        after,
        '@zeta("Bearer-second-secret")\n@alpha\ndef work(value="Bearer-second-secret"):\n    return value\n',
    )
    first = snapshot_tree(before)["sample.py::work"]
    second = snapshot_tree(after)["sample.py::work"]

    rendered = json.dumps(asdict(first), sort_keys=True)
    assert "Bearer-first-secret" not in rendered
    assert "<str:sha256:" in rendered
    assert first.defaults != second.defaults
    assert first.decorators[0].startswith("zeta(")
    assert first.decorators[1] == "alpha"


def test_match_bare_raise_yield_and_shadowed_local_calls_are_distinct(tmp_path):
    tree = tmp_path / "tree"
    _write_module(
        tree,
        '''\
def helper():
    return None

match subject:
    case _:
        def matched():
            yield value
            raise

def work(helper):
    helper()
''',
    )
    functions = snapshot_tree(tree)
    matched = functions["sample.py::matched"]
    assert "yield:name" in matched.return_shapes
    assert "raise:bare" in matched.raises
    assert functions["sample.py::work"].calls_resolved == ()
    assert functions["sample.py::work"].calls_unresolved == ("helper",)


def test_source_contract_syntax_and_adapter_errors_are_controlled(tmp_path):
    before = tmp_path / "before"
    _write_module(before, "def ok():\n    return 1\n")
    output = tmp_path / "result.json"
    invalid_pair = run_semantic_diff(
        before_tree=before,
        after_tree=before,
        before_snapshot=tmp_path / "also.json",
        after_snapshot=None,
        output_path=output,
        check_path=None,
        gate=False,
        cst_adapter="none",
    )
    bad_adapter = run_semantic_diff(
        before_tree=before,
        after_tree=before,
        before_snapshot=None,
        after_snapshot=None,
        output_path=output,
        check_path=None,
        gate=False,
        cst_adapter="surprise",  # type: ignore[arg-type]
    )
    _write_module(before, "def broken(:\n", "broken.py")
    syntax = run_semantic_diff(
        before_tree=before,
        after_tree=FIX / "after",
        before_snapshot=None,
        after_snapshot=None,
        output_path=output,
        check_path=None,
        gate=False,
        cst_adapter="none",
    )

    assert (invalid_pair.state, invalid_pair.summary) == (
        ResultState.ERROR,
        "semantic source invalid",
    )
    assert (bad_adapter.state, bad_adapter.summary) == (
        ResultState.ERROR,
        "semantic cst adapter invalid",
    )
    assert (syntax.state, syntax.summary) == (
        ResultState.ERROR,
        "before tree invalid",
    )
    assert not output.exists()


def test_snapshot_schema_and_check_are_strict_and_baseexception_cleans_up(
    tmp_path, monkeypatch
):
    snapshot = tmp_path / "before.json"
    _semantic_snapshot(snapshot, snapshot_tree(FIX / "before"))
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    data["schema_version"] = True
    snapshot.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "result.json"
    malformed = run_semantic_diff(
        before_tree=None,
        after_tree=FIX / "after",
        before_snapshot=snapshot,
        after_snapshot=None,
        output_path=output,
        check_path=None,
        gate=False,
        cst_adapter="none",
    )
    check = tmp_path / "check.json"
    check.write_text("{not-json", encoding="utf-8")
    check_result = run_semantic_diff(
        before_tree=FIX / "before",
        after_tree=FIX / "after",
        before_snapshot=None,
        after_snapshot=None,
        output_path=output,
        check_path=check,
        gate=False,
        cst_adapter="none",
    )
    output.write_text('{"preserved":true}\n', encoding="utf-8")
    import tools.code_intelligence.semantic_service as service
    monkeypatch.setattr(service.os, "replace", lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()))
    interrupted = run_semantic_diff(
        before_tree=FIX / "before",
        after_tree=FIX / "after",
        before_snapshot=None,
        after_snapshot=None,
        output_path=output,
        check_path=None,
        gate=False,
        cst_adapter="none",
    )

    assert malformed.summary == "before snapshot invalid"
    assert check_result.summary == "semantic check invalid"
    assert interrupted.summary == "semantic artifact write failed"
    assert output.read_text(encoding="utf-8") == '{"preserved":true}\n'
    assert not list(tmp_path.glob(".result.json.*.tmp*"))


def test_vararg_and_kwargs_annotations_follow_signature_order(tmp_path):
    tree = tmp_path / "tree"
    _write_module(
        tree,
        """\
def work(
    first: First,
    /,
    second: Second,
    *items: Items,
    named: Named,
    **options: Options,
):
    return None
""",
    )

    semantics = snapshot_tree(tree)["sample.py::work"]

    assert semantics.annotations == (
        ("first", "First"),
        ("second", "Second"),
        ("items", "Items"),
        ("named", "Named"),
        ("options", "Options"),
    )


def test_defaults_follow_signature_order_across_parameter_kinds(tmp_path):
    tree = tmp_path / "tree"
    _write_module(
        tree,
        """\
def work(zeta=1, /, alpha=2, *, middle=3):
    return None
""",
    )

    semantics = snapshot_tree(tree)["sample.py::work"]

    assert semantics.defaults == (
        ("zeta", "1"),
        ("alpha", "2"),
        ("middle", "3"),
    )


def test_return_shapes_distinguish_none_integer_and_expression_types(tmp_path):
    tree = tmp_path / "tree"
    _write_module(
        tree,
        """\
def work(flag):
    if flag == 1:
        return
    if flag == 2:
        return None
    if flag == 3:
        return 1
    return factory()
""",
    )

    semantics = snapshot_tree(tree)["sample.py::work"]

    assert semantics.return_shapes == ("call", "int", "none")


def test_locations_cover_match_and_trystar_statement_bodies(tmp_path):
    tree = tmp_path / "tree"
    _write_module(
        tree,
        """\
match subject:
    case 1:
        def matched():
            return None

try:
    value()
except* ValueError:
    def grouped():
        return None
""",
    )

    result, output = _run(
        tmp_path / "run",
        before_tree=tree,
        after_tree=tree,
    )
    artifact = json.loads(output.read_text(encoding="utf-8"))

    assert result.state is ResultState.ADVISORY
    assert {
        row["function"] for row in artifact["locations"]["before"]
    } == {"sample.py::grouped", "sample.py::matched"}


def test_lexical_assignments_shadow_locals_and_duplicate_classes_stay_ambiguous(
    tmp_path,
):
    tree = tmp_path / "tree"
    _write_module(
        tree,
        """\
def helper():
    return None

def work():
    helper = external
    helper()

class Duplicate:
    def run(self):
        self.finish()
    def finish(self):
        return 1

class Duplicate:
    def run(self):
        self.finish()
    def finish(self):
        return 2

class Ambiguous:
    def run(self):
        self.finish()
    def finish(self):
        return 1
    def finish(self):
        return 2
""",
    )

    functions = snapshot_tree(tree)

    assert functions["sample.py::work"].calls_resolved == ()
    assert functions["sample.py::work"].calls_unresolved == ("helper",)
    duplicate_runs = [
        (key, value)
        for key, value in functions.items()
        if key.startswith("sample.py::Duplicate") and key.endswith(".run")
    ]
    assert len(duplicate_runs) == 2
    assert duplicate_runs[0][1].calls_resolved == (
        "sample.py::Duplicate.finish",
    )
    assert duplicate_runs[1][1].calls_resolved == (
        "sample.py::Duplicate#2.finish",
    )
    ambiguous = functions["sample.py::Ambiguous.run"]
    assert ambiguous.calls_resolved == ()
    assert ambiguous.calls_unresolved == ("self.finish",)


def test_module_and_class_rebindings_mask_stale_function_declarations(tmp_path):
    tree = tmp_path / "tree"
    _write_module(
        tree,
        """\
def assigned_helper():
    return None

assigned_helper = object()

def assignment_caller():
    return assigned_helper()

def imported_helper():
    return None

import imported_helper

def import_caller():
    return imported_helper()

import restored_helper

def restored_helper():
    return None

def restored_caller():
    return restored_helper()

class Rebound:
    def finish(self):
        return None

    finish = object()

    def run(self):
        return self.finish()

class Restored:
    finish = object()

    def finish(self):
        return None

    def run(self):
        return self.finish()
""",
    )

    functions = snapshot_tree(tree)

    assert functions["sample.py::assignment_caller"].calls_resolved == ()
    assert functions["sample.py::assignment_caller"].calls_unresolved == (
        "assigned_helper",
    )
    assert functions["sample.py::import_caller"].calls_resolved == ()
    assert functions["sample.py::import_caller"].calls_unresolved == (
        "imported_helper",
    )
    assert functions["sample.py::restored_caller"].calls_resolved == (
        "sample.py::restored_helper",
    )
    assert functions["sample.py::restored_caller"].calls_unresolved == ()
    assert functions["sample.py::Rebound.run"].calls_resolved == ()
    assert functions["sample.py::Rebound.run"].calls_unresolved == (
        "self.finish",
    )
    assert functions["sample.py::Restored.run"].calls_resolved == (
        "sample.py::Restored.finish",
    )
    assert functions["sample.py::Restored.run"].calls_unresolved == ()


def test_snapshot_literals_are_fingerprinted_and_never_emitted_raw(tmp_path):
    secret = "Bearer-snapshot-only-secret"
    tree = tmp_path / "tree"
    _write_module(tree, "def work(value):\n    return value\n")
    snapshot = tmp_path / "snapshot.json"
    functions = snapshot_tree(tree)
    _semantic_snapshot(snapshot, functions)
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    data["functions"]["sample.py::work"]["defaults"] = [
        ["value", json.dumps(secret)]
    ]
    snapshot.write_text(json.dumps(data), encoding="utf-8")

    result, output = _run(
        tmp_path / "run",
        before_snapshot=snapshot,
        after_tree=tree,
    )
    rendered = output.read_text(encoding="utf-8")

    assert result.state is ResultState.ADVISORY
    assert secret not in rendered
    assert hashlib.sha256(secret.encode()).hexdigest() in rendered


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: item["functions"]["sample.py::fetch"].__setitem__(
            "positional", ["value", 7]
        ),
        lambda item: item["functions"]["sample.py::fetch"].__setitem__(
            "defaults", [["strict"]]
        ),
        lambda item: item["functions"]["sample.py::fetch"].__setitem__(
            "calls_unresolved", ["zeta", "alpha"]
        ),
        lambda item: item["functions"]["sample.py::fetch"].__setitem__(
            "keyword_only", ["strict", "strict"]
        ),
        lambda item: item["functions"]["sample.py::fetch"].__setitem__(
            "path", "../sample.py"
        ),
        lambda item: item["functions"]["sample.py::fetch"].__setitem__(
            "qualname", "other"
        ),
        lambda item: item.__setitem__("tool_version", ""),
        lambda item: item.__setitem__("generated_at", "not-a-timestamp"),
        lambda item: item.__setitem__("input_hashes", {"tracked_tree": "bad"}),
    ],
)
def test_snapshot_fields_pairs_order_uniqueness_identity_and_envelope_are_strict(
    tmp_path, mutate
):
    snapshot = tmp_path / "snapshot.json"
    _semantic_snapshot(snapshot, snapshot_tree(FIX / "after"))
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    mutate(data)
    snapshot.write_text(json.dumps(data), encoding="utf-8")

    result, output = _run(
        tmp_path / "run",
        before_snapshot=snapshot,
        after_tree=FIX / "after",
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "before snapshot invalid"
    assert not output.exists()


def test_snapshot_json_resource_limits_are_controlled(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(b" " * (2 * 1024 * 1024 + 1))

    result, output = _run(
        tmp_path / "run",
        before_snapshot=snapshot,
        after_tree=FIX / "after",
    )

    assert result.state is ResultState.ERROR
    assert result.evidence == {"stage": "before_snapshot"}
    assert not output.exists()


def test_output_uses_valid_shared_envelope_with_real_source_identity(tmp_path):
    result, output = _run(
        tmp_path,
        before_tree=FIX / "before",
        after_tree=FIX / "after",
    )
    artifact = json.loads(output.read_text(encoding="utf-8"))

    assert result.state is ResultState.ADVISORY
    validate_envelope(artifact, expected_name="bd.semantic-diff")
    assert artifact["tool_version"]
    assert artifact["generated_at"].endswith("Z")
    assert artifact["source_sha"] == hashlib.sha256(
        (
            artifact["input_hashes"]["before_tree"]
            + "\0"
            + artifact["input_hashes"]["after_tree"]
        ).encode()
    ).hexdigest()


def test_wrong_schema_check_is_error_and_preserves_output(tmp_path):
    output = tmp_path / "result.json"
    output.write_text('{"preserved":true}\n', encoding="utf-8")
    check = tmp_path / "check.json"
    check.write_text(
        json.dumps(
            {
                "schema_name": "bd.wrong",
                "schema_version": 1,
                "source_sha": HEX64,
                "tool_version": "fixture",
                "input_hashes": {},
                "generated_at": "2026-07-23T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    result = run_semantic_diff(
        before_tree=FIX / "before",
        after_tree=FIX / "after",
        before_snapshot=None,
        after_snapshot=None,
        output_path=output,
        check_path=check,
        gate=False,
        cst_adapter="none",
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "semantic check invalid"
    assert output.read_text(encoding="utf-8") == '{"preserved":true}\n'


def test_snapshot_and_check_drift_before_replace_preserves_output(
    tmp_path, monkeypatch
):
    import tools.code_intelligence.semantic_service as service

    snapshot = tmp_path / "snapshot.json"
    _semantic_snapshot(snapshot, snapshot_tree(FIX / "before"))
    baseline_dir = tmp_path / "baseline"
    baseline, generated = _run(
        baseline_dir,
        before_snapshot=snapshot,
        after_tree=FIX / "after",
    )
    assert baseline.state is ResultState.ADVISORY
    check = tmp_path / "check.json"
    check.write_bytes(generated.read_bytes())
    output = tmp_path / "result.json"
    output.write_text('{"preserved":true}\n', encoding="utf-8")
    real_replace = service.os.replace
    calls = 0

    def drift_then_replace(source, destination, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            snapshot.write_bytes(snapshot.read_bytes() + b" ")
            check.write_bytes(check.read_bytes() + b" ")
        real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(service.os, "replace", drift_then_replace)
    result = run_semantic_diff(
        before_tree=None,
        after_tree=FIX / "after",
        before_snapshot=snapshot,
        after_snapshot=None,
        output_path=output,
        check_path=check,
        gate=False,
        cst_adapter="none",
    )

    assert result.state is ResultState.ERROR
    assert result.evidence == {}
    assert output.read_text(encoding="utf-8") == '{"preserved":true}\n'


def test_same_bytes_tree_retarget_before_replace_is_detected(tmp_path, monkeypatch):
    import tools.code_intelligence.semantic_service as service

    before = tmp_path / "before"
    after = tmp_path / "after"
    _write_module(before, "def work():\n    return 1\n")
    target = _write_module(after, "def work():\n    return 2\n")
    output = tmp_path / "result.json"
    output.write_text('{"preserved":true}\n', encoding="utf-8")
    real_replace = service.os.replace
    calls = 0

    def retarget_then_replace(source, destination, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            replacement = target.with_suffix(".replacement")
            replacement.write_bytes(target.read_bytes())
            real_replace(replacement, target)
        real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(service.os, "replace", retarget_then_replace)
    result = run_semantic_diff(
        before_tree=before,
        after_tree=after,
        before_snapshot=None,
        after_snapshot=None,
        output_path=output,
        check_path=None,
        gate=False,
        cst_adapter="none",
    )

    assert result.state is ResultState.ERROR
    assert result.evidence == {}
    assert output.read_text(encoding="utf-8") == '{"preserved":true}\n'


def test_tree_rejects_symlinked_ancestor_and_resource_overflow(tmp_path):
    real = tmp_path / "real"
    _write_module(real, "def work():\n    return None\n")
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    with pytest.raises(ValueError, match="semantic tree invalid"):
        snapshot_tree(linked)

    oversized = tmp_path / "oversized"
    _write_module(
        oversized,
        "def work():\n    value = 1\n" + ("    value += 1\n" * 70_000),
    )
    with pytest.raises(ValueError, match="semantic tree invalid"):
        snapshot_tree(oversized)


def test_git_tree_reads_only_tracked_python_files(tmp_path):
    repository = tmp_path / "repository"
    tracked = _write_module(
        repository, "def tracked():\n    return None\n", "src/tracked.py"
    )
    subprocess.run(["git", "init", "-q", repository], check=True)
    subprocess.run(
        ["git", "-C", repository, "add", tracked.relative_to(repository)],
        check=True,
    )
    _write_module(
        repository, "def ignored():\n    return None\n", "src/untracked.py"
    )
    runtime = repository / ".venv" / "bin"
    runtime.mkdir(parents=True)
    try:
        (runtime / "python").symlink_to(sys.executable)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    functions = snapshot_tree(repository)

    assert set(functions) == {"src/tracked.py::tracked"}


def test_git_tree_ignores_entirely_untracked_subdirectory(tmp_path):
    repository = tmp_path / "repository"
    tracked = _write_module(
        repository, "def tracked():\n    return None\n", "tracked.py"
    )
    subprocess.run(["git", "init", "-q", repository], check=True)
    subprocess.run(
        ["git", "-C", repository, "add", tracked.relative_to(repository)],
        check=True,
    )
    untracked_root = repository / "scratch"
    _write_module(
        untracked_root, "def ignored():\n    return None\n", "ignored.py"
    )

    functions = snapshot_tree(untracked_root)

    assert functions == {}


def test_snapshot_redaction_is_idempotent_for_nested_expressions(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """
@guard("decorator-secret")
@guard("decorator-secret")
def work(value: Annotated[str, "annotation-secret"] = factory("default-secret")):
    return sink("body-secret")
""",
    )
    snapshot = tmp_path / "snapshot.json"
    functions = snapshot_tree(source)
    _semantic_snapshot(snapshot, functions)

    result, output = _run(
        tmp_path / "run",
        before_snapshot=snapshot,
        after_tree=source,
    )

    assert result.state is ResultState.ADVISORY
    assert json.loads(output.read_text(encoding="utf-8"))["changes"] == []


def test_scope_aware_resolution_and_duplicate_parent_ownership(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """
def helper():
    return None

def nested_assignment():
    def inner():
        helper = object()
    return helper()

def comprehension_target():
    values = [helper for helper in range(1)]
    return helper()

def imported():
    import helper
    return helper()

def captured(value):
    match value:
        case helper:
            return helper()

def lexical_outer():
    def local_helper():
        return None

    def local_caller():
        return local_helper()

    return local_caller()

def outer():
    def child():
        return None

def outer():
    def child():
        return None

class Worker:
    def finish(self):
        return None

    def run(self):
        self = object()
        return self.finish()
""",
    )

    functions = snapshot_tree(source)

    assert functions["sample.py::nested_assignment"].calls_resolved == (
        "sample.py::helper",
    )
    assert functions["sample.py::comprehension_target"].calls_resolved == (
        "sample.py::helper",
    )
    assert "helper" in functions["sample.py::imported"].calls_unresolved
    assert "helper" in functions["sample.py::captured"].calls_unresolved
    assert "sample.py::outer.child" in functions
    assert "sample.py::outer#2.child" in functions
    assert "self.finish" in functions["sample.py::Worker.run"].calls_unresolved
    assert functions[
        "sample.py::lexical_outer.local_caller"
    ].calls_resolved == ("sample.py::lexical_outer.local_helper",)
    assert functions["sample.py::lexical_outer"].calls_resolved == (
        "sample.py::lexical_outer.local_caller",
    )


def test_function_headers_resolve_in_the_enclosing_lexical_scope(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
def helper():
    return None

@helper()
def f(
    value: helper() = helper(),
) -> helper():
    helper = 1

def g(value=inner()):
    def inner():
        return None
    return inner()

def outer():
    def local_helper():
        return None

    def caller(value=local_helper()):
        return local_helper()

    return caller()
""",
    )

    functions = snapshot_tree(source)

    assert functions["sample.py::f"].calls_resolved == ("sample.py::helper",)
    assert functions["sample.py::f"].calls_unresolved == ()
    assert functions["sample.py::g"].calls_resolved == ("sample.py::g.inner",)
    assert functions["sample.py::g"].calls_unresolved == ("inner",)
    assert functions["sample.py::outer.caller"].calls_resolved == (
        "sample.py::outer.local_helper",
    )
    assert functions["sample.py::outer.caller"].calls_unresolved == ()


def test_nested_headers_do_not_resolve_through_enclosing_non_function_bindings(
    tmp_path,
):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
def helper():
    return None

def parameter_shadow(helper):
    def child(value=helper()):
        return value

def assignment_shadow():
    helper = object()
    def child(value=helper()):
        return value

def import_shadow():
    import helper
    def child(value=helper()):
        return value

class ClassShadow:
    helper = object()
    def method(value=helper()):
        return value
""",
    )

    functions = snapshot_tree(source)

    for key in (
        "sample.py::parameter_shadow.child",
        "sample.py::assignment_shadow.child",
        "sample.py::import_shadow.child",
        "sample.py::ClassShadow.method",
    ):
        assert functions[key].calls_resolved == ()
        assert functions[key].calls_unresolved == ("helper",)


def test_check_deep_schema_is_strict_and_nested_generated_at_is_not_ignored(
    tmp_path,
):
    baseline, generated = _run(
        tmp_path / "baseline",
        before_tree=FIX / "before",
        after_tree=FIX / "after",
    )
    assert baseline.state is ResultState.ADVISORY
    artifact = json.loads(generated.read_text(encoding="utf-8"))
    artifact["changes"][0]["generated_at"] = "nested bypass"
    check = tmp_path / "check.json"
    check.write_text(json.dumps(artifact), encoding="utf-8")
    output = tmp_path / "result.json"
    output.write_text('{"preserved":true}\n', encoding="utf-8")

    result = run_semantic_diff(
        before_tree=FIX / "before",
        after_tree=FIX / "after",
        before_snapshot=None,
        after_snapshot=None,
        output_path=output,
        check_path=check,
        gate=False,
        cst_adapter="none",
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "semantic check invalid"
    assert output.read_text(encoding="utf-8") == '{"preserved":true}\n'


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["positional"].append(value["keyword_only"][0]),
        lambda value: value["defaults"].append(["items", "None"]),
        lambda value: value.__setitem__("qualname", "../unsafe"),
        lambda value: value["return_shapes"].append("secret value"),
    ],
)
def test_snapshot_rejects_impossible_or_unsafe_structural_fields(
    tmp_path, mutate
):
    snapshot = tmp_path / "snapshot.json"
    _semantic_snapshot(snapshot, snapshot_tree(FIX / "after"))
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    value = next(iter(data["functions"].values()))
    mutate(value)
    snapshot.write_text(json.dumps(data), encoding="utf-8")

    result, output = _run(
        tmp_path / "run",
        before_snapshot=snapshot,
        after_tree=FIX / "after",
    )

    assert result.state is ResultState.ERROR
    assert not output.exists()


def test_pep263_source_and_resource_failures_are_controlled(tmp_path, monkeypatch):
    encoded = tmp_path / "encoded"
    encoded.mkdir()
    (encoded / "sample.py").write_bytes(
        '# -*- coding: latin-1 -*-\ndef work(value="é"):\n    return value\n'.encode(
            "latin-1"
        )
    )
    functions = snapshot_tree(encoded)
    assert "sample.py::work" in functions

    import tools.code_intelligence.semantic_service as service

    monkeypatch.setattr(
        service,
        "_capture_tree",
        lambda _path: (_ for _ in ()).throw(RecursionError()),
    )
    result = run_semantic_diff(
        before_tree=encoded,
        after_tree=encoded,
        before_snapshot=None,
        after_snapshot=None,
        output_path=tmp_path / "result.json",
        check_path=None,
        gate=False,
        cst_adapter="none",
    )
    assert result.state is ResultState.ERROR
    assert result.evidence == {"stage": "before_tree"}


def test_output_parent_retarget_cannot_redirect_atomic_write(tmp_path, monkeypatch):
    import tools.code_intelligence.semantic_service as service

    parent = tmp_path / "output"
    parent.mkdir()
    output = parent / "result.json"
    output.write_text('{"preserved":true}\n', encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    detached = tmp_path / "detached"
    real_replace = service.os.replace
    calls = 0

    def retarget_parent_then_replace(source, destination, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            real_replace(parent, detached)
            parent.symlink_to(outside, target_is_directory=True)
        real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(service.os, "replace", retarget_parent_then_replace)
    result = run_semantic_diff(
        before_tree=FIX / "before",
        after_tree=FIX / "after",
        before_snapshot=None,
        after_snapshot=None,
        output_path=output,
        check_path=None,
        gate=False,
        cst_adapter="none",
    )

    assert result.state is ResultState.ERROR
    assert not (outside / "result.json").exists()
    assert (detached / "result.json").read_text(encoding="utf-8") == (
        '{"preserved":true}\n'
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX dir-fd regression")
def test_atomic_write_uses_replace_relative_to_pinned_directory(
    tmp_path, monkeypatch
):
    import tools.code_intelligence.semantic_service as service

    output = tmp_path / "output" / "result.json"
    real_replace = service.os.replace
    calls = []

    def record_replace(source, destination, *args, **kwargs):
        calls.append((source, destination, kwargs))
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(service.os, "replace", record_replace)
    result = run_semantic_diff(
        before_tree=FIX / "before",
        after_tree=FIX / "after",
        before_snapshot=None,
        after_snapshot=None,
        output_path=output,
        check_path=None,
        gate=False,
        cst_adapter="none",
    )

    assert result.state is ResultState.ADVISORY
    assert len(calls) == 2
    for source, destination, kwargs in calls:
        assert "/" not in str(source)
        assert "/" not in str(destination)
        assert kwargs["src_dir_fd"] == kwargs["dst_dir_fd"]
        assert isinstance(kwargs["src_dir_fd"], int)


def test_snapshot_expression_surfaces_cannot_emit_raw_secret_values(tmp_path):
    secret = "Bearer raw snapshot secret"
    snapshot = tmp_path / "snapshot.json"
    _semantic_snapshot(snapshot, snapshot_tree(FIX / "before"))
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    value = next(iter(data["functions"].values()))
    value["calls_unresolved"] = [repr(secret)]
    snapshot.write_text(json.dumps(data), encoding="utf-8")

    result, output = _run(
        tmp_path / "run",
        before_snapshot=snapshot,
        after_tree=FIX / "after",
    )

    assert result.state is ResultState.ADVISORY
    payload = output.read_text(encoding="utf-8")
    assert secret not in payload
    assert hashlib.sha256(secret.encode()).hexdigest() in payload


@pytest.mark.parametrize(
    "mutate",
    [
        lambda artifact: artifact["summary"].__setitem__(
            "total", artifact["summary"]["total"] + 1
        ),
        lambda artifact: artifact["changes"][0].__setitem__(
            "policy", "unknown"
        ),
        lambda artifact: artifact["locations"]["before"][0].__setitem__(
            "line", 0
        ),
        lambda artifact: artifact["cst"].__setitem__("extra", True),
        lambda artifact: artifact.__setitem__("source_sha", "b" * 64),
    ],
)
def test_check_rejects_deep_structural_contradictions(tmp_path, mutate):
    baseline, generated = _run(
        tmp_path / "baseline",
        before_tree=FIX / "before",
        after_tree=FIX / "after",
    )
    assert baseline.state is ResultState.ADVISORY
    artifact = json.loads(generated.read_text(encoding="utf-8"))
    mutate(artifact)
    check = tmp_path / "check.json"
    check.write_text(json.dumps(artifact), encoding="utf-8")

    result, output = _run(
        tmp_path / "run",
        before_tree=FIX / "before",
        after_tree=FIX / "after",
        check_path=check,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "semantic check invalid"
    assert not output.exists()


def test_same_source_reuse_does_not_trust_a_symlink_alias(tmp_path):
    source = tmp_path / "source"
    _write_module(source, "def work():\n    return None\n")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(source, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    result, output = _run(
        tmp_path / "run",
        before_tree=source,
        after_tree=alias,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "after tree invalid"
    assert result.evidence == {"stage": "after_tree"}
    assert not output.exists()


def test_numeric_credentials_are_fingerprinted_without_hiding_safe_numbers(
    tmp_path,
):
    source = tmp_path / "source"
    _write_module(
        source,
        """
@require_otp(654321)
def login(pin=123456, retries=3, *, password=789012):
    return retries
""",
    )

    function = snapshot_tree(source)["sample.py::login"]
    rendered = json.dumps(asdict(function), sort_keys=True)
    defaults = dict(function.defaults)

    assert defaults["pin"] != "123456"
    assert defaults["password"] != "789012"
    assert "654321" not in function.decorators[0]
    for secret in ("123456", "654321", "789012"):
        assert hashlib.sha256(secret.encode("ascii")).hexdigest() in rendered
    assert defaults["retries"] == "3"


def test_call_resolution_observes_function_body_binding_order(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
def restored():
    helper = object()
    def helper():
        return None
    return helper()

def before_definition():
    helper()
    def helper():
        return None

def before_rebinding():
    def helper():
        return None
    helper()
    helper = object()
""",
    )

    functions = snapshot_tree(source)

    assert "sample.py::restored.helper" in functions[
        "sample.py::restored"
    ].calls_resolved
    assert "helper" not in functions["sample.py::restored"].calls_unresolved
    assert functions["sample.py::before_definition"].calls_resolved == ()
    assert "helper" in functions[
        "sample.py::before_definition"
    ].calls_unresolved
    assert "sample.py::before_rebinding.helper" in functions[
        "sample.py::before_rebinding"
    ].calls_resolved
    assert "helper" not in functions[
        "sample.py::before_rebinding"
    ].calls_unresolved


def test_function_headers_observe_enclosing_execution_order(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
def earlier():
    return None

def sees_earlier(value=earlier()):
    return value

earlier = object()

def sees_future(value=future()):
    return value

def future():
    return None

def outer():
    def local_earlier():
        return None
    def sees_local_earlier(value=local_earlier()):
        return value
    def sees_local_future(value=local_future()):
        return value
    def local_future():
        return None
""",
    )

    functions = snapshot_tree(source)

    assert functions["sample.py::sees_earlier"].calls_resolved == (
        "sample.py::earlier",
    )
    assert functions["sample.py::sees_earlier"].calls_unresolved == ()
    assert functions["sample.py::sees_future"].calls_resolved == ()
    assert functions["sample.py::sees_future"].calls_unresolved == ("future",)
    assert functions[
        "sample.py::outer.sees_local_earlier"
    ].calls_resolved == ("sample.py::outer.local_earlier",)
    assert functions[
        "sample.py::outer.sees_local_earlier"
    ].calls_unresolved == ()
    assert functions[
        "sample.py::outer.sees_local_future"
    ].calls_resolved == ()
    assert functions[
        "sample.py::outer.sees_local_future"
    ].calls_unresolved == ("local_future",)


def test_class_headers_use_the_class_namespace_at_definition_time(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
def global_helper():
    return None

class EarlierMethod:
    def helper():
        return None
    def caller(value=helper()):
        return value

class LaterClassBinding:
    def caller(value=global_helper()):
        return value
    global_helper = object()

class EarlierClassBinding:
    global_helper = object()
    def caller(value=global_helper()):
        return value
""",
    )

    functions = snapshot_tree(source)

    assert functions[
        "sample.py::EarlierMethod.caller"
    ].calls_resolved == ("sample.py::EarlierMethod.helper",)
    assert functions[
        "sample.py::EarlierMethod.caller"
    ].calls_unresolved == ()
    assert functions[
        "sample.py::LaterClassBinding.caller"
    ].calls_resolved == ("sample.py::global_helper",)
    assert functions[
        "sample.py::LaterClassBinding.caller"
    ].calls_unresolved == ()
    assert functions[
        "sample.py::EarlierClassBinding.caller"
    ].calls_resolved == ()
    assert functions[
        "sample.py::EarlierClassBinding.caller"
    ].calls_unresolved == ("global_helper",)


def test_global_and_nonlocal_directives_select_the_correct_scope(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
def helper():
    return None

def outer():
    def helper():
        return 1

    def global_caller():
        global helper
        return helper()

    def nonlocal_caller():
        nonlocal helper
        return helper()

    def global_rebound():
        global helper
        helper = object()
        return helper()

    def nonlocal_rebound():
        nonlocal helper
        helper = object()
        return helper()
""",
    )

    functions = snapshot_tree(source)

    assert functions[
        "sample.py::outer.global_caller"
    ].calls_resolved == ("sample.py::helper",)
    assert functions[
        "sample.py::outer.global_caller"
    ].calls_unresolved == ()
    assert functions[
        "sample.py::outer.nonlocal_caller"
    ].calls_resolved == ("sample.py::outer.helper",)
    assert functions[
        "sample.py::outer.nonlocal_caller"
    ].calls_unresolved == ()
    assert "helper" in functions[
        "sample.py::outer.global_rebound"
    ].calls_unresolved
    assert "helper" in functions[
        "sample.py::outer.nonlocal_rebound"
    ].calls_unresolved


def test_executed_class_scope_propagates_global_and_nonlocal_writes(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
def module_helper():
    return None

def nested_global_helper():
    return None

class GlobalHeader:
    global module_helper
    def caller(value=module_helper()):
        return value

class GlobalRebind:
    global module_helper
    module_helper = object()

def module_caller():
    return module_helper()

def conditional_helper():
    return None

class ConditionalGlobal:
    global conditional_helper
    if flag:
        def conditional_helper():
            return 1

def conditional_caller():
    return conditional_helper()

def outer():
    def helper():
        return None

    class GlobalFromFunction:
        global nested_global_helper
        nested_global_helper = object()

    class NonlocalRebind:
        nonlocal helper
        helper = object()

    def global_child():
        global nested_global_helper
        return nested_global_helper()

    return helper()
""",
    )

    functions = snapshot_tree(source)

    assert functions[
        "sample.py::GlobalHeader.caller"
    ].calls_resolved == ("sample.py::module_helper",)
    assert functions[
        "sample.py::GlobalHeader.caller"
    ].calls_unresolved == ()
    assert functions["sample.py::module_caller"].calls_resolved == ()
    assert "module_helper" in functions[
        "sample.py::module_caller"
    ].calls_unresolved
    assert functions["sample.py::conditional_caller"].calls_resolved == ()
    assert "conditional_helper" in functions[
        "sample.py::conditional_caller"
    ].calls_unresolved
    assert "sample.py::outer.helper" not in functions[
        "sample.py::outer"
    ].calls_resolved
    assert "helper" in functions["sample.py::outer"].calls_unresolved
    assert functions[
        "sample.py::outer.global_child"
    ].calls_resolved == ()
    assert "nested_global_helper" in functions[
        "sample.py::outer.global_child"
    ].calls_unresolved


@pytest.mark.parametrize(
    "expression",
    [
        "[(helper := object()) for _ in (0,)]",
        "{(helper := object()) for _ in (0,)}",
        "{_: (helper := object()) for _ in (0,)}",
        "((helper := object()) for _ in (0,))",
    ],
)
def test_comprehension_walrus_invalidates_containing_scope_binding(
    tmp_path, expression
):
    source = tmp_path / "source"
    _write_module(
        source,
        f"""\
def work():
    def helper():
        return None
    {expression}
    return helper()
""",
    )

    function = snapshot_tree(source)["sample.py::work"]

    assert "sample.py::work.helper" not in function.calls_resolved
    assert "helper" in function.calls_unresolved


def test_comprehension_walrus_respects_nonlocal_scope(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
def outer():
    def helper():
        return None

    def child():
        nonlocal helper
        [(helper := object()) for _ in (0,)]
        return helper()
""",
    )

    child = snapshot_tree(source)["sample.py::outer.child"]

    assert "sample.py::outer.helper" not in child.calls_resolved
    assert "helper" in child.calls_unresolved


def test_semantic_scope_work_budget_fails_closed_deterministically(
    tmp_path, monkeypatch
):
    import tools.code_intelligence.semantic_service as service

    monkeypatch.setattr(service, "_MAX_SEMANTIC_WORK", 5_000)
    assignments = "\n".join(
        f"    local_{index} = {index}" for index in range(200)
    )
    branches = "\n".join(
        f"    if flag:\n        local_{index} = {index + 1}"
        for index in range(200)
    )
    source = tmp_path / "source"
    _write_module(
        source,
        f"def work(flag):\n{assignments}\n{branches}\n    return None\n",
    )

    with pytest.raises(ValueError, match="semantic analysis budget exceeded"):
        snapshot_tree(source)


def test_function_header_calls_follow_cpython_evaluation_order(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
def positional_helper():
    return None

def keyword_helper():
    return None

def ordered(
    value: (positional_helper := object()) = positional_helper(),
    *,
    option: (keyword_helper := object()) = keyword_helper(),
):
    return value
""",
    )

    function = snapshot_tree(source)["sample.py::ordered"]

    assert "sample.py::positional_helper" in function.calls_resolved
    assert "sample.py::keyword_helper" in function.calls_resolved
    assert "positional_helper" not in function.calls_unresolved
    assert "keyword_helper" not in function.calls_unresolved


def test_decorator_expressions_run_before_defaults_and_annotations(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
def helper():
    return None

def decorator(value):
    return value

@(helper := decorator)
def decorated(value: (helper := object()) = helper()):
    return value
""",
    )

    function = snapshot_tree(source)["sample.py::decorated"]

    assert "sample.py::helper" not in function.calls_resolved
    assert "helper" in function.calls_unresolved


def test_receiver_resolution_requires_a_real_direct_method(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
class Worker:
    def finish(self):
        return None

    @staticmethod
    def static(self):
        return self.finish()

    def outer(self):
        def shadowed(self):
            return self.finish()

        def captured():
            return self.finish()

        return captured()

    @classmethod
    def class_caller(cls):
        return cls.finish()
""",
    )

    functions = snapshot_tree(source)

    assert functions["sample.py::Worker.static"].calls_resolved == ()
    assert functions["sample.py::Worker.static"].calls_unresolved == (
        "self.finish",
    )
    assert functions[
        "sample.py::Worker.outer.shadowed"
    ].calls_resolved == ()
    assert functions[
        "sample.py::Worker.outer.shadowed"
    ].calls_unresolved == ("self.finish",)
    assert functions[
        "sample.py::Worker.outer.captured"
    ].calls_resolved == ("sample.py::Worker.finish",)
    assert functions[
        "sample.py::Worker.outer.captured"
    ].calls_unresolved == ()
    assert functions[
        "sample.py::Worker.class_caller"
    ].calls_resolved == ("sample.py::Worker.finish",)
    assert functions[
        "sample.py::Worker.class_caller"
    ].calls_unresolved == ()


def test_captured_receiver_keeps_its_owning_class_method_map(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
class Outer:
    def finish(self):
        return None

    def build(self):
        class Inner:
            def finish(self):
                return 1

            @staticmethod
            def captured():
                return self.finish()
""",
    )

    captured = snapshot_tree(source)[
        "sample.py::Outer.build.Inner.captured"
    ]

    assert captured.calls_resolved == ("sample.py::Outer.finish",)
    assert "sample.py::Outer.build.Inner.finish" not in captured.calls_resolved
    assert captured.calls_unresolved == ()


def test_nested_class_nonlocal_receiver_write_revokes_outer_receiver(
    tmp_path,
):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
class Worker:
    def finish(self):
        return None

    def direct(self):
        class Inner:
            nonlocal self
            self = object()
        return self.finish()

    def conditional(self, flag):
        class Inner:
            nonlocal self
            if flag:
                self = object()
        return self.finish()
""",
    )

    functions = snapshot_tree(source)

    for key in (
        "sample.py::Worker.direct",
        "sample.py::Worker.conditional",
    ):
        assert "sample.py::Worker.finish" not in functions[
            key
        ].calls_resolved
        assert "self.finish" in functions[key].calls_unresolved


def test_descriptor_semantics_follow_evaluated_decorator_bindings(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
class Aliased:
    sm = staticmethod
    cm = classmethod

    def finish(self):
        return None

    @sm
    def static(self):
        return self.finish()

    @cm
    def class_call(cls):
        return cls.finish()

class Shadowed:
    staticmethod = classmethod

    def finish(self):
        return None

    @staticmethod
    def class_call(cls):
        return cls.finish()

class Ambiguous:
    staticmethod = object()

    def finish(self):
        return None

    @staticmethod
    def maybe(self):
        return self.finish()
""",
    )

    functions = snapshot_tree(source)

    assert functions["sample.py::Aliased.static"].calls_resolved == ()
    assert functions["sample.py::Aliased.static"].calls_unresolved == (
        "self.finish",
    )
    assert functions[
        "sample.py::Aliased.class_call"
    ].calls_resolved == ("sample.py::Aliased.finish",)
    assert functions[
        "sample.py::Shadowed.class_call"
    ].calls_resolved == ("sample.py::Shadowed.finish",)
    assert functions["sample.py::Ambiguous.maybe"].calls_resolved == ()
    assert functions["sample.py::Ambiguous.maybe"].calls_unresolved == (
        "self.finish",
    )


def test_descriptor_composition_uses_ordered_cpython_transitions(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
def wrapper(value):
    return value

class Composed:
    sm = staticmethod
    cm = classmethod
    unknown = wrapper

    def finish(self):
        return None

    @cm
    @sm
    def class_over_static(value):
        return value.finish()

    @sm
    @cm
    def static_over_class(value):
        return value.finish()

    @cm
    @cm
    def repeated_class(cls):
        return cls.finish()

    @sm
    @sm
    def repeated_static(value):
        return value.finish()

    @cm
    @unknown
    def class_over_unknown(cls):
        return cls.finish()

    @unknown
    @cm
    def unknown_over_class(cls):
        return cls.finish()
""",
    )

    functions = snapshot_tree(source)

    for name, call in (
        ("class_over_static", "value.finish"),
        ("static_over_class", "value.finish"),
        ("repeated_static", "value.finish"),
        ("class_over_unknown", "cls.finish"),
        ("unknown_over_class", "cls.finish"),
    ):
        function = functions[f"sample.py::Composed.{name}"]
        assert function.calls_resolved == ()
        assert function.calls_unresolved == (call,)
    assert functions[
        "sample.py::Composed.repeated_class"
    ].calls_resolved == ("sample.py::Composed.finish",)
    assert functions[
        "sample.py::Composed.repeated_class"
    ].calls_unresolved == ()


def test_post_decoration_callability_controls_bound_call_targets(tmp_path):
    source = tmp_path / "source"
    _write_module(
        source,
        """\
def replace(function):
    return object()

@replace
def module_helper():
    return None

def module_caller():
    return module_helper()

def outer():
    @replace
    def nested_helper():
        return None
    return nested_helper()

class Worker:
    @replace
    def helper(self):
        return None

    @staticmethod
    @classmethod
    def broken(value):
        return value

    @classmethod
    @staticmethod
    def unbound(value):
        return value

    @staticmethod
    @staticmethod
    def repeated_static(value):
        return value

    def run(self):
        self.helper()
        self.broken()
        self.unbound()
        self.repeated_static()
""",
    )

    functions = snapshot_tree(source)

    assert functions["sample.py::module_caller"].calls_resolved == ()
    assert functions["sample.py::module_caller"].calls_unresolved == (
        "module_helper",
    )
    assert "sample.py::outer.nested_helper" not in functions[
        "sample.py::outer"
    ].calls_resolved
    assert "nested_helper" in functions[
        "sample.py::outer"
    ].calls_unresolved
    run = functions["sample.py::Worker.run"]
    assert "sample.py::Worker.helper" not in run.calls_resolved
    assert "sample.py::Worker.broken" not in run.calls_resolved
    assert "self.helper" in run.calls_unresolved
    assert "self.broken" in run.calls_unresolved
    assert "sample.py::Worker.unbound" in run.calls_resolved
    assert "sample.py::Worker.repeated_static" in run.calls_resolved
