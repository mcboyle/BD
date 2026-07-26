"""Evidence-preserving, bounded route reachability analysis."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import multiprocessing
import os
import queue
import re
import stat
import subprocess
import sys
import time
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, TypeAlias
from urllib.parse import urlsplit

from .artifacts import artifact_hash, atomic_write_json, canonical_bytes
from .results import CheckResult, ResultState
from .schemas import SchemaError, make_envelope, validate_envelope, validate_projection
from .snapshot import build_snapshot


JsonValue: TypeAlias = (
    bool | int | float | str | None | list["JsonValue"] | dict[str, "JsonValue"]
)
RouteClass = Literal[
    "public",
    "authenticated",
    "internal",
    "unreachable",
    "unknown",
]

SCHEMA = "bd.reachability"
SCHEMA_VERSION = 1
TOOL_VERSION = "1.0.0"
_TARGET = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:"
    r"[A-Za-z_][A-Za-z0-9_]*$"
)
_SAFE_EXCEPTION_NAMES = {
    "AssertionError": "AssertionError",
    "AttributeError": "AttributeError",
    "FileNotFoundError": "FileNotFoundError",
    "ImportError": "ImportError",
    "KeyError": "KeyError",
    "LookupError": "LookupError",
    "ModuleNotFoundError": "ModuleNotFoundError",
    "NotImplementedError": "NotImplementedError",
    "OSError": "OSError",
    "PermissionError": "PermissionError",
    "ProbeError": "ProbeError",
    "RouteParametersUnresolved": "RouteParametersUnresolved",
    "RuntimeError": "RuntimeError",
    "TypeError": "TypeError",
    "UnsafeMethodNotProbed": "UnsafeMethodNotProbed",
    "ValueError": "ValueError",
}
_SAFE_METHODS = frozenset({"GET"})
_MAX_INPUT_BYTES = 16 * 1024 * 1024
_MAX_CHECK_BYTES = 32 * 1024 * 1024
_MAX_ROUTES = 5_000
_MAX_FACTS = 50_000
_MAX_NODES = 100_000
_MAX_EDGES = 250_000
_MAX_TEXT = 1_000
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
    {
        "app_target",
        "authenticated_fixture",
        "probe_status",
        "adapter_status",
        "global_evidence",
        "routes",
        "summary",
    }
)
_ROUTE_FIELDS = frozenset(
    {
        "rule",
        "methods",
        "classification",
        "confidence",
        "reason",
        "privilege_boundary",
        "evidence",
    }
)
_EVIDENCE_FIELDS = frozenset(
    {
        "auth_probe",
        "auth_gate_facts",
        "operator_wiring",
        "navigation",
        "call_paths",
        "deferrals",
    }
)


class _InputError(ValueError):
    """A content-free, stage-labelled input failure."""

    def __init__(self, stage: str):
        super().__init__(stage)
        self.stage = stage


@dataclass(frozen=True)
class _JsonInput:
    path: Path
    value: dict[str, JsonValue]
    digest: str
    identity: tuple[int, int, int, int, int, int]


@dataclass(frozen=True)
class _SourceInput:
    path: Path
    digest: str
    identity: tuple[int, int, int, int, int, int]


@dataclass(frozen=True)
class ProbeObservation:
    """A deliberately body-, cookie-, and header-free probe result."""

    status: int | None
    location: str | None = None
    exception: str | None = None


def _is_denial(observation: ProbeObservation) -> bool:
    if observation.status in {401, 403}:
        return True
    return bool(
        observation.status is not None
        and 300 <= observation.status < 400
        and observation.location is not None
        and any(
            token in observation.location.casefold()
            for token in ("/login", "/signin", "/auth")
        )
    )


def classify_route(
    *,
    rule: str,
    methods: Sequence[str],
    unauthenticated: ProbeObservation,
    authenticated: ProbeObservation | None,
    auth_gate_facts: Sequence[str],
    operator_wiring: str | None,
    navigation: str | None,
    call_paths: Sequence[Sequence[str]],
) -> dict[str, JsonValue]:
    """Classify one route without promoting one evidence class into another."""
    evidence: dict[str, JsonValue] = {
        "auth_probe": {
            "unauthenticated": asdict(unauthenticated),
            "authenticated": (
                asdict(authenticated) if authenticated is not None else None
            ),
        },
        "auth_gate_facts": sorted(set(auth_gate_facts)),
        "operator_wiring": operator_wiring,
        "navigation": navigation,
        "call_paths": sorted(
            (list(path) for path in call_paths),
            key=lambda path: tuple(path),
        ),
        "deferrals": [],
    }
    if unauthenticated.exception is not None:
        classification: RouteClass = "unknown"
        confidence = "low"
        reason = "unauthenticated probe unavailable"
    elif (
        unauthenticated.status is not None
        and 200 <= unauthenticated.status < 300
    ):
        classification = "public"
        confidence = "high"
        reason = "unauthenticated request succeeded"
    elif (
        authenticated is not None
        and authenticated.exception is None
        and authenticated.status is not None
        and 200 <= authenticated.status < 300
        and _is_denial(unauthenticated)
    ):
        classification = "authenticated"
        confidence = "high"
        reason = "authenticated success followed unauthenticated denial"
    else:
        # Dual denial does not prove that a route is internal, and a literal 404
        # does not prove a parameterized or data-dependent route is unreachable.
        classification = "unknown"
        confidence = "low"
        reason = "evidence does not establish a privilege class"
    privilege_boundary = bool(
        auth_gate_facts or _is_denial(unauthenticated)
    )
    return {
        "rule": rule,
        "methods": sorted(set(methods)),
        "classification": classification,
        "confidence": confidence,
        "reason": reason,
        "privilege_boundary": privilege_boundary,
        "evidence": evidence,
    }


def _validate_target(value: object) -> str:
    if not isinstance(value, str) or _TARGET.fullmatch(value) is None:
        raise _InputError("app_target")
    return value


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _reject_symlink_chain(path: Path, stage: str) -> None:
    candidate = Path(os.path.abspath(path))
    while True:
        try:
            if os.path.lexists(candidate) and stat.S_ISLNK(
                candidate.lstat().st_mode
            ):
                raise _InputError(stage)
        except OSError as error:
            raise _InputError(stage) from error
        if candidate.parent == candidate:
            return
        candidate = candidate.parent


def _decode_json(raw: bytes, stage: str) -> object:
    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _InputError(stage)
            result[key] = value
        return result

    def reject_constant(_value: str) -> object:
        raise _InputError(stage)

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        _InputError,
    ) as error:
        raise _InputError(stage) from error


def _read_json(
    path: Path,
    stage: str,
    *,
    max_bytes: int = _MAX_INPUT_BYTES,
) -> _JsonInput:
    candidate = Path(os.path.abspath(path))
    _reject_symlink_chain(candidate, stage)
    try:
        before = candidate.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise _InputError(stage)
        raw = candidate.read_bytes()
        after = candidate.stat()
    except (OSError, RuntimeError) as error:
        raise _InputError(stage) from error
    before_identity = _identity(before)
    if (
        before_identity != _identity(after)
        or len(raw) != before.st_size
        or len(raw) > max_bytes
    ):
        raise _InputError(stage)
    value = _decode_json(raw, stage)
    if not isinstance(value, dict):
        raise _InputError(stage)
    return _JsonInput(
        candidate,
        value,
        hashlib.sha256(raw).hexdigest(),
        before_identity,
    )


def _target_source(
    repo_root: Path,
    target: str,
    stage: str,
) -> _SourceInput:
    module_name = _validate_target(target).split(":", 1)[0]
    relative = Path(*module_name.split("."))
    package = repo_root / relative / "__init__.py"
    module = (repo_root / relative).with_suffix(".py")
    candidate = package if package.is_file() else module
    candidate = Path(os.path.abspath(candidate))
    try:
        candidate.resolve(strict=True).relative_to(repo_root.resolve(strict=True))
        _reject_symlink_chain(candidate, stage)
        before = candidate.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_INPUT_BYTES:
            raise _InputError(stage)
        raw = candidate.read_bytes()
        after = candidate.stat()
    except (OSError, RuntimeError, ValueError) as error:
        raise _InputError(stage) from error
    before_identity = _identity(before)
    if before_identity != _identity(after) or len(raw) != before.st_size:
        raise _InputError(stage)
    return _SourceInput(
        candidate,
        hashlib.sha256(raw).hexdigest(),
        before_identity,
    )


def _validate_security(value: dict[str, JsonValue]) -> None:
    validate_projection("security_surface", value)
    facts = value["auth_gates"]
    if not isinstance(facts, list) or len(facts) > _MAX_FACTS:
        raise SchemaError("auth gate inventory invalid")
    for fact in facts:
        if not isinstance(fact, Mapping):
            raise SchemaError("auth gate fact invalid")
        for field in ("function", "name", "path", "method"):
            item = fact.get(field)
            if (
                not isinstance(item, str)
                or not item
                or len(item) > _MAX_TEXT
                or any(ord(char) < 32 for char in item)
            ):
                raise SchemaError("auth gate fact invalid")


def _validate_call_graph(value: dict[str, JsonValue]) -> None:
    validate_projection("call_graph", value)
    nodes = value["nodes"]
    edges = value["edges"]
    unresolved = value["unresolved"]
    if (
        not isinstance(nodes, list)
        or len(nodes) > _MAX_NODES
        or not isinstance(edges, list)
        or len(edges) > _MAX_EDGES
        or not isinstance(unresolved, list)
        or len(unresolved) > _MAX_EDGES
    ):
        raise SchemaError("call graph inventory invalid")
    node_set: set[str] = set()
    for node in nodes:
        if (
            not isinstance(node, str)
            or not node
            or len(node) > _MAX_TEXT
            or node in node_set
        ):
            raise SchemaError("call graph node invalid")
        node_set.add(node)
    for edge in edges:
        if not isinstance(edge, Mapping):
            raise SchemaError("call graph edge invalid")
        source = edge.get("from")
        target = edge.get("to")
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or source not in node_set
            or target not in node_set
        ):
            raise SchemaError("call graph edge invalid")


def _load_projection(path: Path, name: str) -> _JsonInput:
    loaded = _read_json(path, name)
    if name == "security_surface":
        _validate_security(loaded.value)
    elif name == "call_graph":
        _validate_call_graph(loaded.value)
    else:
        raise _InputError(name)
    return loaded


def _load_deferrals(path: Path | None) -> tuple[_JsonInput | None, list[dict[str, str]]]:
    if path is None:
        return None, []
    loaded = _read_json(path, "deferrals")
    deferrals = loaded.value.get("deferrals")
    if not isinstance(deferrals, Mapping) or len(deferrals) > _MAX_FACTS:
        raise _InputError("deferrals")
    summaries: list[dict[str, str]] = []
    for key, record in sorted(deferrals.items()):
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 200
            or not isinstance(record, Mapping)
        ):
            raise _InputError("deferrals")
        finding = record.get("finding", key)
        status_value = record.get("status", "unknown")
        if (
            not isinstance(finding, str)
            or not finding
            or len(finding) > 200
            or not isinstance(status_value, str)
            or not status_value
            or len(status_value) > 100
        ):
            raise _InputError("deferrals")
        summaries.append({"finding": finding, "status": status_value})
    summaries = [
        {"finding": finding, "status": status}
        for finding, status in sorted(
            {(row["finding"], row["status"]) for row in summaries}
        )
    ]
    return loaded, summaries


def _safe_location(raw: object) -> str | None:
    if not isinstance(raw, str) or not raw or len(raw) > 8_192:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    path = parsed.path or "/"
    if (
        not path.startswith("/")
        or "\\" in path
        or any(ord(char) < 32 for char in path)
        or len(path) > _MAX_TEXT
    ):
        return None
    # Redirect paths can themselves contain magic-login credentials or signed
    # path tokens. Retain only static landing paths needed for auth-delta
    # classification; all other destinations remain explicit but redacted.
    if path in {"/", "/auth", "/login", "/signin"}:
        return path
    return "/<redacted>"


def _safe_exception_name(value: object) -> str:
    if type(value) is str:
        return _SAFE_EXCEPTION_NAMES.get(value, "ProbeError")
    return "ProbeError"


def _probe_client(client: object, rule: str, method: str) -> dict[str, object]:
    if method not in _SAFE_METHODS:
        return asdict(
            ProbeObservation(
                None,
                None,
                _safe_exception_name("UnsafeMethodNotProbed"),
            )
        )
    if "<" in rule or ">" in rule:
        return asdict(
            ProbeObservation(
                None,
                None,
                _safe_exception_name("RouteParametersUnresolved"),
            )
        )
    try:
        response = client.open(rule, method=method, follow_redirects=False)
        location = _safe_location(response.headers.get("Location"))
        return asdict(ProbeObservation(int(response.status_code), location, None))
    except BaseException as error:  # child boundary; retain type only
        return asdict(
            ProbeObservation(
                None,
                None,
                _safe_exception_name(type(error).__name__),
            )
        )


def _optional_adapters(
    app_target: str,
    repo_root: str,
) -> tuple[dict[str, str], dict[str, str | None], dict[str, str | None]]:
    statuses = {
        "operator_wiring": "unavailable",
        "navigation": "unavailable",
    }
    operator: dict[str, str | None] = {}
    navigation: dict[str, str | None] = {}
    if app_target != "bulk_downloader.app:app":
        return statuses, operator, navigation
    try:
        endpoint_module = importlib.import_module("tools.endpoint_reachability")
        payload = endpoint_module.build(repo_root)
        endpoints = payload.get("endpoints", [])
        if not isinstance(endpoints, list) or len(endpoints) > _MAX_ROUTES:
            raise ValueError("bounded adapter payload")
        for row in endpoints:
            if not isinstance(row, Mapping):
                raise ValueError("adapter row")
            rule = row.get("rule")
            reach = row.get("reach")
            if (
                not isinstance(rule, str)
                or len(rule) > _MAX_TEXT
                or reach not in {"spa", "console", "extension", "dark"}
            ):
                raise ValueError("adapter row")
            operator[rule] = reach
        statuses["operator_wiring"] = "available"
    except BaseException:
        statuses["operator_wiring"] = "unavailable"
    try:
        nav_module = importlib.import_module("tools.nav_reachability")
        findings = [
            *nav_module.check_server(False),
            *nav_module.check_spa(False),
            *nav_module.check_external_nav(False),
        ]
        if len(findings) > _MAX_ROUTES or any(
            not isinstance(item, str) or len(item) > _MAX_TEXT
            for item in findings
        ):
            raise ValueError("bounded adapter payload")
        for finding in findings:
            for rule in operator:
                if rule in finding:
                    navigation[rule] = "orphan"
        statuses["navigation"] = "available"
    except BaseException:
        statuses["navigation"] = "unavailable"
    return statuses, operator, navigation


def _probe_in_child(
    output: multiprocessing.Queue,
    app_target: str,
    authenticated_fixture: str | None,
    repo_root: str,
    app_source: str,
    fixture_source: str | None,
) -> None:
    try:
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        module_name, attribute = app_target.split(":", 1)
        app_module = importlib.import_module(module_name)
        if Path(str(app_module.__file__)).resolve() != Path(app_source).resolve():
            raise ImportError("application module origin mismatch")
        candidate = getattr(app_module, attribute)
        app = candidate
        if not hasattr(app, "test_client") and callable(app):
            app = app()
        if not hasattr(app, "test_client") or not hasattr(app, "url_map"):
            raise TypeError("not a Flask application")
        fixture = None
        if authenticated_fixture is not None:
            fixture_module, fixture_name = authenticated_fixture.split(":", 1)
            imported_fixture = importlib.import_module(fixture_module)
            if (
                fixture_source is None
                or Path(str(imported_fixture.__file__)).resolve()
                != Path(fixture_source).resolve()
            ):
                raise ImportError("fixture module origin mismatch")
            fixture = getattr(imported_fixture, fixture_name)
            if not callable(fixture):
                raise TypeError("authenticated fixture is not callable")
        rows: list[dict[str, object]] = []
        rules = sorted(
            app.url_map.iter_rules(),
            key=lambda item: (str(item.rule), str(item.endpoint)),
        )
        if len(rules) > _MAX_ROUTES:
            raise ValueError("route limit exceeded")
        for rule_object in rules:
            rule = str(rule_object.rule)
            endpoint = str(rule_object.endpoint)
            if (
                not rule.startswith("/")
                or len(rule) > _MAX_TEXT
                or len(endpoint) > _MAX_TEXT
            ):
                raise ValueError("route invalid")
            methods = sorted(
                set(rule_object.methods or set()) - {"HEAD", "OPTIONS"}
            )
            if len(methods) > 16:
                raise ValueError("method limit exceeded")
            for method in methods:
                client = app.test_client()
                unauthenticated = _probe_client(client, rule, method)
                authenticated = None
                if fixture is not None:
                    auth_client = app.test_client()
                    try:
                        configured = fixture(auth_client)
                        if configured is not None:
                            auth_client = configured
                        authenticated = _probe_client(auth_client, rule, method)
                    except BaseException as error:
                        authenticated = asdict(
                            ProbeObservation(
                                None,
                                None,
                                _safe_exception_name(type(error).__name__),
                            )
                        )
                rows.append(
                    {
                        "rule": rule,
                        "endpoint": endpoint,
                        "method": method,
                        "unauthenticated": unauthenticated,
                        "authenticated": authenticated,
                    }
                )
                if len(rows) > _MAX_ROUTES * 8:
                    raise ValueError("probe row limit exceeded")
        statuses, operator, navigation = _optional_adapters(
            app_target,
            repo_root,
        )
        output.put(
            {
                "status": "ok",
                "rows": rows,
                "adapter_status": statuses,
                "operator_wiring": operator,
                "navigation": navigation,
            }
        )
    except BaseException as error:
        output.put(
            {
                "status": "error",
                "exception": _safe_exception_name(type(error).__name__),
            }
        )


def _bounded_probe(
    *,
    app_target: str,
    authenticated_fixture: str | None,
    repo_root: Path,
    timeout_seconds: float,
    app_source: Path,
    fixture_source: Path | None,
) -> tuple[str, dict[str, object] | None]:
    context = multiprocessing.get_context("spawn")
    output = context.Queue(maxsize=1)
    process = context.Process(
        target=_probe_in_child,
        args=(
            output,
            app_target,
            authenticated_fixture,
            str(repo_root),
            str(app_source),
            str(fixture_source) if fixture_source is not None else None,
        ),
    )
    process.start()
    deadline = time.monotonic() + timeout_seconds
    payload: dict[str, object] | None = None
    try:
        while time.monotonic() < deadline:
            try:
                candidate = output.get_nowait()
            except queue.Empty:
                if not process.is_alive():
                    break
                time.sleep(0.01)
                continue
            if isinstance(candidate, dict):
                payload = candidate
            break
        if payload is None and process.is_alive():
            process.terminate()
            process.join(1.0)
            if process.is_alive():
                process.kill()
                process.join(1.0)
            return "timeout", None
        process.join(1.0)
        if process.is_alive():
            process.terminate()
            process.join(1.0)
            if process.is_alive():
                process.kill()
                process.join(1.0)
            return "error", None
        if payload is None or process.exitcode != 0:
            return "crash", None
        return "ok", payload
    finally:
        output.cancel_join_thread()
        output.close()


def _observation(value: object) -> ProbeObservation:
    if not isinstance(value, Mapping) or set(value) != {
        "status",
        "location",
        "exception",
    }:
        raise _InputError("probe_payload")
    status_value = value["status"]
    location = value["location"]
    exception = value["exception"]
    if (
        (status_value is not None and (
            not isinstance(status_value, int)
            or isinstance(status_value, bool)
            or not 100 <= status_value <= 599
        ))
        or (location is not None and (
            not isinstance(location, str)
            or _safe_location(location) != location
        ))
    ):
        raise _InputError("probe_payload")
    return ProbeObservation(
        status_value,
        location,
        (
            _safe_exception_name(exception)
            if exception is not None
            else None
        ),
    )


def _auth_facts(
    security: Mapping[str, JsonValue],
    endpoint: str,
) -> tuple[str, ...]:
    matches: list[str] = []
    facts = security["auth_gates"]
    assert isinstance(facts, list)
    for fact in facts:
        assert isinstance(fact, Mapping)
        function = fact["function"]
        name = fact["name"]
        method = fact["method"]
        assert isinstance(function, str)
        assert isinstance(name, str)
        assert isinstance(method, str)
        if name == endpoint or function.endswith(f"::{endpoint}"):
            matches.append(f"{function}:{method}")
    return tuple(sorted(set(matches)))


def _call_paths(
    call_graph: Mapping[str, JsonValue],
    endpoint: str,
) -> tuple[tuple[str, ...], ...]:
    nodes = call_graph["nodes"]
    edges = call_graph["edges"]
    assert isinstance(nodes, list)
    assert isinstance(edges, list)
    starts = sorted(
        node
        for node in nodes
        if isinstance(node, str) and node.endswith(f"::{endpoint}")
    )
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        assert isinstance(edge, Mapping)
        source = edge["from"]
        target = edge["to"]
        assert isinstance(source, str)
        assert isinstance(target, str)
        adjacency[source].append(target)
    for targets in adjacency.values():
        targets.sort()
    paths: list[tuple[str, ...]] = []
    for start in starts[:100]:
        frontier: deque[tuple[str, ...]] = deque([(start,)])
        visited = {start}
        while frontier and len(paths) < 500:
            path = frontier.popleft()
            for target in adjacency.get(path[-1], []):
                candidate = (*path, target)
                if len(candidate) > 8:
                    continue
                paths.append(candidate)
                if target not in visited and len(candidate) < 8:
                    visited.add(target)
                    frontier.append(candidate)
    return tuple(sorted(paths))


def _validate_probe_payload(
    payload: dict[str, object],
) -> tuple[
    list[dict[str, object]],
    dict[str, str],
    dict[str, str | None],
    dict[str, str | None],
]:
    if payload.get("status") != "ok":
        raise _InputError("probe_child")
    rows = payload.get("rows")
    statuses = payload.get("adapter_status")
    operator = payload.get("operator_wiring")
    navigation = payload.get("navigation")
    if (
        not isinstance(rows, list)
        or len(rows) > _MAX_ROUTES * 8
        or not isinstance(statuses, dict)
        or set(statuses) != {"operator_wiring", "navigation"}
        or any(
            value not in {"available", "unavailable"}
            for value in statuses.values()
        )
        or not isinstance(operator, dict)
        or not isinstance(navigation, dict)
    ):
        raise _InputError("probe_payload")
    return rows, statuses, operator, navigation


def _build_artifact(
    *,
    app_target: str,
    authenticated_fixture: str | None,
    security: _JsonInput,
    call_graph: _JsonInput,
    deferrals: _JsonInput | None,
    deferral_summaries: list[dict[str, str]],
    app_source: _SourceInput,
    fixture_source: _SourceInput | None,
    tracked_source_sha: str,
    payload: dict[str, object],
) -> dict[str, JsonValue]:
    if security.value["source_sha"] != call_graph.value["source_sha"]:
        raise _InputError("projection_binding")
    rows, adapter_status, operator, navigation = _validate_probe_payload(payload)
    classified: list[dict[str, JsonValue]] = []
    seen: set[tuple[str, str]] = set()
    for raw_row in rows:
        if not isinstance(raw_row, Mapping) or set(raw_row) != {
            "rule",
            "endpoint",
            "method",
            "unauthenticated",
            "authenticated",
        }:
            raise _InputError("probe_payload")
        rule = raw_row["rule"]
        endpoint = raw_row["endpoint"]
        method = raw_row["method"]
        if (
            not isinstance(rule, str)
            or not rule.startswith("/")
            or len(rule) > _MAX_TEXT
            or not isinstance(endpoint, str)
            or not endpoint
            or len(endpoint) > _MAX_TEXT
            or not isinstance(method, str)
            or not method
            or len(method) > 32
            or (rule, method) in seen
        ):
            raise _InputError("probe_payload")
        seen.add((rule, method))
        unauthenticated = _observation(raw_row["unauthenticated"])
        authenticated = (
            _observation(raw_row["authenticated"])
            if raw_row["authenticated"] is not None
            else None
        )
        facts = _auth_facts(security.value, endpoint)
        row = classify_route(
            rule=rule,
            methods=(method,),
            unauthenticated=unauthenticated,
            authenticated=authenticated,
            auth_gate_facts=facts,
            operator_wiring=operator.get(rule),
            navigation=navigation.get(rule),
            call_paths=_call_paths(call_graph.value, endpoint),
        )
        # Producer-truth deferrals do not identify routes. Preserve the category
        # without attaching global findings to arbitrary route rows.
        row["evidence"]["deferrals"] = []
        classified.append(row)
    classified.sort(key=lambda row: (str(row["rule"]), tuple(row["methods"])))
    counts = {
        classification: sum(
            row["classification"] == classification for row in classified
        )
        for classification in (
            "public",
            "authenticated",
            "internal",
            "unreachable",
            "unknown",
        )
    }
    privilege_unknown = sum(
        row["classification"] == "unknown" and row["privilege_boundary"] is True
        for row in classified
    )
    input_hashes = {
        "app_source": app_source.digest,
        "call_graph": call_graph.digest,
        "security_surface": security.digest,
        "tracked_tree": tracked_source_sha,
    }
    if fixture_source is not None:
        input_hashes["authenticated_fixture_source"] = fixture_source.digest
    if deferrals is not None:
        input_hashes["deferrals"] = deferrals.digest
    artifact: dict[str, JsonValue] = {
        **make_envelope(
            SCHEMA,
            SCHEMA_VERSION,
            tracked_source_sha,
            TOOL_VERSION,
            input_hashes,
        ),
        "app_target": app_target,
        "authenticated_fixture": authenticated_fixture,
        "probe_status": "complete",
        "adapter_status": {
            **adapter_status,
            "deferrals": (
                "available_unmapped"
                if deferrals is not None
                else "not_configured"
            ),
        },
        "global_evidence": {
            "deferrals": deferral_summaries,
        },
        "routes": classified,
        "summary": {
            **counts,
            "routes": len(classified),
            "privilege_unknown": privilege_unknown,
            "deferrals": len(deferral_summaries),
        },
    }
    validate_reachability_artifact(artifact)
    return artifact


def _exact_mapping(
    value: object,
    fields: frozenset[str],
    message: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise SchemaError(message)
    return value


def _validate_observation(value: object) -> None:
    try:
        _observation(value)
    except _InputError as error:
        raise SchemaError("reachability observation invalid") from error
    assert isinstance(value, Mapping)
    exception = value["exception"]
    if exception is not None and (
        type(exception) is not str
        or exception not in _SAFE_EXCEPTION_NAMES
    ):
        raise SchemaError("reachability observation invalid")


def validate_reachability_artifact(value: object) -> None:
    """Validate the strict ``bd.reachability`` version-1 artifact."""
    validate_envelope(value, SCHEMA, SCHEMA_VERSION)
    artifact = _exact_mapping(value, _ARTIFACT_FIELDS, "artifact fields invalid")
    _validate_target(artifact["app_target"])
    fixture = artifact["authenticated_fixture"]
    if fixture is not None:
        _validate_target(fixture)
    if artifact["probe_status"] != "complete":
        raise SchemaError("probe status invalid")
    statuses = _exact_mapping(
        artifact["adapter_status"],
        frozenset({"operator_wiring", "navigation", "deferrals"}),
        "adapter status invalid",
    )
    if (
        statuses["operator_wiring"] not in {"available", "unavailable"}
        or statuses["navigation"] not in {"available", "unavailable"}
        or statuses["deferrals"]
        not in {"available_unmapped", "not_configured"}
    ):
        raise SchemaError("adapter status invalid")
    expected_hashes = {
        "app_source",
        "call_graph",
        "security_surface",
        "tracked_tree",
    }
    if fixture is not None:
        expected_hashes.add("authenticated_fixture_source")
    if statuses["deferrals"] == "available_unmapped":
        expected_hashes.add("deferrals")
    input_hashes = artifact["input_hashes"]
    if not isinstance(input_hashes, Mapping) or set(input_hashes) != expected_hashes:
        raise SchemaError("reachability input hash fields invalid")
    global_evidence = _exact_mapping(
        artifact["global_evidence"],
        frozenset({"deferrals"}),
        "global evidence invalid",
    )
    global_deferrals = global_evidence["deferrals"]
    if (
        not isinstance(global_deferrals, list)
        or len(global_deferrals) > _MAX_FACTS
    ):
        raise SchemaError("global deferrals invalid")
    previous_deferral: tuple[str, str] | None = None
    for raw_deferral in global_deferrals:
        deferral = _exact_mapping(
            raw_deferral,
            frozenset({"finding", "status"}),
            "global deferral invalid",
        )
        finding = deferral["finding"]
        status_value = deferral["status"]
        if (
            not isinstance(finding, str)
            or not finding
            or len(finding) > 200
            or not isinstance(status_value, str)
            or not status_value
            or len(status_value) > 100
        ):
            raise SchemaError("global deferral invalid")
        key = (finding, status_value)
        if previous_deferral is not None and key <= previous_deferral:
            raise SchemaError("global deferral order invalid")
        previous_deferral = key
    routes = artifact["routes"]
    if not isinstance(routes, list) or len(routes) > _MAX_ROUTES * 8:
        raise SchemaError("routes invalid")
    previous: tuple[str, tuple[str, ...]] | None = None
    privilege_unknown = 0
    counts = defaultdict(int)
    for route in routes:
        row = _exact_mapping(route, _ROUTE_FIELDS, "route invalid")
        rule = row["rule"]
        methods = row["methods"]
        if (
            not isinstance(rule, str)
            or not rule.startswith("/")
            or len(rule) > _MAX_TEXT
            or not isinstance(methods, list)
            or not methods
            or methods != sorted(set(methods))
            or any(
                not isinstance(method, str) or not method or len(method) > 32
                for method in methods
            )
            or row["classification"]
            not in {"public", "authenticated", "internal", "unreachable", "unknown"}
            or row["confidence"] not in {"high", "medium", "low"}
            or not isinstance(row["reason"], str)
            or not row["reason"]
            or len(row["reason"]) > 200
            or not isinstance(row["privilege_boundary"], bool)
        ):
            raise SchemaError("route invalid")
        key = (rule, tuple(methods))
        if previous is not None and key <= previous:
            raise SchemaError("route order invalid")
        previous = key
        evidence = _exact_mapping(
            row["evidence"],
            _EVIDENCE_FIELDS,
            "route evidence invalid",
        )
        auth_probe = _exact_mapping(
            evidence["auth_probe"],
            frozenset({"unauthenticated", "authenticated"}),
            "auth probe invalid",
        )
        _validate_observation(auth_probe["unauthenticated"])
        if auth_probe["authenticated"] is not None:
            _validate_observation(auth_probe["authenticated"])
        facts = evidence["auth_gate_facts"]
        call_paths = evidence["call_paths"]
        deferrals = evidence["deferrals"]
        if (
            not isinstance(facts, list)
            or facts != sorted(set(facts))
            or any(
                not isinstance(fact, str) or not fact or len(fact) > _MAX_TEXT
                for fact in facts
            )
            or evidence["operator_wiring"]
            not in {None, "spa", "console", "extension", "dark"}
            or evidence["navigation"] not in {None, "orphan"}
            or not isinstance(call_paths, list)
            or any(
                not isinstance(path, list)
                or len(path) < 2
                or len(path) > 8
                or any(
                    not isinstance(node, str)
                    or not node
                    or len(node) > _MAX_TEXT
                    for node in path
                )
                for path in call_paths
            )
            or deferrals != []
        ):
            raise SchemaError("route evidence invalid")
        classification = row["classification"]
        assert isinstance(classification, str)
        counts[classification] += 1
        if classification == "unknown" and row["privilege_boundary"]:
            privilege_unknown += 1
    summary = _exact_mapping(
        artifact["summary"],
        frozenset(
            {
                "public",
                "authenticated",
                "internal",
                "unreachable",
                "unknown",
                "routes",
                "privilege_unknown",
                "deferrals",
            }
        ),
        "summary invalid",
    )
    for field in summary:
        if (
            not isinstance(summary[field], int)
            or isinstance(summary[field], bool)
            or summary[field] < 0
        ):
            raise SchemaError("summary invalid")
    if (
        summary["routes"] != len(routes)
        or summary["privilege_unknown"] != privilege_unknown
        or summary["deferrals"] != len(global_deferrals)
        or any(
            summary[name] != counts[name]
            for name in (
                "public",
                "authenticated",
                "internal",
                "unreachable",
                "unknown",
            )
        )
    ):
        raise SchemaError("summary mismatch")


def _verify_input(loaded: _JsonInput) -> bool:
    try:
        current = _read_json(loaded.path, "drift")
    except _InputError:
        return False
    return (
        current.digest == loaded.digest
        and current.identity == loaded.identity
    )


def _verify_source(loaded: _SourceInput) -> bool:
    try:
        before = loaded.path.stat()
        if not stat.S_ISREG(before.st_mode):
            return False
        raw = loaded.path.read_bytes()
        after = loaded.path.stat()
    except OSError:
        return False
    return (
        _identity(before) == loaded.identity
        and _identity(after) == loaded.identity
        and hashlib.sha256(raw).hexdigest() == loaded.digest
    )


def _snapshot_matches(repo_root: Path, expected_sha: str) -> bool:
    try:
        return build_snapshot(repo_root).source_sha == expected_sha
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def analyze_reachability(
    *,
    app_target: str,
    repo_root: Path,
    security_surface_path: Path,
    call_graph_path: Path,
    deferrals_path: Path | None,
    authenticated_fixture: str | None,
    timeout_seconds: float,
) -> tuple[CheckResult, dict[str, JsonValue]]:
    """Analyze route reachability in a bounded child process."""
    try:
        app_target = _validate_target(app_target)
        if authenticated_fixture is not None:
            authenticated_fixture = _validate_target(authenticated_fixture)
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not 0 < float(timeout_seconds) <= 300
        ):
            raise _InputError("timeout")
        repository = Path(os.path.abspath(repo_root))
        _reject_symlink_chain(repository, "repo_root")
        if not repository.is_dir():
            raise _InputError("repo_root")
        app_source = _target_source(repository, app_target, "app_target")
        fixture_source = (
            _target_source(
                repository,
                authenticated_fixture,
                "authenticated_fixture",
            )
            if authenticated_fixture is not None
            else None
        )
        tracked_snapshot = build_snapshot(repository)
    except (_InputError, OSError, subprocess.SubprocessError, ValueError) as error:
        stage = error.stage if isinstance(error, _InputError) else "repo_root"
        summary = {
            "app_target": "app target invalid",
            "timeout": "probe timeout invalid",
            "repo_root": "repository root invalid",
        }.get(stage, "authenticated fixture invalid")
        return (
            CheckResult(
                "reachability",
                ResultState.ERROR,
                summary,
                {"stage": stage},
            ),
            {},
        )
    try:
        security = _load_projection(
            security_surface_path,
            "security_surface",
        )
    except (OSError, ValueError, SchemaError):
        return (
            CheckResult(
                "reachability",
                ResultState.ERROR,
                "security surface invalid",
                {"stage": "security_surface"},
            ),
            {},
        )
    try:
        call_graph = _load_projection(call_graph_path, "call_graph")
    except (OSError, ValueError, SchemaError):
        return (
            CheckResult(
                "reachability",
                ResultState.ERROR,
                "call graph invalid",
                {"stage": "call_graph"},
            ),
            {},
        )
    try:
        deferrals, deferral_summaries = _load_deferrals(deferrals_path)
    except (OSError, ValueError, SchemaError):
        return (
            CheckResult(
                "reachability",
                ResultState.ERROR,
                "deferrals invalid",
                {"stage": "deferrals"},
            ),
            {},
        )
    status, payload = _bounded_probe(
        app_target=app_target,
        authenticated_fixture=authenticated_fixture,
        repo_root=repository,
        timeout_seconds=float(timeout_seconds),
        app_source=app_source.path,
        fixture_source=(
            fixture_source.path if fixture_source is not None else None
        ),
    )
    if status == "timeout":
        return (
            CheckResult(
                "reachability",
                ResultState.TIMEOUT,
                "probe exceeded timeout",
                {"timeout_seconds": float(timeout_seconds)},
            ),
            {},
        )
    if status != "ok" or payload is None:
        return (
            CheckResult(
                "reachability",
                ResultState.ERROR,
                "probe child failed",
                {"stage": "probe_child"},
            ),
            {},
        )
    if payload.get("status") != "ok":
        return (
            CheckResult(
                "reachability",
                ResultState.ERROR,
                "probe child failed",
                {
                    "stage": "probe_child",
                    "exception": _safe_exception_name(
                        payload.get("exception")
                    ),
                },
            ),
            {},
        )
    try:
        artifact = _build_artifact(
            app_target=app_target,
            authenticated_fixture=authenticated_fixture,
            security=security,
            call_graph=call_graph,
            deferrals=deferrals,
            deferral_summaries=deferral_summaries,
            app_source=app_source,
            fixture_source=fixture_source,
            tracked_source_sha=tracked_snapshot.source_sha,
            payload=payload,
        )
    except (ValueError, SchemaError):
        return (
            CheckResult(
                "reachability",
                ResultState.ERROR,
                "probe payload invalid",
                {"stage": "probe_payload"},
            ),
            {},
        )
    immutable_inputs = [security, call_graph]
    if deferrals is not None:
        immutable_inputs.append(deferrals)
    immutable_sources = [app_source]
    if fixture_source is not None:
        immutable_sources.append(fixture_source)
    if (
        not all(_verify_input(item) for item in immutable_inputs)
        or not all(_verify_source(item) for item in immutable_sources)
        or not _snapshot_matches(repository, tracked_snapshot.source_sha)
    ):
        return (
            CheckResult(
                "reachability",
                ResultState.ERROR,
                "reachability input changed",
                {"stage": "drift"},
            ),
            {},
        )
    statuses = artifact["adapter_status"]
    assert isinstance(statuses, dict)
    unavailable = sorted(
        key
        for key, value in statuses.items()
        if value in {"unavailable", "not_configured"}
    )
    summary = artifact["summary"]
    assert isinstance(summary, dict)
    return (
        CheckResult(
            "reachability",
            (
                ResultState.ADVISORY
                if unavailable or summary["unknown"]
                else ResultState.PASS
            ),
            "reachability analysis complete",
            {
                "routes": summary["routes"],
                "privilege_unknown": summary["privilege_unknown"],
                "unavailable_optional_evidence": unavailable,
            },
        ),
        artifact,
    )


def _paths_alias(left: Path, right: Path) -> bool:
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
        if left.exists() and right.exists() and os.path.samefile(left, right):
            return True
    except (OSError, RuntimeError):
        return True
    return False


def _output_is_safe(output: Path, immutable: Sequence[Path]) -> bool:
    candidate = Path(os.path.abspath(output))
    try:
        _reject_symlink_chain(candidate, "output")
        parent = candidate.parent
        while not parent.exists() and parent.parent != parent:
            parent = parent.parent
        if not parent.is_dir():
            return False
    except _InputError:
        return False
    return not any(_paths_alias(candidate, item) for item in immutable)


def _check_artifact(path: Path) -> dict[str, JsonValue]:
    loaded = _read_json(path, "check", max_bytes=_MAX_CHECK_BYTES)
    validate_reachability_artifact(loaded.value)
    return loaded.value


def run_reachability_cli(
    *,
    args: argparse.Namespace,
    repo_root: Path,
) -> CheckResult:
    """Run analysis, strict check/gate policy, and atomic artifact replacement."""
    immutable = [args.security_surface, args.call_graph]
    if args.deferrals is not None:
        immutable.append(args.deferrals)
    if args.check is not None:
        immutable.append(args.check)
    try:
        repository = Path(os.path.abspath(repo_root))
        immutable.append(
            _target_source(repository, args.app, "app_target").path
        )
        if args.authenticated_fixture is not None:
            immutable.append(
                _target_source(
                    repository,
                    args.authenticated_fixture,
                    "authenticated_fixture",
                ).path
            )
    except _InputError as error:
        return CheckResult(
            "reachability",
            ResultState.ERROR,
            (
                "app target invalid"
                if error.stage == "app_target"
                else "authenticated fixture invalid"
            ),
            {"stage": error.stage},
        )
    if not _output_is_safe(args.out, immutable):
        return CheckResult(
            "reachability",
            ResultState.ERROR,
            "reachability artifact path invalid",
            {"stage": "output"},
        )
    expected = None
    if args.check is not None:
        try:
            expected = _check_artifact(args.check)
        except (OSError, ValueError, SchemaError):
            return CheckResult(
                "reachability",
                ResultState.ERROR,
                "reachability check invalid",
                {"stage": "check"},
            )
    result, artifact = analyze_reachability(
        app_target=args.app,
        repo_root=repo_root,
        security_surface_path=args.security_surface,
        call_graph_path=args.call_graph,
        deferrals_path=args.deferrals,
        authenticated_fixture=args.authenticated_fixture,
        timeout_seconds=args.timeout,
    )
    if not artifact:
        return result
    if expected is not None and artifact_hash(expected) != artifact_hash(artifact):
        return CheckResult(
            "reachability",
            ResultState.FAIL,
            "reachability artifact drift",
            {
                "expected": artifact_hash(expected),
                "actual": artifact_hash(artifact),
            },
        )
    try:
        atomic_write_json(args.out, artifact, validate_reachability_artifact)
    except (OSError, ValueError, SchemaError):
        return CheckResult(
            "reachability",
            ResultState.ERROR,
            "reachability artifact write failed",
            {"stage": "output"},
        )
    summary = artifact["summary"]
    assert isinstance(summary, dict)
    if args.gate and summary["privilege_unknown"]:
        return CheckResult(
            "reachability",
            ResultState.FAIL,
            "privilege reachability unknown",
            {"privilege_unknown": summary["privilege_unknown"]},
        )
    return result
