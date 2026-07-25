"""Tests for portable repository discovery and tracked-tree snapshots."""

from __future__ import annotations

import os
import json
import importlib
import inspect
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from collections.abc import Callable

import pytest

from tools.code_intelligence.artifacts import (
    ArtifactDifference,
    artifact_hash,
    atomic_write_json,
    canonical_bytes,
    compare_artifact_dirs,
)
from tools.code_intelligence.paths import discover_repo_root, normalize_repo_path
from tools.code_intelligence.results import CheckResult, ResultState, exit_code
from tools.code_intelligence.schemas import (
    ArtifactEnvelope,
    SchemaError,
    migrate_artifact,
    validate_envelope,
    validate_projection,
)
from tools.code_intelligence.snapshot import (
    build_snapshot,
    main as snapshot_main,
    tracked_files,
)
import tools.code_intelligence.snapshot as snapshot_module


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_valid_artifact(path: Path, *, generated_at: str) -> None:
    atomic_write_json(
        path,
        {
            "schema_name": "example.artifact",
            "schema_version": 1,
            "source_sha": "a" * 64,
            "tool_version": "test",
            "input_hashes": {"tracked_tree": "1" * 64},
            "generated_at": generated_at,
        },
        lambda value: validate_envelope(value, "example.artifact"),
    )


@pytest.fixture
def valid_envelope() -> dict[str, object]:
    return {
        "schema_name": "call_graph",
        "schema_version": 1,
        "source_sha": "a" * 64,
        "tool_version": "1.0.0",
        "input_hashes": {"knowledge_graph": "b" * 64},
        "generated_at": "2026-07-23T00:00:00Z",
    }


@pytest.fixture
def valid_projection(valid_envelope: dict[str, object]) -> dict[str, object]:
    return {
        **valid_envelope,
        "nodes": [],
        "edges": [],
        "unresolved": [],
    }


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Snapshot Tests"],
        cwd=repository,
        check=True,
    )
    return repository


def run_module(module: str, *args: object, check: bool = False) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    return subprocess.run(
        [sys.executable, "-m", module, *map(str, args)],
        cwd=PROJECT_ROOT,
        check=check,
        capture_output=True,
        text=True,
        env=env,
    )


def test_unknown_is_nonzero_only_in_gate_mode() -> None:
    result = CheckResult("coverage", ResultState.UNKNOWN, "coverage absent", {})

    assert exit_code([result], gate=False) == 0
    assert exit_code([result], gate=True) != 0


def test_result_exit_code_blocks_failures_but_not_advisories() -> None:
    advisory = CheckResult("lint", ResultState.ADVISORY, "review", {})
    failures = [
        CheckResult("check", state, "failed", {})
        for state in (ResultState.FAIL, ResultState.TIMEOUT, ResultState.ERROR)
    ]

    assert exit_code([advisory], gate=False) == 0
    assert exit_code([advisory], gate=True) == 0
    assert exit_code(failures, gate=False) != 0


def test_canonical_bytes_omit_generation_time_without_changing_stored_payload() -> None:
    value = {"z": "é", "generated_at": "later", "a": [2, 1]}

    assert canonical_bytes(value) == b'{"a":[2,1],"z":"\xc3\xa9"}\n'
    assert canonical_bytes(value, omit_keys=frozenset()) == (
        b'{"a":[2,1],"generated_at":"later","z":"\xc3\xa9"}\n'
    )
    assert artifact_hash(value) == artifact_hash({**value, "generated_at": "earlier"})


def test_atomic_write_preserves_previous_artifact_on_validation_failure(tmp_path: Path) -> None:
    target = tmp_path / "artifact.json"
    target.write_text('{"old":true}\n', encoding="utf-8")

    with pytest.raises(SchemaError):
        atomic_write_json(target, {"schema": 99}, validate_envelope)

    assert target.read_text(encoding="utf-8") == '{"old":true}\n'
    assert list(tmp_path.glob(".artifact.json.*.tmp")) == []


