"""Typed wrappers around the repository's specialist oracle commands."""

from __future__ import annotations

import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .adapters import (
    AdapterCase,
    AdapterContext,
    JsonValue,
    get_adapter,
    list_adapters,
    register_adapter,
)
from .results import CheckResult, ResultState


BUILTIN_ORACLE_COMMANDS = {
    "consumer-agreement": ("tools/consumer_agreement.py", "--gate"),
    "schema-oracle": ("toolchain/bin/bd-schema-oracle", "--json"),
    "rollback-oracle": ("toolchain/bin/bd-rollback-oracle", "--json"),
    "template-diff": ("toolchain/bin/bd-template-diff", "--json"),
    "plugin-diff": ("toolchain/bin/bd-plugin-diff", "--json"),
    "plugin-permission-diff": (
        "toolchain/bin/bd-plugin-permission-diff",
        "--json",
    ),
    "url-classifier-truth": ("toolchain/bin/bd-fuzz-urlguard", "--json"),
}

_DIFF_NAMES = frozenset(
    {"template-diff", "plugin-diff", "plugin-permission-diff"}
)
_DIFF_FIELDS = {
    "template-diff": frozenset(
        {
            "selectors",
            "api",
            "network_patterns",
            "status",
            "confidence",
            "template_logic",
            "resolutions",
        }
    ),
    "plugin-diff": frozenset(
        {
            "hooks",
            "capabilities",
            "processors",
            "extractors",
            "recognizers",
            "namers",
            "prefilters",
        }
    ),
    "plugin-permission-diff": frozenset(
        {
            "capabilities",
            "caps",
            "permissions",
            "full_access",
            "access_level",
            "api_version",
        }
    ),
}
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_MAX_INPUT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class _CommandOutput:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    overflowed: bool


def _comparison(
    name: str,
    state: ResultState,
    case_id: str,
    *,
    left: JsonValue,
    right: JsonValue,
    equal: bool,
    allowed: bool,
    reason: str,
    exit_code: int | None = None,
    counts: Mapping[str, int] | None = None,
    command: str | None = None,
) -> CheckResult:
    evidence: dict[str, object] = {
        "left": left,
        "right": right,
        "normalized_left": left,
        "normalized_right": right,
        "equal": equal,
        "allowed": allowed,
        "reason": reason,
    }
    # Oracle service consumes only the comparison fields.  Direct adapter
    # callers receive no command output or input bodies either.
    summary = reason
    if exit_code is not None:
        summary = f"{reason}; exit {exit_code}"
    if counts:
        summary += "; " + ", ".join(
            f"{key}={counts[key]}" for key in sorted(counts)
        )
    if command is not None:
        summary = f"{command}: {summary}"
    return CheckResult(name, state, summary, evidence)


def _unknown(name: str, case_id: str, reason: str) -> CheckResult:
    return _comparison(
        name,
        ResultState.UNKNOWN,
        case_id,
        left=None,
        right=None,
        equal=False,
        allowed=True,
        reason=reason,
    )


def _error(name: str, case_id: str, reason: str) -> CheckResult:
    return _comparison(
        name,
        ResultState.ERROR,
        case_id,
        left=None,
        right=None,
        equal=False,
        allowed=True,
        reason=reason,
    )


def _safe_path(
    context: AdapterContext,
    raw: object,
    *,
    directory: bool,
) -> Path:
    if type(raw) is not str or not raw or "\x00" in raw:
        raise ValueError("input path invalid")
    root = context.repo_root.resolve(strict=True)
    candidate = Path(raw)
    if candidate.is_absolute():
        raise ValueError("input path invalid")
    candidate = root / candidate
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("input path invalid") from error
    current = candidate
    while True:
        if current.is_symlink():
            raise ValueError("input path invalid")
        if current == root:
            break
        current = current.parent
    metadata = resolved.stat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(metadata.st_mode):
        raise ValueError("input path invalid")
    return resolved


def _load_json_value(path: Path, max_bytes: int) -> object:
    before = path.stat()
    if before.st_size > min(_MAX_INPUT_BYTES, max_bytes):
        raise ValueError("input JSON invalid")
    raw = path.read_bytes()
    after = path.stat()
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("input JSON invalid")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in items:
            if key in value:
                raise ValueError("input JSON invalid")
            value[key] = item
        return value

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("input JSON invalid")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("input JSON invalid") from error
    return value


def _load_json_object(path: Path, max_bytes: int) -> dict[str, object]:
    value = _load_json_value(path, max_bytes)
    if type(value) is not dict:
        raise ValueError("input JSON invalid")
    return value


