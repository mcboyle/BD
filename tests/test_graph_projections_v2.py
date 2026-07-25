"""Regression coverage for provenance-complete graph projections."""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from tools import graph_build, l0_extract
from tools.code_intelligence.schemas import (
    SchemaError,
    make_envelope,
    validate_projection,
)


ROOT = Path(__file__).resolve().parent.parent
SOURCE_SHA = "a" * 64
INPUT_HASHES = {
    "bulk_downloader/a.py": "b" * 64,
    "bulk_downloader/b.py": "c" * 64,
}


def test_schema_document_matches_implemented_projection_set():
    text = (
        ROOT / "project-knowledge" / "CODE_INTELLIGENCE_SCHEMAS.md"
    ).read_text(encoding="utf-8")

    for name in graph_build.PROJECTION_FILENAMES:
        assert f"`{name}`" in text
    assert "`schema_name`" in text
    assert "`source_sha`" in text
    assert "verified-against: v3.66.817" in text


class GraphFixture(dict[str, dict[str, object]]):
    """Loaded graph projections plus the source identity used to build them."""

    source_sha = SOURCE_SHA


@dataclass(frozen=True)
class GraphCliFixture:
    """One durable projection set exercised only through the public CLI."""

    root: Path
    database: Path
    outdir: Path
    temp_parent: Path

    def run(
        self,
        *arguments: str,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "TMPDIR": str(self.temp_parent),
            **(environment or {}),
        }
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "graph_build.py"),
                "--db",
                str(self.database),
                "--outdir",
                str(self.outdir),
                *arguments,
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )


def _node(
    node_id: str,
    kind: str,
    path: str,
    qualname: str,
    meta: dict[str, object],
) -> tuple[object, ...]:
    return (
        node_id,
        kind,
        path,
        qualname,
        "[1, 4]" if kind == "function" else "",
        INPUT_HASHES[path],
        4,
        json.dumps(meta, sort_keys=True),
    )


