"""Deterministic coverage-gap aggregation for the stable analysis frontend."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import sqlite3
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator, TypeAlias

from .artifacts import artifact_hash, canonical_bytes
from .results import CheckResult, ResultState
from .schemas import SchemaError, make_envelope, validate_envelope
from .snapshot import build_snapshot


JsonValue: TypeAlias = (
    bool | int | float | str | None | list["JsonValue"] | dict[str, "JsonValue"]
)

SCHEMA = "bd.coverage-gaps"
SCHEMA_VERSION = 2
TOOL_VERSION = "1.0.0"

_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_name",
        "schema_version",
        "source_sha",
        "tool_version",
        "input_hashes",
        "generated_at",
        "status",
        "functions",
        "modules",
        "summary",
    }
)
_INPUT_HASH_NAMES = frozenset(
    {
        "coverage_json",
        "knowledge_graph",
        "radon_json",
        "test_catalog_json",
    }
)
_FUNCTION_FIELDS = frozenset(
    {
        "path",
        "function",
        "span",
        "missing_lines",
        "uncovered_fraction",
        "classification",
        "sink_count",
    }
)
_MODULE_FIELDS = frozenset(
    {
        "path",
        "gap_count",
        "max_uncovered_fraction",
        "risk",
        "test_evidence",
    }
)
_RISK_FIELDS = frozenset(
    {
        "score",
        "complexity_max",
        "sink_weight",
        "secret_count",
        "components",
    }
)
_RISK_COMPONENT_FIELDS = frozenset(
    {"complexity", "sink", "secret", "taint_proxy", "prior_defect"}
)
_SUMMARY_FIELDS = frozenset({"functions_with_gaps", "modules_with_gaps"})
_SPAN = re.compile(r"^([1-9][0-9]*)-([1-9][0-9]*)$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_MAX_LINE = 2_147_483_647
_GRAPH_SIDECAR_SUFFIXES = ("", "-wal", "-shm", "-journal")


class _InputError(ValueError):
    """A deliberately content-free input failure."""

    def __init__(self, stage: str):
        super().__init__(stage)
        self.stage = stage


class _DriftError(_InputError):
    """A source changed after its stable analysis read."""


class _AliasError(_InputError):
    """The output aliases one of the immutable inputs."""


@dataclass(frozen=True)
class _PathSignature:
    resolved: str
    link: tuple[int, int, int, int, int, int]
    target: tuple[int, int, int, int, int, int]


@dataclass(frozen=True)
class _JsonInput:
    stage: str
    path: Path
    value: JsonValue
    sha256: str
    signature: _PathSignature


@dataclass(frozen=True)
class _GraphInput:
    source_path: Path
    snapshot_path: Path
    sha256: str
    locations: "_GraphLocations"
    signatures: tuple[tuple[Path, _PathSignature | None], ...]


@dataclass(frozen=True)
class _GraphLocations:
    user_path: Path
    resolved_path: Path
    members: tuple[Path, ...]


def sha256_path(path: Path) -> str:
    """Return the SHA-256 digest of the exact bytes at *path*."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _path_signature(path: Path, stage: str) -> _PathSignature:
    try:
        link = path.lstat()
        target = path.stat()
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise _InputError(stage) from error
    if not stat.S_ISREG(target.st_mode):
        raise _InputError(stage)
    return _PathSignature(
        str(resolved),
        _stat_identity(link),
        _stat_identity(target),
    )


def _verify_path_signature(
    path: Path,
    expected: _PathSignature,
    stage: str,
) -> None:
    try:
        current = _path_signature(path, stage)
    except _InputError as error:
        raise _DriftError(stage) from error
    if current != expected:
        raise _DriftError(stage)


def _graph_locations(path: Path) -> _GraphLocations:
    user_path = Path(os.path.abspath(path))
    user_signature = _path_signature(user_path, "graph")
    resolved_path = Path(user_signature.resolved)
    members: list[Path] = []
    seen: set[str] = set()
    for base in (user_path, resolved_path):
        for suffix in _GRAPH_SIDECAR_SUFFIXES:
            member = Path(f"{base}{suffix}")
            identity = os.path.normcase(os.path.normpath(str(member)))
            if identity not in seen:
                seen.add(identity)
                members.append(member)
    return _GraphLocations(user_path, resolved_path, tuple(members))


