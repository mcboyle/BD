"""Process-bounded execution for typed differential-oracle adapters."""

from __future__ import annotations

import json
import multiprocessing
import os
import signal
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeAlias

from .adapters import (
    AdapterCase,
    AdapterContext,
    AnalysisAdapter,
    JsonValue,
)
from .results import CheckResult, ResultState


_COMPARISON_FIELDS = frozenset(
    {
        "left",
        "right",
        "normalized_left",
        "normalized_right",
        "equal",
        "allowed",
        "reason",
    }
)
_CONTROL_BYTES = 1024
_MAX_RESULT_TEXT = 4096

WireValue: TypeAlias = (
    bool | int | float | str | None | list["WireValue"] | dict[str, "WireValue"]
)


@dataclass(frozen=True)
class OracleComparison:
    case_id: str
    left: JsonValue
    right: JsonValue
    normalized_left: JsonValue
    normalized_right: JsonValue
    equal: bool
    allowed: bool
    reason: str


def _encode(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _send_control(connection: object, kind: str, **fields: object) -> None:
    payload = _encode({"kind": kind, **fields})
    try:
        connection.send_bytes(payload)  # type: ignore[attr-defined]
    except (BrokenPipeError, EOFError, OSError):
        pass


def _worker(
    connection: object,
    adapter: AnalysisAdapter,
    context: AdapterContext,
) -> None:
    try:
        if hasattr(os, "setsid"):
            os.setsid()
        raw_cases = adapter.cases(context)
        cases: list[AdapterCase] = []
        for raw_case in raw_cases:
            if not isinstance(raw_case, AdapterCase):
                _send_control(connection, "invalid")
                return
            cases.append(raw_case)
            if len(cases) > context.budget.max_cases:
                _send_control(connection, "case-limit", cases=len(cases))
                return

        results: list[dict[str, WireValue]] = []
        for case in cases:
            result = adapter.run(case, context)
            if (
                not isinstance(result, CheckResult)
                or result.name != adapter.name
                or not isinstance(result.state, ResultState)
                or not isinstance(result.evidence, Mapping)
                or type(result.summary) is not str
                or not result.summary
                or len(result.summary) > _MAX_RESULT_TEXT
                or not result.summary.isprintable()
            ):
                _send_control(connection, "invalid")
                return
            AdapterCase(case.case_id, {"summary": result.summary})
            evidence = dict(result.evidence)
            if set(evidence) != _COMPARISON_FIELDS:
                _send_control(connection, "invalid")
                return
            # Reuse the shared adapter payload policy at the untrusted worker
            # boundary.  This rejects secret-bearing and resource-hostile
            # evidence before any bytes cross IPC.
            validated = AdapterCase(case.case_id, evidence)
            frozen = validated.payload
            assert isinstance(frozen, Mapping)
            if (
                type(frozen["equal"]) is not bool
                or type(frozen["allowed"]) is not bool
                or type(frozen["reason"]) is not str
                or not frozen["reason"]
                or len(frozen["reason"]) > _MAX_RESULT_TEXT
                or not frozen["reason"].isprintable()
            ):
                _send_control(connection, "invalid")
                return
            results.append(
                {
                    "case_id": case.case_id,
                    "state": result.state.value,
                    "evidence": dict(frozen),
                }
            )

        payload = _encode({"kind": "results", "results": results})
        if len(payload) > context.budget.max_output_bytes:
            _send_control(connection, "output-limit")
            return
        connection.send_bytes(payload)  # type: ignore[attr-defined]
    except BaseException:
        _send_control(connection, "invalid")
    finally:
        try:
            connection.close()  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass


def _worker_session_members(session_id: int) -> tuple[int, ...]:
    """Return Linux processes that still belong to a worker-owned session."""
    proc = "/proc"
    if not os.path.isdir(proc):
        return ()
    members: list[int] = []
    try:
        entries = os.listdir(proc)
    except OSError:
        return ()
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(
                os.path.join(proc, entry, "stat"),
                encoding="utf-8",
            ) as stat_file:
                raw = stat_file.read()
            fields = raw.rpartition(")")[2].split()
            if len(fields) > 3 and int(fields[3]) == session_id:
                members.append(int(entry))
        except (OSError, UnicodeError, ValueError):
            continue
    return tuple(sorted(members, key=lambda pid: pid == session_id))


def _signal_worker_session(session_id: int, action: signal.Signals) -> None:
    members = _worker_session_members(session_id)
    if members:
        for process_id in members:
            try:
                os.kill(process_id, action)
            except (OSError, ProcessLookupError):
                pass
        return
    try:
        os.killpg(session_id, action)
    except (OSError, ProcessLookupError):
        pass


def _terminate_worker(process: multiprocessing.Process) -> None:
    if os.name == "posix" and process.pid is not None:
        try:
            _signal_worker_session(process.pid, signal.SIGTERM)
        except OSError:
            process.terminate()
    else:
        process.terminate()
    process.join(0.5)
    if process.is_alive():
        if os.name == "posix" and process.pid is not None:
            _signal_worker_session(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.join(0.5)
    _cleanup_worker_group(process)


def _cleanup_worker_group(process: multiprocessing.Process) -> None:
    """Kill descendants left behind by an abruptly exited POSIX worker."""
    if os.name != "posix" or process.pid is None:
        return
    for action in (signal.SIGTERM, signal.SIGKILL):
        _signal_worker_session(process.pid, action)


def _decode_payload(raw: bytes) -> dict[str, object]:
    def reject_duplicates(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=reject_duplicates,
        parse_constant=lambda _value: (_ for _ in ()).throw(
            ValueError("non-finite number")
        ),
    )
    if not isinstance(value, dict):
        raise ValueError("worker payload must be an object")
    return value


def _invalid(adapter: AnalysisAdapter, summary: str) -> tuple[
    CheckResult,
    tuple[OracleComparison, ...],
]:
    return CheckResult(adapter.name, ResultState.ERROR, summary, {}), ()


def _parse_results(
    adapter: AnalysisAdapter,
    payload: dict[str, object],
    max_cases: int,
) -> tuple[CheckResult, tuple[OracleComparison, ...]]:
    if set(payload) != {"kind", "results"}:
        return _invalid(adapter, "oracle worker payload invalid")
    raw_results = payload["results"]
    if (
        type(raw_results) is not list
        or not raw_results
        or len(raw_results) > max_cases
    ):
        return _invalid(adapter, "oracle worker payload invalid")

    rows: list[OracleComparison] = []
    states: list[ResultState] = []
    seen: set[str] = set()
    for raw_result in raw_results:
        if (
            type(raw_result) is not dict
            or set(raw_result) != {"case_id", "state", "evidence"}
        ):
            return _invalid(adapter, "oracle worker payload invalid")
        case_id = raw_result["case_id"]
        raw_state = raw_result["state"]
        evidence = raw_result["evidence"]
        if (
            type(case_id) is not str
            or case_id in seen
            or type(raw_state) is not str
            or type(evidence) is not dict
            or set(evidence) != _COMPARISON_FIELDS
        ):
            return _invalid(adapter, "oracle worker payload invalid")
        try:
            state = ResultState(raw_state)
            validated = AdapterCase(case_id, evidence)
        except (TypeError, ValueError):
            return _invalid(adapter, "oracle worker payload invalid")
        clean = validated.payload
        assert isinstance(clean, Mapping)
        if (
            type(clean["equal"]) is not bool
            or type(clean["allowed"]) is not bool
            or type(clean["reason"]) is not str
            or not clean["reason"]
            or len(clean["reason"]) > _MAX_RESULT_TEXT
            or not clean["reason"].isprintable()
        ):
            return _invalid(adapter, "oracle worker payload invalid")
        seen.add(case_id)
        states.append(state)
        rows.append(
            OracleComparison(
                case_id,
                clean["left"],
                clean["right"],
                clean["normalized_left"],
                clean["normalized_right"],
                clean["equal"],
                clean["allowed"],
                clean["reason"],
            )
        )

    forbidden = sum(not row.equal and not row.allowed for row in rows)
    if ResultState.ERROR in states:
        state = ResultState.ERROR
    elif ResultState.TIMEOUT in states:
        state = ResultState.TIMEOUT
    elif ResultState.FAIL in states or forbidden:
        state = ResultState.FAIL
    elif ResultState.UNKNOWN in states:
        state = ResultState.UNKNOWN
    elif ResultState.ADVISORY in states:
        state = ResultState.ADVISORY
    else:
        state = ResultState.PASS
    return (
        CheckResult(
            adapter.name,
            state,
            f"{len(rows)} comparisons; {forbidden} forbidden divergences",
            {"comparisons": len(rows), "forbidden": forbidden},
        ),
        tuple(rows),
    )


def run_oracle_adapter(
    adapter: AnalysisAdapter,
    context: AdapterContext,
) -> tuple[CheckResult, tuple[OracleComparison, ...]]:
    """Run one adapter within case, wall-time, and IPC byte budgets."""
    if adapter.kind != "oracle":
        return _invalid(adapter, "adapter kind must be oracle")

    process_context = multiprocessing.get_context("spawn")
    receiving, sending = process_context.Pipe(duplex=False)
    process = process_context.Process(
        target=_worker,
        args=(sending, adapter, context),
    )
    try:
        process.start()
    except (AttributeError, OSError, RuntimeError, TypeError):
        receiving.close()
        sending.close()
        return (
            CheckResult(
                adapter.name,
                ResultState.ERROR,
                "oracle worker failed",
                {"stage": "start"},
            ),
            (),
        )
    sending.close()
    deadline = time.monotonic() + context.budget.timeout_seconds
    while not receiving.poll(
        min(0.05, max(0.0, deadline - time.monotonic()))
    ):
        if not process.is_alive() or time.monotonic() >= deadline:
            break

    if not receiving.poll():
        process.join(max(0.0, deadline - time.monotonic()))
        if process.is_alive():
            _terminate_worker(process)
            receiving.close()
            return (
                CheckResult(
                    adapter.name,
                    ResultState.TIMEOUT,
                    "oracle budget exceeded",
                    {"timeout_seconds": context.budget.timeout_seconds},
                ),
                (),
            )
        _cleanup_worker_group(process)
        receiving.close()
        return (
            CheckResult(
                adapter.name,
                ResultState.ERROR,
                "oracle worker failed",
                {"exitcode": process.exitcode},
            ),
            (),
        )
    try:
        raw = receiving.recv_bytes(
            maxlength=max(
                context.budget.max_output_bytes,
                _CONTROL_BYTES,
            )
        )
        payload = _decode_payload(raw)
    except (EOFError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        process.join(max(0.0, deadline - time.monotonic()))
        if process.is_alive():
            _terminate_worker(process)
        else:
            _cleanup_worker_group(process)
        if process.exitcode != 0:
            return (
                CheckResult(
                    adapter.name,
                    ResultState.ERROR,
                    "oracle worker failed",
                    {"exitcode": process.exitcode},
                ),
                (),
            )
        return _invalid(adapter, "oracle worker payload invalid")
    finally:
        receiving.close()

    process.join(max(0.0, deadline - time.monotonic()))
    if process.is_alive():
        _terminate_worker(process)
        return (
            CheckResult(
                adapter.name,
                ResultState.TIMEOUT,
                "oracle budget exceeded",
                {"timeout_seconds": context.budget.timeout_seconds},
            ),
            (),
        )
    _cleanup_worker_group(process)
    if process.exitcode != 0:
        return (
            CheckResult(
                adapter.name,
                ResultState.ERROR,
                "oracle worker failed",
                {"exitcode": process.exitcode},
            ),
            (),
        )

    kind = payload.get("kind")
    if kind == "case-limit" and set(payload) == {"kind", "cases"}:
        count = payload.get("cases")
        if type(count) is int and count > context.budget.max_cases:
            return (
                CheckResult(
                    adapter.name,
                    ResultState.ERROR,
                    "adapter exceeded max_cases",
                    {"cases": count},
                ),
                (),
            )
    if kind == "output-limit" and set(payload) == {"kind"}:
        return _invalid(adapter, "oracle worker output exceeded budget")
    if kind == "invalid" and set(payload) == {"kind"}:
        return _invalid(adapter, "oracle worker payload invalid")
    if kind != "results":
        return _invalid(adapter, "oracle worker payload invalid")
    return _parse_results(adapter, payload, context.budget.max_cases)