def test_atomic_write_cleans_temporary_file_when_replacement_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "artifact.json"
    write_valid_artifact(target, generated_at="2026-07-23T00:00:00Z")
    original = target.read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("injected replacement failure")

    import tools.code_intelligence.artifacts as artifacts_module

    monkeypatch.setattr(artifacts_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replacement failure"):
        write_valid_artifact(target, generated_at="2026-07-23T00:01:00Z")

    assert target.read_bytes() == original
    assert list(tmp_path.glob(".artifact.json.*.tmp")) == []


def test_artifact_compare_ignores_only_generation_time(tmp_path: Path) -> None:
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    write_valid_artifact(left / "A.json", generated_at="2026-07-23T00:00:00Z")
    write_valid_artifact(right / "A.json", generated_at="2026-07-23T00:01:00Z")

    assert compare_artifact_dirs(left, right) == ()
    assert compare_artifact_dirs(left, right, ignore_generation_time=False) == (
        ArtifactDifference("stale", "A.json"),
    )
    value = json.loads((right / "A.json").read_text(encoding="utf-8"))
    value["source_sha"] = "f" * 64
    atomic_write_json(right / "A.json", value, validate_envelope)

    assert compare_artifact_dirs(left, right)[0].state == "stale"


def test_artifact_compare_reports_sorted_member_and_json_differences(tmp_path: Path) -> None:
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    write_valid_artifact(left / "missing.json", generated_at="2026-07-23T00:00:00Z")
    write_valid_artifact(right / "unexpected.json", generated_at="2026-07-23T00:00:00Z")
    (right / "broken.json").write_text("not json", encoding="utf-8")

    differences = compare_artifact_dirs(left, right)

    assert differences == (
        ArtifactDifference("malformed", "broken.json"),
        ArtifactDifference("missing", "missing.json"),
        ArtifactDifference("unexpected", "unexpected.json"),
    )


def test_artifact_compare_accepts_any_valid_json_value(tmp_path: Path) -> None:
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "null.json").write_text("null\n", encoding="utf-8")
    (right / "null.json").write_text("null\n", encoding="utf-8")

    assert compare_artifact_dirs(left, right) == ()


def test_artifact_compare_treats_non_json_members_as_malformed(tmp_path: Path) -> None:
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (right / "notes.txt").write_text("not an artifact", encoding="utf-8")

    assert compare_artifact_dirs(left, right) == (
        ArtifactDifference("malformed", "notes.txt"),
    )


def test_artifact_compare_cli_returns_nonzero_and_prints_differences(tmp_path: Path) -> None:
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    write_valid_artifact(left / "A.json", generated_at="2026-07-23T00:00:00Z")
    write_valid_artifact(right / "A.json", generated_at="2026-07-23T00:01:00Z")

    matching = run_module(
        "tools.code_intelligence.artifacts",
        "compare",
        "--left", left,
        "--right", right,
        "--ignore-generation-time",
    )
    assert matching.returncode == 0
    assert matching.stdout == ""

    (right / "A.json").write_text("not json", encoding="utf-8")
    different = run_module(
        "tools.code_intelligence.artifacts",
        "compare",
        "--left", left,
        "--right", right,
        "--ignore-generation-time",
    )
    assert different.returncode == 1
    assert different.stdout == "malformed A.json\n"


def test_snapshot_hash_changes_with_tracked_dirty_bytes(git_repo: Path) -> None:
    target = git_repo / "bulk_downloader" / "sample.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)

    first = build_snapshot(git_repo)
    target.write_text("VALUE = 2\n", encoding="utf-8")
    second = build_snapshot(git_repo)

    assert first.source_sha != second.source_sha
    assert second.files[0].path == "bulk_downloader/sample.py"