def _graph_source_signatures(
    locations: _GraphLocations,
) -> tuple[tuple[Path, _PathSignature | None], ...]:
    records: list[tuple[Path, _PathSignature | None]] = []
    for member in locations.members:
        signature = (
            _path_signature(member, "graph")
            if os.path.lexists(member)
            else None
        )
        records.append((member, signature))
    return tuple(records)


def _verify_graph_signatures(graph: _GraphInput) -> None:
    try:
        locations = _graph_locations(graph.source_path)
        if locations != graph.locations:
            raise _DriftError("graph")
        current = _graph_source_signatures(locations)
    except _InputError as error:
        raise _DriftError("graph") from error
    if _graph_content_signatures(current) != _graph_content_signatures(
        graph.signatures
    ):
        raise _DriftError("graph")


def _graph_content_signatures(
    records: tuple[tuple[Path, _PathSignature | None], ...],
) -> tuple[object, ...]:
    normalized: list[object] = []
    for path, signature in records:
        if signature is None:
            normalized.append((path, None))
        elif str(path).endswith("-shm"):
            normalized.append(
                (
                    path,
                    signature.resolved,
                    signature.link[:4],
                    signature.target[:4],
                )
            )
        else:
            normalized.append((path, signature))
    return tuple(normalized)


def _paths_alias(left: Path, right: Path) -> bool:
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
    except (OSError, RuntimeError) as error:
        raise _AliasError("output") from error
    if os.path.lexists(left) and os.path.lexists(right):
        try:
            return os.path.samefile(left, right)
        except OSError as error:
            raise _AliasError("output") from error
    return False


def _validate_output_separation(
    output_path: Path,
    inputs: Sequence[Path | None],
) -> None:
    for input_path in inputs:
        if input_path is not None and _paths_alias(output_path, input_path):
            raise _AliasError("output")


def _is_exact_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_unit_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


def _is_wholly_uncovered(missing_count: int, span_length: int) -> bool:
    return missing_count * 100 > span_length * 85


def _safe_relative_path(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 4096
        or "\\" in value
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
        or _WINDOWS_DRIVE.match(value)
    ):
        return False
    path = PurePosixPath(value)
    raw_parts = value.split("/")
    return (
        not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in raw_parts)
        and path.as_posix() == value
    )


def _normalize_report_path(value: object, stage: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 4096
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
        or value.startswith(("\\\\", "//"))
        or _WINDOWS_DRIVE.match(value)
    ):
        raise _InputError(stage)
    normalized = value.replace("\\", "/")
    if not _safe_relative_path(normalized):
        raise _InputError(stage)
    return normalized


def _strict_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def _strict_float(raw_value: str) -> float:
    value = float(raw_value)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _strict_json_text(raw: str) -> JsonValue:
    value = json.loads(
        raw,
        parse_float=_strict_float,
        parse_constant=_reject_constant,
        object_pairs_hook=_strict_object,
    )
    json.dumps(value, allow_nan=False)
    return value


def _strict_json_path(path: Path, stage: str) -> _JsonInput:
    try:
        before = _path_signature(path, stage)
        raw = path.read_bytes()
        after = _path_signature(path, stage)
        if before != after:
            raise _DriftError(stage)
        value = _strict_json_text(raw.decode("utf-8"))
    except _DriftError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise _InputError(stage) from error
    return _JsonInput(
        stage,
        path,
        value,
        hashlib.sha256(raw).hexdigest(),
        after,
    )


def _validated_lines(value: object, stage: str) -> list[int]:
    if not isinstance(value, list):
        raise _InputError(stage)
    if any(
        not _is_exact_int(line) or not 1 <= line <= _MAX_LINE
        for line in value
    ):
        raise _InputError(stage)
    if len(value) != len(set(value)):
        raise _InputError(stage)
    return sorted(value)