def _write_schema_2_graph(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE nodes(
                id TEXT PRIMARY KEY,
                kind TEXT,
                path TEXT,
                qualname TEXT,
                span TEXT,
                sha256 TEXT,
                lines INTEGER,
                meta_json TEXT
            );
            CREATE TABLE edges(
                src TEXT,
                dst TEXT,
                kind TEXT,
                meta_json TEXT
            );
            CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
            """
        )
        connection.executemany(
            "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
            [
                _node(
                    "bulk_downloader/a.py",
                    "module",
                    "bulk_downloader/a.py",
                    "bulk_downloader/a.py",
                    {"imports": ["bulk_downloader/b.py"]},
                ),
                _node(
                    "bulk_downloader/b.py",
                    "module",
                    "bulk_downloader/b.py",
                    "bulk_downloader/b.py",
                    {"imports": []},
                ),
                _node(
                    "bulk_downloader/a.py::caller",
                    "function",
                    "bulk_downloader/a.py",
                    "caller",
                    {
                        "args": [],
                        "auth_calls": [
                            {
                                "name": "authorize_request",
                                "at": 1,
                                "method": "name_substring",
                                "confidence": 0.6,
                            }
                        ],
                        "calls": [{"name": "dynamic_target", "at": 2}],
                        "decorators": ["login_required"],
                        "raises": ["ValueError"],
                        "secrets": ["API_TOKEN"],
                        "sinks": [
                            {
                                "kind": "sql_fstring",
                                "at": 2,
                                "method": "name_substring",
                                "confidence": 0.6,
                            },
                            {
                                "kind": "subprocess",
                                "at": 3,
                                "shell": True,
                                "method": "name_substring",
                                "confidence": 0.6,
                            },
                            {
                                "kind": "path",
                                "at": 4,
                                "method": "name_substring",
                                "confidence": 0.6,
                            },
                        ],
                    },
                ),
                _node(
                    "bulk_downloader/a.py::sample",
                    "function",
                    "bulk_downloader/a.py",
                    "sample",
                    {
                        "args": [],
                        "calls": [],
                        "config_reads": [{"key": "LIMIT", "at": 1}],
                        "config_writes": [{"key": "LIMIT", "at": 2}],
                        "concurrency_ops": [
                            {
                                "kind": "lock",
                                "name": "state_lock",
                                "operation": "context",
                                "at": 3,
                                "method": "name_substring",
                                "confidence": 0.6,
                            }
                        ],
                        "metric_emits": [
                            {
                                "name": "sample.calls",
                                "operation": "increment",
                                "at": 4,
                                "method": "name_substring",
                                "confidence": 0.6,
                            }
                        ],
                    },
                ),
                _node(
                    "bulk_downloader/b.py::target",
                    "function",
                    "bulk_downloader/b.py",
                    "target",
                    {"args": [], "calls": []},
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO edges VALUES(?,?,?,?)",
            [
                (
                    "bulk_downloader/a.py",
                    "bulk_downloader/a.py::caller",
                    "contains",
                    "{}",
                ),
                (
                    "bulk_downloader/a.py",
                    "bulk_downloader/a.py::sample",
                    "contains",
                    "{}",
                ),
                (
                    "bulk_downloader/b.py",
                    "bulk_downloader/b.py::target",
                    "contains",
                    "{}",
                ),
                (
                    "bulk_downloader/a.py",
                    "bulk_downloader/b.py",
                    "imports",
                    "{}",
                ),
                (
                    "bulk_downloader/a.py::caller",
                    "dynamic_target",
                    "call",
                    "{}",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO meta VALUES(?,?)",
            [
                ("schema", "2"),
                ("schema_name", "knowledge_graph"),
                ("schema_version", "2"),
                ("source_sha", SOURCE_SHA),
                ("input_hashes", json.dumps(INPUT_HASHES, sort_keys=True)),
            ],
        )


@pytest.fixture
def graph_fixture(tmp_path: Path) -> GraphFixture:
    database = tmp_path / "KNOWLEDGE_GRAPH.db"
    outdir = tmp_path / "projections"
    _write_schema_2_graph(database)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "graph_build.py"),
            "--db",
            str(database),
            "--outdir",
            str(outdir),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return GraphFixture(
        {
            artifact.name: json.loads(artifact.read_text(encoding="utf-8"))
            for artifact in sorted(outdir.glob("*.json"))
        }
    )


@pytest.fixture
def graph_cli_fixture(tmp_path: Path) -> GraphCliFixture:
    root = tmp_path / "repo"
    database = root / "artifacts" / "KNOWLEDGE_GRAPH.db"
    outdir = root / "projections"
    temp_parent = tmp_path / "temporary"
    database.parent.mkdir(parents=True)
    temp_parent.mkdir()
    _write_schema_2_graph(database)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "graph_build.py"),
            "--db",
            str(database),
            "--outdir",
            str(outdir),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return GraphCliFixture(root, database, outdir, temp_parent)


def _directory_bytes(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _graph_check_temporaries(parent: Path) -> list[Path]:
    return sorted(parent.glob("bd-graph-check-*"))


def _graph_check_temporaries_recursive(parent: Path) -> list[Path]:
    return sorted(parent.rglob("bd-graph-check-*"))


def _gitless_environment(tmp_path: Path) -> dict[str, str]:
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir(exist_ok=True)
    return {"PATH": str(empty_path)}


def test_check_mode_detects_projection_drift_without_overwrite(
    graph_cli_fixture,
):
    target = graph_cli_fixture.outdir / "CALL_GRAPH.json"
    original = target.read_bytes()
    value = json.loads(original)
    value["nodes"].append("fabricated")
    target.write_text(json.dumps(value), encoding="utf-8")
    changed = target.read_bytes()

    result = graph_cli_fixture.run("--check")

    assert result.returncode != 0
    assert "CALL_GRAPH.json: stale" in result.stdout
    assert target.read_bytes() == changed
    assert _graph_check_temporaries(graph_cli_fixture.temp_parent) == []


def test_check_mode_ignores_only_generated_at_and_preserves_targets(
    graph_cli_fixture,
):
    target = graph_cli_fixture.outdir / "CALL_GRAPH.json"
    value = json.loads(target.read_text(encoding="utf-8"))
    value["generated_at"] = "1999-12-31T23:59:59Z"
    target.write_text(json.dumps(value), encoding="utf-8")
    timestamp_only = _directory_bytes(graph_cli_fixture.outdir)

    matching = graph_cli_fixture.run("--check")

    assert matching.returncode == 0, matching.stdout + matching.stderr
    assert matching.stdout == ""
    assert _directory_bytes(graph_cli_fixture.outdir) == timestamp_only

    value["tool_version"] = "fabricated"
    target.write_text(json.dumps(value), encoding="utf-8")
    changed = _directory_bytes(graph_cli_fixture.outdir)

    stale = graph_cli_fixture.run("--check")

    assert stale.returncode != 0
    assert stale.stdout.splitlines() == ["CALL_GRAPH.json: stale"]
    assert _directory_bytes(graph_cli_fixture.outdir) == changed


def test_check_mode_reports_deterministic_missing_unexpected_and_malformed(
    graph_cli_fixture,
):
    secret = "Bearer raw-projection-secret"
    (graph_cli_fixture.outdir / "CALL_GRAPH.json").unlink()
    (graph_cli_fixture.outdir / "CONFIG_LINEAGE.json").write_text(
        json.dumps({"schema_name": "config_lineage", "detail": secret}),
        encoding="utf-8",
    )
    (graph_cli_fixture.outdir / "EXTRA.json").write_text(
        '{"extra":true}\n',
        encoding="utf-8",
    )
    (graph_cli_fixture.outdir / "NONFINITE.json").write_text(
        '{"value":1e9999}\n',
        encoding="utf-8",
    )
    before = _directory_bytes(graph_cli_fixture.outdir)

    result = graph_cli_fixture.run("--check")

    assert result.returncode != 0
    assert result.stdout.splitlines() == [
        "CALL_GRAPH.json: missing",
        "CONFIG_LINEAGE.json: malformed",
        "EXTRA.json: unexpected",
        "NONFINITE.json: malformed",
    ]
    assert secret not in result.stdout + result.stderr
    assert _directory_bytes(graph_cli_fixture.outdir) == before
    assert _graph_check_temporaries(graph_cli_fixture.temp_parent) == []


@pytest.mark.parametrize(
    ("filename", "canonical_member", "duplicate_key"),
    [
        (
            "CALL_GRAPH.json",
            '"tool_version":"graph-build-2"',
            "tool_version",
        ),
        (
            "CONFIG_LINEAGE.json",
            '"effect":null',
            "effect",
        ),
    ],
)
def test_check_mode_rejects_duplicate_json_members_recursively_without_echo(
    graph_cli_fixture,
    filename,
    canonical_member,
    duplicate_key,
):
    secret = "Bearer duplicate-shadow-secret"
    target = graph_cli_fixture.outdir / filename
    text = target.read_text(encoding="utf-8")
    assert text.count(canonical_member) >= 1
    duplicate = f'"{duplicate_key}":"{secret}",{canonical_member}'
    target.write_text(
        text.replace(canonical_member, duplicate, 1),
        encoding="utf-8",
    )
    changed = target.read_bytes()

    result = graph_cli_fixture.run("--check")

    assert result.returncode != 0
    assert result.stdout.splitlines() == [f"{filename}: malformed"]
    assert result.stderr == ""
    assert duplicate_key not in result.stdout + result.stderr
    assert secret not in result.stdout + result.stderr
    assert target.read_bytes() == changed


def test_generated_projection_duplicate_members_are_malformed(
    graph_cli_fixture,
    tmp_path,
):
    secret = "Bearer generated-duplicate-shadow"
    generated = tmp_path / "generated"
    graph_build.build(graph_cli_fixture.database, generated)
    target = generated / "CALL_GRAPH.json"
    text = target.read_text(encoding="utf-8")
    canonical_member = '"tool_version":"graph-build-2"'
    duplicate = f'"tool_version":"{secret}",{canonical_member}'
    target.write_text(
        text.replace(canonical_member, duplicate, 1),
        encoding="utf-8",
    )

    differences = graph_build._projection_differences(
        generated,
        graph_cli_fixture.outdir,
        tmp_path / "comparison",
    )

    assert differences == (("CALL_GRAPH.json", "malformed"),)
    assert secret not in repr(differences)


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_check_mode_cleans_scratch_and_preserves_targets_on_failure_or_interrupt(
    graph_cli_fixture,
    monkeypatch,
    failure,
):
    before = _directory_bytes(graph_cli_fixture.outdir)
    overlap = graph_cli_fixture.outdir / "nested-tmp"
    overlap.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(overlap))

    def fail_after_scratch_write(_database, scratch):
        assert not Path(scratch).resolve().is_relative_to(
            graph_cli_fixture.outdir.resolve()
        )
        Path(scratch).mkdir(parents=True)
        Path(scratch, "partial.json").write_text("partial", encoding="utf-8")
        raise failure("synthetic graph check stop")

    monkeypatch.setattr(graph_build, "build", fail_after_scratch_write)

    with pytest.raises(failure, match="synthetic graph check stop"):
        graph_build.check_projections(
            graph_cli_fixture.database,
            graph_cli_fixture.outdir,
        )

    assert _directory_bytes(graph_cli_fixture.outdir) == before
    assert _graph_check_temporaries_recursive(graph_cli_fixture.outdir) == []
    assert _graph_check_temporaries(
        graph_cli_fixture.outdir.parent
    ) == []


@pytest.mark.parametrize("nested_tmpdir", [False, True])
def test_check_mode_places_scratch_outside_overlapping_tmpdir_and_keeps_drift(
    graph_cli_fixture,
    monkeypatch,
    capsys,
    nested_tmpdir,
):
    overlap = graph_cli_fixture.outdir
    if nested_tmpdir:
        overlap = overlap / "nested" / "temporary"
        overlap.mkdir(parents=True)
    monkeypatch.setattr(tempfile, "tempdir", str(overlap))
    original_build = graph_build.build

    def guarded_build(database, scratch):
        assert not Path(scratch).resolve().is_relative_to(
            graph_cli_fixture.outdir.resolve()
        )
        return original_build(database, scratch)

    monkeypatch.setattr(graph_build, "build", guarded_build)
    clean = _directory_bytes(graph_cli_fixture.outdir)

    assert graph_build.check_projections(
        graph_cli_fixture.database,
        graph_cli_fixture.outdir,
    ) == 0
    assert capsys.readouterr().out == ""
    assert _directory_bytes(graph_cli_fixture.outdir) == clean

    target = graph_cli_fixture.outdir / "CALL_GRAPH.json"
    value = json.loads(target.read_text(encoding="utf-8"))
    value["nodes"].append("fabricated-overlap-drift")
    target.write_text(json.dumps(value), encoding="utf-8")
    changed = _directory_bytes(graph_cli_fixture.outdir)

    assert graph_build.check_projections(
        graph_cli_fixture.database,
        graph_cli_fixture.outdir,
    ) == 1
    assert capsys.readouterr().out.splitlines() == [
        "CALL_GRAPH.json: stale"
    ]
    assert _directory_bytes(graph_cli_fixture.outdir) == changed
    assert _graph_check_temporaries_recursive(
        graph_cli_fixture.outdir
    ) == []
    assert _graph_check_temporaries(
        graph_cli_fixture.outdir.parent
    ) == []


def test_check_mode_missing_target_uses_normal_temp_without_creating_parents(
    graph_cli_fixture,
    tmp_path,
    monkeypatch,
    capsys,
):
    absent_root = tmp_path / "absent-output-ancestry"
    target = absent_root / "nested" / "projections"
    normal_temp = tmp_path / "normal-temporary-root"
    normal_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(normal_temp))
    original_temporary_directory = tempfile.TemporaryDirectory
    attempted_roots = []

    def recording_temporary_directory(*args, **kwargs):
        attempted_roots.append(Path(kwargs["dir"]).resolve())
        return original_temporary_directory(*args, **kwargs)

    monkeypatch.setattr(
        graph_build.tempfile,
        "TemporaryDirectory",
        recording_temporary_directory,
    )

    result = graph_build.check_projections(
        graph_cli_fixture.database,
        target,
    )

    assert result == 1
    assert capsys.readouterr().out.splitlines() == [
        f"{filename}: missing"
        for filename in sorted(graph_build.PROJECTION_FILENAMES)
    ]
    assert attempted_roots == [normal_temp.resolve()]
    assert not absent_root.exists()
    assert _graph_check_temporaries(normal_temp) == []


def test_check_mode_readonly_target_mount_uses_external_fallback(
    graph_cli_fixture,
    monkeypatch,
    capsys,
):
    protected_mount = graph_cli_fixture.outdir.parent.resolve()
    overlapping_temp = graph_cli_fixture.outdir / "configured-temp"
    overlapping_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(overlapping_temp))
    original_temporary_directory = tempfile.TemporaryDirectory
    attempted_roots = []

    def reject_protected_mount(*args, **kwargs):
        candidate = Path(kwargs["dir"]).resolve()
        attempted_roots.append(candidate)
        if candidate.is_relative_to(protected_mount):
            raise PermissionError("simulated read-only target mount")
        return original_temporary_directory(*args, **kwargs)

    monkeypatch.setattr(
        graph_build.tempfile,
        "TemporaryDirectory",
        reject_protected_mount,
    )
    clean = _directory_bytes(graph_cli_fixture.outdir)

    assert graph_build.check_projections(
        graph_cli_fixture.database,
        graph_cli_fixture.outdir,
    ) == 0
    assert capsys.readouterr().out == ""
    assert _directory_bytes(graph_cli_fixture.outdir) == clean
    assert attempted_roots
    assert any(
        not candidate.is_relative_to(protected_mount)
        for candidate in attempted_roots
    )

    target = graph_cli_fixture.outdir / "CALL_GRAPH.json"
    value = json.loads(target.read_text(encoding="utf-8"))
    value["nodes"].append("fabricated-readonly-drift")
    target.write_text(json.dumps(value), encoding="utf-8")
    changed = _directory_bytes(graph_cli_fixture.outdir)
    attempted_roots.clear()

    assert graph_build.check_projections(
        graph_cli_fixture.database,
        graph_cli_fixture.outdir,
    ) == 1
    assert capsys.readouterr().out.splitlines() == [
        "CALL_GRAPH.json: stale"
    ]
    assert _directory_bytes(graph_cli_fixture.outdir) == changed
    assert any(
        not candidate.is_relative_to(protected_mount)
        for candidate in attempted_roots
    )
    assert _graph_check_temporaries_recursive(
        graph_cli_fixture.outdir
    ) == []


def test_check_mode_without_safe_temp_root_fails_controlled_and_content_free(
    graph_cli_fixture,
    monkeypatch,
    capsys,
):
    secret = "Bearer unavailable-temp-secret"
    before = _directory_bytes(graph_cli_fixture.outdir)

    def unavailable_temporary_directory(*_args, **_kwargs):
        raise PermissionError(secret)

    monkeypatch.setattr(
        graph_build.tempfile,
        "TemporaryDirectory",
        unavailable_temporary_directory,
    )

    result = graph_build.check_projections(
        graph_cli_fixture.database,
        graph_cli_fixture.outdir,
    )

    output = capsys.readouterr()
    assert result == 1
    assert output.out == (
        "graph check: ERROR -- no safe temporary directory available\n"
    )
    assert output.err == ""
    assert secret not in output.out + output.err
    assert _directory_bytes(graph_cli_fixture.outdir) == before


def test_gitless_explicit_root_defaults_build_database_sibling_projections(
    tmp_path,
):
    root = tmp_path / "repo"
    database = root / "artifacts" / "KNOWLEDGE_GRAPH.db"
    database.parent.mkdir(parents=True)
    _write_schema_2_graph(database)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "graph_build.py"),
            "--root",
            str(root),
        ],
        cwd=ROOT,
        env={**os.environ, **_gitless_environment(tmp_path)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    projections = sorted(
        path.name
        for path in (root / "artifacts").glob("*.json")
    )
    assert projections == sorted(graph_build.PROJECTION_FILENAMES)


def test_gitless_help_and_fully_explicit_modes_skip_repository_discovery(
    graph_cli_fixture,
    tmp_path,
):
    gitless = _gitless_environment(tmp_path)
    help_result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "graph_build.py"),
            "--help",
        ],
        cwd=tmp_path,
        env={**os.environ, **gitless},
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0, help_result.stdout + help_result.stderr
    assert "--root" in help_result.stdout
    assert "--check" in help_result.stdout

    explicit_outdir = tmp_path / "explicit-projections"
    build_result = graph_cli_fixture.run(
        "--outdir",
        str(explicit_outdir),
        environment=gitless,
    )
    assert build_result.returncode == 0, (
        build_result.stdout + build_result.stderr
    )
    assert sorted(path.name for path in explicit_outdir.glob("*.json")) == (
        sorted(graph_build.PROJECTION_FILENAMES)
    )

    pin = tmp_path / "explicit.content.sha256"
    write_result = graph_cli_fixture.run(
        "--hash-pin",
        str(pin),
        "--write-hash",
        environment=gitless,
    )
    assert write_result.returncode == 0, (
        write_result.stdout + write_result.stderr
    )
    check_result = graph_cli_fixture.run(
        "--hash-pin",
        str(pin),
        "--check-hash",
        environment=gitless,
    )
    assert check_result.returncode == 0, (
        check_result.stdout + check_result.stderr
    )


def test_call_graph_keeps_unresolved_details_and_provenance(graph_fixture):
    call_graph = graph_fixture["CALL_GRAPH.json"]
    assert call_graph["schema_name"] == "call_graph"
    assert call_graph["source_sha"] == graph_fixture.source_sha
    assert call_graph["unresolved"] == [
        {
            "from": "bulk_downloader/a.py::caller",
            "name": "dynamic_target",
            "reason": "missing",
            "confidence": 0.0,
        }
    ]


def test_module_catalog_has_reverse_dependencies(graph_fixture):
    modules = graph_fixture["MODULE_CATALOG.json"]["modules"]
    assert modules["bulk_downloader/b.py"]["depended_by"] == [
        "bulk_downloader/a.py"
    ]


def test_config_concurrency_and_metrics_are_emitted(graph_fixture):
    config = graph_fixture["CONFIG_LINEAGE.json"]["settings"]["LIMIT"]
    assert config["readers"] == ["bulk_downloader/a.py::sample"]
    assert config["writers"] == ["bulk_downloader/a.py::sample"]
    assert config["effect"] is None
    assert config["gui_exposure"] is None
    assert config["runtime_tunable"] is None
    assert config["confidence"] == 0.5
    assert config["field_confidence"] == {
        "effect": 0.0,
        "gui_exposure": 0.0,
        "runtime_tunable": 0.0,
    }
    assert config["provenance"]["method"] == "l0_static_analysis"
    assert config["provenance"]["unknown_fields"] == {
        "effect": 0.0,
        "gui_exposure": 0.0,
        "runtime_tunable": 0.0,
    }

    concurrency = graph_fixture["CONCURRENCY_MAP.json"]
    assert concurrency["shared_state"] == []
    assert concurrency["locks"][0]["name"] == "state_lock"
    assert concurrency["locks"][0]["confidence"] == 0.6
    assert concurrency["locks"][0]["method"] == "name_substring"
    assert concurrency["locks"][0]["call_site"] == {
        "path": "bulk_downloader/a.py",
        "line": 3,
    }
    assert concurrency["locks"][0]["function"] == (
        "bulk_downloader/a.py::sample"
    )
    assert concurrency["locks"][0]["source"]["path"] == (
        concurrency["locks"][0]["path"]
    )
    assert concurrency["locks"][0]["source"]["function"] == (
        concurrency["locks"][0]["function"]
    )
    assert concurrency["operations"] == concurrency["locks"]

    metrics = graph_fixture["METRICS_CATALOG.json"]["metrics"]
    assert metrics[0]["name"] == "sample.calls"
    assert metrics[0]["operation"] == "increment"
    assert metrics[0]["call_site"] == {
        "path": "bulk_downloader/a.py",
        "line": 4,
    }
    assert metrics[0]["function"] == "bulk_downloader/a.py::sample"
    assert metrics[0]["containing_function"] == (
        "bulk_downloader/a.py::sample"
    )
    assert metrics[0]["method"] == "name_substring"
    assert metrics[0]["confidence"] == 0.6
    assert metrics[0]["source"]["path"] == metrics[0]["path"]
    assert metrics[0]["source"]["function"] == metrics[0]["function"]

    read_site = config["provenance"]["read_sites"][0]
    write_site = config["provenance"]["write_sites"][0]
    assert read_site["source"] == {
        "key": "LIMIT",
        "at": 1,
        "path": "bulk_downloader/a.py",
        "function": "bulk_downloader/a.py::sample",
    }
    assert write_site["source"] == {
        "key": "LIMIT",
        "at": 2,
        "path": "bulk_downloader/a.py",
        "function": "bulk_downloader/a.py::sample",
    }


def test_all_nine_projections_use_valid_shared_envelopes(graph_fixture):
    expected_names = {
        "CALL_GRAPH.json": "call_graph",
        "CONFIG_LINEAGE.json": "config_lineage",
        "CONCURRENCY_MAP.json": "concurrency_map",
        "MODULE_CATALOG.json": "module_catalog",
        "SECURITY_SURFACE.json": "security_surface",
        "ERROR_CATALOG.json": "error_catalog",
        "METRICS_CATALOG.json": "metrics_catalog",
        "TAINT_MAP.json": "taint_map",
        "DEAD_CODE.json": "dead_code",
    }

    assert set(graph_fixture) == set(expected_names)
    for filename, schema_name in expected_names.items():
        artifact = graph_fixture[filename]
        validate_projection(schema_name, artifact)
        assert artifact["schema"] == 1
        assert artifact["schema_name"] == schema_name
        assert artifact["schema_version"] == 1
        assert artifact["source_sha"] == SOURCE_SHA
        assert artifact["input_hashes"] == INPUT_HASHES
        assert artifact["tool_version"]
        assert artifact["generated_at"].endswith("Z")


def test_task6_projection_schemas_reject_guessed_or_unbounded_evidence():
    config = {
        **make_envelope(
            "config_lineage",
            1,
            SOURCE_SHA,
            "test",
            INPUT_HASHES,
        ),
        "settings": {
            "LIMIT": {
                "readers": [],
                "writers": [],
                "effect": "guessed",
                "gui_exposure": None,
                "runtime_tunable": None,
                "confidence": 0.5,
                "field_confidence": {
                    "effect": 0.0,
                    "gui_exposure": 0.0,
                    "runtime_tunable": 0.0,
                },
                "provenance": {
                    "method": "l0_static_analysis",
                    "read_sites": [],
                    "write_sites": [],
                    "unknown_fields": {
                        "effect": 0.0,
                        "gui_exposure": 0.0,
                        "runtime_tunable": 0.0,
                    },
                },
            }
        },
    }
    with pytest.raises(SchemaError, match="effect"):
        validate_projection("config_lineage", config)

    concurrency = {
        **make_envelope(
            "concurrency_map",
            1,
            SOURCE_SHA,
            "test",
            INPUT_HASHES,
        ),
        "shared_state": [],
        "locks": [],
        "operations": [
            {
                "kind": "lock",
                "name": "state_lock",
                "operation": "context",
                "at": 3,
                "call_site": {
                    "path": "bulk_downloader/a.py",
                    "line": 3,
                },
                "path": "bulk_downloader/a.py",
                "function": "bulk_downloader/a.py::sample",
                "method": "name_substring",
                "confidence": 1.5,
                "source": {
                    "kind": "lock",
                    "name": "state_lock",
                    "operation": "context",
                    "at": 3,
                    "method": "name_substring",
                    "confidence": 1.5,
                    "path": "bulk_downloader/a.py",
                    "function": "bulk_downloader/a.py::sample",
                },
            }
        ],
    }
    with pytest.raises(SchemaError, match="confidence"):
        validate_projection("concurrency_map", concurrency)


def _with_correlated_task6_sources(artifact):
    artifact = copy.deepcopy(artifact)
    if artifact["schema_name"] == "config_lineage":
        records = [
            site
            for setting in artifact["settings"].values()
            for field in ("read_sites", "write_sites")
            for site in setting["provenance"][field]
        ]
    elif artifact["schema_name"] == "concurrency_map":
        records = [*artifact["locks"], *artifact["operations"]]
    else:
        records = artifact["metrics"]
    for record in records:
        record["source"]["path"] = record["path"]
        record["source"]["function"] = record["function"]
    return artifact


def test_config_schema_rejects_uncorrelated_provenance_and_coverage(
    graph_fixture,
):
    base = _with_correlated_task6_sources(
        graph_fixture["CONFIG_LINEAGE.json"]
    )

    for inventory, sites in (
        ("readers", "read_sites"),
        ("writers", "write_sites"),
    ):
        setting = base["settings"]["LIMIT"]

        extra_site = copy.deepcopy(base)
        site = extra_site["settings"]["LIMIT"]["provenance"][sites][0]
        site["function"] = "bulk_downloader/a.py::other"
        site["source"]["function"] = site["function"]
        with pytest.raises(SchemaError, match="coverage|membership"):
            validate_projection("config_lineage", extra_site)

        missing_site = copy.deepcopy(base)
        missing_site["settings"]["LIMIT"][inventory].append(
            "bulk_downloader/a.py::other"
        )
        missing_site["settings"]["LIMIT"][inventory].sort()
        with pytest.raises(SchemaError, match="coverage"):
            validate_projection("config_lineage", missing_site)

        duplicate_site = copy.deepcopy(base)
        duplicate = duplicate_site["settings"]["LIMIT"]["provenance"][
            sites
        ][0]
        duplicate_site["settings"]["LIMIT"]["provenance"][sites].append(
            copy.deepcopy(duplicate)
        )
        with pytest.raises(SchemaError, match="duplicate"):
            validate_projection("config_lineage", duplicate_site)

        wrong_key = copy.deepcopy(base)
        wrong_key["settings"]["LIMIT"]["provenance"][sites][0]["source"][
            "key"
        ] = "OTHER"
        with pytest.raises(SchemaError, match="key"):
            validate_projection("config_lineage", wrong_key)

        wrong_line = copy.deepcopy(base)
        wrong_line["settings"]["LIMIT"]["provenance"][sites][0]["source"][
            "at"
        ] = 99
        with pytest.raises(SchemaError, match="line"):
            validate_projection("config_lineage", wrong_line)

        wrong_path = copy.deepcopy(base)
        wrong_path["settings"]["LIMIT"]["provenance"][sites][0]["source"][
            "path"
        ] = "bulk_downloader/other.py"
        with pytest.raises(SchemaError, match="path"):
            validate_projection("config_lineage", wrong_path)

        wrong_function = copy.deepcopy(base)
        wrong_function["settings"]["LIMIT"]["provenance"][sites][0][
            "source"
        ]["function"] = "bulk_downloader/a.py::other"
        with pytest.raises(SchemaError, match="function"):
            validate_projection("config_lineage", wrong_function)


def test_config_schema_requires_exact_mechanical_confidence(graph_fixture):
    artifact = _with_correlated_task6_sources(
        graph_fixture["CONFIG_LINEAGE.json"]
    )
    artifact["settings"]["LIMIT"]["confidence"] = 0.6

    with pytest.raises(SchemaError, match="0.5"):
        validate_projection("config_lineage", artifact)


@pytest.mark.parametrize(
    "unsafe_key",
    [
        "Bearer raw-fact-secret",
        "mode=true",
        "line\nbreak",
        "\x00control",
        "dotted.key",
        "hyphen-key",
        "9starts_with_digit",
    ],
)
def test_config_builder_rejects_unsafe_keys_without_echoing_value(
    unsafe_key,
):
    graph = graph_build.GraphInput(
        source_sha=SOURCE_SHA,
        input_hashes=INPUT_HASHES,
        modules={},
        functions={
            "bulk_downloader/a.py::sample": {
                "path": "bulk_downloader/a.py",
                "meta": {
                    "config_reads": [{"key": unsafe_key, "at": 1}],
                    "config_writes": [],
                },
            }
        },
        calls=(),
        contains={},
    )

    with pytest.raises(ValueError, match="safe config-key grammar") as raised:
        graph_build.build_config_lineage(graph)

    assert unsafe_key not in str(raised.value)


@pytest.mark.parametrize(
    "unsafe_key",
    [
        "Bearer raw-fact-secret",
        "mode=true",
        "line\nbreak",
        "\x00control",
        "dotted.key",
        "hyphen-key",
        "9starts_with_digit",
    ],
)
def test_config_schema_rejects_unsafe_keys_without_echoing_value(
    graph_fixture,
    unsafe_key,
):
    artifact = copy.deepcopy(graph_fixture["CONFIG_LINEAGE.json"])
    setting = artifact["settings"].pop("LIMIT")
    for sites in ("read_sites", "write_sites"):
        for site in setting["provenance"][sites]:
            site["key"] = unsafe_key
            site["source"]["key"] = unsafe_key
    artifact["settings"][unsafe_key] = setting

    with pytest.raises(SchemaError, match="safe config-key grammar") as raised:
        validate_projection("config_lineage", artifact)

    assert unsafe_key not in str(raised.value)


def test_safe_config_key_exemption_keeps_nested_secret_checks(graph_fixture):
    artifact = copy.deepcopy(graph_fixture["CONFIG_LINEAGE.json"])
    setting = artifact["settings"].pop("LIMIT")
    for sites in ("read_sites", "write_sites"):
        for site in setting["provenance"][sites]:
            site["key"] = "API_TOKEN"
            site["source"]["key"] = "API_TOKEN"
    artifact["settings"]["API_TOKEN"] = setting

    validate_projection("config_lineage", artifact)

    secret = "Bearer nested-config-secret"
    artifact["settings"]["API_TOKEN"]["details"] = {
        "api_token": secret,
    }
    with pytest.raises(SchemaError, match="api_token") as raised:
        validate_projection("config_lineage", artifact)

    assert secret not in str(raised.value)


def test_heuristic_schema_rejects_noncanonical_names_and_operations(
    graph_fixture,
):
    concurrency = _with_correlated_task6_sources(
        graph_fixture["CONCURRENCY_MAP.json"]
    )
    concurrency["locks"] = []
    for field in ("name", "operation"):
        artifact = copy.deepcopy(concurrency)
        record = artifact["operations"][0]
        record[field] = f" {record[field]} "
        record["source"][field] = record[field]
        with pytest.raises(SchemaError, match="canonical"):
            validate_projection("concurrency_map", artifact)

    metrics = _with_correlated_task6_sources(
        graph_fixture["METRICS_CATALOG.json"]
    )
    for field in ("name", "operation"):
        artifact = copy.deepcopy(metrics)
        record = artifact["metrics"][0]
        record[field] = f" {record[field]} "
        record["source"][field] = record[field]
        with pytest.raises(SchemaError, match="canonical"):
            validate_projection("metrics_catalog", artifact)


def test_heuristic_schema_rejects_source_conflicts_and_trust_inflation(
    graph_fixture,
):
    concurrency = _with_correlated_task6_sources(
        graph_fixture["CONCURRENCY_MAP.json"]
    )
    concurrency["locks"] = []
    operation = concurrency["operations"][0]
    for field, contradictory in (
        ("kind", "thread"),
        ("name", "other_lock"),
        ("operation", "acquire"),
        ("method", "other_rule"),
        ("confidence", 0.4),
        ("path", "bulk_downloader/other.py"),
        ("function", "bulk_downloader/a.py::other"),
    ):
        artifact = copy.deepcopy(concurrency)
        artifact["operations"][0]["source"][field] = contradictory
        with pytest.raises(SchemaError, match=field):
            validate_projection("concurrency_map", artifact)

    wrong_line = copy.deepcopy(concurrency)
    wrong_line["operations"][0]["source"]["at"] = 99
    with pytest.raises(SchemaError, match="line"):
        validate_projection("concurrency_map", wrong_line)

    inflated = copy.deepcopy(concurrency)
    inflated["operations"][0]["confidence"] = 1.0
    inflated["operations"][0]["source"]["confidence"] = 1.0
    with pytest.raises(SchemaError, match="name_substring|0.6"):
        validate_projection("concurrency_map", inflated)

    metrics = _with_correlated_task6_sources(
        graph_fixture["METRICS_CATALOG.json"]
    )
    metric = metrics["metrics"][0]
    for field, contradictory in (
        ("name", "other.calls"),
        ("operation", "observe"),
        ("method", "other_rule"),
        ("confidence", 0.4),
        ("path", "bulk_downloader/other.py"),
        ("function", "bulk_downloader/a.py::other"),
    ):
        artifact = copy.deepcopy(metrics)
        artifact["metrics"][0]["source"][field] = contradictory
        with pytest.raises(SchemaError, match=field):
            validate_projection("metrics_catalog", artifact)

    wrong_metric_line = copy.deepcopy(metrics)
    wrong_metric_line["metrics"][0]["source"]["at"] = 99
    with pytest.raises(SchemaError, match="line"):
        validate_projection("metrics_catalog", wrong_metric_line)

    inflated_metric = copy.deepcopy(metrics)
    inflated_metric["metrics"][0]["confidence"] = 1.0
    inflated_metric["metrics"][0]["source"]["confidence"] = 1.0
    with pytest.raises(SchemaError, match="name_substring|0.6"):
        validate_projection("metrics_catalog", inflated_metric)

    assert operation["method"] == metric["method"] == "name_substring"


def test_shared_state_schema_rejects_scalars_and_contradictions():
    valid_record = {
        "name": "state_cache",
        "readers": ["bulk_downloader/a.py::sample"],
        "writers": [],
        "confidence": 0.0,
        "provenance": {
            "method": None,
            "confidence": 0.0,
        },
    }
    artifact = {
        **make_envelope(
            "concurrency_map",
            1,
            SOURCE_SHA,
            "test",
            INPUT_HASHES,
        ),
        "shared_state": [valid_record],
        "locks": [],
        "operations": [],
    }
    validate_projection("concurrency_map", artifact)

    scalar = copy.deepcopy(artifact)
    scalar["shared_state"] = ["state_cache"]
    with pytest.raises(SchemaError, match="shared_state"):
        validate_projection("concurrency_map", scalar)

    contradictory = copy.deepcopy(artifact)
    contradictory["shared_state"][0]["confidence"] = 0.5
    with pytest.raises(SchemaError, match="confidence"):
        validate_projection("concurrency_map", contradictory)


def test_known_heuristic_builders_normalize_and_reject_inflation():
    function_id = "bulk_downloader/a.py::sample"

    def graph_with(confidence):
        return graph_build.GraphInput(
            source_sha=SOURCE_SHA,
            input_hashes=INPUT_HASHES,
            modules={},
            functions={
                function_id: {
                    "path": "bulk_downloader/a.py",
                    "meta": {
                        "concurrency_ops": [
                            {
                                "kind": " lock ",
                                "name": " state_lock ",
                                "operation": " context ",
                                "at": 2,
                                "method": "name_substring",
                                "confidence": confidence,
                            }
                        ],
                        "metric_emits": [
                            {
                                "name": " sample.calls ",
                                "operation": " increment ",
                                "at": 3,
                                "method": "name_substring",
                                "confidence": confidence,
                            }
                        ],
                    },
                }
            },
            calls=(),
            contains={},
        )

    graph = graph_with(0.6)
    operation = graph_build.build_concurrency_map(graph)["operations"][0]
    metric = graph_build.build_metrics_catalog(graph)["metrics"][0]
    assert (operation["kind"], operation["name"], operation["operation"]) == (
        "lock",
        "state_lock",
        "context",
    )
    assert (metric["name"], metric["operation"]) == (
        "sample.calls",
        "increment",
    )

    with pytest.raises(ValueError, match="name_substring|0.6"):
        graph_build.build_concurrency_map(graph_with(1.0))
    with pytest.raises(ValueError, match="name_substring|0.6"):
        graph_build.build_metrics_catalog(graph_with(1.0))


def test_task6_builders_do_not_copy_unknown_fact_fields():
    secret = "Bearer raw-fact-secret"
    graph = graph_build.GraphInput(
        source_sha=SOURCE_SHA,
        input_hashes=INPUT_HASHES,
        modules={},
        functions={
            "bulk_downloader/a.py::sample": {
                "path": "bulk_downloader/a.py",
                "meta": {
                    "config_reads": [
                        {"key": "LIMIT", "at": 1, "detail": secret},
                        {"key": "LIMIT", "at": 1, "detail": secret},
                    ],
                    "config_writes": [],
                    "concurrency_ops": [
                        {
                            "kind": "lock",
                            "name": "state_lock",
                            "operation": "context",
                            "at": 2,
                            "method": "name_substring",
                            "confidence": 0.6,
                            "detail": secret,
                        }
                    ],
                    "metric_emits": [
                        {
                            "name": "sample.calls",
                            "operation": "increment",
                            "at": 3,
                            "method": "name_substring",
                            "confidence": 0.6,
                            "detail": secret,
                        }
                    ],
                },
            }
        },
        calls=(),
        contains={},
    )

    projections = [
        graph_build.build_config_lineage(graph),
        graph_build.build_concurrency_map(graph),
        graph_build.build_metrics_catalog(graph),
    ]

    assert secret not in json.dumps(projections, sort_keys=True)
    for projection in projections:
        assert "detail" not in json.dumps(projection, sort_keys=True)
    config = projections[0]["settings"]["LIMIT"]
    assert len(config["provenance"]["read_sites"]) == 1


def test_task6_build_is_byte_deterministic_with_fixed_generated_at(
    tmp_path, monkeypatch
):
    database = tmp_path / "KNOWLEDGE_GRAPH.db"
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_schema_2_graph(database)
    original_make_envelope = graph_build.make_envelope

    def fixed_envelope(*args, **kwargs):
        envelope = original_make_envelope(*args, **kwargs)
        envelope["generated_at"] = "2026-07-23T00:00:00Z"
        return envelope

    monkeypatch.setattr(graph_build, "make_envelope", fixed_envelope)

    graph_build.build(database, first)
    graph_build.build(database, second)

    first_artifacts = {
        path.name: path.read_bytes()
        for path in sorted(first.glob("*.json"))
    }
    second_artifacts = {
        path.name: path.read_bytes()
        for path in sorted(second.glob("*.json"))
    }
    assert len(first_artifacts) == 9
    assert first_artifacts == second_artifacts


def test_resolver_labels_exact_heuristic_and_ambiguous_results():
    functions = {
        "m.py::service.target": {"qual": "service.target"},
        "m.py::helper": {"qual": "helper"},
        "a.py::first.duplicate": {"qual": "first.duplicate"},
        "b.py::second.duplicate": {"qual": "second.duplicate"},
    }
    edges, unresolved = graph_build.resolve_calls(
        functions,
        [
            ("caller", "service.target"),
            ("caller", "package.helper"),
            ("caller", "duplicate"),
        ],
    )

    assert edges == [
        {
            "from": "caller",
            "to": "m.py::service.target",
            "kind": "call",
            "reason": "exact_qualified",
            "confidence": 1.0,
        },
        {
            "from": "caller",
            "to": "m.py::helper",
            "kind": "call",
            "reason": "unique_last_segment",
            "confidence": 0.6,
        },
    ]
    assert unresolved == [
        {
            "from": "caller",
            "name": "duplicate",
            "reason": "ambiguous",
            "candidates": [
                "a.py::first.duplicate",
                "b.py::second.duplicate",
            ],
            "confidence": 0.0,
        }
    ]


def test_resolver_preserves_edge_metadata_losslessly():
    functions = {"m.py::helper": {"qual": "helper"}}

    edges, unresolved = graph_build.resolve_calls(
        functions,
        [
            ("caller", "package.helper", {"at": 7}),
            ("caller", "dynamic_target", {"at": 8}),
        ],
    )

    assert edges == [
        {
            "from": "caller",
            "to": "m.py::helper",
            "kind": "call",
            "at": 7,
            "reason": "unique_last_segment",
            "confidence": 0.6,
        }
    ]
    assert unresolved == [
        {
            "from": "caller",
            "name": "dynamic_target",
            "at": 8,
            "reason": "missing",
            "confidence": 0.0,
        }
    ]


def test_annotated_projections_keep_paths_and_mechanical_provenance(graph_fixture):
    security = graph_fixture["SECURITY_SURFACE.json"]
    sql_site = security["sql_sites"][0]
    assert sql_site["at"] == "bulk_downloader/a.py:2"
    assert sql_site["path"] == "bulk_downloader/a.py"
    assert sql_site["function"] == "bulk_downloader/a.py::caller"
    assert sql_site["method"] == "name_substring"
    assert sql_site["confidence"] == 0.6

    handler = graph_fixture["ERROR_CATALOG.json"]["handlers"][0]
    assert handler["at"] == "bulk_downloader/a.py:[1, 4]"
    assert handler["fn"] == "caller"
    assert handler["path"] == "bulk_downloader/a.py"
    assert handler["function"] == "bulk_downloader/a.py::caller"
    assert handler["provenance"]["method"] == "l0_static_analysis"

    taint = graph_fixture["TAINT_MAP.json"]
    assert taint["paths"] == []
    assert taint["sinks"][0]["source_path"] == "bulk_downloader/a.py"
    assert taint["sinks"][0]["source_function"] == (
        "bulk_downloader/a.py::caller"
    )
    assert taint["sinks"][0]["method"] == "name_substring"
    assert taint["sinks"][0]["confidence"] == 0.6

    module = graph_fixture["MODULE_CATALOG.json"]["modules"][
        "bulk_downloader/a.py"
    ]
    assert module["purpose"] is None
    assert module["data_flow"] is None
    assert module["provenance"] == {
        "method": "l0_static_analysis",
        "node_id": "bulk_downloader/a.py",
        "path": "bulk_downloader/a.py",
        "sha256": INPUT_HASHES["bulk_downloader/a.py"],
    }


def test_dead_code_explains_confidence_and_dynamic_framework_exclusions(
    graph_fixture,
):
    entries = graph_fixture["DEAD_CODE.json"]["uncalled"]
    caller = next(
        entry
        for entry in entries
        if entry["fn"] == "bulk_downloader/a.py::caller"
    )

    assert caller["reason"] == "no_resolved_intra_repo_caller"
    assert caller["confidence"] == 0.5
    assert caller["excluded_evidence"] == [
        "dynamic_dispatch",
        "framework_registration",
        "route_or_cli_entrypoint",
    ]
    assert caller["path"] == "bulk_downloader/a.py"


def test_content_hash_includes_edge_metadata(tmp_path):
    database = tmp_path / "KNOWLEDGE_GRAPH.db"
    _write_schema_2_graph(database)
    original = graph_build.content_hash(database)

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE edges SET meta_json = ? WHERE kind = 'call'",
            ('{"at": 99}',),
        )
    edge_metadata_changed = graph_build.content_hash(database)
    assert edge_metadata_changed != original


def test_content_hash_includes_graph_metadata(tmp_path):
    database = tmp_path / "KNOWLEDGE_GRAPH.db"
    _write_schema_2_graph(database)
    original = graph_build.content_hash(database)

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE meta SET v = ? WHERE k = 'source_sha'",
            ("d" * 64,),
        )
    graph_metadata_changed = graph_build.content_hash(database)
    assert graph_metadata_changed != original


def _write_schema_1_graph(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE nodes(
                id TEXT,
                kind TEXT,
                path TEXT,
                qualname TEXT,
                span TEXT,
                sha256 TEXT,
                lines INTEGER,
                meta_json TEXT
            );
            CREATE TABLE edges(src TEXT, dst TEXT, kind TEXT);
            """
        )
        connection.execute(
            "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
            (
                "legacy.py",
                "module",
                "legacy.py",
                "legacy.py",
                "",
                "e" * 64,
                1,
                '{"imports": []}',
            ),
        )


