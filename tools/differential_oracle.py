#!/usr/bin/env python3
"""Run bounded typed differential-oracle adapters."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.code_intelligence.adapters import (  # noqa: E402
    AdapterBudget,
    AdapterCase,
    AdapterContext,
    get_adapter,
)
from tools.code_intelligence.artifacts import (  # noqa: E402
    atomic_write_json,
    canonical_bytes,
)
from tools.code_intelligence.oracle_adapters import (  # noqa: E402
    register_builtin_oracles,
)
from tools.code_intelligence.oracle_service import (  # noqa: E402
    run_oracle_adapter,
)
from tools.code_intelligence.paths import discover_repo_root  # noqa: E402
from tools.code_intelligence.results import (  # noqa: E402
    CheckResult,
    ResultState,
    exit_code,
)
from tools.code_intelligence.schemas import (  # noqa: E402
    SchemaError,
    make_envelope,
    validate_envelope,
)
from tools.code_intelligence.snapshot import build_snapshot  # noqa: E402


SCHEMA = "bd.differential-oracle"
SCHEMA_VERSION = 1
TOOL_VERSION = "1.0.0"
_MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
_MAX_HASHED_INPUT_BYTES = 64 * 1024 * 1024
_ENVELOPE_FIELDS = frozenset(
    {
        "schema_name",
        "schema_version",
        "source_sha",
        "tool_version",
        "input_hashes",
        "generated_at",
    }
)
_ARTIFACT_FIELDS = _ENVELOPE_FIELDS | frozenset(
    {"seed", "budget", "results", "summary"}
)
_RESULT_FIELDS = frozenset(
    {"name", "state", "summary", "evidence", "comparisons"}
)
_COMPARISON_FIELDS = frozenset(
    {
        "case_id",
        "left",
        "right",
        "normalized_left",
        "normalized_right",
        "equal",
        "allowed",
        "reason",
    }
)


class _CliError(ValueError):
    pass


def _exact_int(value: object) -> bool:
    return type(value) is int


def _safe_corpus(root: Path, raw: Path | None) -> Path | None:
    if raw is None:
        return None
    candidate = raw if raw.is_absolute() else root / raw
    current = Path(os.path.abspath(candidate))
    while True:
        if current.is_symlink():
            raise _CliError("corpus path invalid")
        if current == root:
            break
        if current.parent == current:
            raise _CliError("corpus path invalid")
        current = current.parent
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise _CliError("corpus path invalid") from error
    if not resolved.is_dir():
        raise _CliError("corpus path invalid")
    return resolved


def _corpus_hash(
    corpus: Path | None,
    max_cases: int,
) -> str | None:
    if corpus is None:
        return None
    digest = hashlib.sha256()
    files = [path for path in sorted(corpus.rglob("*")) if path.is_file()]
    if len(files) > max_cases:
        raise _CliError("corpus exceeded max_cases")
    total = 0
    for path in files:
        if path.is_symlink():
            raise _CliError("corpus input invalid")
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise _CliError("corpus input invalid")
        total += metadata.st_size
        if total > _MAX_HASHED_INPUT_BYTES:
            raise _CliError("corpus input budget exceeded")
        raw = path.read_bytes()
        after = path.stat()
        if (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise _CliError("corpus input changed while hashing")
        relative = path.relative_to(corpus).as_posix()
        digest.update(relative.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(raw).digest())
        digest.update(b"\n")
    return digest.hexdigest()


def _execution_hash(
    names: Sequence[str],
    args: argparse.Namespace,
    corpus_digest: str | None,
) -> str:
    manifest = {
        "adapters": list(names),
        "seed": args.seed,
        "timeout_seconds": args.timeout,
        "max_cases": args.max_cases,
        "max_output_bytes": args.max_output_bytes,
        "corpus_tree": corpus_digest,
    }
    return hashlib.sha256(
        canonical_bytes(manifest, omit_keys=frozenset())
    ).hexdigest()


def validate_oracle_artifact(value: object) -> None:
    """Strictly validate the durable differential-oracle schema."""
    validate_envelope(value, SCHEMA, SCHEMA_VERSION)
    if not isinstance(value, Mapping) or set(value) != _ARTIFACT_FIELDS:
        raise SchemaError("differential-oracle artifact fields are invalid")
    if not _exact_int(value["seed"]):
        raise SchemaError("differential-oracle seed must be an integer")
    budget = value["budget"]
    if (
        type(budget) is not dict
        or set(budget)
        != {"timeout_seconds", "max_cases", "max_output_bytes"}
    ):
        raise SchemaError("differential-oracle budget is invalid")
    try:
        AdapterBudget(
            budget["timeout_seconds"],
            budget["max_cases"],
            budget["max_output_bytes"],
        )
    except (TypeError, ValueError) as error:
        raise SchemaError("differential-oracle budget is invalid") from error

    results = value["results"]
    if type(results) is not list:
        raise SchemaError("differential-oracle results must be a list")
    names: list[str] = []
    for raw_result in results:
        if (
            type(raw_result) is not dict
            or set(raw_result) != _RESULT_FIELDS
        ):
            raise SchemaError("differential-oracle result is invalid")
        name = raw_result["name"]
        state = raw_result["state"]
        summary = raw_result["summary"]
        evidence = raw_result["evidence"]
        comparisons = raw_result["comparisons"]
        if (
            type(name) is not str
            or type(state) is not str
            or type(summary) is not str
            or not summary
            or len(summary) > 4096
            or not summary.isprintable()
            or type(evidence) is not dict
            or set(evidence) != {"comparisons", "forbidden"}
            or type(evidence["comparisons"]) is not int
            or evidence["comparisons"] < 0
            or type(evidence["forbidden"]) is not int
            or evidence["forbidden"] < 0
            or type(comparisons) is not list
        ):
            raise SchemaError("differential-oracle result is invalid")
        try:
            AdapterCase(name, {"summary": summary})
        except (TypeError, ValueError) as error:
            raise SchemaError(
                "differential-oracle result is invalid"
            ) from error
        try:
            ResultState(state)
        except ValueError as error:
            raise SchemaError(
                "differential-oracle result state is invalid"
            ) from error
        names.append(name)
        case_ids: list[str] = []
        for comparison in comparisons:
            if (
                type(comparison) is not dict
                or set(comparison) != _COMPARISON_FIELDS
            ):
                raise SchemaError(
                    "differential-oracle comparison is invalid"
                )
            case_id = comparison["case_id"]
            if (
                type(case_id) is not str
                or type(comparison["equal"]) is not bool
                or type(comparison["allowed"]) is not bool
                or type(comparison["reason"]) is not str
                or not comparison["reason"]
                or len(comparison["reason"]) > 4096
                or not comparison["reason"].isprintable()
            ):
                raise SchemaError(
                    "differential-oracle comparison is invalid"
                )
            try:
                AdapterCase(
                    case_id,
                    {
                        key: comparison[key]
                        for key in (
                            "left",
                            "right",
                            "normalized_left",
                            "normalized_right",
                            "equal",
                            "allowed",
                            "reason",
                        )
                    },
                )
            except (TypeError, ValueError) as error:
                raise SchemaError(
                    "differential-oracle comparison is invalid"
                ) from error
            case_ids.append(case_id)
        if case_ids != sorted(set(case_ids)):
            raise SchemaError(
                "differential-oracle comparisons must be deterministic"
            )
        if evidence["comparisons"] != len(comparisons):
            raise SchemaError(
                "differential-oracle comparison count is inconsistent"
            )
        forbidden = sum(
            not row["equal"] and not row["allowed"]
            for row in comparisons
        )
        if evidence["forbidden"] != forbidden:
            raise SchemaError(
                "differential-oracle forbidden count is inconsistent"
            )
    if names != sorted(set(names)):
        raise SchemaError(
            "differential-oracle results must be deterministic"
        )

    summary = value["summary"]
    expected_states = {state.value for state in ResultState}
    if (
        type(summary) is not dict
        or set(summary) != expected_states | {"total"}
        or type(summary["total"]) is not int
        or summary["total"] != len(results)
        or any(
            type(summary[state]) is not int or summary[state] < 0
            for state in expected_states
        )
    ):
        raise SchemaError("differential-oracle summary is invalid")
    observed = {
        state: sum(result["state"] == state for result in results)
        for state in expected_states
    }
    if any(summary[state] != observed[state] for state in expected_states):
        raise SchemaError("differential-oracle summary is inconsistent")


def _load_check(path: Path) -> dict[str, object]:
    if path.is_symlink():
        raise _CliError("check artifact invalid")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_ARTIFACT_BYTES:
        raise _CliError("check artifact invalid")
    raw = path.read_bytes()
    after = path.stat()
    if (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) or len(raw) != metadata.st_size:
        raise _CliError("check artifact invalid")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in items:
            if key in value:
                raise _CliError("check artifact invalid")
            value[key] = item
        return value

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("check artifact invalid")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise _CliError("check artifact invalid") from error
    if type(value) is not dict:
        raise _CliError("check artifact invalid")
    validate_oracle_artifact(value)
    return value


def _artifact(
    source_sha: str,
    input_hashes: Mapping[str, str],
    args: argparse.Namespace,
    completed: Sequence[
        tuple[CheckResult, tuple[object, ...]]
    ],
) -> dict[str, object]:
    value = make_envelope(
        SCHEMA,
        SCHEMA_VERSION,
        source_sha,
        TOOL_VERSION,
        input_hashes,
    )
    results = []
    for result, comparisons in completed:
        comparison_rows = sorted(
            (asdict(comparison) for comparison in comparisons),
            key=lambda row: row["case_id"],
        )
        results.append(
            {
                "name": result.name,
                "state": result.state.value,
                "summary": result.summary,
                "evidence": {
                    "comparisons": len(comparison_rows),
                    "forbidden": sum(
                        not row["equal"] and not row["allowed"]
                        for row in comparison_rows
                    ),
                },
                "comparisons": comparison_rows,
            }
        )
    results.sort(key=lambda row: row["name"])
    state_counts = {
        state.value: sum(
            result["state"] == state.value for result in results
        )
        for state in ResultState
    }
    value.update(
        {
            "seed": args.seed,
            "budget": {
                "timeout_seconds": args.timeout,
                "max_cases": args.max_cases,
                "max_output_bytes": args.max_output_bytes,
            },
            "results": results,
            "summary": {"total": len(results), **state_counts},
        }
    )
    validate_oracle_artifact(value)
    return value


def run_oracle_cli(args: argparse.Namespace) -> int:
    """Execute selected adapters and write or strictly check one artifact."""
    try:
        if args.root is None:
            raise _CliError("--root is required")
        root = discover_repo_root(args.root)
        corpus = _safe_corpus(root, args.corpus)
        budget = AdapterBudget(
            args.timeout,
            args.max_cases,
            args.max_output_bytes,
        )
        names = tuple(sorted(set(args.adapters or ())))
        if len(names) != len(args.adapters or ()):
            raise _CliError("duplicate adapter selection")
        expected = _load_check(args.check) if args.check is not None else None
        output_path = Path(os.path.abspath(args.out))
        if args.check is not None:
            check_path = args.check.resolve(strict=True)
            if output_path == check_path or (
                output_path.exists()
                and os.path.samefile(output_path, check_path)
            ):
                raise _CliError("output aliases check artifact")
        if corpus is not None:
            try:
                output_path.relative_to(corpus)
            except ValueError:
                pass
            else:
                raise _CliError("output aliases corpus")
        adapters = [get_adapter(name) for name in names]
        snapshot = build_snapshot(root)
        hashes = {"tracked_tree": snapshot.source_sha}
        corpus_digest = _corpus_hash(corpus, budget.max_cases)
        if corpus_digest is not None:
            hashes["corpus_tree"] = corpus_digest
        hashes["execution_manifest"] = _execution_hash(
            names,
            args,
            corpus_digest,
        )
        context = AdapterContext(
            root,
            args.out.parent.resolve(),
            corpus if corpus is not None else root,
            args.seed,
            budget,
        )
        completed = [
            run_oracle_adapter(adapter, context)
            for adapter in adapters
        ]
        artifact = _artifact(
            snapshot.source_sha,
            hashes,
            args,
            completed,
        )
        if build_snapshot(root).source_sha != snapshot.source_sha:
            raise _CliError("source changed during oracle run")
        if _corpus_hash(corpus, budget.max_cases) != corpus_digest:
            raise _CliError("corpus changed during oracle run")

        if args.check is not None:
            assert expected is not None
            if canonical_bytes(_load_check(args.check)) != canonical_bytes(
                expected
            ):
                raise _CliError("check artifact changed during oracle run")
            if canonical_bytes(expected) != canonical_bytes(artifact):
                print("differential-oracle check differs")
                return 1
            print("differential-oracle check matches")
        else:
            atomic_write_json(
                args.out,
                artifact,
                validate_oracle_artifact,
            )
        if args.json:
            sys.stdout.buffer.write(
                canonical_bytes(artifact, omit_keys=frozenset())
            )
        return exit_code(
            (result for result, _rows in completed),
            args.gate,
        )
    except (
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        SchemaError,
        subprocess.SubprocessError,
    ):
        print("differential-oracle failed")
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="differential_oracle.py")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--adapter", action="append", dest="adapters")
    parser.add_argument("--list-adapters", action="store_true")
    parser.add_argument("--corpus", type=Path, required=False)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-cases", type=int, default=1000)
    parser.add_argument("--max-output-bytes", type=int, default=1048576)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("DIFFERENTIAL_ORACLE.json"),
    )
    parser.add_argument("--check", type=Path)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    names = register_builtin_oracles()
    if args.list_adapters:
        print("\n".join(names))
        return 0
    if not args.adapters:
        parser.error("at least one --adapter is required")
    return run_oracle_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