def _load_coverage(value: JsonValue) -> dict[str, dict[str, JsonValue]]:
    if not isinstance(value, Mapping):
        raise _InputError("coverage")
    files = value.get("files")
    if not isinstance(files, Mapping):
        raise _InputError("coverage")
    result: dict[str, dict[str, JsonValue]] = {}
    for file_path, report in files.items():
        normalized = _normalize_report_path(file_path, "coverage")
        if normalized in result or not isinstance(report, Mapping):
            raise _InputError("coverage")
        missing = _validated_lines(report.get("missing_lines", []), "coverage")
        result[normalized] = {"missing_lines": missing}
    return result


def _load_radon(value: JsonValue | None) -> dict[str, JsonValue] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _InputError("radon")
    result: dict[str, JsonValue] = {}
    for file_path, blocks in value.items():
        normalized = _normalize_report_path(file_path, "radon")
        if normalized in result or not isinstance(blocks, list):
            raise _InputError("radon")
        validated_blocks: list[JsonValue] = []
        for block in blocks:
            if not isinstance(block, Mapping):
                raise _InputError("radon")
            complexity = block.get("complexity", 0)
            if (
                not _is_exact_int(complexity)
                or not 0 <= complexity <= _MAX_LINE
            ):
                raise _InputError("radon")
            validated_blocks.append(dict(block))
        result[normalized] = validated_blocks
    return result


def _load_test_catalog(value: JsonValue | None) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _InputError("catalog")
    mapped = value.get("mapped", {})
    if not isinstance(mapped, Mapping):
        raise _InputError("catalog")
    result: dict[str, list[str]] = {}
    for module_name, evidence in mapped.items():
        normalized_module = _normalize_report_path(module_name, "catalog")
        module_key = PurePosixPath(normalized_module).name
        if module_key in result or not isinstance(evidence, list):
            raise _InputError("catalog")
        normalized_evidence = [
            _normalize_report_path(item, "catalog") for item in evidence
        ]
        if len(normalized_evidence) != len(set(normalized_evidence)):
            raise _InputError("catalog")
        result[module_key] = sorted(normalized_evidence)
    return result


def _sqlite_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise _InputError("graph")
    try:
        uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
        return sqlite3.connect(uri, uri=True)
    except (OSError, ValueError, sqlite3.Error) as error:
        raise _InputError("graph") from error


@contextmanager
def _graph_snapshot(
    path: Path,
    expected_locations: _GraphLocations | None = None,
) -> Iterator[_GraphInput]:
    with tempfile.TemporaryDirectory(prefix=".bd-coverage-map.") as raw_directory:
        snapshot_path = Path(raw_directory) / "analysis-graph.db"
        source: sqlite3.Connection | None = None
        destination: sqlite3.Connection | None = None
        try:
            locations = _graph_locations(path)
            if (
                expected_locations is not None
                and locations != expected_locations
            ):
                raise _DriftError("graph")
            source_uri = locations.resolved_path.as_uri() + "?mode=ro"
            source = sqlite3.connect(source_uri, uri=True)
            destination = sqlite3.connect(snapshot_path)
            source.backup(destination)
            destination.commit()
            destination.close()
            destination = None
        except _DriftError:
            raise
        except (OSError, ValueError, sqlite3.Error) as error:
            raise _InputError("graph") from error
        finally:
            if destination is not None:
                destination.close()
            if source is not None:
                source.close()
        after_locations = _graph_locations(path)
        if after_locations != locations:
            raise _DriftError("graph")
        after = _graph_source_signatures(after_locations)
        graph = _GraphInput(
            path,
            snapshot_path,
            sha256_path(snapshot_path),
            after_locations,
            after,
        )
        yield graph