def test_normalize_repo_path_rejects_escape(git_repo: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside repository"):
        normalize_repo_path(git_repo, tmp_path / "escape.py")


def test_tracked_files_are_sorted_and_exclude_ignored_files(git_repo: Path) -> None:
    (git_repo / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (git_repo / "z.py").write_text("Z = 1\n", encoding="utf-8")
    (git_repo / "a.py").write_text("A = 1\n", encoding="utf-8")
    (git_repo / "ignored.py").write_text("IGNORED = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)

    assert tracked_files(git_repo) == (".gitignore", "a.py", "z.py")


def test_discover_repo_root_from_subdirectory(git_repo: Path) -> None:
    nested = git_repo / "bulk_downloader" / "nested"
    nested.mkdir(parents=True)

    assert discover_repo_root(nested) == git_repo.resolve()


def test_snapshot_rejects_tracked_symlink_escaping_repository(git_repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("OUTSIDE = 1\n", encoding="utf-8")
    link = git_repo / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")
    subprocess.run(["git", "add", "linked.py"], cwd=git_repo, check=True)

    with pytest.raises(ValueError, match="outside repository"):
        build_snapshot(git_repo)


def test_snapshot_cli_check_detects_dirty_tracked_tree(git_repo: Path, tmp_path: Path) -> None:
    target = git_repo / "bulk_downloader" / "sample.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    output = tmp_path / "snapshot.json"

    run_module(
        "tools.code_intelligence.snapshot",
        "--root",
        git_repo,
        "--scope",
        "tracked",
        "--out",
        output,
        check=True,
    )
    target.write_text("VALUE = 3\n", encoding="utf-8")
    checked = run_module(
        "tools.code_intelligence.snapshot",
        "--root",
        git_repo,
        "--scope",
        "tracked",
        "--check",
        output,
    )

    assert checked.returncode != 0
    assert "source SHA differs" in checked.stdout


def test_snapshot_cli_out_is_atomic_when_replace_fails(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = git_repo / "sample.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "sample.py"], cwd=git_repo, check=True)
    output = tmp_path / "snapshot.json"
    original = b"existing snapshot bytes\n"
    output.write_bytes(original)

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)

    result = snapshot_main(["--root", str(git_repo), "--scope", "tracked", "--out", str(output)])

    assert result == 1
    assert output.read_bytes() == original
    assert not list(tmp_path.glob(".snapshot.json.*.tmp"))


def test_snapshot_cli_emits_full_task_one_envelope(git_repo: Path, tmp_path: Path) -> None:
    target = git_repo / "sample.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "sample.py"], cwd=git_repo, check=True)
    output = tmp_path / "snapshot.json"

    assert snapshot_main(["--root", str(git_repo), "--scope", "tracked", "--out", str(output)]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_name"] == "code_intelligence.tree_snapshot"
    assert payload["schema_version"] == 1
    assert payload["source_sha"] == build_snapshot(git_repo).source_sha
    assert payload["tool_version"] == "1"
    assert payload["input_hashes"] == {"tracked_tree": payload["source_sha"]}
    assert isinstance(payload["generated_at"], str)
    assert payload["scope"] == "tracked"
    assert payload["files"] == [{
        "path": "sample.py",
        "sha256": build_snapshot(git_repo).files[0].sha256,
        "size": len(b"VALUE = 1\n"),
        "lines": 1,
    }]


def test_snapshot_cli_check_ignores_generated_timestamp(git_repo: Path, tmp_path: Path) -> None:
    target = git_repo / "sample.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "sample.py"], cwd=git_repo, check=True)
    output = tmp_path / "snapshot.json"
    assert snapshot_main(["--root", str(git_repo), "--scope", "tracked", "--out", str(output)]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["generated_at"] = "2000-01-01T00:00:00Z"
    output.write_text(json.dumps(payload), encoding="utf-8")

    assert snapshot_main(["--root", str(git_repo), "--scope", "tracked", "--check", str(output)]) == 0


def test_snapshot_supports_tracked_non_utf8_filename(git_repo: Path) -> None:
    filename = b"non_utf8_\xff.py"
    target = os.fsencode(git_repo) + b"/" + filename
    with open(target, "wb") as source:
        source.write(b"VALUE = 1\n")
    subprocess.run([b"git", b"add", filename], cwd=os.fsencode(git_repo), check=True)

    snapshot = build_snapshot(git_repo)

    assert snapshot.files[0].path == os.fsdecode(filename)


@pytest.mark.skipif(os.name != "posix", reason="literal backslash filenames are POSIX-specific")
def test_snapshot_preserves_literal_backslash_in_tracked_filename(git_repo: Path) -> None:
    filename = "literal\\backslash.py"
    (git_repo / filename).write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=git_repo, check=True)

    snapshot = build_snapshot(git_repo)

    assert snapshot.files[0].path == filename


def test_make_envelope_has_the_task_one_public_signature_and_shape() -> None:
    schemas = importlib.import_module("tools.code_intelligence.schemas")
    make_envelope = schemas.make_envelope

    assert list(inspect.signature(make_envelope).parameters) == [
        "schema_name",
        "schema_version",
        "source_sha",
        "tool_version",
        "input_hashes",
    ]
    envelope = make_envelope(
        "example.snapshot",
        7,
        "source-sha",
        "test-tool",
        {"tracked_tree": "input-sha"},
    )

    assert set(envelope) == {
        "schema_name",
        "schema_version",
        "source_sha",
        "tool_version",
        "input_hashes",
        "generated_at",
    }
    assert envelope["schema_name"] == "example.snapshot"
    assert envelope["schema_version"] == 7
    assert envelope["source_sha"] == "source-sha"
    assert envelope["tool_version"] == "test-tool"
    assert envelope["input_hashes"] == {"tracked_tree": "input-sha"}
    assert datetime.fromisoformat(envelope["generated_at"].replace("Z", "+00:00")).tzinfo


def test_artifact_envelope_declares_the_public_metadata_contract() -> None:
    assert tuple(ArtifactEnvelope.__annotations__) == (
        "schema_name",
        "schema_version",
        "source_sha",
        "tool_version",
        "input_hashes",
        "generated_at",
    )


@pytest.mark.parametrize("mutator,message", [
    (lambda value: value.pop("source_sha"), "source_sha"),
    (lambda value: value.__setitem__("schema_version", 2), "unsupported"),
    (lambda value: value.__setitem__("source_sha", "xyz"), "64 lowercase hex"),
])
def test_envelope_rejects_invalid_metadata(
    valid_envelope: dict[str, object],
    mutator: Callable[[dict[str, object]], object],
    message: str,
) -> None:
    mutator(valid_envelope)

    with pytest.raises(SchemaError, match=message):
        validate_envelope(valid_envelope, "call_graph")


@pytest.mark.parametrize(
    ("field", "invalid", "message"),
    [
        ("schema_name", 1, "schema_name"),
        ("schema_version", True, "schema_version"),
        ("tool_version", "", "tool_version"),
        ("input_hashes", [], "input_hashes"),
        ("generated_at", "not-a-timestamp", "generated_at"),
    ],
)
def test_envelope_rejects_invalid_field_types(
    valid_envelope: dict[str, object],
    field: str,
    invalid: object,
    message: str,
) -> None:
    valid_envelope[field] = invalid

    with pytest.raises(SchemaError, match=message):
        validate_envelope(valid_envelope)


def test_envelope_rejects_invalid_input_hash_without_echoing_it(
    valid_envelope: dict[str, object],
) -> None:
    invalid_hash = "Bearer do-not-echo"
    valid_envelope["input_hashes"] = {"knowledge_graph": invalid_hash}

    with pytest.raises(SchemaError, match="input_hashes") as raised:
        validate_envelope(valid_envelope)

    assert invalid_hash not in str(raised.value)


@pytest.mark.parametrize("supported_version", [True, 1.0])
def test_envelope_supported_version_must_be_exact_integer(
    valid_envelope: dict[str, object],
    supported_version: object,
) -> None:
    with pytest.raises(SchemaError, match="supported_version"):
        validate_envelope(
            valid_envelope,
            supported_version=supported_version,
        )


def test_projection_rejects_secret_values(
    valid_projection: dict[str, object],
) -> None:
    secret = "Bearer abcdef"
    valid_projection["nested"] = {"authorization": secret}

    with pytest.raises(SchemaError, match="secret-like") as raised:
        validate_projection("call_graph", valid_projection)

    assert secret not in str(raised.value)


@pytest.mark.parametrize("allowed", [True, False, "redacted", "[REDACTED]"])
def test_projection_allows_non_secret_markers_on_sensitive_keys(
    valid_projection: dict[str, object],
    allowed: object,
) -> None:
    valid_projection["credential"] = allowed

    validate_projection("call_graph", valid_projection)


@pytest.mark.parametrize(
    ("name", "payload_key", "payload"),
    [
        ("call_graph", "nodes", []),
        ("module_catalog", "modules", {}),
        ("security_surface", "auth_gates", []),
        ("error_catalog", "handlers", []),
        ("taint_map", "sources", []),
        ("dead_code", "uncalled", []),
        ("config_lineage", "settings", {}),
        ("concurrency_map", "shared_state", []),
        ("metrics_catalog", "metrics", []),
    ],
)
def test_projection_schemas_require_exact_payload_types(
    valid_envelope: dict[str, object],
    name: str,
    payload_key: str,
    payload: object,
) -> None:
    required_payloads = {
        "call_graph": {"nodes": [], "edges": [], "unresolved": []},
        "module_catalog": {"modules": {}},
        "security_surface": {
            "auth_gates": [],
            "secret_sites": [],
            "sql_sites": [],
            "subprocess_sites": [],
            "path_sinks": [],
            "totals": {},
        },
        "error_catalog": {"handlers": []},
        "taint_map": {"sources": [], "sinks": [], "paths": []},
        "dead_code": {"uncalled": [], "uncalled_total": 0, "unreachable_routes": []},
        "config_lineage": {"settings": {}},
        "concurrency_map": {"shared_state": [], "locks": [], "operations": []},
        "metrics_catalog": {"metrics": []},
    }
    projection = {
        **valid_envelope,
        "schema_name": name,
        **required_payloads[name],
    }
    validate_projection(name, projection)
    projection[payload_key] = {} if isinstance(payload, list) else []

    with pytest.raises(SchemaError, match=payload_key):
        validate_projection(name, projection)


def test_projection_rejects_unknown_schema(valid_envelope: dict[str, object]) -> None:
    with pytest.raises(SchemaError, match="unknown projection"):
        validate_projection("future_projection", valid_envelope)


@pytest.fixture
def legacy_contracts() -> dict[str, object]:
    return {
        "contracts": {
            "C0001": {
                "producer": "legacy",
                "allowed_divergences": ["missing optional label"],
            }
        },
        "_meta": {
            "generated": "2026-07-14T22:43:30+00:00",
            "version_context": "v3.66.754",
        },
        "extension": {"retain": True},
    }


def test_migration_preserves_unknown_contract_payload_fields(
    legacy_contracts: dict[str, object],
) -> None:
    migrated = migrate_artifact("contracts", legacy_contracts)

    contract = migrated["contracts"]["C0001"]
    assert contract["allowed_divergences"] == ["missing optional label"]
    assert migrated["extension"] == {"retain": True}
    validate_envelope(migrated, "contracts")


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("invariants", {"schema": 1, "invariants": {}, "_meta": {"generated": "now"}}),
        ("coverage_gaps", {"gaps": [], "total": 0, "_meta": {"generated": "now"}}),
    ],
)
def test_registered_legacy_migrations_preserve_payload(
    name: str,
    payload: dict[str, object],
) -> None:
    migrated = migrate_artifact(name, payload)

    for key, value in payload.items():
        if key not in {"schema", "_meta"}:
            assert migrated[key] == value
    validate_envelope(migrated, name)


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": 2, "_meta": {}},
        {"schema_version": 2},
        {"_meta": {"schema_version": 2}},
        {"payload": "unversioned"},
    ],
)
def test_migration_rejects_unregistered_or_future_versions(
    payload: dict[str, object],
) -> None:
    with pytest.raises(SchemaError, match="no migration path"):
        migrate_artifact("contracts", payload)


def test_migration_rejects_unknown_kind(legacy_contracts: dict[str, object]) -> None:
    with pytest.raises(SchemaError, match="no migration path"):
        migrate_artifact("unknown", legacy_contracts)


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": 1, "schema_version": 2, "_meta": {}},
        {"schema": 1, "_meta": {"schema_version": 2}},
        {"schema_version": 1, "_meta": {"schema": 2}},
    ],
)
def test_migration_identity_version_conflicts_fail_closed(
    payload: dict[str, object],
) -> None:
    with pytest.raises(SchemaError, match="^no migration path$"):
        migrate_artifact("contracts", payload)


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        (
            "contracts",
            {
                "schema": 1,
                "schema_name": "contracts",
                "_meta": {"schema_name": "invariants"},
            },
        ),
        (
            "contracts",
            {
                "schema": 1,
                "schema_name": "invariants",
                "_meta": {},
            },
        ),
    ],
)
def test_migration_identity_name_conflicts_and_kind_mismatch_fail_closed(
    kind: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(SchemaError, match="^no migration path$"):
        migrate_artifact(kind, payload)


def test_migration_accepts_consistent_top_level_only_legacy_v1() -> None:
    migrated = migrate_artifact(
        "contracts",
        {
            "schema": 1,
            "schema_version": 1,
            "schema_name": "contracts",
            "contracts": {"C0001": {"unknown_field": "retained"}},
        },
    )

    assert migrated["contracts"]["C0001"]["unknown_field"] == "retained"
    assert migrated["source_binding"] == "unbound_legacy"
    validate_envelope(migrated, "contracts")


@pytest.mark.parametrize("target_version", [True, 1.0])
def test_migration_target_version_must_be_exact_integer(
    legacy_contracts: dict[str, object],
    target_version: object,
) -> None:
    with pytest.raises(SchemaError, match="^no migration path$"):
        migrate_artifact(
            "contracts",
            legacy_contracts,
            target_version=target_version,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": 1, "source_sha": "malformed", "_meta": {}},
        {"schema": 1, "_meta": {"source_sha": "malformed"}},
    ],
)
def test_migration_source_identity_rejects_malformed_explicit_sha(
    payload: dict[str, object],
) -> None:
    with pytest.raises(SchemaError, match="source metadata"):
        migrate_artifact("contracts", payload)