def _replace_function_meta(
    database: Path,
    function_id: str,
    updates: dict[str, object],
) -> None:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT meta_json FROM nodes WHERE id = ?",
            (function_id,),
        ).fetchone()
        assert row is not None
        metadata = json.loads(row[0])
        metadata.update(updates)
        connection.execute(
            "UPDATE nodes SET meta_json = ? WHERE id = ?",
            (json.dumps(metadata, sort_keys=True), function_id),
        )


def _write_package_import_graph(path: Path) -> None:
    _write_schema_2_graph(path)
    package_path = "bulk_downloader/__init__.py"
    package_sha = "d" * 64
    input_hashes = {**INPUT_HASHES, package_path: package_sha}
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
            (
                package_path,
                "module",
                package_path,
                package_path,
                "",
                package_sha,
                1,
                '{"imports": []}',
            ),
        )
        module_meta = json.dumps(
            {"imports": ["bulk_downloader"]},
            sort_keys=True,
        )
        connection.execute(
            "UPDATE nodes SET meta_json = ? WHERE id = ?",
            (module_meta, "bulk_downloader/a.py"),
        )
        connection.execute("DELETE FROM edges WHERE kind = 'imports'")
        connection.execute(
            "INSERT INTO edges VALUES(?,?,?,?)",
            (
                "bulk_downloader/a.py",
                "bulk_downloader",
                "imports",
                "{}",
            ),
        )
        connection.execute(
            "UPDATE meta SET v = ? WHERE k = 'input_hashes'",
            (json.dumps(input_hashes, sort_keys=True),),
        )