def _validated_meta(raw: object) -> dict[str, JsonValue]:
    if not isinstance(raw, str):
        raise _InputError("graph")
    try:
        value = _strict_json_text(raw or "{}")
    except (ValueError, json.JSONDecodeError) as error:
        raise _InputError("graph") from error
    if not isinstance(value, Mapping):
        raise _InputError("graph")
    sinks = value.get("sinks", [])
    secrets = value.get("secrets", [])
    if not isinstance(sinks, list) or not isinstance(secrets, list):
        raise _InputError("graph")
    for sink in sinks:
        if not isinstance(sink, Mapping):
            raise _InputError("graph")
        kind = sink.get("kind")
        at = sink.get("at")
        if not isinstance(kind, str) or not kind or kind != kind.strip():
            raise _InputError("graph")
        if at is not None and (
            not _is_exact_int(at) or not 1 <= at <= _MAX_LINE
        ):
            raise _InputError("graph")
    if any(
        not isinstance(secret, str)
        or not secret
        or secret != secret.strip()
        or len(secret) > 4096
        or "\x00" in secret
        for secret in secrets
    ):
        raise _InputError("graph")
    return dict(value)


def _spans(
    graph_path: Path,
) -> list[tuple[str, str, int, int, dict[str, JsonValue]]]:
    database = _sqlite_read_only(graph_path)
    try:
        rows = database.execute(
            "SELECT kind,path,qualname,span,lines,meta_json "
            "FROM nodes WHERE kind IN ('module','function') "
            "ORDER BY kind,path,qualname,span"
        ).fetchall()
    except sqlite3.Error as error:
        raise _InputError("graph") from error
    finally:
        database.close()

    result: list[tuple[str, str, int, int, dict[str, JsonValue]]] = []
    identities: set[tuple[str, str, str]] = set()
    module_paths: set[str] = set()
    function_paths: set[str] = set()
    for kind, file_path, qualname, span, lines, meta_json in rows:
        if (
            kind not in {"module", "function"}
            or not _safe_relative_path(file_path)
            or not _is_exact_int(lines)
            or not 0 <= lines <= _MAX_LINE
        ):
            raise _InputError("graph")
        meta = _validated_meta(meta_json)
        if kind == "module":
            if file_path in module_paths:
                raise _InputError("graph")
            module_paths.add(file_path)
            continue
        if (
            not isinstance(qualname, str)
            or not qualname
            or qualname != qualname.strip()
            or len(qualname) > 4096
            or "\x00" in qualname
            or not isinstance(span, str)
        ):
            raise _InputError("graph")
        match = _SPAN.fullmatch(span)
        if match is None:
            raise _InputError("graph")
        start, end = (int(part) for part in match.groups())
        if end < start or end > _MAX_LINE:
            raise _InputError("graph")
        if lines != end - start + 1:
            raise _InputError("graph")
        sinks = meta.get("sinks", [])
        assert isinstance(sinks, list)
        for sink in sinks:
            assert isinstance(sink, Mapping)
            at = sink.get("at")
            if (
                not _is_exact_int(at)
                or not start <= at <= end
            ):
                raise _InputError("graph")
        identity = (file_path, qualname, span)
        if identity in identities:
            raise _InputError("graph")
        identities.add(identity)
        function_paths.add(file_path)
        result.append((file_path, qualname, start, end, meta))
    if not function_paths.issubset(module_paths):
        raise _InputError("graph")
    return result


def _score_from_reports(
    *,
    graph_path: Path,
    radon_report: Mapping[str, JsonValue] | None,
) -> dict[str, dict[str, JsonValue]]:
    """Resolve the legacy-root scorer without adding a frozen import edge."""
    scorer = getattr(importlib.import_module("tools.risk_score"), "score_from_reports")
    return scorer(graph_path=graph_path, radon_report=radon_report)