def test_migration_source_identity_rejects_conflicting_explicit_shas() -> None:
    with pytest.raises(SchemaError, match="source metadata"):
        migrate_artifact(
            "contracts",
            {
                "schema": 1,
                "source_sha": "a" * 64,
                "_meta": {"source_sha": "b" * 64},
            },
        )


def test_migration_marks_genuinely_absent_source_as_unbound(
    legacy_contracts: dict[str, object],
) -> None:
    migrated = migrate_artifact("contracts", legacy_contracts)

    assert migrated["source_sha"] == "0" * 64
    assert migrated["source_binding"] == "unbound_legacy"
    validate_envelope(migrated, "contracts")


def test_migration_accepts_repeated_consistent_identity_declarations() -> None:
    migrated = migrate_artifact(
        "contracts",
        {
            "schema": 1,
            "schema_version": 1,
            "schema_name": "contracts",
            "source_sha": "0" * 64,
            "source_binding": "unbound_legacy",
            "_meta": {
                "schema": 1,
                "schema_version": 1,
                "schema_name": "contracts",
                "source_sha": "0" * 64,
                "source_binding": "unbound_legacy",
            },
        },
    )

    assert migrated["source_sha"] == "0" * 64
    assert migrated["source_binding"] == "unbound_legacy"
    validate_envelope(migrated, "contracts")