def _write_bare_stem_import_graph(path: Path) -> None:
    _write_schema_2_graph(path)
    vendor_path = "vendor/json.py"
    vendor_sha = "f" * 64
    input_hashes = {**INPUT_HASHES, vendor_path: vendor_sha}
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
            (
                vendor_path,
                "module",
                vendor_path,
                vendor_path,
                "",
                vendor_sha,
                1,
                '{"imports": []}',
            ),
        )
        connection.execute(
            "UPDATE nodes SET meta_json = ? WHERE id = ?",
            ('{"imports": ["json"]}', "bulk_downloader/a.py"),
        )
        connection.execute("DELETE FROM edges WHERE kind = 'imports'")
        connection.execute(
            "INSERT INTO edges VALUES(?,?,?,?)",
            (
                "bulk_downloader/a.py",
                "json",
                "imports",
                "{}",
            ),
        )
        connection.execute(
            "UPDATE meta SET v = ? WHERE k = 'input_hashes'",
            (json.dumps(input_hashes, sort_keys=True),),
        )


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def test_schema_1_graph_fixture_remains_supported(tmp_path):
    database = tmp_path / "legacy.db"
    outdir = tmp_path / "legacy-projections"
    _write_schema_1_graph(database)

    graph_build.build(database, outdir)

    for artifact_path in sorted(outdir.glob("*.json")):
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        validate_projection(artifact["schema_name"], artifact)
        assert artifact["source_sha"] == "0" * 64
        assert artifact["source_binding"] == "unbound_legacy"
        assert artifact["input_hashes"] == {}


