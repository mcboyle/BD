"""Command-line frontend for deterministic, secret-safe fuzz replay."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sys
import re
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.code_intelligence.adapters import (
    AdapterBudget,
    AdapterCase,
    AdapterContext,
    get_adapter,
)
from tools.code_intelligence.artifacts import atomic_write_json, canonical_bytes
from tools.code_intelligence.fuzz_adapters import BUILTIN_FUZZ_COMMANDS, register_builtin_fuzzers
from tools.code_intelligence.fuzz_service import (
    FuzzFinding,
    _validate_hypothesis_runtime,
    hypothesis_result_name,
    load_corpus,
    run_fuzz_adapter,
    run_hypothesis_adapter,
)
from tools.code_intelligence.paths import discover_repo_root
from tools.code_intelligence.results import CheckResult, ResultState, exit_code
from tools.code_intelligence.schemas import make_envelope, validate_envelope
from tools.code_intelligence.snapshot import build_snapshot


_SCHEMA = "bd.fuzz-results"
_VERSION = 1
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")
_SAFE_HASH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,511}$")
_MAX_SUMMARY = 4096
_RESULT_STATES = frozenset(state.value for state in ResultState)
_FINDING_STATES = frozenset({"fail", "timeout", "error"})


def _paths_alias(first: Path, second: Path) -> bool:
    if first.resolve() == second.resolve():
        return True
    try:
        return first.samefile(second)
    except OSError:
        return False


def _finding_record(finding: FuzzFinding) -> dict[str, object]:
    return {
        "adapter": finding.adapter,
        "case_id": finding.case_id,
        "state": finding.state,
        "fingerprint": finding.fingerprint,
        "summary": finding.summary,
        "reproducer": finding.reproducer,
    }


def _valid_summary(value: object) -> bool:
    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_SUMMARY
        or not value.isprintable()
    ):
        return False
    try:
        AdapterCase("artifact-summary", {"summary": value})
    except (TypeError, ValueError):
        return False
    return True


def _contains_generated_at(value: object) -> bool:
    stack = [value]
    while stack:
        current = stack.pop()
        if type(current) is dict:
            if "generated_at" in current:
                return True
            stack.extend(current.values())
        elif type(current) is list:
            stack.extend(current)
    return False


def _valid_reproducer(
    value: object,
    adapter: str,
    case_id: str,
    fingerprint: str,
) -> bool:
    if value is None:
        return True
    if (
        type(value) is not str
        or not value
        or len(value) > 4096
        or not value.isprintable()
        or "\\" in value
    ):
        return False
    path = PurePosixPath(value)
    parts = value.split("/")
    expected_name = f"{adapter}--{case_id}--{fingerprint[:12]}.json"
    return (
        not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in parts)
        and path.name == expected_name
    )


def _validate_artifact(value: object) -> None:
    try:
        if type(value) is not dict or set(value) != {
            "schema_name",
            "schema_version",
            "source_sha",
            "tool_version",
            "input_hashes",
            "generated_at",
            "seed",
            "results",
            "findings",
        }:
            raise ValueError
        validate_envelope(value, _SCHEMA, _VERSION)
        if (
            type(value["seed"]) is not int
            or type(value["input_hashes"]) is not dict
            or not value["input_hashes"]
            or type(value["results"]) is not list
            or not value["results"]
            or type(value["findings"]) is not list
        ):
            raise ValueError
        if any(
            _SAFE_HASH_NAME.fullmatch(input_name) is None
            for input_name in value["input_hashes"]
        ):
            raise ValueError

        result_names: set[str] = set()
        for result in value["results"]:
            if type(result) is not dict or set(result) != {
                "name",
                "state",
                "summary",
                "evidence",
            }:
                raise ValueError
            name = result["name"]
            state = result["state"]
            evidence = result["evidence"]
            if (
                type(name) is not str
                or _SAFE_NAME.fullmatch(name) is None
                or name in result_names
                or type(state) is not str
                or state not in _RESULT_STATES
                or not _valid_summary(result["summary"])
                or type(evidence) is not dict
                or _contains_generated_at(evidence)
            ):
                raise ValueError
            AdapterCase("artifact-evidence", evidence)
            result_names.add(name)

        finding_fingerprints: set[str] = set()
        for finding in value["findings"]:
            if type(finding) is not dict or set(finding) != {
                "adapter",
                "case_id",
                "state",
                "fingerprint",
                "summary",
                "reproducer",
            }:
                raise ValueError
            adapter = finding["adapter"]
            case_id = finding["case_id"]
            state = finding["state"]
            fingerprint = finding["fingerprint"]
            if (
                type(adapter) is not str
                or _SAFE_NAME.fullmatch(adapter) is None
                or type(case_id) is not str
                or type(state) is not str
                or state not in _FINDING_STATES
                or type(fingerprint) is not str
                or _HEX64.fullmatch(fingerprint) is None
                or fingerprint in finding_fingerprints
                or not _valid_summary(finding["summary"])
            ):
                raise ValueError
            AdapterCase(case_id, {})
            expected_fingerprint = hashlib.sha256(
                f"{adapter}\0{case_id}\0{state}".encode("utf-8")
            ).hexdigest()
            if (
                fingerprint != expected_fingerprint
                or not _valid_reproducer(
                    finding["reproducer"],
                    adapter,
                    case_id,
                    fingerprint,
                )
            ):
                raise ValueError
            AdapterCase("artifact-finding", finding)
            finding_fingerprints.add(fingerprint)
    except (KeyError, TypeError, ValueError):
        raise ValueError("fuzz results invalid") from None


def _comparison_bytes(value: dict[str, object]) -> bytes:
    """Canonicalize a fuzz artifact without only its envelope timestamp."""
    projection = {
        key: item
        for key, item in value.items()
        if key != "generated_at"
    }
    return canonical_bytes(projection, omit_keys=frozenset())


def _artifact(root: Path, seed: int, corpus: Path | None, selected: tuple[str, ...], results: list[CheckResult], findings: list[FuzzFinding]) -> dict[str, object]:
    snapshot = build_snapshot(root)
    input_hashes = {"corpus": hashlib.sha256(corpus.read_bytes()).hexdigest()} if corpus is not None else {}
    hypothesis_runtime_hashes: set[str] = set()
    hypothesis_adapters: dict[str, str] = {}
    for name in selected:
        matching = [
            result
            for result in results
            if (
                result.name == name
                and result.evidence.get("generator") != "hypothesis"
            )
        ]
        if len(matching) != 1:
            raise ValueError("fuzz results invalid")
        case_hash = matching[0].evidence.get("case_corpus_sha256")
        if type(case_hash) is not str or _HEX64.fullmatch(case_hash) is None:
            raise ValueError("fuzz results invalid")
        input_hashes[f"adapter.{name}.cases"] = case_hash
        script = BUILTIN_FUZZ_COMMANDS.get(name)
        if script is not None:
            input_hashes[f"wrapper.{name}"] = hashlib.sha256((root / script).read_bytes()).hexdigest()
        generated_name = hypothesis_result_name(name)
        if generated_name in hypothesis_adapters:
            raise ValueError("fuzz results invalid")
        hypothesis_adapters[generated_name] = name
    generated_adapters: set[str] = set()
    for result in results:
        if result.evidence.get("generator") != "hypothesis":
            continue
        generated_adapter = hypothesis_adapters.get(result.name)
        if (
            generated_adapter is None
            or generated_adapter in generated_adapters
        ):
            raise ValueError("fuzz results invalid")
        generated_adapters.add(generated_adapter)
        case_hash = result.evidence.get("case_corpus_sha256")
        input_name = f"generator.{generated_adapter}.cases"
        if (
            type(case_hash) is not str
            or _HEX64.fullmatch(case_hash) is None
            or input_name in input_hashes
        ):
            raise ValueError("fuzz results invalid")
        input_hashes[input_name] = case_hash
        runtime = result.evidence.get("hypothesis_runtime")
        runtime_hash = result.evidence.get("hypothesis_runtime_sha256")
        if (
            result.state is ResultState.ADVISORY
            and result.summary == "hypothesis not applicable"
        ):
            if runtime is not None or runtime_hash is not None:
                raise ValueError("fuzz results invalid") from None
            continue
        try:
            hypothesis_runtime_hashes.add(
                _validate_hypothesis_runtime(runtime, runtime_hash)
            )
        except ValueError:
            raise ValueError("fuzz results invalid") from None
    if len(hypothesis_runtime_hashes) > 1:
        raise ValueError("fuzz results invalid")
    if hypothesis_runtime_hashes:
        input_hashes["generator.hypothesis.runtime"] = next(
            iter(hypothesis_runtime_hashes)
        )
    if not input_hashes or any(type(value) is not str or _HEX64.fullmatch(value) is None for value in input_hashes.values()):
        raise ValueError("fuzz results invalid")
    payload = make_envelope(_SCHEMA, _VERSION, snapshot.source_sha, "1", input_hashes)
    payload.update({
        "seed": seed,
        "results": [
            {"name": result.name, "state": result.state.value, "summary": result.summary, "evidence": dict(result.evidence)}
            for result in results
        ],
        "findings": [_finding_record(finding) for finding in findings],
    })
    _validate_artifact(payload)
    return payload


def run_fuzz_cli(args: argparse.Namespace) -> int:
    try:
        root = discover_repo_root(args.root if args.root is not None else Path.cwd())
        corpus = args.corpus.resolve() if args.corpus is not None else None
        output = (root / args.out).resolve() if not args.out.is_absolute() else args.out.resolve()
        selected = tuple(sorted(set(args.adapters)))
        if corpus is not None:
            load_corpus(corpus, max_cases=args.max_cases)
            if _paths_alias(corpus, output):
                return 1
            if any(name in BUILTIN_FUZZ_COMMANDS for name in selected):
                raise ValueError(
                    "explicit corpus is unsupported by builtin-corpus adapters"
                )
        context = AdapterContext(root, root / ".code-intelligence", corpus.parent if corpus is not None else root, args.seed, AdapterBudget(args.timeout, args.max_cases, args.max_output_bytes))
        results: list[CheckResult] = []
        findings: list[FuzzFinding] = []
        for name in selected:
            adapter = get_adapter(name)
            result, adapter_findings = run_fuzz_adapter(adapter, context, reproducer_dir=(root / args.reproducer_dir).resolve() if not args.reproducer_dir.is_absolute() else args.reproducer_dir)
            results.append(result)
            findings.extend(adapter_findings)
        if args.generator == "hypothesis":
            if importlib.util.find_spec("hypothesis") is None:
                occupied_names = {result.name for result in results}
                unavailable_name = "hypothesis"
                suffix = 0
                while unavailable_name in occupied_names:
                    unavailable_name = f"hypothesis.unavailable.{suffix}"
                    suffix += 1
                if _SAFE_NAME.fullmatch(unavailable_name) is None:
                    raise ValueError("fuzz results invalid")
                results.append(CheckResult(
                    unavailable_name,
                    ResultState.UNKNOWN,
                    "hypothesis is unavailable",
                    {
                        "availability": "unavailable",
                        "requested_generator": "hypothesis",
                    },
                ))
            else:
                for name in selected:
                    generated, generated_findings = run_hypothesis_adapter(get_adapter(name), context, reproducer_dir=(root / args.reproducer_dir).resolve() if not args.reproducer_dir.is_absolute() else args.reproducer_dir)
                    results.append(generated)
                    findings.extend(generated_findings)
        findings.sort(key=lambda finding: (finding.adapter, finding.case_id, finding.fingerprint))
        expected: object | None = None
        if args.check is not None:
            check_path = args.check.resolve()
            expected_raw = check_path.read_bytes()
            if check_path == output.resolve():
                return 1
            expected = json.loads(expected_raw.decode("utf-8"))
            _validate_artifact(expected)
        artifact = _artifact(root, args.seed, corpus, selected, results, findings)
        atomic_write_json(output, artifact, _validate_artifact)
        if expected is not None:
            if _comparison_bytes(expected) != _comparison_bytes(artifact):
                return 1
        if args.json:
            print(canonical_bytes(artifact, omit_keys=frozenset()).decode("utf-8"), end="")
        else:
            print(f"{len(results)} adapters; {len(findings)} findings")
        return exit_code(results, args.gate)
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fuzz_harness.py")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--adapter", action="append", dest="adapters")
    parser.add_argument("--list-adapters", action="store_true")
    parser.add_argument(
        "--corpus",
        type=Path,
        help=(
            "versioned corpus for external registered adapters; built-in "
            "adapters own internal corpora and reject this option"
        ),
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-cases", type=int, default=1000)
    parser.add_argument("--max-output-bytes", type=int, default=1048576)
    parser.add_argument("--generator", choices=("none", "hypothesis"), default="none")
    parser.add_argument("--reproducer-dir", type=Path, default=Path("regression_corpus/reproducers"))
    parser.add_argument("--out", type=Path, default=Path("FUZZ_RESULTS.json"))
    parser.add_argument("--check", type=Path)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    names = register_builtin_fuzzers()
    if args.list_adapters:
        print("\n".join(names))
        return 0
    if not args.adapters:
        parser.error("at least one --adapter is required")
    return run_fuzz_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