def test_envelope_rejects_zero_sha_without_unbound_marker(
    valid_envelope: dict[str, object],
) -> None:
    valid_envelope["source_sha"] = "0" * 64

    with pytest.raises(SchemaError, match="source_binding"):
        validate_envelope(valid_envelope)


def test_envelope_rejects_unbound_marker_with_nonzero_sha(
    valid_envelope: dict[str, object],
) -> None:
    valid_envelope["source_binding"] = "unbound_legacy"

    with pytest.raises(SchemaError, match="source_binding"):
        validate_envelope(valid_envelope)


@pytest.mark.parametrize(
    ("key", "secret"),
    [
        ("api_token", "api-secret-value"),
        ("session_cookie", "cookie-secret-value"),
        ("privateKey", "private-key-value"),
        ("credentials", "credential-value"),
        ("otp", 123456),
    ],
)
def test_projection_rejects_semantic_secret_keys_without_echoing_values(
    valid_projection: dict[str, object],
    key: str,
    secret: object,
) -> None:
    valid_projection["nested"] = {key: secret}

    with pytest.raises(SchemaError, match="secret-like") as raised:
        validate_projection("call_graph", valid_projection)

    assert str(secret) not in str(raised.value)


@pytest.mark.parametrize(
    ("location", "key", "secret"),
    [
        ("top", "secrets", "Bearer misplaced-top"),
        ("top", "secret_sites", ["PASSWORD"]),
        ("nested", "secrets", "Bearer misplaced-nested"),
        ("nested", "secret_sites", ["API_TOKEN"]),
    ],
)
def test_projection_rejects_misplaced_structural_secret_keys(
    valid_projection: dict[str, object],
    location: str,
    key: str,
    secret: object,
) -> None:
    target = valid_projection
    if location == "nested":
        valid_projection["nested"] = {}
        target = valid_projection["nested"]
    target[key] = secret

    with pytest.raises(SchemaError, match="secret-like") as raised:
        validate_projection("call_graph", valid_projection)
    assert str(secret) not in str(raised.value)