def test_projection_validation_failure_preserves_prior_artifact(
    tmp_path, monkeypatch
):
    database = tmp_path / "KNOWLEDGE_GRAPH.db"
    outdir = tmp_path / "projections"
    outdir.mkdir()
    prior = outdir / "CALL_GRAPH.json"
    prior.write_text('{"prior": true}\n', encoding="utf-8")
    _write_schema_2_graph(database)

    def reject_projection(_name, _value):
        raise RuntimeError("validation rejected projection")

    monkeypatch.setattr(
        graph_build,
        "validate_projection",
        reject_projection,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="validation rejected projection"):
        graph_build.build(database, outdir)

    assert prior.read_text(encoding="utf-8") == '{"prior": true}\n'


def test_task6_validation_failure_happens_before_any_projection_write(
    tmp_path, monkeypatch
):
    database = tmp_path / "KNOWLEDGE_GRAPH.db"
    outdir = tmp_path / "projections"
    outdir.mkdir()
    prior = outdir / "CALL_GRAPH.json"
    prior.write_text('{"prior": true}\n', encoding="utf-8")
    _write_schema_2_graph(database)
    original_validate = graph_build.validate_projection

    def reject_metrics(name, value):
        if name == "metrics_catalog":
            raise RuntimeError("metrics validation rejected projection")
        return original_validate(name, value)

    monkeypatch.setattr(graph_build, "validate_projection", reject_metrics)

    with pytest.raises(
        RuntimeError, match="metrics validation rejected projection"
    ):
        graph_build.build(database, outdir)

    assert prior.read_text(encoding="utf-8") == '{"prior": true}\n'
    assert sorted(path.name for path in outdir.glob("*.json")) == [
        "CALL_GRAPH.json"
    ]


