"""Deterministic, process-contained replay for fuzz adapters."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from dataclasses import dataclass
from pathlib import Path
import re
import sys
import time
from typing import Literal

from .adapters import AdapterCase, AdapterContext, AnalysisAdapter
from .artifacts import atomic_write_json, canonical_bytes
from .oracle_service import _cleanup_worker_group, _terminate_worker
from .results import CheckResult, ResultState
from .schemas import make_envelope, validate_envelope
from .snapshot import build_snapshot


_CORPUS_MAX_BYTES = 16 * 1024 * 1024
_CONTROL_BYTES = 1024
_MAX_SUMMARY = 4096
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_WORKER_OUTPUT_ERROR = "worker-output-error"
_HYPOTHESIS_RESULT_SUFFIX = ".hypothesis"
_MAX_RESULT_NAME_LENGTH = 256


@dataclass(frozen=True)
class FuzzFinding:
    adapter: str
    case_id: str
    state: Literal["fail", "timeout", "error"]
    fingerprint: str
    summary: str
    reproducer: str | None


@dataclass(frozen=True)
class _PendingReproducer:
    target: Path
    adapter: str
    case_id: str
    seed: int
    payload: object
    case_hash: str


def _safe_json_object(path: Path) -> dict[str, object]:
    try:
        metadata = path.stat()
        if metadata.st_size > _CORPUS_MAX_BYTES:
            raise ValueError
        raw = path.read_bytes()
        if path.stat().st_size != metadata.st_size:
            raise ValueError
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise ValueError("fuzz corpus invalid") from None
    if type(value) is not dict:
        raise ValueError("fuzz corpus invalid")
    return value


def _reject_duplicates(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def load_corpus(path: Path, *, max_cases: int) -> tuple[AdapterCase, ...]:
    """Load one frozen schema-1 corpus in stable case-ID order."""
    payload = _safe_json_object(path)
    rows = payload.get("cases")
    if payload.get("schema") != 1 or type(rows) is not list or len(rows) > max_cases:
        raise ValueError("fuzz corpus invalid")
    cases: list[AdapterCase] = []
    identifiers: set[str] = set()
    try:
        for row in rows:
            if type(row) is not dict or set(row) != {"id", "payload"}:
                raise ValueError
            case = AdapterCase(row["id"], row["payload"])
            if case.case_id in identifiers:
                raise ValueError
            identifiers.add(case.case_id)
            cases.append(case)
    except (TypeError, ValueError):
        raise ValueError("fuzz corpus invalid") from None
    return tuple(sorted(cases, key=lambda case: case.case_id))


def _fingerprint(adapter: str, case_id: str, state: str) -> str:
    return hashlib.sha256(f"{adapter}\0{case_id}\0{state}".encode("utf-8")).hexdigest()


def effective_case_hash(cases: tuple[AdapterCase, ...]) -> str:
    """Hash the ordered, JSON-safe cases actually replayed by an adapter."""
    rows = [{"id": case.case_id, "payload": case.payload} for case in cases]
    return hashlib.sha256(canonical_bytes(rows, omit_keys=frozenset())).hexdigest()


def hypothesis_result_name(adapter_name: str) -> str:
    """Derive a bounded, collision-resistant result identity for Hypothesis."""
    unbounded = f"{adapter_name}{_HYPOTHESIS_RESULT_SUFFIX}"
    if len(unbounded) <= _MAX_RESULT_NAME_LENGTH:
        return unbounded
    digest = hashlib.sha256(adapter_name.encode("utf-8")).hexdigest()
    prefix_length = (
        _MAX_RESULT_NAME_LENGTH
        - len(_HYPOTHESIS_RESULT_SUFFIX)
        - len(digest)
        - 1
    )
    return (
        f"{adapter_name[:prefix_length]}.{digest}"
        f"{_HYPOTHESIS_RESULT_SUFFIX}"
    )


def _validate_hypothesis_runtime(descriptor: object, descriptor_hash: object) -> str:
    """Validate one exact worker-attested Hypothesis runtime identity."""
    if (
        type(descriptor) is not dict
        or set(descriptor) != {"engine", "version"}
        or descriptor.get("engine") != "hypothesis"
    ):
        raise ValueError("hypothesis runtime invalid")
    version = descriptor.get("version")
    if (
        type(version) is not str
        or not version
        or len(version) > 256
        or version != version.strip()
        or not version.isprintable()
        or type(descriptor_hash) is not str
        or _HEX64.fullmatch(descriptor_hash) is None
    ):
        raise ValueError("hypothesis runtime invalid")
    try:
        AdapterCase("hypothesis-runtime", descriptor)
    except (TypeError, ValueError):
        raise ValueError("hypothesis runtime invalid") from None
    expected = hashlib.sha256(
        canonical_bytes(descriptor, omit_keys=frozenset())
    ).hexdigest()
    if descriptor_hash != expected:
        raise ValueError("hypothesis runtime invalid")
    return descriptor_hash


def _supports_descendant_containment() -> bool:
    return (
        os.name == "posix"
        and sys.platform.startswith("linux")
        and hasattr(os, "setsid")
        and hasattr(os, "killpg")
        and os.path.isfile("/proc/self/stat")
    )


def _send(connection: object, payload: dict[str, object], budget: int) -> None:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > budget:
        raw = b'{"kind":"overflow"}'
    try:
        connection.send_bytes(raw)  # type: ignore[attr-defined]
    except (BrokenPipeError, EOFError, OSError):
        pass


def _worker(connection: object, adapter: AnalysisAdapter, case: AdapterCase, context: AdapterContext) -> None:
    try:
        os.setsid()
    except (AttributeError, OSError):
        _send(connection, {"kind": "error"}, max(context.budget.max_output_bytes, _CONTROL_BYTES))
        return
    try:
        try:
            result = adapter.run(case, context)
        except BaseException:
            _send(
                connection,
                {"kind": "error"},
                max(context.budget.max_output_bytes, _CONTROL_BYTES),
            )
            return
        if (
            isinstance(result, CheckResult)
            and type(result.summary) is str
            and len(result.summary) > _MAX_SUMMARY
        ):
            _send(
                connection,
                {"kind": "invalid"},
                max(context.budget.max_output_bytes, _CONTROL_BYTES),
            )
            return
        try:
            if (
                not isinstance(result, CheckResult)
                or result.name != adapter.name
                or not isinstance(result.state, ResultState)
                or type(result.summary) is not str
                or not result.summary
                or not result.summary.isprintable()
            ):
                raise ValueError
            AdapterCase(case.case_id, {"summary": result.summary})
        except (TypeError, ValueError):
            _send(
                connection,
                {"kind": "invalid"},
                max(context.budget.max_output_bytes, _CONTROL_BYTES),
            )
            return
        _send(
            connection,
            {"kind": "result", "state": result.state.value, "summary": result.summary},
            context.budget.max_output_bytes,
        )
    finally:
        try:
            connection.close()  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass


def _cases_worker(connection: object, adapter: AnalysisAdapter, context: AdapterContext) -> None:
    try:
        os.setsid()
    except (AttributeError, OSError):
        _send(connection, {"kind": "error"}, max(context.budget.max_output_bytes, _CONTROL_BYTES))
        return
    try:
        cases: list[AdapterCase] = []
        for case in adapter.cases(context):
            if not isinstance(case, AdapterCase) or len(cases) >= context.budget.max_cases:
                raise ValueError
            cases.append(case)
        _send(
            connection,
            {"kind": "cases", "cases": [{"id": case.case_id, "payload": case.payload} for case in cases]},
            context.budget.max_output_bytes,
        )
    except BaseException:
        _send(connection, {"kind": "error"}, max(context.budget.max_output_bytes, _CONTROL_BYTES))
    finally:
        try:
            connection.close()  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass


def _hypothesis_cases_worker(connection: object, adapter: AnalysisAdapter, context: AdapterContext) -> None:
    """Generate at most max_cases JSON-safe cases, seeded and database-free."""
    try:
        os.setsid()
        strategy_factory = getattr(adapter, "hypothesis_strategy", None)
        if not callable(strategy_factory):
            _send(connection, {"kind": "not-applicable"}, max(context.budget.max_output_bytes, _CONTROL_BYTES))
            return
        import hypothesis
        from hypothesis import given, seed, settings

        runtime = {
            "engine": "hypothesis",
            "version": getattr(hypothesis, "__version__", None),
        }
        runtime_hash = hashlib.sha256(
            canonical_bytes(runtime, omit_keys=frozenset())
        ).hexdigest()
        _validate_hypothesis_runtime(runtime, runtime_hash)

        rows: list[AdapterCase] = []

        @seed(context.seed)
        @settings(max_examples=context.budget.max_cases, database=None, deadline=None)
        @given(strategy_factory(context))
        def collect(payload: object) -> None:
            frozen = AdapterCase("hypothesis", payload).payload
            digest = hashlib.sha256(canonical_bytes(frozen, omit_keys=frozenset())).hexdigest()[:16]
            rows.append(AdapterCase(f"hypothesis-{len(rows):04d}-{digest}", frozen))

        collect()
        _send(
            connection,
            {
                "kind": "cases",
                "cases": [
                    {"id": row.case_id, "payload": row.payload}
                    for row in rows
                ],
                "runtime": runtime,
                "runtime_sha256": runtime_hash,
            },
            context.budget.max_output_bytes,
        )
    except BaseException:
        _send(connection, {"kind": "error"}, max(context.budget.max_output_bytes, _CONTROL_BYTES))
    finally:
        try:
            connection.close()  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass


def _receive_cases(
    adapter: AnalysisAdapter,
    context: AdapterContext,
    *,
    hypothesis: bool = False,
) -> tuple[
    ResultState,
    tuple[AdapterCase, ...],
    tuple[dict[str, str], str] | None,
]:
    if not _supports_descendant_containment():
        return ResultState.ERROR, (), None
    process_context = multiprocessing.get_context("spawn")
    receiving, sending = process_context.Pipe(duplex=False)
    process = process_context.Process(target=_hypothesis_cases_worker if hypothesis else _cases_worker, args=(sending, adapter, context))
    try:
        process.start()
    except (AttributeError, OSError, RuntimeError, TypeError):
        receiving.close()
        sending.close()
        return ResultState.ERROR, (), None
    sending.close()
    raw: bytes | None = None
    deadline = time.monotonic() + context.budget.timeout_seconds
    try:
        while time.monotonic() < deadline:
            if receiving.poll(min(0.05, max(0.0, deadline - time.monotonic()))):
                raw = receiving.recv_bytes(maxlength=max(context.budget.max_output_bytes, _CONTROL_BYTES))
                break
            if not process.is_alive():
                break
        if raw is None:
            if process.is_alive():
                _terminate_worker(process)
                return ResultState.TIMEOUT, (), None
            return ResultState.ERROR, (), None
        try:
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
            if hypothesis and payload == {"kind": "not-applicable"}:
                return ResultState.ADVISORY, (), None
            expected_keys = (
                {"kind", "cases", "runtime", "runtime_sha256"}
                if hypothesis
                else {"kind", "cases"}
            )
            if (
                type(payload) is not dict
                or set(payload) != expected_keys
                or payload["kind"] != "cases"
            ):
                raise ValueError
            raw_cases = payload["cases"]
            if type(raw_cases) is not list or len(raw_cases) > context.budget.max_cases:
                raise ValueError
            cases = tuple(AdapterCase(row["id"], row["payload"]) for row in raw_cases if type(row) is dict and set(row) == {"id", "payload"})
            if len(cases) != len(raw_cases) or len({case.case_id for case in cases}) != len(cases):
                raise ValueError
            runtime_attestation = None
            if hypothesis:
                runtime_hash = _validate_hypothesis_runtime(
                    payload["runtime"],
                    payload["runtime_sha256"],
                )
                runtime_attestation = (dict(payload["runtime"]), runtime_hash)
        except (EOFError, OSError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return ResultState.ERROR, (), None
        return (
            ResultState.PASS,
            tuple(sorted(cases, key=lambda case: case.case_id)),
            runtime_attestation,
        )
    finally:
        receiving.close()
        process.join(0.25)
        if process.is_alive():
            _terminate_worker(process)
        else:
            _cleanup_worker_group(process)


def _receive_case(
    adapter: AnalysisAdapter, case: AdapterCase, context: AdapterContext
) -> tuple[str, str]:
    if not _supports_descendant_containment():
        return "error", "descendant containment unavailable"
    process_context = multiprocessing.get_context("spawn")
    receiving, sending = process_context.Pipe(duplex=False)
    process = process_context.Process(target=_worker, args=(sending, adapter, case, context))
    try:
        process.start()
    except (AttributeError, OSError, RuntimeError, TypeError):
        receiving.close()
        sending.close()
        return "error", "fuzz worker failed"
    sending.close()
    raw: bytes | None = None
    deadline = time.monotonic() + context.budget.timeout_seconds
    try:
        while time.monotonic() < deadline:
            if receiving.poll(min(0.05, max(0.0, deadline - time.monotonic()))):
                raw = receiving.recv_bytes(maxlength=max(context.budget.max_output_bytes, _CONTROL_BYTES))
                break
            if not process.is_alive():
                break
        if raw is None:
            if process.is_alive():
                _terminate_worker(process)
                return "timeout", "case exceeded timeout"
            return "error", "fuzz worker failed"
        try:
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return "error", "fuzz worker failed"
        if type(payload) is not dict:
            return "error", "fuzz worker failed"
        if payload == {"kind": "overflow"}:
            return _WORKER_OUTPUT_ERROR, "fuzz worker output exceeded budget"
        if payload == {"kind": "invalid"}:
            return _WORKER_OUTPUT_ERROR, "fuzz worker output invalid"
        if payload == {"kind": "error"}:
            return "error", "adapter raised"
        if set(payload) != {"kind", "state", "summary"} or payload.get("kind") != "result":
            return "error", "fuzz worker failed"
        try:
            state = ResultState(payload["state"])
            summary = payload["summary"]
            AdapterCase(case.case_id, {"summary": summary})
        except (TypeError, ValueError):
            return "error", "fuzz worker failed"
        if type(summary) is not str or not summary or len(summary) > _MAX_SUMMARY or not summary.isprintable():
            return "error", "fuzz worker failed"
        return state.value, summary
    except (EOFError, OSError):
        if process.is_alive():
            _terminate_worker(process)
        return "error", "fuzz worker failed"
    finally:
        receiving.close()
        process.join(0.25)
        if process.is_alive():
            _terminate_worker(process)
        else:
            _cleanup_worker_group(process)


def validate_fuzz_reproducer(payload: object) -> None:
    """Validate the secret-safe, replayable JSON persisted for one finding."""
    validate_envelope(payload, "bd.fuzz-reproducer", 1)
    if type(payload) is not dict or set(payload) != {
        "schema_name", "schema_version", "source_sha", "tool_version",
        "input_hashes", "generated_at", "adapter", "case_id", "seed", "payload",
    }:
        raise ValueError("fuzz reproducer invalid")
    if type(payload["seed"]) is not int or set(payload["input_hashes"]) != {"case_payload"}:
        raise ValueError("fuzz reproducer invalid")
    if not _SAFE_COMPONENT.fullmatch(payload["adapter"]) or not _SAFE_COMPONENT.fullmatch(payload["case_id"]):
        raise ValueError("fuzz reproducer invalid")
    AdapterCase(payload["case_id"], payload["payload"])


def _stage_reproducer(
    adapter: AnalysisAdapter,
    case: AdapterCase,
    state: Literal["fail", "timeout", "error"],
    context: AdapterContext,
    reproducer_dir: Path,
) -> tuple[str, str | None, _PendingReproducer | None]:
    fingerprint = _fingerprint(adapter.name, case.case_id, state)
    if not _SAFE_COMPONENT.fullmatch(adapter.name) or not _SAFE_COMPONENT.fullmatch(case.case_id):
        return fingerprint, None, None
    target = reproducer_dir / f"{adapter.name}--{case.case_id}--{fingerprint[:12]}.json"
    case_hash = hashlib.sha256(canonical_bytes(case.payload, omit_keys=frozenset())).hexdigest()
    try:
        relative = target.resolve().relative_to(context.repo_root.resolve())
    except ValueError:
        reproducer = target.name
    else:
        reproducer = relative.as_posix()
    return (
        fingerprint,
        reproducer,
        _PendingReproducer(
            target,
            adapter.name,
            case.case_id,
            context.seed,
            case.payload,
            case_hash,
        ),
    )


def _write_staged_reproducers(
    pending: list[_PendingReproducer],
    context: AdapterContext,
) -> None:
    if not pending:
        return
    source_sha = build_snapshot(context.repo_root).source_sha
    writes: list[tuple[Path, dict[str, object]]] = []
    for item in pending:
        payload = make_envelope(
            "bd.fuzz-reproducer",
            1,
            source_sha,
            "1",
            {"case_payload": item.case_hash},
        )
        payload.update(
            {
                "adapter": item.adapter,
                "case_id": item.case_id,
                "seed": item.seed,
                "payload": item.payload,
            }
        )
        validate_fuzz_reproducer(payload)
        writes.append((item.target, payload))
    for target, payload in writes:
        atomic_write_json(target, payload, validate_fuzz_reproducer)


def run_fuzz_adapter(
    adapter: AnalysisAdapter,
    context: AdapterContext,
    *,
    reproducer_dir: Path,
) -> tuple[CheckResult, tuple[FuzzFinding, ...]]:
    """Replay each frozen case in its own contained worker process."""
    if adapter.kind != "fuzz":
        return CheckResult(adapter.name, ResultState.ERROR, "adapter kind must be fuzz", {}), ()
    findings: list[FuzzFinding] = []
    pending_reproducers: list[_PendingReproducer] = []
    saw_unknown = False
    saw_advisory = False
    case_state, cases, _runtime = _receive_cases(adapter, context)
    if case_state is ResultState.TIMEOUT:
        return CheckResult(adapter.name, ResultState.TIMEOUT, "fuzz corpus exceeded timeout", {}), ()
    if case_state is not ResultState.PASS:
        return CheckResult(adapter.name, ResultState.ERROR, "fuzz corpus invalid", {}), ()
    case_hash = effective_case_hash(cases)
    for case in cases:
        state, summary = _receive_case(adapter, case, context)
        if state == _WORKER_OUTPUT_ERROR:
            return CheckResult(
                adapter.name,
                ResultState.ERROR,
                summary,
                {"seed": context.seed, "case_corpus_sha256": case_hash},
            ), ()
        if state == ResultState.PASS.value:
            continue
        if state == ResultState.ADVISORY.value:
            saw_advisory = True
            continue
        if state == ResultState.UNKNOWN.value:
            saw_unknown = True
            continue
        if state not in {"fail", "timeout", "error"}:
            state, summary = "error", "fuzz worker failed"
        finding_state: Literal["fail", "timeout", "error"] = state
        fingerprint, reproducer, pending = _stage_reproducer(
            adapter,
            case,
            finding_state,
            context,
            reproducer_dir,
        )
        if pending is not None:
            pending_reproducers.append(pending)
        findings.append(FuzzFinding(adapter.name, case.case_id, finding_state, fingerprint, summary, reproducer))
    findings.sort(key=lambda finding: (finding.adapter, finding.case_id, finding.fingerprint))
    final_state = ResultState.FAIL if findings else (
        ResultState.UNKNOWN if saw_unknown else (ResultState.ADVISORY if saw_advisory else ResultState.PASS)
    )
    _write_staged_reproducers(pending_reproducers, context)
    return (
        CheckResult(adapter.name, final_state, f"{len(findings)} fuzz failures", {"findings": len(findings), "seed": context.seed, "case_corpus_sha256": case_hash}),
        tuple(findings),
    )


def run_hypothesis_adapter(adapter: AnalysisAdapter, context: AdapterContext, *, reproducer_dir: Path) -> tuple[CheckResult, tuple[FuzzFinding, ...]]:
    """Replay seeded generated payloads for adapters opting into hypothesis_strategy.

    IDs are generation-order plus a payload SHA prefix; the Hypothesis database is
    disabled, and the containing worker enforces the normal time/output budgets.
    """
    if adapter.kind != "fuzz":
        return CheckResult(adapter.name, ResultState.ERROR, "adapter kind must be fuzz", {}), ()
    state, cases, runtime_attestation = _receive_cases(
        adapter,
        context,
        hypothesis=True,
    )
    name = hypothesis_result_name(adapter.name)
    if state is ResultState.ADVISORY:
        return CheckResult(name, ResultState.ADVISORY, "hypothesis not applicable", {"generator": "hypothesis", "case_corpus_sha256": effective_case_hash(cases)}), ()
    if state is ResultState.TIMEOUT:
        return CheckResult(name, ResultState.TIMEOUT, "hypothesis generation exceeded timeout", {"generator": "hypothesis"}), ()
    if state is not ResultState.PASS:
        return CheckResult(name, ResultState.ERROR, "hypothesis generation failed", {"generator": "hypothesis"}), ()
    if runtime_attestation is None:
        return CheckResult(name, ResultState.ERROR, "hypothesis generation failed", {"generator": "hypothesis"}), ()
    runtime, runtime_hash = runtime_attestation
    case_hash = effective_case_hash(cases)
    evidence = {
        "generator": "hypothesis",
        "seed": context.seed,
        "case_corpus_sha256": case_hash,
        "hypothesis_runtime": runtime,
        "hypothesis_runtime_sha256": runtime_hash,
    }
    findings: list[FuzzFinding] = []
    pending_reproducers: list[_PendingReproducer] = []
    saw_unknown = saw_advisory = False
    for case in cases:
        result_state, summary = _receive_case(adapter, case, context)
        if result_state == _WORKER_OUTPUT_ERROR:
            return CheckResult(
                name,
                ResultState.ERROR,
                summary,
                evidence,
            ), ()
        if result_state == ResultState.ADVISORY.value:
            saw_advisory = True
            continue
        if result_state == ResultState.PASS.value:
            continue
        if result_state == ResultState.UNKNOWN.value:
            saw_unknown = True
            continue
        finding_state: Literal["fail", "timeout", "error"] = result_state if result_state in {"fail", "timeout", "error"} else "error"
        fingerprint, reproducer, pending = _stage_reproducer(
            adapter,
            case,
            finding_state,
            context,
            reproducer_dir,
        )
        if pending is not None:
            pending_reproducers.append(pending)
        findings.append(FuzzFinding(adapter.name, case.case_id, finding_state, fingerprint, summary, reproducer))
    final = ResultState.FAIL if findings else (ResultState.UNKNOWN if saw_unknown else (ResultState.ADVISORY if saw_advisory else ResultState.PASS))
    _write_staged_reproducers(pending_reproducers, context)
    evidence["findings"] = len(findings)
    return CheckResult(
        name,
        final,
        f"{len(findings)} hypothesis fuzz failures",
        evidence,
    ), tuple(
        sorted(
            findings,
            key=lambda finding: (
                finding.adapter,
                finding.case_id,
                finding.fingerprint,
            ),
        )
    )