def test_security_surface_secret_sites_shape_and_children_are_validated(
    valid_envelope: dict[str, object],
) -> None:
    projection = {
        **valid_envelope,
        "schema_name": "security_surface",
        "auth_gates": [],
        "secret_sites": [{"field": "API_TOKEN"}],
        "sql_sites": [],
        "subprocess_sites": [],
        "path_sinks": [],
        "totals": {},
    }
    validate_projection("security_surface", projection)

    secret = "nested-secret-value"
    projection["secret_sites"].append({"api_token": secret})
    with pytest.raises(SchemaError, match="secret-like") as raised:
        validate_projection("security_surface", projection)
    assert secret not in str(raised.value)


def test_security_surface_rejects_scalar_secret_sites_without_echoing_value(
    valid_envelope: dict[str, object],
) -> None:
    secret = "Bearer wrong-container"
    projection = {
        **valid_envelope,
        "schema_name": "security_surface",
        "auth_gates": [],
        "secret_sites": secret,
        "sql_sites": [],
        "subprocess_sites": [],
        "path_sinks": [],
        "totals": {},
    }

    with pytest.raises(SchemaError, match="secret_sites") as raised:
        validate_projection("security_surface", projection)
    assert secret not in str(raised.value)


@pytest.mark.parametrize(
    "secret_site",
    ["Bearer wrong-entry", 123456],
)
def test_security_surface_rejects_non_mapping_secret_site_entries(
    valid_envelope: dict[str, object],
    secret_site: object,
) -> None:
    projection = {
        **valid_envelope,
        "schema_name": "security_surface",
        "auth_gates": [],
        "secret_sites": [secret_site],
        "sql_sites": [],
        "subprocess_sites": [],
        "path_sinks": [],
        "totals": {},
    }

    with pytest.raises(SchemaError, match="secret-like") as raised:
        validate_projection("security_surface", projection)
    assert str(secret_site) not in str(raised.value)


