"""Tests for the stable ``bd-coverage-map`` analysis frontend."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import tools.code_intelligence.coverage_service as coverage_service_module
from tools.code_intelligence.artifacts import artifact_hash
from tools.code_intelligence.coverage_service import (
    _catalog_evidence,
    _load_test_catalog,
    build_coverage_content,
    run_coverage_map,
    sha256_path,
)
from tools.code_intelligence.results import ResultState
from tools.code_intelligence.snapshot import TreeSnapshot, build_snapshot
from tools.risk_score import score, score_from_reports


ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "code_intelligence" / "coverage"
SCRIPT = ROOT / "tools" / "coverage_map.py"
LAUNCHER = ROOT / "toolchain" / "bin" / "bd-coverage-map"


@pytest.fixture(autouse=True)
def source_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TreeSnapshot:
    """Use the real snapshotter on a tiny source tree in service-level tests."""
    repository = tmp_path / "source-repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    (repository / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=repository, check=True)
    snapshot = build_snapshot(repository)
    monkeypatch.setattr(
        "tools.code_intelligence.coverage_service.build_snapshot",
        lambda _root: snapshot,
    )
    return snapshot


def _graph(
    path: Path,
    *,
    parse_meta: str = '{"sinks":[],"secrets":[]}',
    emit_meta: str = (
        '{"sinks":[{"kind":"fetch","at":7}],'
        '"secrets":["API_TOKEN"]}'
    ),
) -> None:
    database = sqlite3.connect(path)
    database.execute(
        "CREATE TABLE nodes("
        "id TEXT, kind TEXT, path TEXT, qualname TEXT, span TEXT, "
        "sha256 TEXT, lines INTEGER, meta_json TEXT)"
    )
    database.execute(
        "CREATE TABLE edges(src TEXT, dst TEXT, kind TEXT, meta_json TEXT)"
    )
    database.execute(
        "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
        (
            "bulk_downloader/sample.py",
            "module",
            "bulk_downloader/sample.py",
            "bulk_downloader/sample.py",
            "",
            "fixture-sha",
            8,
            "{}",
        ),
    )
    database.execute(
        "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
        (
            "sample::parse",
            "function",
            "bulk_downloader/sample.py",
            "parse",
            "1-4",
            "",
            4,
            parse_meta,
        ),
    )
    database.execute(
        "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
        (
            "sample::emit",
            "function",
            "bulk_downloader/sample.py",
            "emit",
            "6-8",
            "",
            3,
            emit_meta,
        ),
    )
    database.commit()
    database.close()


def _run(
    *,
    graph: Path,
    output: Path,
    coverage: Path | None = FIX / "coverage.json",
    radon: Path | None = FIX / "radon.json",
    catalog: Path | None = FIX / "test_catalog.json",
    check: Path | None = None,
    gate: bool = False,
):
    return run_coverage_map(
        coverage_path=coverage,
        graph_path=graph,
        repo_root=ROOT,
        output_path=output,
        radon_path=radon,
        test_catalog_path=catalog,
        check_path=check,
        gate=gate,
    )


def _cli(*arguments: object, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, arguments)],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_catalog_keeps_same_basename_modules_distinct() -> None:
    module_paths = {
        "package_a/worker.py",
        "package_b/worker.py",
    }
    catalog = _load_test_catalog(
        {
            "mapped": {
                "package_a/worker.py": ["tests/test_package_a_worker.py"],
                "package_b/worker.py": ["tests/test_package_b_worker.py"],
            }
        }
    )

    assert _catalog_evidence(catalog, "package_a/worker.py", module_paths) == [
        "tests/test_package_a_worker.py"
    ]
    assert _catalog_evidence(catalog, "package_b/worker.py", module_paths) == [
        "tests/test_package_b_worker.py"
    ]


def test_catalog_does_not_cross_assign_full_path_evidence() -> None:
    module_paths = {
        "package_a/worker.py",
        "package_b/worker.py",
    }
    catalog = _load_test_catalog(
        {
            "mapped": {
                "package_a/worker.py": ["tests/test_package_a_worker.py"],
            }
        }
    )

    assert _catalog_evidence(catalog, "package_b/worker.py", module_paths) == []


def test_legacy_catalog_basename_requires_unique_graph_module() -> None:
    evidence = ["tests/test_worker.py"]
    catalog = _load_test_catalog({"mapped": {"worker.py": evidence}})

    assert _catalog_evidence(
        catalog,
        "package_a/worker.py",
        {"package_a/worker.py", "package_b/worker.py"},
    ) == []
    assert _catalog_evidence(
        catalog,
        "package_a/worker.py",
        {"package_a/worker.py"},
    ) == evidence


def test_generates_valid_deterministic_function_and_module_coverage(
    tmp_path: Path,
    source_snapshot: TreeSnapshot,
) -> None:
    graph = tmp_path / "graph.db"
    output = tmp_path / "COVERAGE_GAPS.json"
    _graph(graph)

    result = _run(graph=graph, output=output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert result.state is ResultState.ADVISORY
    assert payload["schema_name"] == "bd.coverage-gaps"
    assert payload["schema_version"] == 2
    assert payload["source_sha"] == source_snapshot.source_sha
    assert set(payload["input_hashes"]) == {
        "coverage_json",
        "knowledge_graph",
        "radon_json",
        "test_catalog_json",
    }
    assert payload["input_hashes"]["coverage_json"] == sha256_path(
        FIX / "coverage.json"
    )
    assert payload["input_hashes"]["radon_json"] == sha256_path(
        FIX / "radon.json"
    )
    assert payload["input_hashes"]["test_catalog_json"] == sha256_path(
        FIX / "test_catalog.json"
    )
    assert len(payload["input_hashes"]["knowledge_graph"]) == 64
    assert payload["summary"] == {
        "functions_with_gaps": 2,
        "modules_with_gaps": 1,
    }
    assert [
        (row["path"], row["function"], row["uncovered_fraction"])
        for row in payload["functions"]
    ] == [
        ("bulk_downloader/sample.py", "emit", 0.666667),
        ("bulk_downloader/sample.py", "parse", 0.25),
    ]
    assert payload["functions"][0]["classification"] == "partial"
    assert payload["functions"][0]["sink_count"] == 1
    assert payload["modules"] == [
        {
            "path": "bulk_downloader/sample.py",
            "gap_count": 2,
            "max_uncovered_fraction": 0.666667,
            "risk": {
                "score": 0.85,
                "complexity_max": 4,
                "sink_weight": 2,
                "secret_count": 1,
                "components": {
                    "complexity": 1.0,
                    "sink": 1.0,
                    "secret": 1.0,
                    "taint_proxy": 1.0,
                    "prior_defect": 0.0,
                },
            },
            "test_evidence": ["test_sample.py"],
        }
    ]
    assert "API_TOKEN" not in output.read_text(encoding="utf-8")


def test_missing_coverage_is_unknown_and_gate_is_fail(tmp_path: Path) -> None:
    graph = tmp_path / "graph.db"
    advisory_output = tmp_path / "advisory.json"
    gate_output = tmp_path / "gate.json"
    _graph(graph)

    advisory = _run(
        graph=graph,
        output=advisory_output,
        coverage=None,
        radon=None,
        catalog=None,
    )
    gated = _run(
        graph=graph,
        output=gate_output,
        coverage=None,
        radon=None,
        catalog=None,
        gate=True,
    )

    payload = json.loads(advisory_output.read_text(encoding="utf-8"))
    assert advisory.state is ResultState.UNKNOWN
    assert gated.state is ResultState.FAIL
    assert payload["status"] == "unknown"
    assert payload["functions"] == []
    assert payload["modules"] == []
    assert payload["summary"] == {
        "functions_with_gaps": 0,
        "modules_with_gaps": 0,
    }
    assert artifact_hash(payload) == artifact_hash(
        json.loads(gate_output.read_text(encoding="utf-8"))
    )


def test_check_ignores_only_generation_timestamp_and_writes_after_match(
    tmp_path: Path,
) -> None:
    graph = tmp_path / "graph.db"
    expected = tmp_path / "expected.json"
    output = tmp_path / "output.json"
    _graph(graph)
    first = _run(graph=graph, output=expected, radon=None, catalog=None)
    assert first.state is ResultState.ADVISORY
    original = json.loads(expected.read_text(encoding="utf-8"))
    original["generated_at"] = "2026-07-23T00:00:00Z"
    expected.write_text(json.dumps(original), encoding="utf-8")

    result = _run(
        graph=graph,
        output=output,
        radon=None,
        catalog=None,
        check=expected,
    )

    assert result.state is ResultState.ADVISORY
    assert artifact_hash(original) == artifact_hash(
        json.loads(output.read_text(encoding="utf-8"))
    )


@pytest.mark.parametrize(
    ("field", "replacement", "expected_state"),
    [
        ("source_sha", "f" * 64, ResultState.FAIL),
            (
                "input_hashes",
                {
                    "knowledge_graph": "e" * 64,
                    "coverage_json": sha256_path(FIX / "coverage.json"),
                },
                ResultState.FAIL,
            ),
        (
            "summary",
            {"functions_with_gaps": 99, "modules_with_gaps": 1},
            ResultState.ERROR,
        ),
    ],
)
def test_check_detects_any_non_timestamp_drift_before_replacing_output(
    tmp_path: Path,
    field: str,
    replacement: object,
    expected_state: ResultState,
) -> None:
    graph = tmp_path / "graph.db"
    expected = tmp_path / "expected.json"
    output = tmp_path / "output.json"
    _graph(graph)
    _run(graph=graph, output=expected, radon=None, catalog=None)
    payload = json.loads(expected.read_text(encoding="utf-8"))
    payload[field] = replacement
    expected.write_text(json.dumps(payload), encoding="utf-8")
    preserved = b'{"preserved":true}\n'
    output.write_bytes(preserved)

    result = _run(
        graph=graph,
        output=output,
        radon=None,
        catalog=None,
        check=expected,
    )

    assert result.state is expected_state
    assert output.read_bytes() == preserved
    assert str(expected) not in result.summary
    assert str(expected) not in json.dumps(dict(result.evidence))


@pytest.mark.parametrize(
    "raw",
    [
        '{"files":{},"files":{}}',
        '{"files":{"bulk_downloader/sample.py":{"missing_lines":[NaN]}}}',
        '{"files":{"bulk_downloader/sample.py":{"missing_lines":[Infinity]}}}',
        '{"files":{"bulk_downloader/sample.py":{"missing_lines":[1e9999]}}}',
        '{"files":[]}',
        '{"files":{"../outside.py":{"missing_lines":[1]}}}',
        '{"files":{"bulk_downloader/sample.py":{"missing_lines":[true]}}}',
        '{"files":{"bulk_downloader/sample.py":{"missing_lines":[0]}}}',
    ],
)
def test_invalid_coverage_is_content_free_error_and_preserves_output(
    tmp_path: Path,
    raw: str,
) -> None:
    graph = tmp_path / "graph.db"
    coverage = tmp_path / "coverage.json"
    output = tmp_path / "output.json"
    _graph(graph)
    coverage.write_text(raw, encoding="utf-8")
    preserved = b'{"preserved":true}\n'
    output.write_bytes(preserved)

    result = _run(
        graph=graph,
        output=output,
        coverage=coverage,
        radon=None,
        catalog=None,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "coverage input invalid"
    assert dict(result.evidence) == {"stage": "coverage"}
    assert output.read_bytes() == preserved
    assert raw not in result.summary
    assert raw not in json.dumps(dict(result.evidence))


@pytest.mark.parametrize(
    ("input_name", "raw"),
    [
        ("radon", '{"bulk_downloader/sample.py":[{"complexity":-1}]}'),
        ("radon", '{"bulk_downloader/sample.py":[{"complexity":NaN}]}'),
        ("radon", '{"bulk_downloader/sample.py":[],"bulk_downloader/sample.py":[]}'),
        ("catalog", '{"mapped":{"sample.py":"test_sample.py"}}'),
        ("catalog", '{"mapped":{"sample.py":["../secret.txt"]}}'),
    ],
)
def test_invalid_optional_report_is_controlled_and_preserves_output(
    tmp_path: Path,
    input_name: str,
    raw: str,
) -> None:
    graph = tmp_path / "graph.db"
    report = tmp_path / f"{input_name}.json"
    output = tmp_path / "output.json"
    _graph(graph)
    report.write_text(raw, encoding="utf-8")
    preserved = b'{"preserved":true}\n'
    output.write_bytes(preserved)

    kwargs = {
        "graph": graph,
        "output": output,
        "radon": None,
        "catalog": None,
    }
    kwargs[input_name] = report
    result = _run(**kwargs)

    assert result.state is ResultState.ERROR
    assert result.summary == f"{input_name} input invalid"
    assert dict(result.evidence) == {"stage": input_name}
    assert output.read_bytes() == preserved
    assert raw not in result.summary
    assert raw not in json.dumps(dict(result.evidence))


def test_invalid_sqlite_and_graph_metadata_are_controlled_without_creation(
    tmp_path: Path,
) -> None:
    missing_graph = tmp_path / "missing.db"
    first_output = tmp_path / "first.json"
    first_output.write_text("preserved\n", encoding="utf-8")

    missing = _run(
        graph=missing_graph,
        output=first_output,
        radon=None,
        catalog=None,
    )

    assert missing.state is ResultState.ERROR
    assert missing.summary == "knowledge graph invalid"
    assert not missing_graph.exists()
    assert first_output.read_text(encoding="utf-8") == "preserved\n"

    graph = tmp_path / "graph.db"
    second_output = tmp_path / "second.json"
    _graph(graph, emit_meta='{"sinks":"DO_NOT_DISCLOSE","secrets":[]}')
    second_output.write_text("preserved\n", encoding="utf-8")

    malformed = _run(
        graph=graph,
        output=second_output,
        radon=None,
        catalog=None,
    )

    assert malformed.state is ResultState.ERROR
    assert malformed.summary == "knowledge graph invalid"
    assert dict(malformed.evidence) == {"stage": "graph"}
    assert "DO_NOT_DISCLOSE" not in malformed.summary
    assert "DO_NOT_DISCLOSE" not in json.dumps(dict(malformed.evidence))
    assert second_output.read_text(encoding="utf-8") == "preserved\n"


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"schema_name":"bd.coverage-gaps","schema_name":"other"}',
        '{"measurement":NaN}',
    ],
)
def test_invalid_check_is_controlled_and_preserves_output(
    tmp_path: Path,
    raw: str,
) -> None:
    graph = tmp_path / "graph.db"
    check = tmp_path / "check.json"
    output = tmp_path / "output.json"
    _graph(graph)
    check.write_text(raw, encoding="utf-8")
    preserved = b'{"preserved":true}\n'
    output.write_bytes(preserved)

    result = _run(
        graph=graph,
        output=output,
        radon=None,
        catalog=None,
        check=check,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "check artifact invalid"
    assert dict(result.evidence) == {"stage": "check"}
    assert output.read_bytes() == preserved


def test_build_content_returns_stable_types_and_pure_risk_scores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = tmp_path / "graph.db"
    _graph(graph)

    result, content = build_coverage_content(
        coverage_path=FIX / "coverage.json",
        graph_path=graph,
        repo_root=ROOT,
        radon_path=FIX / "radon.json",
        test_catalog_path=FIX / "test_catalog.json",
    )
    pure = score_from_reports(
        graph_path=graph,
        radon_report=json.loads(
            (FIX / "radon.json").read_text(encoding="utf-8")
        ),
    )
    monkeypatch.setattr(
        "tools.risk_score.radon_cc",
        lambda _radon, _root, _files: {"bulk_downloader/sample.py": 4},
    )
    legacy = score(str(graph), str(ROOT), "unused-radon")

    assert result.state is ResultState.ADVISORY
    assert set(content) == {"status", "functions", "modules", "summary"}
    assert pure["bulk_downloader/sample.py"]["complexity_max"] == 4
    assert pure["bulk_downloader/sample.py"]["secret_count"] == 1
    assert set(legacy["bulk_downloader/sample.py"]) == {
        "risk",
        "max_cc",
        "sink_weight",
        "secrets",
        "lines",
        "prior_defect",
    }


def test_help_and_explicit_root_mode_do_not_depend_on_cwd_repo(
    tmp_path: Path,
) -> None:
    help_run = _cli("--help", cwd=tmp_path)

    assert help_run.returncode == 0
    for option in ("--coverage", "--graph", "--out", "--check", "--gate"):
        assert option in help_run.stdout
    assert "Traceback" not in help_run.stderr

    graph = tmp_path / "graph.db"
    output = tmp_path / "unknown.json"
    _graph(graph)
    explicit = _cli(
        "--root",
        ROOT,
        "--graph",
        graph,
        "--out",
        output,
        "--json",
        cwd=tmp_path,
    )

    assert explicit.returncode == 0
    assert json.loads(explicit.stdout)["state"] == "unknown"
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "unknown"


def test_cli_unknown_gate_and_invalid_input_exit_without_traceback_or_content(
    tmp_path: Path,
) -> None:
    graph = tmp_path / "graph.db"
    output = tmp_path / "unknown.json"
    _graph(graph)
    gated = _cli(
        "--root",
        ROOT,
        "--graph",
        graph,
        "--out",
        output,
        "--gate",
        "--json",
    )
    assert gated.returncode != 0
    assert json.loads(gated.stdout)["state"] == "fail"

    secret = "DO_NOT_DISCLOSE_INPUT_VALUE"
    bad_coverage = tmp_path / "bad.json"
    bad_output = tmp_path / "bad-output.json"
    bad_coverage.write_text(secret, encoding="utf-8")
    invalid = _cli(
        "--root",
        ROOT,
        "--graph",
        graph,
        "--coverage",
        bad_coverage,
        "--out",
        bad_output,
        "--json",
    )
    assert invalid.returncode != 0
    assert json.loads(invalid.stdout)["state"] == "error"
    assert "Traceback" not in invalid.stderr
    assert secret not in invalid.stdout + invalid.stderr
    assert not bad_output.exists()


def test_launcher_is_executable_and_portable_from_outside_repo(
    tmp_path: Path,
) -> None:
    assert os.access(LAUNCHER, os.X_OK)

    help_run = subprocess.run(
        [str(LAUNCHER), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert help_run.returncode == 0
    assert help_run.stdout.startswith("usage: bd-coverage-map")
    assert "--coverage" in help_run.stdout
    assert "Traceback" not in help_run.stderr


@pytest.mark.parametrize("aliased_input", ["coverage", "graph", "radon", "catalog"])
def test_output_must_not_alias_any_analysis_input(
    tmp_path: Path,
    aliased_input: str,
) -> None:
    graph = tmp_path / "graph.db"
    coverage = tmp_path / "coverage.json"
    radon = tmp_path / "radon.json"
    catalog = tmp_path / "catalog.json"
    _graph(graph)
    coverage.write_bytes((FIX / "coverage.json").read_bytes())
    radon.write_bytes((FIX / "radon.json").read_bytes())
    catalog.write_bytes((FIX / "test_catalog.json").read_bytes())
    inputs = {
        "coverage": coverage,
        "graph": graph,
        "radon": radon,
        "catalog": catalog,
    }
    before = {name: path.read_bytes() for name, path in inputs.items()}

    result = run_coverage_map(
        coverage_path=coverage,
        graph_path=graph,
        repo_root=ROOT,
        output_path=inputs[aliased_input],
        radon_path=radon,
        test_catalog_path=catalog,
        check_path=None,
        gate=False,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "coverage artifact path invalid"
    assert dict(result.evidence) == {"stage": "output"}
    assert {
        name: path.read_bytes() for name, path in inputs.items()
    } == before


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_output_link_alias_is_rejected_without_replacing_the_link(
    tmp_path: Path,
    link_kind: str,
) -> None:
    graph = tmp_path / "graph.db"
    coverage = tmp_path / "coverage.json"
    output = tmp_path / "output.json"
    _graph(graph)
    coverage.write_bytes((FIX / "coverage.json").read_bytes())
    if link_kind == "symlink":
        output.symlink_to(coverage)
    else:
        os.link(coverage, output)
    before = coverage.read_bytes()
    before_inode = output.stat().st_ino

    result = _run(
        graph=graph,
        output=output,
        coverage=coverage,
        radon=None,
        catalog=None,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "coverage artifact path invalid"
    assert coverage.read_bytes() == before
    assert output.stat().st_ino == before_inode
    if link_kind == "symlink":
        assert output.is_symlink()
    else:
        assert output.samefile(coverage)


def test_check_and_output_alias_is_rejected_without_update_mode(
    tmp_path: Path,
) -> None:
    graph = tmp_path / "graph.db"
    check = tmp_path / "check.json"
    _graph(graph)
    _run(graph=graph, output=check, radon=None, catalog=None)
    before = check.read_bytes()

    result = _run(
        graph=graph,
        output=check,
        radon=None,
        catalog=None,
        check=check,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "coverage artifact path invalid"
    assert check.read_bytes() == before


def test_each_json_input_is_read_once_and_hashed_from_that_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = tmp_path / "graph.db"
    coverage = tmp_path / "coverage.json"
    radon = tmp_path / "radon.json"
    catalog = tmp_path / "catalog.json"
    output = tmp_path / "output.json"
    _graph(graph)
    coverage.write_bytes((FIX / "coverage.json").read_bytes())
    radon.write_bytes((FIX / "radon.json").read_bytes())
    catalog.write_bytes((FIX / "test_catalog.json").read_bytes())
    originals = {
        path: path.read_bytes() for path in (coverage, radon, catalog)
    }
    counts = {path: 0 for path in originals}
    original_read_bytes = Path.read_bytes

    def counted_read_bytes(path: Path) -> bytes:
        if path in counts:
            counts[path] += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
    result = _run(
        graph=graph,
        output=output,
        coverage=coverage,
        radon=radon,
        catalog=catalog,
    )

    assert result.state is ResultState.ADVISORY
    assert counts == {path: 1 for path in originals}
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["input_hashes"]["coverage_json"] == hashlib.sha256(
        originals[coverage]
    ).hexdigest()
    assert payload["input_hashes"]["radon_json"] == hashlib.sha256(
        originals[radon]
    ).hexdigest()
    assert payload["input_hashes"]["test_catalog_json"] == hashlib.sha256(
        originals[catalog]
    ).hexdigest()


def test_json_input_drift_before_replace_preserves_existing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_snapshot: TreeSnapshot,
) -> None:
    graph = tmp_path / "graph.db"
    coverage = tmp_path / "coverage.json"
    output = tmp_path / "output.json"
    _graph(graph)
    coverage.write_bytes((FIX / "coverage.json").read_bytes())
    preserved = b'{"preserved":true}\n'
    output.write_bytes(preserved)
    calls = 0

    def mutate_on_recheck(_root: Path) -> TreeSnapshot:
        nonlocal calls
        calls += 1
        if calls == 2:
            coverage.write_text('{"files":{}}\n', encoding="utf-8")
        return source_snapshot

    monkeypatch.setattr(
        coverage_service_module,
        "build_snapshot",
        mutate_on_recheck,
    )
    result = _run(
        graph=graph,
        output=output,
        coverage=coverage,
        radon=None,
        catalog=None,
    )

    assert calls == 2
    assert result.state is ResultState.ERROR
    assert result.summary == "coverage input changed during analysis"
    assert dict(result.evidence) == {"stage": "coverage"}
    assert output.read_bytes() == preserved


def test_repository_drift_before_replace_preserves_existing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_snapshot: TreeSnapshot,
) -> None:
    graph = tmp_path / "graph.db"
    output = tmp_path / "output.json"
    _graph(graph)
    preserved = b'{"preserved":true}\n'
    output.write_bytes(preserved)
    changed = TreeSnapshot("f" * 64, source_snapshot.files)
    snapshots = iter((source_snapshot, changed))
    monkeypatch.setattr(
        coverage_service_module,
        "build_snapshot",
        lambda _root: next(snapshots),
    )

    result = _run(graph=graph, output=output, radon=None, catalog=None)

    assert result.state is ResultState.ERROR
    assert result.summary == "repository changed during analysis"
    assert dict(result.evidence) == {"stage": "root"}
    assert output.read_bytes() == preserved


def test_graph_drift_before_replace_preserves_existing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_snapshot: TreeSnapshot,
) -> None:
    graph = tmp_path / "graph.db"
    output = tmp_path / "output.json"
    _graph(graph)
    preserved = b'{"preserved":true}\n'
    output.write_bytes(preserved)
    calls = 0

    def mutate_graph_on_recheck(_root: Path) -> TreeSnapshot:
        nonlocal calls
        calls += 1
        if calls == 2:
            database = sqlite3.connect(graph)
            database.execute(
                "UPDATE nodes SET qualname='emit_changed' "
                "WHERE id='sample::emit'"
            )
            database.commit()
            database.close()
        return source_snapshot

    monkeypatch.setattr(
        coverage_service_module,
        "build_snapshot",
        mutate_graph_on_recheck,
    )
    result = _run(graph=graph, output=output, radon=None, catalog=None)

    assert calls == 2
    assert result.state is ResultState.ERROR
    assert result.summary == "knowledge graph changed during analysis"
    assert dict(result.evidence) == {"stage": "graph"}
    assert output.read_bytes() == preserved


def test_wal_backed_graph_is_analyzed_and_hashed_as_one_snapshot(
    tmp_path: Path,
) -> None:
    graph = tmp_path / "graph.db"
    output = tmp_path / "output.json"
    _graph(graph)
    writer = sqlite3.connect(graph)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    writer.execute(
        "UPDATE nodes SET qualname='emit_from_wal' WHERE id='sample::emit'"
    )
    writer.commit()
    assert Path(f"{graph}-wal").is_file()
    raw_graph_hash = hashlib.sha256(graph.read_bytes()).hexdigest()
    try:
        result = _run(graph=graph, output=output, radon=None, catalog=None)
    finally:
        writer.close()

    assert result.state is ResultState.ADVISORY
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert any(
        function["function"] == "emit_from_wal"
        for function in payload["functions"]
    )
    assert payload["input_hashes"]["knowledge_graph"] != raw_graph_hash


def test_graph_snapshot_and_output_temp_are_cleaned_on_baseexception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = tmp_path / "graph.db"
    output = tmp_path / "output.json"
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    _graph(graph)
    observed_snapshot = False

    def interrupt_replace(_source: object, _destination: object) -> None:
        nonlocal observed_snapshot
        observed_snapshot = any(
            temp_root.rglob("analysis-graph.db")
        )
        raise KeyboardInterrupt

    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    monkeypatch.setattr(
        "tools.code_intelligence.artifacts.os.replace",
        interrupt_replace,
    )
    with pytest.raises(KeyboardInterrupt):
        _run(graph=graph, output=output, radon=None, catalog=None)

    assert observed_snapshot is True
    assert list(temp_root.iterdir()) == []
    assert not output.exists()


def test_payload_duplicate_function_identity_makes_check_malformed(
    tmp_path: Path,
) -> None:
    graph = tmp_path / "graph.db"
    check = tmp_path / "check.json"
    output = tmp_path / "output.json"
    _graph(graph)
    _run(graph=graph, output=check, radon=None, catalog=None)
    payload = json.loads(check.read_text(encoding="utf-8"))
    payload["functions"].insert(0, dict(payload["functions"][0]))
    payload["summary"]["functions_with_gaps"] += 1
    payload["modules"][0]["gap_count"] += 1
    check.write_text(json.dumps(payload), encoding="utf-8")
    preserved = b'{"preserved":true}\n'
    output.write_bytes(preserved)

    result = _run(
        graph=graph,
        output=output,
        radon=None,
        catalog=None,
        check=check,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "check artifact invalid"
    assert output.read_bytes() == preserved


@pytest.mark.parametrize("status", ["measured", "unknown"])
def test_check_provenance_hash_must_match_coverage_status(
    tmp_path: Path,
    status: str,
) -> None:
    graph = tmp_path / "graph.db"
    check = tmp_path / "check.json"
    output = tmp_path / "output.json"
    _graph(graph)
    coverage = FIX / "coverage.json" if status == "measured" else None
    _run(
        graph=graph,
        output=check,
        coverage=coverage,
        radon=None,
        catalog=None,
    )
    payload = json.loads(check.read_text(encoding="utf-8"))
    if status == "measured":
        del payload["input_hashes"]["coverage_json"]
    else:
        payload["input_hashes"]["coverage_json"] = "a" * 64
    check.write_text(json.dumps(payload), encoding="utf-8")
    preserved = b'{"preserved":true}\n'
    output.write_bytes(preserved)

    result = _run(
        graph=graph,
        output=output,
        coverage=coverage,
        radon=None,
        catalog=None,
        check=check,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "check artifact invalid"
    assert output.read_bytes() == preserved


@pytest.mark.parametrize(
    "graph_mutation",
    ["missing_module", "lines_mismatch", "sink_outside_span"],
)
def test_graph_relationship_errors_are_controlled_and_preserve_output(
    tmp_path: Path,
    graph_mutation: str,
) -> None:
    graph = tmp_path / "graph.db"
    output = tmp_path / "output.json"
    _graph(graph)
    database = sqlite3.connect(graph)
    if graph_mutation == "missing_module":
        database.execute("DELETE FROM nodes WHERE kind='module'")
    elif graph_mutation == "lines_mismatch":
        database.execute(
            "UPDATE nodes SET lines=2 WHERE id='sample::parse'"
        )
    else:
        database.execute(
            "UPDATE nodes SET meta_json=? WHERE id='sample::emit'",
            (
                json.dumps(
                    {
                        "sinks": [{"kind": "fetch", "at": 99}],
                        "secrets": [],
                    }
                ),
            ),
        )
    database.commit()
    database.close()
    preserved = b'{"preserved":true}\n'
    output.write_bytes(preserved)

    result = _run(graph=graph, output=output, radon=None, catalog=None)

    assert result.state is ResultState.ERROR
    assert result.summary == "knowledge graph invalid"
    assert dict(result.evidence) == {"stage": "graph"}
    assert output.read_bytes() == preserved


def test_windows_report_paths_normalize_and_match_posix_graph(
    tmp_path: Path,
) -> None:
    graph = tmp_path / "graph.db"
    coverage = tmp_path / "coverage.json"
    radon = tmp_path / "radon.json"
    catalog = tmp_path / "catalog.json"
    output = tmp_path / "output.json"
    _graph(graph)
    coverage.write_text(
        json.dumps(
            {
                "files": {
                    r"bulk_downloader\sample.py": {
                        "missing_lines": [3, 7, 8]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    radon.write_text(
        json.dumps(
            {
                r"bulk_downloader\sample.py": [
                    {"name": "parse", "complexity": 4}
                ]
            }
        ),
        encoding="utf-8",
    )
    catalog.write_text(
        json.dumps(
            {
                "mapped": {
                    r"bulk_downloader\sample.py": [
                        r"tests\test_sample.py"
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = _run(
        graph=graph,
        output=output,
        coverage=coverage,
        radon=radon,
        catalog=catalog,
    )

    assert result.state is ResultState.ADVISORY
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"]["functions_with_gaps"] == 2
    assert payload["modules"][0]["risk"]["complexity_max"] == 4
    assert payload["modules"][0]["test_evidence"] == [
        "tests/test_sample.py"
    ]


def test_report_paths_colliding_after_normalization_are_rejected(
    tmp_path: Path,
) -> None:
    graph = tmp_path / "graph.db"
    coverage = tmp_path / "coverage.json"
    output = tmp_path / "output.json"
    _graph(graph)
    coverage.write_text(
        json.dumps(
            {
                "files": {
                    "bulk_downloader/sample.py": {"missing_lines": [3]},
                    r"bulk_downloader\sample.py": {"missing_lines": [7]},
                }
            }
        ),
        encoding="utf-8",
    )
    preserved = b'{"preserved":true}\n'
    output.write_bytes(preserved)

    result = _run(
        graph=graph,
        output=output,
        coverage=coverage,
        radon=None,
        catalog=None,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "coverage input invalid"
    assert output.read_bytes() == preserved


@pytest.mark.parametrize(
    "unsafe_path",
    [
        r"C:\repo\sample.py",
        r"\\server\share\sample.py",
        "/absolute/sample.py",
        r"..\outside.py",
        "",
        "bulk_downloader/\x01sample.py",
    ],
)
def test_unsafe_report_paths_remain_rejected(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    graph = tmp_path / "graph.db"
    coverage = tmp_path / "coverage.json"
    output = tmp_path / "output.json"
    _graph(graph)
    coverage.write_text(
        json.dumps(
            {"files": {unsafe_path: {"missing_lines": [1]}}}
        ),
        encoding="utf-8",
    )

    result = _run(
        graph=graph,
        output=output,
        coverage=coverage,
        radon=None,
        catalog=None,
    )

    assert result.state is ResultState.ERROR
    assert not output.exists()


@pytest.mark.parametrize(
    ("span", "missing_count", "classification"),
    [
        (20, 17, "partial"),
        (20, 18, "wholly"),
        (100_007, 85_006, "wholly"),
    ],
)
def test_wholly_threshold_uses_exact_integer_comparison(
    tmp_path: Path,
    span: int,
    missing_count: int,
    classification: str,
) -> None:
    graph = tmp_path / "graph.db"
    coverage = tmp_path / "coverage.json"
    output = tmp_path / "output.json"
    database = sqlite3.connect(graph)
    database.execute(
        "CREATE TABLE nodes("
        "id TEXT, kind TEXT, path TEXT, qualname TEXT, span TEXT, "
        "sha256 TEXT, lines INTEGER, meta_json TEXT)"
    )
    database.execute(
        "CREATE TABLE edges(src TEXT, dst TEXT, kind TEXT, meta_json TEXT)"
    )
    database.execute(
        "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
        (
            "sample",
            "module",
            "bulk_downloader/sample.py",
            "bulk_downloader/sample.py",
            "",
            "fixture",
            span,
            "{}",
        ),
    )
    database.execute(
        "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
        (
            "sample::function",
            "function",
            "bulk_downloader/sample.py",
            "function",
            f"1-{span}",
            "",
            span,
            "{}",
        ),
    )
    database.commit()
    database.close()
    coverage.write_text(
        json.dumps(
            {
                "files": {
                    "bulk_downloader/sample.py": {
                        "missing_lines": list(range(1, missing_count + 1))
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = _run(
        graph=graph,
        output=output,
        coverage=coverage,
        radon=None,
        catalog=None,
    )

    assert result.state is ResultState.ADVISORY
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["functions"][0]["classification"] == classification
    if span == 100_007:
        assert payload["functions"][0]["uncovered_fraction"] == 0.85


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_output_must_not_use_potential_graph_sidecar_name(
    tmp_path: Path,
    suffix: str,
) -> None:
    graph = tmp_path / "graph.db"
    output = Path(f"{graph}{suffix}")
    _graph(graph)
    before = graph.read_bytes()
    assert not os.path.lexists(output)

    result = _run(graph=graph, output=output, radon=None, catalog=None)

    assert result.state is ResultState.ERROR
    assert result.summary == "coverage artifact path invalid"
    assert graph.read_bytes() == before
    assert not os.path.lexists(output)


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_output_must_not_alias_live_wal_sidecar(
    tmp_path: Path,
    suffix: str,
) -> None:
    graph = tmp_path / "graph.db"
    _graph(graph)
    writer = sqlite3.connect(graph)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    writer.execute(
        "UPDATE nodes SET qualname='emit_live' WHERE id='sample::emit'"
    )
    writer.commit()
    sidecars = [
        Path(f"{graph}{member}") for member in ("-wal", "-shm")
    ]
    assert all(path.is_file() for path in sidecars)
    before = {path: path.read_bytes() for path in [graph, *sidecars]}
    try:
        result = _run(
            graph=graph,
            output=Path(f"{graph}{suffix}"),
            radon=None,
            catalog=None,
        )
        after = {path: path.read_bytes() for path in [graph, *sidecars]}
    finally:
        writer.close()

    assert result.state is ResultState.ERROR
    assert result.summary == "coverage artifact path invalid"
    assert after == before


def test_output_must_not_alias_live_rollback_journal(
    tmp_path: Path,
) -> None:
    graph = tmp_path / "graph.db"
    journal = Path(f"{graph}-journal")
    _graph(graph)
    writer = sqlite3.connect(graph)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        "UPDATE nodes SET qualname='emit_pending' WHERE id='sample::emit'"
    )
    assert journal.is_file()
    before_graph = graph.read_bytes()
    before_journal = journal.read_bytes()
    try:
        result = _run(
            graph=graph,
            output=journal,
            radon=None,
            catalog=None,
        )
        assert journal.read_bytes() == before_journal
        assert graph.read_bytes() == before_graph
    finally:
        writer.rollback()
        writer.close()

    assert result.state is ResultState.ERROR
    assert result.summary == "coverage artifact path invalid"


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_output_link_must_not_alias_live_wal_sidecar(
    tmp_path: Path,
    link_kind: str,
) -> None:
    graph = tmp_path / "graph.db"
    output = tmp_path / "output.json"
    wal = Path(f"{graph}-wal")
    _graph(graph)
    writer = sqlite3.connect(graph)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    writer.execute(
        "UPDATE nodes SET qualname='emit_live' WHERE id='sample::emit'"
    )
    writer.commit()
    assert wal.is_file()
    if link_kind == "symlink":
        output.symlink_to(wal)
    else:
        os.link(wal, output)
    before = wal.read_bytes()
    before_inode = output.stat().st_ino
    try:
        result = _run(
            graph=graph,
            output=output,
            radon=None,
            catalog=None,
        )
        assert wal.read_bytes() == before
        assert output.stat().st_ino == before_inode
    finally:
        writer.close()

    assert result.state is ResultState.ERROR
    assert result.summary == "coverage artifact path invalid"


@pytest.mark.parametrize("base_name", ["user", "resolved"])
@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_symlinked_graph_reserves_user_and_resolved_sidecar_names(
    tmp_path: Path,
    base_name: str,
    suffix: str,
) -> None:
    target_directory = tmp_path / "target"
    target_directory.mkdir()
    target = target_directory / "graph.db"
    user_graph = tmp_path / "graph-link.db"
    _graph(target)
    user_graph.symlink_to(target)
    bases = {"user": user_graph, "resolved": target}
    output = Path(f"{bases[base_name]}{suffix}")
    before = target.read_bytes()
    assert not os.path.lexists(output)

    result = _run(
        graph=user_graph,
        output=output,
        radon=None,
        catalog=None,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "coverage artifact path invalid"
    assert target.read_bytes() == before
    assert user_graph.is_symlink()
    assert not os.path.lexists(output)


def test_symlinked_graph_target_wal_drift_is_detected_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_snapshot: TreeSnapshot,
) -> None:
    target = tmp_path / "target.db"
    user_graph = tmp_path / "graph-link.db"
    output = tmp_path / "output.json"
    _graph(target)
    user_graph.symlink_to(target)
    writer = sqlite3.connect(target)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    writer.execute(
        "UPDATE nodes SET qualname='emit_wal_1' WHERE id='sample::emit'"
    )
    writer.commit()
    preserved = b'{"preserved":true}\n'
    output.write_bytes(preserved)
    calls = 0

    def mutate_target_wal(_root: Path) -> TreeSnapshot:
        nonlocal calls
        calls += 1
        if calls == 2:
            writer.execute(
                "UPDATE nodes SET qualname='emit_wal_2' "
                "WHERE id='sample::emit'"
            )
            writer.commit()
        return source_snapshot

    monkeypatch.setattr(
        coverage_service_module,
        "build_snapshot",
        mutate_target_wal,
    )
    try:
        result = _run(
            graph=user_graph,
            output=output,
            radon=None,
            catalog=None,
        )
    finally:
        writer.close()

    assert calls == 2
    assert result.state is ResultState.ERROR
    assert result.summary == "knowledge graph changed during analysis"
    assert output.read_bytes() == preserved


def test_graph_sidecar_creation_during_analysis_is_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_snapshot: TreeSnapshot,
) -> None:
    graph = tmp_path / "graph.db"
    output = tmp_path / "output.json"
    _graph(graph)
    preserved = b'{"preserved":true}\n'
    output.write_bytes(preserved)
    writers: list[sqlite3.Connection] = []
    calls = 0

    def create_wal_on_recheck(_root: Path) -> TreeSnapshot:
        nonlocal calls
        calls += 1
        if calls == 2:
            writer = sqlite3.connect(graph)
            assert writer.execute(
                "PRAGMA journal_mode=WAL"
            ).fetchone()[0] == "wal"
            writer.execute(
                "UPDATE nodes SET qualname='emit_wal' "
                "WHERE id='sample::emit'"
            )
            writer.commit()
            writers.append(writer)
        return source_snapshot

    monkeypatch.setattr(
        coverage_service_module,
        "build_snapshot",
        create_wal_on_recheck,
    )
    try:
        result = _run(
            graph=graph,
            output=output,
            radon=None,
            catalog=None,
        )
    finally:
        for writer in writers:
            writer.close()

    assert calls == 2
    assert result.state is ResultState.ERROR
    assert result.summary == "knowledge graph changed during analysis"
    assert output.read_bytes() == preserved


def test_graph_sidecar_disappearance_during_analysis_is_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_snapshot: TreeSnapshot,
) -> None:
    graph = tmp_path / "graph.db"
    output = tmp_path / "output.json"
    _graph(graph)
    writer = sqlite3.connect(graph)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    writer.execute(
        "UPDATE nodes SET qualname='emit_wal' WHERE id='sample::emit'"
    )
    writer.commit()
    assert Path(f"{graph}-wal").is_file()
    preserved = b'{"preserved":true}\n'
    output.write_bytes(preserved)
    calls = 0

    def close_wal_on_recheck(_root: Path) -> TreeSnapshot:
        nonlocal calls
        calls += 1
        if calls == 2:
            writer.close()
        return source_snapshot

    monkeypatch.setattr(
        coverage_service_module,
        "build_snapshot",
        close_wal_on_recheck,
    )
    result = _run(
        graph=graph,
        output=output,
        radon=None,
        catalog=None,
    )

    assert calls == 2
    assert result.state is ResultState.ERROR
    assert result.summary == "knowledge graph changed during analysis"
    assert output.read_bytes() == preserved


def test_graph_symlink_retarget_during_analysis_is_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_snapshot: TreeSnapshot,
) -> None:
    first_target = tmp_path / "first.db"
    second_target = tmp_path / "second.db"
    user_graph = tmp_path / "graph-link.db"
    output = tmp_path / "output.json"
    _graph(first_target)
    _graph(second_target)
    user_graph.symlink_to(first_target)
    preserved = b'{"preserved":true}\n'
    output.write_bytes(preserved)
    calls = 0

    def retarget_on_recheck(_root: Path) -> TreeSnapshot:
        nonlocal calls
        calls += 1
        if calls == 2:
            user_graph.unlink()
            user_graph.symlink_to(second_target)
        return source_snapshot

    monkeypatch.setattr(
        coverage_service_module,
        "build_snapshot",
        retarget_on_recheck,
    )
    result = _run(
        graph=user_graph,
        output=output,
        radon=None,
        catalog=None,
    )

    assert calls == 2
    assert result.state is ResultState.ERROR
    assert result.summary == "knowledge graph changed during analysis"
    assert output.read_bytes() == preserved


def test_symlinked_wal_graph_snapshot_cleans_up_on_baseexception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.db"
    user_graph = tmp_path / "graph-link.db"
    output = tmp_path / "output.json"
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    _graph(target)
    user_graph.symlink_to(target)
    writer = sqlite3.connect(target)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    writer.execute(
        "UPDATE nodes SET qualname='emit_wal' WHERE id='sample::emit'"
    )
    writer.commit()
    observed_snapshot = False

    def interrupt_replace(_source: object, _destination: object) -> None:
        nonlocal observed_snapshot
        observed_snapshot = any(temp_root.rglob("analysis-graph.db"))
        raise KeyboardInterrupt

    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    monkeypatch.setattr(
        "tools.code_intelligence.artifacts.os.replace",
        interrupt_replace,
    )
    try:
        with pytest.raises(KeyboardInterrupt):
            _run(
                graph=user_graph,
                output=output,
                radon=None,
                catalog=None,
            )
    finally:
        writer.close()

    assert observed_snapshot is True
    assert list(temp_root.iterdir()) == []
    assert not output.exists()
