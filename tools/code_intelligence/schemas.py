"""Versioned artifact envelopes and projection-schema validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .artifacts import atomic_write_json


HEX64 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_ENVELOPE = frozenset({
    "schema_name",
    "schema_version",
    "source_sha",
    "tool_version",
    "input_hashes",
    "generated_at",
})
PROJECTION_SCHEMAS: dict[str, dict[str, type[object]]] = {
    "call_graph": {"nodes": list, "edges": list, "unresolved": list},
    "module_catalog": {"modules": dict},
    "security_surface": {
        "auth_gates": list,
        "secret_sites": list,
        "sql_sites": list,
        "subprocess_sites": list,
        "path_sinks": list,
        "totals": dict,
    },
    "error_catalog": {"handlers": list},
    "taint_map": {"sources": list, "sinks": list, "paths": list},
    "dead_code": {
        "uncalled": list,
        "uncalled_total": int,
        "unreachable_routes": list,
    },
    "config_lineage": {"settings": dict},
    "concurrency_map": {
        "shared_state": list,
        "locks": list,
        "operations": list,
    },
    "metrics_catalog": {"metrics": list},
}

_SENSITIVE_TOKENS = frozenset({
    "password",
    "passwd",
    "secret",
    "token",
    "cookie",
    "authorization",
    "bearer",
    "signing",
    "otp",
    "credential",
})
_COUNT_TOKENS = frozenset({"count", "total", "size"})
_TOKEN_SINGULARS = {
    "authorizations": "authorization",
    "bearers": "bearer",
    "cookies": "cookie",
    "counts": "count",
    "credentials": "credential",
    "keys": "key",
    "otps": "otp",
    "passwords": "password",
    "passwds": "passwd",
    "secrets": "secret",
    "signings": "signing",
    "sizes": "size",
    "tokens": "token",
    "totals": "total",
}
_REDACTED_MARKERS = frozenset({
    "***",
    "<redacted>",
    "[redacted]",
    "masked",
    "redacted",
})
_FIELD_IDENTIFIER = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:[.-][A-Za-z_][A-Za-z0-9_]*)*$"
)
_CONFIG_KEY_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,199}")
# Matched to tools.l0_extract.SECRET_RE. Keeping the predicate local avoids a
# schemas -> producer import cycle while accepting exactly the producer's
# structural secret-name vocabulary, including concatenated forms.
_L0_SECRET_NAME = re.compile(
    r"(password|passwd|secret|token|cookie|api[_-]?key|authorization|bearer|"
    r"private[_-]?key|signing|otp|credential|session[_-]?key|access[_-]?token)",
    re.I,
)
_UNKNOWN_SOURCE_SHA = "0" * 64
_UNBOUND_LEGACY = "unbound_legacy"
_MIGRATOR_VERSION = "schema-migrator-1"
_CONFIG_L2_FIELDS = (
    "effect",
    "gui_exposure",
    "runtime_tunable",
)


class SchemaError(ValueError):
    """Raised when an artifact violates its registered schema."""


def is_safe_config_key(value: object) -> bool:
    """Return whether *value* is a safe schema-1 config identifier.

    Config keys are ASCII identifiers of at most 200 characters: they start
    with a letter or underscore and continue with letters, digits, or
    underscores. This covers every key in the repository production scope
    while excluding whitespace, controls, punctuation, and value syntax before
    a settings key can receive the structural-identifier secret exemption.
    """
    return (
        isinstance(value, str)
        and _CONFIG_KEY_IDENTIFIER.fullmatch(value) is not None
    )


@dataclass(frozen=True)
class ArtifactEnvelope:
    """Typed representation of the shared artifact metadata."""

    schema_name: str
    schema_version: int
    source_sha: str
    tool_version: str
    input_hashes: Mapping[str, str]
    generated_at: str


def make_envelope(
    schema_name: str,
    schema_version: int,
    source_sha: str,
    tool_version: str,
    input_hashes: Mapping[str, str],
) -> dict[str, object]:
    """Create the shared metadata carried by durable code-intelligence artifacts."""
    return {
        "schema_name": schema_name,
        "schema_version": schema_version,
        "source_sha": source_sha,
        "tool_version": tool_version,
        "input_hashes": dict(input_hashes),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _is_exact_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_timestamp(value: object) -> None:
    if not isinstance(value, str) or not value:
        raise SchemaError("generated_at must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SchemaError("generated_at must be a timezone-aware timestamp") from error
    if parsed.tzinfo is None:
        raise SchemaError("generated_at must be a timezone-aware timestamp")


def validate_envelope(
    value: object,
    expected_name: str | None = None,
    supported_version: int = 1,
) -> None:
    """Validate the shared metadata of one artifact."""
    if not _is_exact_int(supported_version):
        raise SchemaError("supported_version must be an integer")
    if not isinstance(value, Mapping):
        raise SchemaError("artifact must be an object")
    missing = sorted(REQUIRED_ENVELOPE - value.keys())
    if missing:
        raise SchemaError(f"missing envelope fields: {', '.join(missing)}")

    schema_name = value["schema_name"]
    if not isinstance(schema_name, str) or not schema_name:
        raise SchemaError("schema_name must be a non-empty string")
    if expected_name is not None and schema_name != expected_name:
        raise SchemaError(f"expected schema {expected_name}")

    schema_version = value["schema_version"]
    if not _is_exact_int(schema_version):
        raise SchemaError("schema_version must be an integer")
    if schema_version != supported_version:
        raise SchemaError("unsupported schema version")

    source_sha = value["source_sha"]
    if not isinstance(source_sha, str) or HEX64.fullmatch(source_sha) is None:
        raise SchemaError("source_sha must be 64 lowercase hex characters")
    source_binding = value.get("source_binding")
    if source_sha == _UNKNOWN_SOURCE_SHA and source_binding != _UNBOUND_LEGACY:
        raise SchemaError(
            "all-zero source_sha requires source_binding unbound_legacy"
        )
    if source_sha != _UNKNOWN_SOURCE_SHA and source_binding == _UNBOUND_LEGACY:
        raise SchemaError(
            "source_binding unbound_legacy requires an all-zero source_sha"
        )

    tool_version = value["tool_version"]
    if not isinstance(tool_version, str) or not tool_version:
        raise SchemaError("tool_version must be a non-empty string")

    input_hashes = value["input_hashes"]
    if not isinstance(input_hashes, Mapping):
        raise SchemaError("input_hashes must be an object")
    for input_name, input_hash in input_hashes.items():
        if (
            not isinstance(input_name, str)
            or not input_name
            or not isinstance(input_hash, str)
            or HEX64.fullmatch(input_hash) is None
        ):
            raise SchemaError(
                "input_hashes keys must be non-empty strings and values must be "
                "64 lowercase hex characters"
            )

    _validate_timestamp(value["generated_at"])


def _normalized_key(key: str) -> str:
    with_word_boundaries = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return "_".join(
        part
        for part in re.split(r"[^a-z0-9]+", with_word_boundaries.casefold())
        if part
    )


def _semantic_key_tokens(key: str) -> tuple[str, ...]:
    return tuple(
        _TOKEN_SINGULARS.get(token, token)
        for token in _normalized_key(key).split("_")
        if token
    )


def _is_sensitive_key(key: str) -> bool:
    tokens = _semantic_key_tokens(key)
    return (
        bool(_SENSITIVE_TOKENS.intersection(tokens))
        or any(
            left == "private" and right == "key"
            for left, right in zip(tokens, tokens[1:])
        )
    )


def _is_count_style_key(key: str) -> bool:
    return bool(_COUNT_TOKENS.intersection(_semantic_key_tokens(key)))


def _is_safe_sensitive_value(key: str, value: object) -> bool:
    if isinstance(value, bool):
        return True
    if _is_exact_int(value) and _is_count_style_key(key):
        return value >= 0
    return isinstance(value, str) and value.strip().lower() in _REDACTED_MARKERS


def _is_module_secret_inventory(
    projection_name: str,
    path: tuple[object, ...],
    key: str,
) -> bool:
    return (
        projection_name == "module_catalog"
        and len(path) == 2
        and path[0] == "modules"
        and key == "secrets"
    )


def _validate_module_secret_inventory(value: object) -> None:
    if not isinstance(value, list) or any(
        not isinstance(item, str)
        or _FIELD_IDENTIFIER.fullmatch(item) is None
        or _L0_SECRET_NAME.search(item) is None
        for item in value
    ):
        raise SchemaError(
            "secret-like value is forbidden for module_catalog secrets"
        )


def _is_security_secret_sites(
    projection_name: str,
    path: tuple[object, ...],
    key: str,
) -> bool:
    return (
        projection_name == "security_surface"
        and not path
        and key == "secret_sites"
    )


def _is_structural_identifier_key(
    projection_name: str,
    path: tuple[object, ...],
    key: str,
) -> bool:
    return (
        path == ("input_hashes",)
        or (
            projection_name == "module_catalog"
            and path == ("modules",)
        )
        or (
            projection_name == "config_lineage"
            and path == ("settings",)
            and is_safe_config_key(key)
        )
    )


def _reject_secret_values(
    value: object,
    projection_name: str,
    path: tuple[object, ...] = (),
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                if _is_module_secret_inventory(projection_name, path, key):
                    _validate_module_secret_inventory(item)
                elif _is_security_secret_sites(projection_name, path, key):
                    if not isinstance(item, list) or any(
                        not isinstance(site, Mapping) for site in item
                    ):
                        raise SchemaError(
                            "secret-like value is forbidden for security_surface "
                            "secret_sites"
                        )
                elif (
                    _is_sensitive_key(key)
                    and not _is_structural_identifier_key(
                        projection_name, path, key
                    )
                    and not _is_safe_sensitive_value(key, item)
                    and not (
                        path == ("totals",)
                        and _is_exact_int(item)
                        and item >= 0
                    )
                ):
                    raise SchemaError(
                        f"secret-like value is forbidden for key {key}"
                    )
            _reject_secret_values(item, projection_name, path + (key,))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_secret_values(item, projection_name, path + (index,))


def _validate_confidence(value: object, context: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 <= value <= 1.0
    ):
        raise SchemaError(f"{context} confidence must be between 0.0 and 1.0")


def _mapping_field(
    value: Mapping[str, object],
    field: str,
    context: str,
) -> Mapping[str, object]:
    field_value = value.get(field)
    if not isinstance(field_value, Mapping):
        raise SchemaError(f"{context} {field} must be an object")
    return field_value


def _string_field(
    value: Mapping[str, object],
    field: str,
    context: str,
) -> str:
    field_value = value.get(field)
    if not isinstance(field_value, str) or not field_value:
        raise SchemaError(f"{context} {field} must be a non-empty string")
    return field_value


def _canonical_string_field(
    value: Mapping[str, object],
    field: str,
    context: str,
) -> str:
    field_value = _string_field(value, field, context)
    if field_value != field_value.strip():
        raise SchemaError(f"{context} {field} must be canonical")
    return field_value


def _validate_call_site(
    value: Mapping[str, object],
    context: str,
) -> Mapping[str, object]:
    call_site = _mapping_field(value, "call_site", context)
    path = _string_field(call_site, "path", f"{context} call_site")
    line = call_site.get("line")
    if line is not None and not _is_exact_int(line):
        raise SchemaError(f"{context} call_site line must be an integer or null")
    if value.get("path") != path:
        raise SchemaError(f"{context} path must match call_site path")
    return call_site


def _validate_site_record(
    value: object,
    context: str,
    required_fields: tuple[str, ...],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SchemaError(f"{context} must be an object")
    for field in required_fields:
        _string_field(value, field, context)
    _string_field(value, "path", context)
    _string_field(value, "function", context)
    source = _mapping_field(value, "source", context)
    call_site = _validate_call_site(value, context)
    for field in ("path", "function"):
        if source.get(field) != value[field]:
            raise SchemaError(
                f"{context} source {field} must match outer {field}"
            )
    if value.get("at") != call_site.get("line"):
        raise SchemaError(f"{context} outer at must match call_site line")
    if source.get("at") != call_site.get("line"):
        raise SchemaError(f"{context} source line must match call_site line")
    return source


def _validate_string_inventory(value: object, context: str) -> list[str]:
    if (
        type(value) is not list
        or any(
            not isinstance(item, str) or not item
            for item in value
        )
        or value != sorted(set(value))
    ):
        raise SchemaError(f"{context} must be sorted unique strings")
    return value


def _validate_evidence_policy(
    method: object,
    confidence: object,
    context: str,
) -> None:
    _validate_confidence(confidence, context)
    if method is None:
        if confidence != 0.0:
            raise SchemaError(
                f"{context} unlabeled evidence confidence must be 0.0"
            )
        return
    if not isinstance(method, str) or not method:
        raise SchemaError(f"{context} method must be non-empty or null")
    if method != method.strip():
        raise SchemaError(f"{context} method must be canonical")
    if method == "name_substring" and confidence != 0.6:
        raise SchemaError(
            f"{context} name_substring confidence must be 0.6"
        )


def _validate_unknown_config_fields(
    value: Mapping[str, object],
    context: str,
) -> None:
    for field in _CONFIG_L2_FIELDS:
        if value.get(field) is not None:
            raise SchemaError(f"{context} {field} must remain null")

    field_confidence = _mapping_field(
        value, "field_confidence", context
    )
    if set(field_confidence) != set(_CONFIG_L2_FIELDS):
        raise SchemaError(
            f"{context} field_confidence must cover all unknown fields"
        )
    for field in _CONFIG_L2_FIELDS:
        if field_confidence[field] != 0.0:
            raise SchemaError(
                f"{context} {field} confidence must be 0.0"
            )


def _validate_config_lineage(value: Mapping[str, object]) -> None:
    settings = _mapping_field(value, "settings", "config_lineage")
    for key, raw_setting in settings.items():
        if not is_safe_config_key(key):
            raise SchemaError(
                "config_lineage setting key must use safe config-key grammar"
            )
        context = f"config_lineage setting {key}"
        if not isinstance(raw_setting, Mapping):
            raise SchemaError(f"{context} must be an object")
        for field in ("readers", "writers"):
            _validate_string_inventory(
                raw_setting.get(field), f"{context} {field}"
            )
        _validate_unknown_config_fields(raw_setting, context)
        confidence = raw_setting.get("confidence")
        _validate_confidence(confidence, context)
        if confidence != 0.5:
            raise SchemaError(
                f"{context} mechanical confidence must be exactly 0.5"
            )

        provenance = _mapping_field(raw_setting, "provenance", context)
        if provenance.get("method") != "l0_static_analysis":
            raise SchemaError(
                f"{context} provenance method must be l0_static_analysis"
            )
        for inventory_field, sites_field in (
            ("readers", "read_sites"),
            ("writers", "write_sites"),
        ):
            sites = provenance.get(sites_field)
            if type(sites) is not list:
                raise SchemaError(
                    f"{context} provenance {sites_field} must be a list"
                )
            site_keys = [
                json.dumps(
                    site,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                for site in sites
            ]
            if len(site_keys) != len(set(site_keys)):
                raise SchemaError(
                    f"{context} provenance {sites_field} has duplicate sites"
                )
            if site_keys != sorted(site_keys):
                raise SchemaError(
                    f"{context} provenance {sites_field} must be deterministic"
                )
            site_functions = []
            for index, site in enumerate(sites):
                source = _validate_site_record(
                    site,
                    f"{context} provenance {sites_field}[{index}]",
                    (),
                )
                assert isinstance(site, Mapping)
                expected_source_fields = {
                    "key", "at", "path", "function"
                }
                if set(source) != expected_source_fields:
                    raise SchemaError(
                        f"{context} provenance {sites_field}[{index}] "
                        "source fields are invalid"
                    )
                if site.get("key") != key or source.get("key") != key:
                    raise SchemaError(
                        f"{context} provenance {sites_field}[{index}] "
                        "source key must match setting key"
                    )
                site_functions.append(site["function"])
            inventory = raw_setting[inventory_field]
            assert isinstance(inventory, list)
            if set(site_functions) != set(inventory):
                raise SchemaError(
                    f"{context} provenance {sites_field} coverage must "
                    f"match {inventory_field}"
                )
        unknown_fields = _mapping_field(
            provenance, "unknown_fields", f"{context} provenance"
        )
        if (
            set(unknown_fields) != set(_CONFIG_L2_FIELDS)
            or any(
                unknown_fields[field] != 0.0
                for field in _CONFIG_L2_FIELDS
            )
        ):
            raise SchemaError(
                f"{context} provenance unknown field confidence must be 0.0"
            )


def _validate_heuristic_site(
    value: object,
    context: str,
    required_fields: tuple[str, ...],
) -> None:
    source = _validate_site_record(value, context, required_fields)
    assert isinstance(value, Mapping)
    for field in required_fields:
        _canonical_string_field(value, field, context)
        if source.get(field) != value[field]:
            raise SchemaError(
                f"{context} source {field} must match outer {field}"
            )
    method = value.get("method")
    confidence = value.get("confidence")
    if source.get("method") != method:
        raise SchemaError(
            f"{context} source method must match outer method"
        )
    if source.get("confidence") != confidence:
        raise SchemaError(
            f"{context} source confidence must match outer confidence"
        )
    expected_source_fields = {
        *required_fields,
        "at",
        "method",
        "confidence",
        "path",
        "function",
    }
    if set(source) != expected_source_fields:
        raise SchemaError(f"{context} source fields are invalid")
    _validate_evidence_policy(method, confidence, context)


def _validate_shared_state(value: object, context: str) -> str:
    if not isinstance(value, Mapping):
        raise SchemaError(f"{context} must be an object")
    required_fields = {
        "name",
        "readers",
        "writers",
        "confidence",
        "provenance",
    }
    if set(value) != required_fields:
        raise SchemaError(f"{context} fields are invalid")
    name = _canonical_string_field(value, "name", context)
    readers = _validate_string_inventory(
        value.get("readers"), f"{context} readers"
    )
    writers = _validate_string_inventory(
        value.get("writers"), f"{context} writers"
    )
    if not readers and not writers:
        raise SchemaError(f"{context} must have a reader or writer")
    confidence = value.get("confidence")
    provenance = _mapping_field(value, "provenance", context)
    if set(provenance) != {"method", "confidence"}:
        raise SchemaError(f"{context} provenance fields are invalid")
    if provenance.get("confidence") != confidence:
        raise SchemaError(
            f"{context} confidence contradicts provenance confidence"
        )
    _validate_evidence_policy(
        provenance.get("method"),
        confidence,
        f"{context} provenance",
    )
    return name


def _validate_concurrency_map(value: Mapping[str, object]) -> None:
    shared_state = value["shared_state"]
    locks = value["locks"]
    operations = value["operations"]
    assert isinstance(shared_state, list)
    assert isinstance(locks, list)
    assert isinstance(operations, list)
    state_names = [
        _validate_shared_state(
            state, f"concurrency_map shared_state[{index}]"
        )
        for index, state in enumerate(shared_state)
    ]
    if len(state_names) != len(set(state_names)):
        raise SchemaError("concurrency_map shared_state names must be unique")
    if state_names != sorted(state_names):
        raise SchemaError(
            "concurrency_map shared_state must be deterministic"
        )
    for index, operation in enumerate(operations):
        _validate_heuristic_site(
            operation,
            f"concurrency_map operations[{index}]",
            ("kind", "name", "operation"),
        )
    for index, lock in enumerate(locks):
        _validate_heuristic_site(
            lock,
            f"concurrency_map locks[{index}]",
            ("kind", "name", "operation"),
        )
        assert isinstance(lock, Mapping)
        if lock["kind"] != "lock":
            raise SchemaError(
                f"concurrency_map locks[{index}] kind must be lock"
            )
        if lock not in operations:
            raise SchemaError(
                f"concurrency_map locks[{index}] must also be an operation"
            )


def _validate_metrics_catalog(value: Mapping[str, object]) -> None:
    metrics = value["metrics"]
    assert isinstance(metrics, list)
    for index, metric in enumerate(metrics):
        _validate_heuristic_site(
            metric,
            f"metrics_catalog metrics[{index}]",
            ("name", "operation"),
        )
        assert isinstance(metric, Mapping)
        _canonical_string_field(
            metric,
            "containing_function",
            f"metrics_catalog metrics[{index}]",
        )
        if metric["containing_function"] != metric["function"]:
            raise SchemaError(
                f"metrics_catalog metrics[{index}] containing function mismatch"
            )


def validate_projection(name: str, value: object) -> None:
    """Validate one registered graph projection and reject embedded secrets."""
    schema = PROJECTION_SCHEMAS.get(name)
    if schema is None:
        raise SchemaError(f"unknown projection schema: {name}")
    validate_envelope(value, name)
    assert isinstance(value, Mapping)
    for field_name, expected_type in schema.items():
        if field_name not in value:
            raise SchemaError(f"missing projection field: {field_name}")
        field_value = value[field_name]
        if expected_type is int:
            valid_type = _is_exact_int(field_value)
        else:
            valid_type = type(field_value) is expected_type
        if not valid_type:
            raise SchemaError(
                f"projection field {field_name} must be {expected_type.__name__}"
            )
    if name == "config_lineage":
        _validate_config_lineage(value)
    elif name == "concurrency_map":
        _validate_concurrency_map(value)
    elif name == "metrics_catalog":
        _validate_metrics_catalog(value)
    _reject_secret_values(value, name)


def _legacy_metadata(value: Mapping[str, object]) -> Mapping[str, object] | None:
    metadata = value.get("_meta")
    if metadata is None:
        return None
    if not isinstance(metadata, Mapping):
        raise SchemaError("no migration path")
    return metadata


def _legacy_identity(name: str, value: Mapping[str, object]) -> int:
    metadata = _legacy_metadata(value)
    declarations = (value,) if metadata is None else (value, metadata)
    versions = [
        declaration[field_name]
        for declaration in declarations
        for field_name in ("schema_version", "schema")
        if field_name in declaration
    ]
    if not versions:
        if metadata is None:
            raise SchemaError("no migration path")
        versions = [1]
    if (
        any(not _is_exact_int(version) for version in versions)
        or len(set(versions)) != 1
    ):
        raise SchemaError("no migration path")

    declared_names = [
        declaration["schema_name"]
        for declaration in declarations
        if "schema_name" in declaration
    ]
    if (
        any(
            not isinstance(declared_name, str) or not declared_name
            for declared_name in declared_names
        )
        or len(set(declared_names)) > 1
        or (declared_names and declared_names[0] != name)
    ):
        raise SchemaError("no migration path")
    version = versions[0]
    assert isinstance(version, int)
    return version


def _legacy_timestamp(value: Mapping[str, object]) -> str:
    metadata = value.get("_meta")
    if isinstance(metadata, Mapping):
        generated = metadata.get("generated")
        try:
            _validate_timestamp(generated)
        except SchemaError:
            pass
        else:
            assert isinstance(generated, str)
            return generated
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _legacy_source_identity(
    value: Mapping[str, object],
) -> tuple[str, bool]:
    metadata = _legacy_metadata(value)
    declarations = (value,) if metadata is None else (value, metadata)
    source_shas = [
        declaration["source_sha"]
        for declaration in declarations
        if "source_sha" in declaration
    ]
    if (
        any(
            not isinstance(source_sha, str)
            or HEX64.fullmatch(source_sha) is None
            for source_sha in source_shas
        )
        or len(set(source_shas)) > 1
    ):
        raise SchemaError("invalid source metadata")

    bindings = [
        declaration["source_binding"]
        for declaration in declarations
        if "source_binding" in declaration
    ]
    if (
        any(
            not isinstance(binding, str) or not binding
            for binding in bindings
        )
        or len(set(bindings)) > 1
    ):
        raise SchemaError("invalid source metadata")

    if not source_shas:
        if bindings and bindings[0] != _UNBOUND_LEGACY:
            raise SchemaError("invalid source metadata")
        return _UNKNOWN_SOURCE_SHA, True

    source_sha = source_shas[0]
    assert isinstance(source_sha, str)
    if source_sha == _UNKNOWN_SOURCE_SHA:
        if not bindings or bindings[0] != _UNBOUND_LEGACY:
            raise SchemaError("invalid source metadata")
        return source_sha, True
    if _UNBOUND_LEGACY in bindings:
        raise SchemaError("invalid source metadata")
    return source_sha, False


def _legacy_content_hash(value: Mapping[str, object]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _migrate_legacy_v1(
    name: str,
    value: Mapping[str, object],
) -> dict[str, object]:
    source_sha, source_is_unbound = _legacy_source_identity(value)
    migrated = dict(value)
    migrated.update(
        make_envelope(
            name,
            1,
            source_sha,
            _MIGRATOR_VERSION,
            {"legacy_artifact": _legacy_content_hash(value)},
        )
    )
    migrated["generated_at"] = _legacy_timestamp(value)
    if source_is_unbound:
        migrated["source_binding"] = _UNBOUND_LEGACY
    return migrated


Migration = Callable[[str, Mapping[str, object]], dict[str, object]]
MIGRATION_REGISTRY: dict[tuple[str, int, int], Migration] = {
    ("invariants", 1, 1): _migrate_legacy_v1,
    ("contracts", 1, 1): _migrate_legacy_v1,
    ("coverage_gaps", 1, 1): _migrate_legacy_v1,
}
_LEGACY_KINDS = frozenset(key[0] for key in MIGRATION_REGISTRY)


def migrate_artifact(
    name: str,
    value: Mapping[str, object],
    *,
    target_version: int = 1,
) -> dict[str, object]:
    """Migrate a registered legacy artifact without dropping payload fields."""
    if not _is_exact_int(target_version):
        raise SchemaError("no migration path")
    source_version = _legacy_identity(name, value)
    migration = MIGRATION_REGISTRY.get((name, source_version, target_version))
    if migration is None:
        raise SchemaError("no migration path")
    return migration(name, value)


def _load_json(path: Path) -> object:
    with path.open(encoding="utf-8") as source:
        return json.load(
            source,
            parse_float=_parse_json_float,
            parse_constant=lambda _constant: _reject_nonfinite_json(),
        )


def _parse_json_float(raw_value: str) -> float:
    value = float(raw_value)
    if not math.isfinite(value):
        _reject_nonfinite_json()
    return value


def _reject_nonfinite_json() -> None:
    raise SchemaError("non-finite JSON number is not allowed")


def _validator(kind: str) -> Callable[[object], None]:
    if kind in PROJECTION_SCHEMAS:
        return lambda value: validate_projection(kind, value)
    if kind in _LEGACY_KINDS:
        return lambda value: validate_envelope(value, kind)
    raise SchemaError(f"unknown schema kind: {kind}")


def main(argv: Sequence[str] | None = None) -> int:
    """Validate or migrate versioned JSON artifacts."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate one JSON artifact")
    validate.add_argument("--kind", required=True)
    validate.add_argument("--file", type=Path, required=True)

    migrate = commands.add_parser("migrate", help="migrate one legacy JSON artifact")
    migrate.add_argument("--kind", required=True)
    migrate.add_argument("--input", type=Path, required=True)
    migrate.add_argument("--out", type=Path, required=True)

    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "validate":
            _validator(arguments.kind)(_load_json(arguments.file))
            return 0

        value = _load_json(arguments.input)
        if not isinstance(value, Mapping):
            raise SchemaError("artifact must be an object")
        migrated = migrate_artifact(arguments.kind, value)
        atomic_write_json(arguments.out, migrated, _validator(arguments.kind))
        return 0
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, SchemaError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
