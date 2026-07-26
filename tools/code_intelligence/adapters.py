"""Typed registry for portable code-intelligence analysis adapters."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Literal, Protocol, Sequence, TypeAlias

from .results import CheckResult


JsonValue: TypeAlias = (
    bool | int | float | str | None | list["JsonValue"] | dict[str, "JsonValue"]
)

_ADAPTER_KINDS = frozenset({"oracle", "fuzz", "coverage", "reachability"})
_SECRET_TEXT = re.compile(r"(?:^bearer\s+|-----BEGIN [A-Z ]*PRIVATE KEY-----)", re.IGNORECASE)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KEY_SEPARATOR = re.compile(r"[^a-zA-Z0-9]+")
_SAFE_ADAPTER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SECRET_SINGLE_TOKENS = frozenset(
    {"authorization", "cookie", "credential", "password", "secret", "token"}
)
_SECRET_TOKEN_PLURALS = {
    "authorizations": "authorization",
    "bodies": "body",
    "cookies": "cookie",
    "credentials": "credential",
    "headers": "header",
    "keys": "key",
    "passwords": "password",
    "queries": "query",
    "secrets": "secret",
    "tokens": "token",
}
_SECRET_TOKEN_SEQUENCES = frozenset(
    {
        ("api", "key"),
        ("api", "token"),
        ("private", "key"),
        ("access", "token"),
        ("authorization", "header"),
        ("signed", "query"),
        ("raw", "body"),
    }
)
_SECRET_COMPACT_ENDINGS = frozenset(
    {
        "apikey",
        "apikeys",
        "apitoken",
        "apitokens",
        "privatekey",
        "privatekeys",
        "accesstoken",
        "accesstokens",
        "authorization",
        "authorizations",
        "authorizationheader",
        "authorizationheaders",
        "signedquery",
        "signedqueries",
        "rawbody",
        "rawbodies",
    }
)
_REDACTION_MARKERS = frozenset({"<redacted>", "[redacted]"})

# Payloads have deterministic resource limits: 64 container levels, 10,000 JSON
# nodes, and 4,096 integer bits.  The limits keep validation and snapshots
# portable, bounded, and safely below Python's JSON integer conversion limit.
_MAX_JSON_DEPTH = 64
_MAX_JSON_NODES = 10_000
_MAX_JSON_INTEGER_BITS = 4_096
_MAX_IDENTIFIER_LENGTH = 256


@dataclass(frozen=True)
class AdapterBudget:
    timeout_seconds: float
    max_cases: int
    max_output_bytes: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive and finite")
        _require_positive_int("max_cases", self.max_cases)
        _require_positive_int("max_output_bytes", self.max_output_bytes)


@dataclass(frozen=True)
class AdapterContext:
    repo_root: Path
    artifacts_dir: Path
    corpus_dir: Path
    seed: int
    budget: AdapterBudget

    def __post_init__(self) -> None:
        for field_name in ("repo_root", "artifacts_dir", "corpus_dir"):
            if not isinstance(getattr(self, field_name), Path):
                raise TypeError(f"{field_name} must be a Path")
        if type(self.seed) is not int:
            raise TypeError("seed must be an int")
        if not isinstance(self.budget, AdapterBudget):
            raise TypeError("budget must be an AdapterBudget")


@dataclass(frozen=True)
class AdapterCase:
    case_id: str
    payload: JsonValue

    def __post_init__(self) -> None:
        if not _is_printable_identifier(self.case_id):
            raise ValueError("case_id must be a nonempty printable identifier")
        if not _is_json_safe(self.payload):
            raise ValueError("payload must be JSON-safe and secret-free")
        object.__setattr__(self, "payload", _freeze_json(self.payload))


class AnalysisAdapter(Protocol):
    name: str
    kind: Literal["oracle", "fuzz", "coverage", "reachability"]

    def cases(self, context: AdapterContext) -> Sequence[AdapterCase]: ...

    def run(self, case: AdapterCase, context: AdapterContext) -> CheckResult: ...


_REGISTRY: dict[str, AnalysisAdapter] = {}


class _FrozenDict(dict[str, JsonValue]):
    """A JSON-serializable dictionary that rejects all mutation methods."""

    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("adapter case payload is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable

    def __reduce_ex__(self, _protocol: int) -> tuple[object, tuple[object, ...]]:
        return _rebuild_frozen_dict, (tuple(dict.items(self)),)


class _FrozenList(list[JsonValue]):
    """A JSON-serializable list that rejects all mutation methods."""

    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("adapter case payload is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable

    def __reduce_ex__(self, _protocol: int) -> tuple[object, tuple[object, ...]]:
        return _rebuild_frozen_list, (tuple(list.__iter__(self)),)


def _new_frozen_dict(items: object) -> _FrozenDict:
    """Populate immutable dictionary storage through the base class only."""
    frozen = _FrozenDict.__new__(_FrozenDict)
    dict.__init__(frozen)
    dict.update(frozen, items)
    return frozen


def _new_frozen_list(items: object) -> _FrozenList:
    """Populate immutable list storage through the base class only."""
    frozen = _FrozenList.__new__(_FrozenList)
    list.__init__(frozen)
    list.extend(frozen, items)
    return frozen


def _rebuild_frozen_dict(items: object) -> _FrozenDict:
    """Rebuild a validated immutable dictionary during pickle reconstruction."""
    try:
        payload = dict(items)
    except Exception:
        raise ValueError("payload must be JSON-safe and secret-free") from None
    if not _is_json_safe(payload):
        raise ValueError("payload must be JSON-safe and secret-free")
    return _freeze_json(payload)  # type: ignore[return-value]


def _rebuild_frozen_list(items: object) -> _FrozenList:
    """Rebuild a validated immutable list during pickle reconstruction."""
    try:
        payload = list(items)
    except Exception:
        raise ValueError("payload must be JSON-safe and secret-free") from None
    if not _is_json_safe(payload):
        raise ValueError("payload must be JSON-safe and secret-free")
    return _freeze_json(payload)  # type: ignore[return-value]


@dataclass(frozen=True)
class _RegisteredAdapter:
    """Stable registry identity while preserving the source adapter behavior."""

    name: str
    kind: str
    _delegate: AnalysisAdapter

    def cases(self, context: AdapterContext) -> Sequence[AdapterCase]:
        return self._delegate.cases(context)

    def run(self, case: AdapterCase, context: AdapterContext) -> CheckResult:
        return self._delegate.run(case, context)


def register_adapter(adapter: AnalysisAdapter) -> None:
    """Register one valid adapter, preserving names as its stable identity."""
    try:
        name = adapter.name
        kind = adapter.kind
        cases = adapter.cases
        run = adapter.run
    except Exception:
        raise TypeError("adapter must provide name, kind, cases, and run") from None
    if not _is_safe_adapter_name(name):
        raise ValueError("adapter name must be a nonempty safe identifier")
    if not isinstance(kind, str) or kind not in _ADAPTER_KINDS:
        raise ValueError("unsupported adapter kind")
    if not callable(cases) or not callable(run):
        raise TypeError("adapter must provide name, kind, cases, and run")
    if name in _REGISTRY:
        raise ValueError(f"duplicate adapter: {name}")
    _REGISTRY[name] = _RegisteredAdapter(name, kind, adapter)


def get_adapter(name: str) -> AnalysisAdapter:
    """Return a registered adapter or raise an explicit lookup error."""
    if not _is_safe_adapter_name(name):
        raise KeyError("unknown adapter")
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown adapter: {name}") from None


def list_adapters(*, kind: str | None = None) -> tuple[str, ...]:
    """List adapter names in deterministic lexical order."""
    return tuple(
        sorted(
            name for name, adapter in _REGISTRY.items()
            if kind is None or adapter.kind == kind
        )
    )


def clear_adapters_for_test() -> None:
    """Clear process-local registry state for isolated tests."""
    _REGISTRY.clear()


def artifact_filename_component(logical_id: str) -> str:
    """Return a bounded cross-platform artifact component without exposing the ID.

    Logical IDs remain available as metadata.  Future artifact projections must
    use this generic safe slug plus stable hash instead of embedding raw IDs.
    """
    if not _is_printable_identifier(logical_id):
        raise ValueError("logical_id must be a nonempty printable identifier")
    return f"artifact-{hashlib.sha256(logical_id.encode('utf-8')).hexdigest()[:32]}"


def _require_positive_int(field_name: str, value: object) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _is_json_safe(value: object) -> bool:
    """Contain all hostile payload behavior behind a value-free result."""
    try:
        return _validate_json(value)
    except Exception:
        return False


def _validate_json(value: object) -> bool:
    """Validate JSON payloads iteratively, including cycle and resource limits."""
    active_containers: set[int] = set()
    nodes_seen = 0
    stack: list[tuple[object, int, bool]] = [(value, 0, False)]
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active_containers.discard(id(current))
            continue
        nodes_seen += 1
        if nodes_seen > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            return False
        if current is None or type(current) is bool:
            continue
        if type(current) is str:
            if _SECRET_TEXT.search(current):
                return False
            continue
        if type(current) is int:
            if current.bit_length() > _MAX_JSON_INTEGER_BITS:
                return False
            try:
                json.dumps(current, allow_nan=False)
            except (OverflowError, TypeError, ValueError):
                return False
            continue
        if type(current) is float:
            if not math.isfinite(current):
                return False
            try:
                json.dumps(current, allow_nan=False)
            except (OverflowError, TypeError, ValueError):
                return False
            continue
        if type(current) in (list, _FrozenList):
            if id(current) in active_containers or len(current) > _MAX_JSON_NODES - nodes_seen:
                return False
            active_containers.add(id(current))
            stack.append((current, depth, True))
            stack.extend((item, depth + 1, False) for item in reversed(current))
            continue
        if type(current) in (dict, _FrozenDict):
            if id(current) in active_containers or len(current) > _MAX_JSON_NODES - nodes_seen:
                return False
            active_containers.add(id(current))
            stack.append((current, depth, True))
            for key, item in current.items():
                if type(key) is not str:
                    return False
                metadata_kind = _secret_metadata_kind(key)
                if metadata_kind is not None and not _is_safe_secret_metadata(metadata_kind, item):
                    return False
                stack.append((item, depth + 1, False))
            continue
        return False
    return True


def _freeze_json(value: JsonValue) -> JsonValue:
    """Copy a previously validated payload into immutable JSON-compatible containers."""
    if type(value) in (list, _FrozenList):
        return _new_frozen_list(_freeze_json(item) for item in value)
    if type(value) in (dict, _FrozenDict):
        return _new_frozen_dict((key, _freeze_json(item)) for key, item in value.items())
    return value


def _is_printable_identifier(value: object) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= _MAX_IDENTIFIER_LENGTH
        and value == value.strip()
        and value.isprintable()
    )


def _is_safe_adapter_name(value: object) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= _MAX_IDENTIFIER_LENGTH
        and _SAFE_ADAPTER_NAME.fullmatch(value) is not None
        and value not in {".", ".."}
    )


def _secret_metadata_kind(key: str) -> str | None:
    """Return a sensitive key's allowed metadata suffix, or an empty marker."""
    normalized = _KEY_SEPARATOR.sub("_", _CAMEL_BOUNDARY.sub("_", key)).strip("_").lower()
    base, suffix = _strip_secret_metadata_suffix(normalized)
    parts = [_SECRET_TOKEN_PLURALS.get(part, part) for part in base.split("_") if part]
    compact = "".join(parts)
    if (
        any(part in _SECRET_SINGLE_TOKENS - {"authorization"} for part in parts)
        or (parts and parts[-1] == "authorization")
        or any(tuple(parts[index:index + 2]) in _SECRET_TOKEN_SEQUENCES for index in range(len(parts) - 1))
        or _ends_with_sensitive_compact_marker(compact)
    ):
        return suffix
    return None


def _strip_secret_metadata_suffix(normalized: str) -> tuple[str, str]:
    for suffix in ("redacted", "count"):
        if normalized.endswith(f"_{suffix}"):
            return normalized.removesuffix(f"_{suffix}"), suffix
        if normalized.endswith(suffix):
            base = normalized.removesuffix(suffix).rstrip("_")
            if _ends_with_sensitive_compact_marker(base.replace("_", "")):
                return base, suffix
    return normalized, ""


def _ends_with_sensitive_compact_marker(compact: str) -> bool:
    return any(compact.endswith(marker) for marker in _SECRET_COMPACT_ENDINGS)


def _is_safe_secret_metadata(kind: str, value: object) -> bool:
    if kind == "redacted":
        return value is True or (type(value) is str and value.lower() in _REDACTION_MARKERS)
    if kind == "count":
        return type(value) is int and value >= 0
    return False