@pytest.mark.parametrize(
    "secret_value",
    [
        "Bearer module-secret",
        ["Bearer module-secret"],
        [123456],
        {"field": "PASSWORD"},
    ],
)
def test_module_catalog_rejects_invalid_secret_inventory_values(
    valid_envelope: dict[str, object],
    secret_value: object,
) -> None:
    projection = {
        **valid_envelope,
        "schema_name": "module_catalog",
        "modules": {
            "bulk_downloader/sample.py": {
                "secrets": secret_value,
            }
        },
    }

    with pytest.raises(SchemaError, match="secret-like") as raised:
        validate_projection("module_catalog", projection)
    assert str(secret_value) not in str(raised.value)


def test_module_catalog_allows_safe_secret_field_identifiers(
    valid_envelope: dict[str, object],
) -> None:
    projection = {
        **valid_envelope,
        "schema_name": "module_catalog",
        "modules": {
            "bulk_downloader/sample.py": {
                "secrets": ["PASSWORD", "accounts.password", "api_token"],
            }
        },
    }

    validate_projection("module_catalog", projection)


def test_projection_allows_numeric_metadata_only_for_count_style_keys(
    valid_projection: dict[str, object],
) -> None:
    valid_projection["password_count"] = 4
    valid_projection["token_total"] = 5
    valid_projection["cookie_size"] = 6

    validate_projection("call_graph", valid_projection)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity", "1e9999"])