def test_validator_accepts_legacy_sensitive_named_totals_counts():
    artifact = {
        **make_envelope(
            "security_surface",
            1,
            SOURCE_SHA,
            "test",
            INPUT_HASHES,
        ),
        "auth_gates": [],
        "secret_sites": [],
        "sql_sites": [],
        "subprocess_sites": [],
        "path_sinks": [],
        "totals": {
            "secret_sites": 0,
            "token_sites": 2,
        },
    }

    validate_projection("security_surface", artifact)


def test_sensitive_structural_paths_still_validate_nested_secret_values():
    secret = "Bearer nested-module-secret"
    artifact = {
        **make_envelope(
            "module_catalog",
            1,
            SOURCE_SHA,
            "test",
            {"bulk_downloader/token.py": "d" * 64},
        ),
        "modules": {
            "bulk_downloader/password_reset.py": {
                "api_token": secret,
            }
        },
    }

    with pytest.raises(SchemaError, match="api_token") as raised:
        validate_projection("module_catalog", artifact)

    assert secret not in str(raised.value)


def test_schema_2_requires_input_hashes_metadata(tmp_path):
    database = tmp_path / "missing-input-hashes.db"
    _write_schema_2_graph(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM meta WHERE k = 'input_hashes'")

    with pytest.raises(ValueError, match="input_hashes"):
        graph_build.load(database)


def test_schema_2_accepts_present_empty_input_hashes_metadata(tmp_path):
    database = tmp_path / "empty-input-hashes.db"
    _write_schema_2_graph(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE meta SET v = '{}' WHERE k = 'input_hashes'"
        )

    graph = graph_build.load(database)

    assert graph.input_hashes == {}


def test_package_import_target_maps_only_to_package_initializer(tmp_path):
    database = tmp_path / "package-import.db"
    outdir = tmp_path / "package-projections"
    _write_package_import_graph(database)

    graph_build.build(database, outdir)
    modules = json.loads(
        (outdir / "MODULE_CATALOG.json").read_text(encoding="utf-8")
    )["modules"]

    assert modules["bulk_downloader/__init__.py"]["depended_by"] == [
        "bulk_downloader/a.py"
    ]
    assert modules["bulk_downloader/b.py"]["depended_by"] == []


def test_bare_import_name_does_not_guess_vendor_module_by_stem(tmp_path):
    database = tmp_path / "bare-import.db"
    outdir = tmp_path / "bare-import-projections"
    _write_bare_stem_import_graph(database)

    graph_build.build(database, outdir)
    modules = json.loads(
        (outdir / "MODULE_CATALOG.json").read_text(encoding="utf-8")
    )["modules"]

    assert modules["bulk_downloader/a.py"]["depends_on"] == ["json"]
    assert modules["vendor/json.py"]["depended_by"] == []


def test_positive_taint_path_is_explicitly_heuristic_and_keeps_source_fact(
    tmp_path,
):
    database = tmp_path / "taint.db"
    outdir = tmp_path / "taint-projections"
    _write_schema_2_graph(database)
    path_fact = {
        "from": "request.args",
        "to": "cursor.execute",
        "method": "proved",
        "confidence": 1.0,
        "proof": True,
        "verified": True,
        "reason": "source_to_sink_proof",
        "steps": ["request.args", "cursor.execute"],
    }
    _replace_function_meta(
        database,
        "bulk_downloader/a.py::caller",
        {"taint_paths": [path_fact]},
    )

    graph_build.build(database, outdir)
    paths = json.loads(
        (outdir / "TAINT_MAP.json").read_text(encoding="utf-8")
    )["paths"]

    assert len(paths) == 1
    path = paths[0]
    assert path["source"] == path_fact
    assert path["method"] == "explicit_path_heuristic"
    assert 0.0 < path["confidence"] < 1.0
    assert path["proof"] is False
    assert path["explicit"] is True
    assert path["reason"] == "explicit_l0_path_evidence"
    assert "verified" not in path
    assert path["steps"] == path_fact["steps"]


def test_l0_sensitive_production_paths_validate_all_nine_projections(
    tmp_path,
):
    root = tmp_path / "repo"
    package = root / "bulk_downloader"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        '"""Fixture package."""\n',
        encoding="utf-8",
    )
    (package / "token.py").write_text(
        "def label():\n    return 'safe'\n",
        encoding="utf-8",
    )
    (package / "password_reset.py").write_text(
        "def reset_enabled():\n    return False\n",
        encoding="utf-8",
    )
    (package / "worker.py").write_text(
        "import bulk_downloader\n"
        "def run():\n"
        "    return bulk_downloader\n",
        encoding="utf-8",
    )
    _git(root, "init", "-q")
    _git(root, "add", "bulk_downloader")
    database = tmp_path / "production-shaped.db"
    outdir = tmp_path / "production-shaped-projections"

    l0_extract.build_db(root, database)
    graph_build.build(database, outdir)

    expected_names = {
        "CALL_GRAPH.json": "call_graph",
        "CONFIG_LINEAGE.json": "config_lineage",
        "CONCURRENCY_MAP.json": "concurrency_map",
        "MODULE_CATALOG.json": "module_catalog",
        "SECURITY_SURFACE.json": "security_surface",
        "ERROR_CATALOG.json": "error_catalog",
        "METRICS_CATALOG.json": "metrics_catalog",
        "TAINT_MAP.json": "taint_map",
        "DEAD_CODE.json": "dead_code",
    }
    artifacts = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(outdir.glob("*.json"))
    }
    assert set(artifacts) == set(expected_names)
    for filename, schema_name in expected_names.items():
        validate_projection(schema_name, artifacts[filename])

    modules = artifacts["MODULE_CATALOG.json"]["modules"]
    assert modules["bulk_downloader/__init__.py"]["depended_by"] == [
        "bulk_downloader/worker.py"
    ]
    assert modules["bulk_downloader/token.py"]["depended_by"] == []