def _error_result(
    stage: str,
    *,
    changed: bool = False,
    alias: bool = False,
) -> CheckResult:
    labels = {
        "coverage": "coverage input invalid",
        "radon": "radon input invalid",
        "catalog": "catalog input invalid",
        "graph": "knowledge graph invalid",
        "root": "repository snapshot invalid",
        "check": "check artifact invalid",
        "output": "coverage artifact write failed",
    }
    if changed:
        labels.update(
            {
                "coverage": "coverage input changed during analysis",
                "radon": "radon input changed during analysis",
                "catalog": "catalog input changed during analysis",
                "graph": "knowledge graph changed during analysis",
                "root": "repository changed during analysis",
                "check": "check artifact changed during analysis",
            }
        )
    if alias:
        labels["output"] = "coverage artifact path invalid"
    return CheckResult(
        "bd-coverage-map",
        ResultState.ERROR,
        labels[stage],
        {"stage": stage},
    )


def _build_coverage_content_from_values(
    *,
    coverage_value: JsonValue | None,
    graph_path: Path,
    radon_value: JsonValue | None,
    test_catalog_value: JsonValue | None,
) -> tuple[CheckResult, dict[str, JsonValue]]:
    spans = _spans(graph_path)
    radon_report = _load_radon(radon_value)
    test_catalog = _load_test_catalog(test_catalog_value)
    if coverage_value is None:
        return (
            CheckResult(
                "bd-coverage-map",
                ResultState.UNKNOWN,
                "coverage input absent",
                {"coverage": "absent"},
            ),
            {
                "status": "unknown",
                "functions": [],
                "modules": [],
                "summary": {
                    "functions_with_gaps": 0,
                    "modules_with_gaps": 0,
                },
            },
        )
    coverage = _load_coverage(coverage_value)
    try:
        risk = _score_from_reports(
            graph_path=graph_path,
            radon_report=radon_report,
        )
    except (
        AttributeError,
        ImportError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        sqlite3.Error,
    ) as error:
        raise _InputError("graph") from error
    function_paths = {file_path for file_path, *_rest in spans}
    if not function_paths.issubset(risk):
        raise _InputError("graph")

    functions: list[dict[str, JsonValue]] = []
    for file_path, qualname, start, end, meta in spans:
        file_coverage = coverage.get(file_path)
        if file_coverage is None:
            continue
        missing_report = file_coverage["missing_lines"]
        assert isinstance(missing_report, list)
        missing = [
            line
            for line in missing_report
            if isinstance(line, int) and start <= line <= end
        ]
        if not missing:
            continue
        span_length = end - start + 1
        fraction = len(missing) / span_length
        sinks = meta.get("sinks", [])
        assert isinstance(sinks, list)
        functions.append(
            {
                "path": file_path,
                "function": qualname,
                "span": f"{start}-{end}",
                "missing_lines": missing,
                "uncovered_fraction": round(fraction, 6),
                "classification": (
                    "wholly"
                    if _is_wholly_uncovered(len(missing), span_length)
                    else "partial"
                ),
                "sink_count": len(sinks),
            }
        )
    functions.sort(
        key=lambda row: (
            -float(row["uncovered_fraction"]),
            str(row["path"]),
            str(row["function"]),
            str(row["span"]),
        )
    )

    by_module: dict[str, dict[str, JsonValue]] = {}
    for function in functions:
        file_path = str(function["path"])
        module_name = PurePosixPath(file_path).name
        record = by_module.setdefault(
            file_path,
            {
                "path": file_path,
                "gap_count": 0,
                "max_uncovered_fraction": 0.0,
                "risk": risk[file_path],
                "test_evidence": test_catalog.get(module_name, []),
            },
        )
        record["gap_count"] = int(record["gap_count"]) + 1
        record["max_uncovered_fraction"] = max(
            float(record["max_uncovered_fraction"]),
            float(function["uncovered_fraction"]),
        )

    content: dict[str, JsonValue] = {
        "status": "measured",
        "functions": functions,
        "modules": [by_module[key] for key in sorted(by_module)],
        "summary": {
            "functions_with_gaps": len(functions),
            "modules_with_gaps": len(by_module),
        },
    }
    state = ResultState.ADVISORY if functions else ResultState.PASS
    return (
        CheckResult(
            "bd-coverage-map",
            state,
            f"{len(functions)} function coverage gaps",
            dict(content["summary"]),
        ),
        content,
    )