def _validate_schema_tree(
    work: Path,
    context: AdapterContext,
) -> None:
    members = sorted(
        path
        for path in work.rglob("*.json")
        if "node_modules" not in path.parts
    )
    if len(members) > context.budget.max_cases:
        raise ValueError("schema input exceeded max_cases")
    total = 0
    for member in members:
        if member.is_symlink():
            raise ValueError("schema input invalid")
        metadata = member.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("schema input invalid")
        total += metadata.st_size
        if total > _MAX_INPUT_BYTES:
            raise ValueError("schema input invalid")
        _load_json_value(member, _MAX_INPUT_BYTES)


def _signal_command_group(process_id: int, action: signal.Signals) -> None:
    try:
        os.killpg(process_id, action)
    except (OSError, ProcessLookupError):
        pass


def _supports_descendant_containment() -> bool:
    return (
        os.name == "posix"
        and sys.platform.startswith("linux")
        and sys.version_info >= (3, 11)
        and hasattr(os, "killpg")
        and os.path.isfile("/proc/self/stat")
    )


def _cleanup_command_group(process_id: int) -> None:
    """Reap descendants that outlive a completed POSIX wrapper."""
    _signal_command_group(process_id, signal.SIGTERM)
    _signal_command_group(process_id, signal.SIGKILL)