def test_schema_cli_rejects_nonfinite_json_without_touching_output(
    tmp_path: Path,
    constant: str,
) -> None:
    source = tmp_path / "legacy.json"
    output = tmp_path / "normalized.json"
    original = b'{"preserved":true}\n'
    source.write_text(
        '{"schema":1,"_meta":{},"measurement":' + constant + "}",
        encoding="utf-8",
    )
    output.write_bytes(original)

    result = run_module(
        "tools.code_intelligence.schemas",
        "migrate",
        "--kind",
        "contracts",
        "--input",
        source,
        "--out",
        output,
    )

    assert result.returncode != 0
    assert "non-finite JSON number" in result.stderr
    assert "Traceback" not in result.stderr
    assert output.read_bytes() == original


def test_schema_cli_validates_and_migrates_atomically(
    tmp_path: Path,
    legacy_contracts: dict[str, object],
) -> None:
    source = tmp_path / "legacy.json"
    migrated = tmp_path / "migrated.json"
    source.write_text(json.dumps(legacy_contracts), encoding="utf-8")

    migration = run_module(
        "tools.code_intelligence.schemas",
        "migrate",
        "--kind",
        "contracts",
        "--input",
        source,
        "--out",
        migrated,
    )
    validation = run_module(
        "tools.code_intelligence.schemas",
        "validate",
        "--kind",
        "contracts",
        "--file",
        migrated,
    )

    assert migration.returncode == 0, migration.stderr
    assert validation.returncode == 0, validation.stderr
    assert json.loads(migrated.read_text(encoding="utf-8"))["extension"] == {
        "retain": True
    }


def test_snapshot_serialization_uses_shared_envelope(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schemas = importlib.import_module("tools.code_intelligence.schemas")
    assert snapshot_module.make_envelope is schemas.make_envelope
    (git_repo / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "sample.py"], cwd=git_repo, check=True)
    output = tmp_path / "snapshot.json"
    calls: list[tuple[object, ...]] = []

    def recorded_envelope(*args: object) -> dict[str, object]:
        calls.append(args)
        return {
            "schema_name": "code_intelligence.tree_snapshot",
            "schema_version": 1,
            "source_sha": str(args[2]),
            "tool_version": "1",
            "input_hashes": dict(args[4]),
            "generated_at": "2000-01-01T00:00:00Z",
        }

    monkeypatch.setattr(snapshot_module, "make_envelope", recorded_envelope)

    assert snapshot_main(["--root", str(git_repo), "--scope", "tracked", "--out", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert len(calls) == 1
    assert payload["scope"] == "tracked"
    assert payload["files"][0]["path"] == "sample.py"


@pytest.mark.skipif(os.name != "posix", reason="literal backslash filenames are POSIX-specific")
def test_production_scope_preserves_literal_backslash_filename(git_repo: Path, tmp_path: Path) -> None:
    filename = "bulk_downloader/literal\\name.py"
    target = git_repo / filename
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=git_repo, check=True)
    output = tmp_path / "snapshot.json"

    assert snapshot_main(["--root", str(git_repo), "--scope", "production", "--out", str(output)]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert [fact["path"] for fact in payload["files"]] == [filename]