def build_coverage_content(
    *,
    coverage_path: Path | None,
    graph_path: Path,
    repo_root: Path,
    radon_path: Path | None = None,
    test_catalog_path: Path | None = None,
) -> tuple[CheckResult, dict[str, JsonValue]]:
    """Build deterministic coverage content without writing an artifact."""
    del repo_root  # Source binding is applied by run_coverage_map.
    try:
        coverage = (
            _strict_json_path(coverage_path, "coverage")
            if coverage_path is not None
            else None
        )
        radon = (
            _strict_json_path(radon_path, "radon")
            if radon_path is not None
            else None
        )
        catalog = (
            _strict_json_path(test_catalog_path, "catalog")
            if test_catalog_path is not None
            else None
        )
        with _graph_snapshot(graph_path) as graph:
            return _build_coverage_content_from_values(
                coverage_value=coverage.value if coverage else None,
                graph_path=graph.snapshot_path,
                radon_value=radon.value if radon else None,
                test_catalog_value=catalog.value if catalog else None,
            )
    except _DriftError as error:
        return _error_result(error.stage, changed=True), {}
    except _InputError as error:
        return _error_result(error.stage), {}


def _validate_risk(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != _RISK_FIELDS:
        raise SchemaError("coverage risk fields are invalid")
    if not _is_unit_number(value["score"]):
        raise SchemaError("coverage risk score must be between 0 and 1")
    for field in ("complexity_max", "sink_weight", "secret_count"):
        if not _is_exact_int(value[field]) or value[field] < 0:
            raise SchemaError(f"coverage risk {field} must be non-negative")
    components = value["components"]
    if (
        not isinstance(components, Mapping)
        or set(components) != _RISK_COMPONENT_FIELDS
        or any(not _is_unit_number(item) for item in components.values())
    ):
        raise SchemaError("coverage risk components are invalid")


def _validate_function(value: object) -> tuple[float, str, str, str]:
    if not isinstance(value, Mapping) or set(value) != _FUNCTION_FIELDS:
        raise SchemaError("coverage function fields are invalid")
    if not _safe_relative_path(value["path"]):
        raise SchemaError("coverage function path is invalid")
    function = value["function"]
    if (
        not isinstance(function, str)
        or not function
        or function != function.strip()
        or "\x00" in function
    ):
        raise SchemaError("coverage function name is invalid")
    span = value["span"]
    if not isinstance(span, str):
        raise SchemaError("coverage function span is invalid")
    match = _SPAN.fullmatch(span)
    if match is None:
        raise SchemaError("coverage function span is invalid")
    start, end = (int(part) for part in match.groups())
    if end < start or end > _MAX_LINE:
        raise SchemaError("coverage function span is invalid")
    missing = value["missing_lines"]
    if (
        not isinstance(missing, list)
        or not missing
        or missing != sorted(set(missing))
        or any(
            not _is_exact_int(line) or not start <= line <= end
            for line in missing
        )
    ):
        raise SchemaError("coverage function missing lines are invalid")
    fraction = value["uncovered_fraction"]
    if not _is_unit_number(fraction) or fraction == 0:
        raise SchemaError("coverage function fraction is invalid")
    expected_fraction = round(len(missing) / (end - start + 1), 6)
    if fraction != expected_fraction:
        raise SchemaError("coverage function fraction is inconsistent")
    classification = value["classification"]
    if classification not in {"partial", "wholly"}:
        raise SchemaError("coverage function classification is invalid")
    expected_classification = (
        "wholly"
        if _is_wholly_uncovered(len(missing), end - start + 1)
        else "partial"
    )
    if classification != expected_classification:
        raise SchemaError("coverage function classification is inconsistent")
    sink_count = value["sink_count"]
    if not _is_exact_int(sink_count) or sink_count < 0:
        raise SchemaError("coverage function sink count is invalid")
    return float(fraction), str(value["path"]), function, span


def _validate_payload(value: object) -> None:
    validate_envelope(value, SCHEMA, SCHEMA_VERSION)
    if not isinstance(value, Mapping) or set(value) != _TOP_LEVEL_FIELDS:
        raise SchemaError("coverage artifact fields are invalid")
    input_hashes = value["input_hashes"]
    assert isinstance(input_hashes, Mapping)
    if (
        "knowledge_graph" not in input_hashes
        or not set(input_hashes).issubset(_INPUT_HASH_NAMES)
    ):
        raise SchemaError("coverage input hashes are invalid")
    status = value["status"]
    functions = value["functions"]
    modules = value["modules"]
    summary = value["summary"]
    if status not in {"measured", "unknown"}:
        raise SchemaError("coverage status is invalid")
    if not isinstance(functions, list) or not isinstance(modules, list):
        raise SchemaError("coverage collections are invalid")
    function_order = [_validate_function(function) for function in functions]
    function_identities = [
        (file_path, function, span)
        for _fraction, file_path, function, span in function_order
    ]
    if len(function_identities) != len(set(function_identities)):
        raise SchemaError("coverage function identities must be unique")
    expected_function_order = sorted(
        function_order,
        key=lambda item: (-item[0], item[1], item[2], item[3]),
    )
    if function_order != expected_function_order:
        raise SchemaError("coverage functions are not deterministic")
    function_fractions: dict[str, list[float]] = {}
    for fraction, file_path, _function, _span in function_order:
        function_fractions.setdefault(file_path, []).append(fraction)
    module_paths: list[str] = []
    for module in modules:
        if not isinstance(module, Mapping) or set(module) != _MODULE_FIELDS:
            raise SchemaError("coverage module fields are invalid")
        module_path = module["path"]
        if not _safe_relative_path(module_path):
            raise SchemaError("coverage module path is invalid")
        module_paths.append(module_path)
        gap_count = module["gap_count"]
        if not _is_exact_int(gap_count) or gap_count < 1:
            raise SchemaError("coverage module gap count is invalid")
        if not _is_unit_number(module["max_uncovered_fraction"]):
            raise SchemaError("coverage module fraction is invalid")
        fractions = function_fractions.get(module_path, [])
        if (
            gap_count != len(fractions)
            or not fractions
            or module["max_uncovered_fraction"] != max(fractions)
        ):
            raise SchemaError("coverage module summary is inconsistent")
        _validate_risk(module["risk"])
        evidence = module["test_evidence"]
        if (
            not isinstance(evidence, list)
            or evidence != sorted(set(evidence))
            or any(not _safe_relative_path(item) for item in evidence)
        ):
            raise SchemaError("coverage test evidence is invalid")
    if module_paths != sorted(module_paths) or len(module_paths) != len(
        set(module_paths)
    ):
        raise SchemaError("coverage modules are not deterministic")
    if set(module_paths) != set(function_fractions):
        raise SchemaError("coverage modules do not match coverage functions")
    if not isinstance(summary, Mapping) or set(summary) != _SUMMARY_FIELDS:
        raise SchemaError("coverage summary fields are invalid")
    if any(
        not _is_exact_int(summary[field]) or summary[field] < 0
        for field in _SUMMARY_FIELDS
    ):
        raise SchemaError("coverage summary counts are invalid")
    if (
        summary["functions_with_gaps"] != len(functions)
        or summary["modules_with_gaps"] != len(modules)
    ):
        raise SchemaError("coverage summary counts are inconsistent")
    if status == "unknown" and (
        functions
        or modules
        or summary["functions_with_gaps"] != 0
        or summary["modules_with_gaps"] != 0
    ):
        raise SchemaError("unknown coverage content must be empty")
    has_coverage_hash = "coverage_json" in input_hashes
    if (
        (status == "measured" and not has_coverage_hash)
        or (status == "unknown" and has_coverage_hash)
    ):
        raise SchemaError("coverage status provenance is inconsistent")


def _atomic_write_json_checked(
    path: Path,
    value: object,
    validator: Callable[[object], None],
    before_replace: Callable[[], None],
) -> None:
    validator(value)
    payload = canonical_bytes(value, omit_keys=frozenset())
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        before_replace()
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def run_coverage_map(
    *,
    coverage_path: Path | None,
    graph_path: Path,
    repo_root: Path,
    output_path: Path,
    radon_path: Path | None,
    test_catalog_path: Path | None,
    check_path: Path | None,
    gate: bool,
) -> CheckResult:
    """Build, optionally check, and atomically write a coverage-gap artifact."""
    non_graph_inputs = (
        coverage_path,
        radon_path,
        test_catalog_path,
        check_path,
    )
    try:
        initial_graph_locations = _graph_locations(graph_path)
        alias_inputs = (
            *initial_graph_locations.members,
            *non_graph_inputs,
        )
        _validate_output_separation(output_path, alias_inputs)
    except _AliasError:
        return _error_result("output", alias=True)
    except _InputError:
        return _error_result("graph")
    try:
        snapshot = build_snapshot(repo_root)
    except (OSError, ValueError, subprocess.SubprocessError):
        return _error_result("root")

    try:
        coverage = (
            _strict_json_path(coverage_path, "coverage")
            if coverage_path is not None
            else None
        )
        radon = (
            _strict_json_path(radon_path, "radon")
            if radon_path is not None
            else None
        )
        catalog = (
            _strict_json_path(test_catalog_path, "catalog")
            if test_catalog_path is not None
            else None
        )
        check = (
            _strict_json_path(check_path, "check")
            if check_path is not None
            else None
        )
        json_inputs = tuple(
            report
            for report in (coverage, radon, catalog, check)
            if report is not None
        )
        with _graph_snapshot(
            graph_path,
            expected_locations=initial_graph_locations,
        ) as graph:
            result, content = _build_coverage_content_from_values(
                coverage_value=coverage.value if coverage else None,
                graph_path=graph.snapshot_path,
                radon_value=radon.value if radon else None,
                test_catalog_value=catalog.value if catalog else None,
            )
            input_hashes = {"knowledge_graph": graph.sha256}
            if coverage is not None:
                input_hashes["coverage_json"] = coverage.sha256
            if radon is not None:
                input_hashes["radon_json"] = radon.sha256
            if catalog is not None:
                input_hashes["test_catalog_json"] = catalog.sha256
            payload: dict[str, object] = {
                **make_envelope(
                    SCHEMA,
                    SCHEMA_VERSION,
                    snapshot.source_sha,
                    TOOL_VERSION,
                    input_hashes,
                ),
                **content,
            }
            try:
                _validate_payload(payload)
            except (SchemaError, TypeError, ValueError) as error:
                raise _InputError("output") from error

            if check is not None:
                try:
                    _validate_payload(check.value)
                except (SchemaError, TypeError, ValueError) as error:
                    raise _InputError("check") from error
                if artifact_hash(check.value) != artifact_hash(payload):
                    return CheckResult(
                        "bd-coverage-map",
                        ResultState.FAIL,
                        "coverage artifact drift",
                        {"check": "different"},
                    )

            def verify_unchanged() -> None:
                try:
                    current_snapshot = build_snapshot(repo_root)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    raise _DriftError("root") from error
                if current_snapshot != snapshot:
                    raise _DriftError("root")
                for report in json_inputs:
                    _verify_path_signature(
                        report.path,
                        report.signature,
                        report.stage,
                    )
                _verify_graph_signatures(graph)
                _validate_output_separation(
                    output_path,
                    (*graph.locations.members, *non_graph_inputs),
                )

            _atomic_write_json_checked(
                output_path,
                payload,
                _validate_payload,
                verify_unchanged,
            )
    except _AliasError:
        return _error_result("output", alias=True)
    except _DriftError as error:
        return _error_result(error.stage, changed=True)
    except _InputError as error:
        return _error_result(error.stage)
    except (OSError, SchemaError, TypeError, ValueError):
        return _error_result("output")
    if result.state is ResultState.UNKNOWN and gate:
        return CheckResult(
            "bd-coverage-map",
            ResultState.FAIL,
            "coverage required in gate mode",
            {"coverage": "absent"},
        )
    return result