def _terminate(process: subprocess.Popen[bytes]) -> None:
    _signal_command_group(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        _signal_command_group(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            pass


def _run_bounded(
    command: Sequence[str],
    context: AdapterContext,
) -> _CommandOutput:
    if not _supports_descendant_containment():
        raise subprocess.SubprocessError(
            "descendant containment unavailable"
        )
    process = subprocess.Popen(
        list(command),
        cwd=context.repo_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        env={
            "PATH": os.defpath,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        },
        process_group=0,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    limit = context.budget.max_output_bytes
    chunks: dict[str, bytearray] = {
        "stdout": bytearray(),
        "stderr": bytearray(),
    }
    total = 0
    overflowed = False
    overflow = threading.Event()
    lock = threading.Lock()

    def drain(label: str, stream: object) -> None:
        nonlocal total, overflowed
        while True:
            block = stream.read(65536)  # type: ignore[attr-defined]
            if not block:
                return
            with lock:
                room = max(0, limit - total)
                if room:
                    kept = block[:room]
                    chunks[label].extend(kept)
                    total += len(kept)
                if len(block) > room:
                    overflowed = True
                    overflow.set()

    readers = [
        threading.Thread(
            target=drain,
            args=("stdout", process.stdout),
            daemon=True,
        ),
        threading.Thread(
            target=drain,
            args=("stderr", process.stderr),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + context.budget.timeout_seconds
    timed_out = False
    while process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            _terminate(process)
            break
        if overflow.wait(min(0.01, remaining)):
            _terminate(process)
            break
    _cleanup_command_group(process.pid)
    for reader in readers:
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
    if any(reader.is_alive() for reader in readers):
        timed_out = True
        _terminate(process)
    return _CommandOutput(
        process.returncode,
        bytes(chunks["stdout"]),
        bytes(chunks["stderr"]),
        timed_out,
        overflowed,
    )


def _parse_json_output(raw: bytes) -> dict[str, object]:
    if not raw:
        raise ValueError("wrapper JSON invalid")
    try:
        def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in items:
                if key in result:
                    raise ValueError("wrapper JSON invalid")
                result[key] = item
            return result

        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("wrapper JSON invalid")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("wrapper JSON invalid") from error
    if type(value) is not dict:
        raise ValueError("wrapper JSON invalid")
    return value


def _integer_count(
    value: Mapping[str, object],
    field: str,
) -> int:
    raw = value.get(field)
    if type(raw) is not int or raw < 0:
        raise ValueError("wrapper JSON invalid")
    return raw


@dataclass(frozen=True)
class CommandOracleAdapter:
    name: str
    script: str
    mode_flag: str
    kind: str = "oracle"

    def cases(self, context: AdapterContext) -> Sequence[AdapterCase]:
        manifest = context.corpus_dir / f"{self.name}.json"
        if not manifest.is_file():
            return (AdapterCase("default", {}),)
        try:
            manifest_path = manifest.resolve(strict=True)
            manifest_path.relative_to(context.repo_root.resolve(strict=True))
            value = _load_json_object(
                manifest_path,
                context.budget.max_output_bytes,
            )
            raw_cases = value.get("cases")
            if type(raw_cases) is not list or not raw_cases:
                raise ValueError("case manifest invalid")
            cases = []
            for raw_case in raw_cases:
                if (
                    type(raw_case) is not dict
                    or set(raw_case) != {"case_id", "payload"}
                ):
                    raise ValueError("case manifest invalid")
                cases.append(
                    AdapterCase(
                        raw_case["case_id"],
                        raw_case["payload"],
                    )
                )
            return tuple(cases)
        except (OSError, TypeError, ValueError):
            return (
                AdapterCase(
                    "invalid-corpus",
                    {"manifest_invalid": True},
                ),
            )

    def _command(
        self,
        case: AdapterCase,
        context: AdapterContext,
        script: Path,
    ) -> list[str]:
        payload = case.payload
        assert isinstance(payload, Mapping)
        command = [sys.executable, str(script)]
        if self.name == "consumer-agreement":
            # The specialist has no root argument and hard-codes this location.
            # Do not inspect or execute caller inputs when that binding cannot
            # represent the explicit repository root.
            if context.repo_root.resolve() != Path("/home/claude/work"):
                raise RuntimeError("consumer wrapper lacks explicit root")
            contracts = _safe_path(
                context,
                payload.get("contracts"),
                directory=False,
            )
            contract_value = _load_json_object(
                contracts,
                context.budget.max_output_bytes,
            )
            records = contract_value.get("contracts")
            if type(records) is not list:
                raise ValueError("contracts invalid")
            for record in records:
                if type(record) is not dict:
                    raise ValueError("contracts invalid")
                _safe_path(
                    context,
                    record.get("file"),
                    directory=False,
                )
            command.extend(["--contracts", str(contracts), "--gate"])
        elif self.name == "schema-oracle":
            work = _safe_path(
                context,
                payload.get("work"),
                directory=True,
            )
            _validate_schema_tree(work, context)
            command.extend(["--work", str(work), "--json"])
        elif self.name == "rollback-oracle":
            failure = payload.get("failure")
            if type(failure) is not str or _SAFE_TOKEN.fullmatch(failure) is None:
                raise ValueError("failure invalid")
            touched = payload.get("touched")
            if type(touched) is not int or not 0 <= touched <= 1_000_000:
                raise ValueError("touched invalid")
            command.extend(
                ["--failure", failure, "--touched", str(touched)]
            )
            tier = payload.get("tier")
            if tier is not None:
                if type(tier) is not str or _SAFE_TOKEN.fullmatch(tier) is None:
                    raise ValueError("tier invalid")
                command.extend(["--tier", tier])
            command.append("--json")
        elif self.name in _DIFF_NAMES:
            old = _safe_path(
                context,
                payload.get("old"),
                directory=False,
            )
            new = _safe_path(
                context,
                payload.get("new"),
                directory=False,
            )
            _load_json_object(old, context.budget.max_output_bytes)
            _load_json_object(new, context.budget.max_output_bytes)
            if os.path.samefile(old, new):
                raise ValueError("diff inputs must be distinct")
            command.extend([str(old), str(new), "--json"])
        elif self.name == "url-classifier-truth":
            if payload:
                raise ValueError("url classifier takes no inputs")
            command.append("--json")
        else:
            raise ValueError("unknown oracle adapter")
        return command

    def run(
        self,
        case: AdapterCase,
        context: AdapterContext,
    ) -> CheckResult:
        relative_script = Path(self.script).as_posix()
        lexical_script = context.repo_root / relative_script
        try:
            metadata = lexical_script.lstat()
        except FileNotFoundError:
            return _unknown(
                self.name,
                case.case_id,
                "optional wrapper unavailable",
            )
        except OSError:
            return _error(
                self.name,
                case.case_id,
                "wrapper path invalid",
            )
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
        ):
            return _error(
                self.name,
                case.case_id,
                "wrapper path invalid",
            )
        try:
            script = _safe_path(
                context,
                relative_script,
                directory=False,
            )
        except (OSError, ValueError):
            return _error(
                self.name,
                case.case_id,
                "wrapper path invalid",
            )
        try:
            command = self._command(case, context, script)
        except RuntimeError:
            return _unknown(
                self.name,
                case.case_id,
                "wrapper lacks explicit root",
            )
        except (OSError, TypeError, ValueError):
            return _error(self.name, case.case_id, "adapter input invalid")

        try:
            output = _run_bounded(command, context)
        except (OSError, subprocess.SubprocessError):
            return _error(
                self.name,
                case.case_id,
                "wrapped command failed",
            )
        if output.timed_out:
            return _comparison(
                self.name,
                ResultState.TIMEOUT,
                case.case_id,
                left=None,
                right=None,
                equal=False,
                allowed=True,
                reason="wrapped command timed out",
                command=relative_script,
            )
        if output.overflowed:
            return _error(
                self.name,
                case.case_id,
                "wrapped command output exceeded budget",
            )
        if self.name == "consumer-agreement":
            text = output.stdout.decode("utf-8", "replace")
            if output.returncode == 0 and "GATE PASS:" in text:
                return _comparison(
                    self.name,
                    ResultState.PASS,
                    case.case_id,
                    left=0,
                    right=0,
                    equal=True,
                    allowed=False,
                    reason="consumer contracts agree",
                    exit_code=0,
                    command=relative_script,
                )
            if output.returncode == 1 and "GATE FAIL:" in text:
                return _comparison(
                    self.name,
                    ResultState.FAIL,
                    case.case_id,
                    left=1,
                    right=0,
                    equal=False,
                    allowed=False,
                    reason="consumer contracts diverge",
                    exit_code=1,
                    command=relative_script,
                )
            return _error(
                self.name,
                case.case_id,
                "wrapped command failed",
            )

        try:
            value = _parse_json_output(output.stdout)
            return self._map_json_result(
                case,
                output.returncode,
                value,
                relative_script,
            )
        except (TypeError, ValueError):
            return _error(
                self.name,
                case.case_id,
                "wrapped command JSON invalid",
            )

    def _map_json_result(
        self,
        case: AdapterCase,
        returncode: int,
        value: Mapping[str, object],
        command: str,
    ) -> CheckResult:
        payload = case.payload
        assert isinstance(payload, Mapping)
        allow = payload.get("allow", False)
        if type(allow) is not bool:
            raise ValueError("allow invalid")

        if self.name == "schema-oracle":
            if set(value) != {
                "schemas",
                "invalid",
                "conflicts",
                "summary",
            }:
                raise ValueError("wrapper JSON invalid")
            invalid = value["invalid"]
            conflicts = value["conflicts"]
            if (
                type(value["schemas"]) is not int
                or value["schemas"] < 0
                or type(invalid) is not list
                or type(conflicts) is not list
            ):
                raise ValueError("wrapper JSON invalid")
            for record in invalid:
                if (
                    type(record) is not dict
                    or set(record) != {"file", "error"}
                    or any(type(item) is not str for item in record.values())
                ):
                    raise ValueError("wrapper JSON invalid")
            for record in conflicts:
                if (
                    type(record) is not dict
                    or set(record) != {"key", "types"}
                    or type(record["key"]) is not str
                    or type(record["types"]) is not list
                    or any(
                        type(item) is not str
                        for item in record["types"]
                    )
                ):
                    raise ValueError("wrapper JSON invalid")
            summary = value.get("summary")
            if type(summary) is not dict:
                raise ValueError("wrapper JSON invalid")
            counts = {
                field: _integer_count(summary, field)
                for field in ("schemas", "invalid", "conflicts")
            }
            if (
                counts["schemas"] != value["schemas"]
                or counts["invalid"] != len(invalid)
                or counts["conflicts"] != len(conflicts)
            ):
                raise ValueError("wrapper JSON invalid")
            bad = counts["invalid"] + counts["conflicts"]
            if returncode not in ({1} if bad else {0}):
                raise ValueError("wrapper exit contradicts JSON")
            return _comparison(
                self.name,
                ResultState.FAIL if bad else ResultState.PASS,
                case.case_id,
                left=bad,
                right=0,
                equal=bad == 0,
                allowed=False,
                reason=(
                    "schema conflicts found"
                    if bad
                    else "schemas agree"
                ),
                exit_code=returncode,
                counts=counts,
                command=command,
            )
        if self.name in _DIFF_NAMES:
            if set(value) != {
                "added_keys",
                "removed_keys",
                "changed",
                "summary",
            }:
                raise ValueError("wrapper JSON invalid")
            added = value["added_keys"]
            removed = value["removed_keys"]
            changed = value["changed"]
            if (
                type(added) is not list
                or type(removed) is not list
                or type(changed) is not list
                or any(type(item) is not str for item in added)
                or any(type(item) is not str for item in removed)
                or added != sorted(set(added))
                or removed != sorted(set(removed))
            ):
                raise ValueError("wrapper JSON invalid")
            for record in changed:
                if (
                    type(record) is not dict
                    or set(record) != {"field", "was", "now"}
                    or record["field"] not in _DIFF_FIELDS[self.name]
                ):
                    raise ValueError("wrapper JSON invalid")
            summary = value.get("summary")
            if type(summary) is not dict:
                raise ValueError("wrapper JSON invalid")
            counts = {
                field: _integer_count(summary, field)
                for field in ("added", "removed", "changed")
            }
            if (
                counts["added"] != len(added)
                or counts["removed"] != len(removed)
                or counts["changed"] != len(changed)
            ):
                raise ValueError("wrapper JSON invalid")
            drift = sum(counts.values())
            if returncode not in ({1} if drift else {0}):
                raise ValueError("wrapper exit contradicts JSON")
            if drift and allow:
                state = ResultState.ADVISORY
            elif drift:
                state = ResultState.FAIL
            else:
                state = ResultState.PASS
            return _comparison(
                self.name,
                state,
                case.case_id,
                left=drift,
                right=0,
                equal=drift == 0,
                allowed=allow,
                reason=(
                    "allowed artifact drift"
                    if drift and allow
                    else "artifact drift"
                    if drift
                    else "artifacts agree"
                ),
                exit_code=returncode,
                counts=counts,
                command=command,
            )
        if self.name == "rollback-oracle":
            if set(value) != {
                "decision",
                "reasons",
                "failure",
                "touched",
                "tier",
            }:
                raise ValueError("wrapper JSON invalid")
            decision = value.get("decision")
            reasons = value.get("reasons")
            if (
                returncode != 0
                or decision not in {"ROLLBACK", "PATCH-FORWARD", "HOLD"}
                or type(reasons) is not list
                or any(
                    type(reason) is not str
                    or not reason
                    or len(reason) > 4096
                    or not reason.isprintable()
                    for reason in reasons
                )
                or type(value.get("touched")) is not int
                or value.get("failure") != payload.get("failure", "").lower()
                or value.get("touched") != payload.get("touched")
                or value.get("tier") != payload.get("tier")
            ):
                raise ValueError("wrapper JSON invalid")
            return _comparison(
                self.name,
                ResultState.ADVISORY,
                case.case_id,
                left=decision,
                right=decision,
                equal=True,
                allowed=True,
                reason="rollback recommendation produced",
                exit_code=returncode,
                command=command,
            )
        if self.name == "url-classifier-truth":
            if set(value) != {"dangerous", "overblock", "summary"}:
                raise ValueError("wrapper JSON invalid")
            dangerous = value["dangerous"]
            overblocked = value["overblock"]
            if type(dangerous) is not list or type(overblocked) is not list:
                raise ValueError("wrapper JSON invalid")
            if any(
                type(record) is not dict
                or set(record) != {"url", "got", "class"}
                or any(type(item) is not str for item in record.values())
                for record in dangerous
            ) or any(
                type(record) is not dict
                or set(record) != {"url", "got", "class", "why"}
                or any(type(item) is not str for item in record.values())
                for record in overblocked
            ):
                raise ValueError("wrapper JSON invalid")
            summary = value.get("summary")
            if type(summary) is not dict:
                raise ValueError("wrapper JSON invalid")
            counts = {
                field: _integer_count(summary, field)
                for field in ("tested", "bypass", "overblock")
            }
            bypass = counts["bypass"]
            overblock = counts["overblock"]
            if (
                bypass != len(dangerous)
                or overblock != len(overblocked)
                or counts["tested"] != 26
            ):
                raise ValueError("wrapper JSON invalid")
            if returncode not in ({1} if bypass else {0}):
                raise ValueError("wrapper exit contradicts JSON")
            state = (
                ResultState.FAIL
                if bypass
                else ResultState.ADVISORY
                if overblock
                else ResultState.PASS
            )
            return _comparison(
                self.name,
                state,
                case.case_id,
                left=bypass,
                right=0,
                equal=bypass == 0,
                allowed=False,
                reason=(
                    "URL guard bypass found"
                    if bypass
                    else "URL guard overblock found"
                    if overblock
                    else "URL classifier matches truth"
                ),
                exit_code=returncode,
                counts=counts,
                command=command,
            )
        raise ValueError("unknown oracle adapter")


def register_builtin_oracles() -> tuple[str, ...]:
    """Register all specialist wrappers and return their sorted names."""
    registered = set(list_adapters())
    for name, (script, mode_flag) in BUILTIN_ORACLE_COMMANDS.items():
        if name not in registered:
            continue
        existing = get_adapter(name)
        delegate = getattr(existing, "_delegate", None)
        if (
            not isinstance(delegate, CommandOracleAdapter)
            or delegate.script != script
            or delegate.mode_flag != mode_flag
            or delegate.kind != "oracle"
        ):
            raise ValueError(f"adapter collision: {name}")
    for name, (script, mode_flag) in BUILTIN_ORACLE_COMMANDS.items():
        if name not in registered:
            register_adapter(CommandOracleAdapter(name, script, mode_flag))
    return tuple(sorted(BUILTIN_ORACLE_COMMANDS))