def test_l0_concatenated_secret_identifiers_validate_as_inventory(tmp_path):
    root = tmp_path / "repo"
    package = root / "bulk_downloader"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "identifiers.py").write_text(
        "def inventory(\n"
        "    cookiejar,\n"
        "    privatekey,\n"
        "    apikey,\n"
        "    sessionkey,\n"
        "    accesstoken,\n"
        "    authorizationcache,\n"
        "):\n"
        "    return None\n",
        encoding="utf-8",
    )
    _git(root, "init", "-q")
    _git(root, "add", "bulk_downloader")
    database = tmp_path / "concatenated-secrets.db"
    outdir = tmp_path / "concatenated-secret-projections"

    l0_extract.build_db(root, database)
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT meta_json FROM nodes "
            "WHERE id = 'bulk_downloader/identifiers.py::inventory'"
        ).fetchone()
    assert row is not None
    l0_secrets = json.loads(row[0])["secrets"]
    assert l0_secrets == [
        "accesstoken",
        "apikey",
        "authorizationcache",
        "cookiejar",
        "privatekey",
        "sessionkey",
    ]

    graph_build.build(database, outdir)
    modules = json.loads(
        (outdir / "MODULE_CATALOG.json").read_text(encoding="utf-8")
    )["modules"]

    assert modules["bulk_downloader/identifiers.py"]["secrets"] == l0_secrets


def test_module_secret_inventory_still_rejects_arbitrary_identifier():
    artifact = {
        **make_envelope(
            "module_catalog",
            1,
            SOURCE_SHA,
            "test",
            INPUT_HASHES,
        ),
        "modules": {
            "bulk_downloader/module.py": {
                "secrets": ["ordinary_identifier"],
            }
        },
    }

    with pytest.raises(SchemaError, match="secret-like"):
        validate_projection("module_catalog", artifact)
