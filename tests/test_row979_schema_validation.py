"""Row 979: Enterprise Schema Validation & Fast Serialization Runtime Migration (msgspec & pydantic-v2).

llm_exec.validate_schema is schema_runtime.validate_schema: llm_exec's minimal
dialect; a schema object's first call is interpreted, from its second call on it
is compiled once into a fail-fast check. Pinned here:
  * the answers -- every verdict and reason equals the pre-row-979 validator's,
    frozen below as _legacy_validate_schema (llm_exec.py at 0e7383cf, verbatim),
    on the first (interpreted) and on the repeat (compiled) call;
  * the cost structure -- a first call is the legacy walk plus one frame (nothing
    compiled), a schema object compiles once, a repeat call is exactly two Python
    frames; the validation path imports no serialization backend;
  * that an engine fault is raised, not masked by a fallback;
  * the serialization half on both backends (msgspec when installed, stdlib json
    always -- CI installs no msgspec).
"""
from __future__ import annotations

import collections
import copy
import dataclasses
import gc
import importlib.util
import json
import math
import os
import random
import subprocess
import sys
from typing import Any

import pytest

from bulk_downloader import llm_exec, llm_schemas

BD_GATE_SCOPE = "repo-wide"

UI = llm_schemas.UI_SCREENSHOT_REVIEW
FT = llm_schemas.FAILURE_TRIAGE
FT_OK = {"category": "auth", "reason_code": "E401", "retryable": False,
         "suggested_action": "refresh", "confidence": 0.9}
FT_ENUM = "['auth', 'permanent', 'transient', 'rate_limited', 'unknown']"


# ── the pre-row-979 validator, frozen (llm_exec.py at 0e7383cf, verbatim) ──
_LEGACY_PYTYPES = {
    "string": str, "number": (int, float), "integer": int,
    "boolean": bool, "array": list, "object": dict, "null": type(None),
}


def _legacy_check_type(value, t):
    py = _LEGACY_PYTYPES.get(t)
    if py is None:
        return True, ""
    if t in ("integer", "number") and isinstance(value, bool):
        return False, f"expected {t}, got boolean"
    if isinstance(value, py):
        return True, ""
    return False, f"expected {t}"


def _legacy_validate_schema(value, schema):
    if not schema:
        return True, ""
    if "enum" in schema:
        if value not in schema["enum"]:
            return False, f"{value!r} not in enum {schema['enum']}"
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            return False, "expected object"
        for k in schema.get("required", []):
            if k not in value:
                return False, f"missing required key: {k}"
        for k, sub in (schema.get("properties") or {}).items():
            if k not in value or not isinstance(sub, dict):
                continue
            if "enum" in sub and value[k] not in sub["enum"]:
                return False, f"key {k}: {value[k]!r} not in enum {sub['enum']}"
            if "type" in sub:
                ok, why = _legacy_check_type(value[k], sub["type"])
                if not ok:
                    return False, f"key {k}: {why}"
        return True, ""
    if t == "array":
        if not isinstance(value, list):
            return False, "expected array"
        items = schema.get("items")
        if items:
            for i, el in enumerate(value):
                ok, why = _legacy_validate_schema(el, items)
                if not ok:
                    return False, f"item {i}: {why}"
        return True, ""
    if t:
        return _legacy_check_type(value, t)
    return True, ""


def _outcome(fn, *args, **kwargs):
    """("ok", result) or ("raise", exception type, message): the assertion's subject."""
    try:
        return ("ok", fn(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001 -- a raise is an outcome to compare
        return ("raise", type(exc).__name__, str(exc))


# ── wiring ────────────────────────────────────────────────────────────────
def test_llm_exec_validate_schema_is_the_engine():
    """The product path (llm_exec execute(): cache hit + result gate) runs the engine."""
    from bulk_downloader import schema_runtime

    assert llm_exec.validate_schema.__module__ == "bulk_downloader.schema_runtime"
    assert llm_exec.validate_schema is schema_runtime.validate_schema
    assert llm_exec.validate_schema({"category": "auth"}, {"type": "object", "required": ["category"]}) == (True, "")
    assert llm_exec.validate_schema({"wrong_key": 123}, {"type": "object", "required": ["category"]}) == (
        False, "missing required key: category")


# ── answers: the legacy dialect, check by check ───────────────────────────
@pytest.mark.parametrize("value,schema,expected", [
    pytest.param({"severity": "low"}, UI, (False, "missing required key: findings"), id="required"),
    pytest.param({"findings": [], "severity": "critical"}, UI,
                 (False, "key severity: 'critical' not in enum ['none', 'low', 'medium', 'high']"),
                 id="property-enum"),
    pytest.param({"category": "auth", "reason_code": 7}, FT, (False, "key reason_code: expected string"),
                 id="string-type"),
    pytest.param("nope", {"enum": ["low", "high"]}, (False, "'nope' not in enum ['low', 'high']"),
                 id="top-level-enum"),
    pytest.param([FT_OK, {"category": "x"}], {"type": "array", "items": FT},
                 (False, f"item 1: key category: 'x' not in enum {FT_ENUM}"), id="array-item"),
    pytest.param("text", UI, (False, "expected object"), id="not-object"),
    pytest.param({}, {"type": "array"}, (False, "expected array"), id="not-array"),
    pytest.param({"category": "auth", "retryable": "yes"}, FT, (False, "key retryable: expected boolean"),
                 id="boolean-type"),
    pytest.param({"category": "auth", "confidence": "high"}, FT, (False, "key confidence: expected number"),
                 id="number-type"),
    pytest.param({"n": True}, {"type": "object", "properties": {"n": {"type": "integer"}}},
                 (False, "key n: expected integer, got boolean"), id="integer-not-bool"),
    pytest.param(True, {"type": "number"}, (False, "expected number, got boolean"), id="scalar-number-not-bool"),
    pytest.param(0, {"type": "null"}, (False, "expected null"), id="scalar-null"),
    pytest.param(3, {"type": "string", "enum": [3]}, (False, "expected string"), id="enum-then-type"),
    pytest.param({"category": "bogus", "retryable": "yes", "confidence": "high"}, FT,
                 (False, f"key category: 'bogus' not in enum {FT_ENUM}"), id="first-failure-only"),
    pytest.param(FT_OK, FT, (True, ""), id="valid-object"),
    pytest.param([FT_OK, FT_OK], {"type": "array", "items": FT}, (True, ""), id="valid-array"),
    pytest.param({"findings": [], "x": object()}, UI, (True, ""), id="unknown-keys-ignored"),
    pytest.param(object(), {"type": "any"}, (True, ""), id="unknown-type-accepts"),
    pytest.param("anything", None, (True, ""), id="no-schema"),
])
def test_each_check_answers_in_the_legacy_words(value, schema, expected):
    assert llm_exec.validate_schema(value, schema) == expected
    assert _legacy_validate_schema(value, schema) == expected


def test_typeless_schema_keeps_the_legacy_verdict():
    """No "type": required/properties are not checked (the dialect never did)."""
    typeless = {"required": ["a"], "properties": {"a": {"type": "string"}}}
    assert llm_exec.validate_schema({}, typeless) == (True, "")
    assert llm_exec.validate_schema({"a": 1}, typeless) == (True, "")
    assert llm_exec.validate_schema("text", typeless) == (True, "")
    with_enum = {"enum": ["y"], "required": ["a"]}
    assert llm_exec.validate_schema("x", with_enum) == (False, "'x' not in enum ['y']")
    assert llm_exec.validate_schema("y", with_enum) == (True, "")


_KEYS = ("a", "b", "c", "category")
_TYPES = ("string", "number", "integer", "boolean", "null", "array", "object", "any", "")
_ENUM_POOL = ("x", "auth", 0, 1, 2.5, True, False, None, [1], {"a": 1})
_SCALARS = ("x", "", "auth", 0, 1, -3, 2.5, True, False, None)
_MALFORMED = (
    {"type": ["string", "null"]},                                    # unhashable type
    {"type": 5},                                                     # hashable non-str type
    {"type": "object", "required": "ab"},                            # str: iterated per character
    {"type": "object", "required": None},
    {"type": "object", "required": [1, "a"]},
    {"type": "object", "properties": [1]},
    {"type": "object", "properties": {"a": {"type": ["x"]}}},
    {"type": "object", "properties": {"a": {"enum": None}}},
    {"type": "object", "properties": {"a": {"type": 7}}},
    {"type": "array", "items": [{"type": "string"}]},
    {"type": "array", "items": {"type": "object", "required": None}},
    {"enum": 5},
    ["type", "enum"],
)


def _gen_value(rng, depth=0):
    r = rng.random()
    if depth < 2 and r < 0.25:
        return {k: _gen_value(rng, depth + 1) for k in rng.sample(_KEYS, rng.randint(0, 4))}
    if depth < 2 and r < 0.4:
        return [_gen_value(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    return rng.choice(_SCALARS)


def _gen_prop(rng):
    if rng.random() < 0.1:
        return "not-a-dict"
    prop = {}
    if rng.random() < 0.8:
        prop["type"] = rng.choice(_TYPES)
    if rng.random() < 0.3:
        prop["enum"] = rng.sample(_ENUM_POOL, rng.randint(1, 3))
    return prop


def _gen_schema(rng, depth=0):
    if rng.random() < 0.08:
        return rng.choice(_MALFORMED)
    schema: dict[str, Any] = {}
    t = rng.choice(("object", "object", "array", "string", "number", "integer",
                    "boolean", "null", None, None, "any", ""))
    if t is not None:
        schema["type"] = t
    if rng.random() < 0.2:
        schema["enum"] = rng.sample(_ENUM_POOL, rng.randint(1, 3))
    if t in ("object", None) or rng.random() < 0.15:
        if rng.random() < 0.7:
            schema["required"] = rng.sample(_KEYS, rng.randint(0, 3))
        if rng.random() < 0.8:
            schema["properties"] = {k: _gen_prop(rng) for k in rng.sample(_KEYS, rng.randint(0, 4))}
    if t == "array" and depth < 2 and rng.random() < 0.8:
        schema["items"] = _gen_schema(rng, depth + 1) if rng.random() < 0.9 else {}
    if rng.random() < 0.15:  # the same schema as a dict subclass: odd shapes answer the same too
        return collections.OrderedDict(schema)
    return schema


def _value_for(rng, schema):
    if isinstance(schema, dict) and rng.random() < 0.6:
        keys = list(schema.get("required") or []) if isinstance(schema.get("required"), list) else []
        props = schema.get("properties")
        keys += list(props) if isinstance(props, dict) else []
        if schema.get("type") == "array":
            return [_value_for(rng, schema.get("items") or {}) for _ in range(rng.randint(0, 3))]
        return {k: _gen_value(rng, 1) for k in keys if isinstance(k, str) and rng.random() < 0.8}
    return _gen_value(rng)


def test_twin_diff_against_the_frozen_legacy_validator():
    """4000 seeded (schema, value) pairs: engine == frozen legacy, raises included,
    on each schema object's first call (interpreted) and on its repeat (compiled)."""
    rng = random.Random(979)
    mismatches = []
    seen: collections.Counter = collections.Counter()
    for _ in range(4000):
        schema = _gen_schema(rng)
        value = _value_for(rng, schema)
        want = _outcome(_legacy_validate_schema, value, schema)
        first = _outcome(llm_exec.validate_schema, value, schema)
        repeat = _outcome(llm_exec.validate_schema, value, schema)
        if first != want or repeat != want:
            mismatches.append((schema, value, want, first, repeat))
        seen[want[0] if want[0] == "raise" else ("ok", want[1][0])] += 1
        if (isinstance(schema, dict) and not schema.get("type") and isinstance(value, dict)
                and isinstance(schema.get("required"), list)
                and any(k not in value for k in schema["required"])):
            seen["typeless-missing-required"] += 1
        if any(schema is m for m in _MALFORMED):
            seen["malformed"] += 1
        if type(schema) is collections.OrderedDict:
            seen["dict-subclass", want[0] if want[0] == "raise" else want[1][0]] += 1
    assert mismatches == [], f"{len(mismatches)} divergences, first: {mismatches[:3]}"
    for category in (("ok", True), ("ok", False), "raise", "typeless-missing-required", "malformed",
                     ("dict-subclass", True), ("dict-subclass", False)):
        assert seen[category] > 0, (category, seen)


# ── cost structure (the E1 perf fix, asserted structurally, not by wall clock) ──
_BD = os.sep + "bulk_downloader" + os.sep
_LEGACY_TO_ENGINE = {"_legacy_validate_schema": "_interpret_schema", "_legacy_check_type": "_check_type"}


def _profiled(where, fn, *args):
    """(outcome, names of the functions under `where` entered) for one call."""
    names = []

    def profile(frame, event, arg):
        if event == "call" and where in frame.f_code.co_filename:
            names.append(frame.f_code.co_name)

    sys.setprofile(profile)
    try:
        outcome = _outcome(fn, *args)
    finally:
        sys.setprofile(None)
    return outcome, names


@pytest.mark.parametrize("value,schema", [
    pytest.param({"findings": [{"x": 1}], "severity": "low", "accessibility": []}, UI, id="valid-full"),
    pytest.param({"findings": []}, UI, id="valid-min"),
    pytest.param({"severity": "low"}, UI, id="missing-required"),
    pytest.param({"findings": [], "severity": "critical"}, UI, id="bad-enum"),
    pytest.param("no json object", UI, id="not-object"),
    pytest.param([FT_OK, {"category": "auth", "retryable": 1}], {"type": "array", "items": FT}, id="array-items"),
])
def test_a_first_call_is_the_legacy_walk(value, schema):
    """A schema object's first call compiles nothing: it is validate_schema plus the
    frozen legacy's own walk, frame for frame -- a schema used once (a fresh object
    per call) costs one frame more than before row 979, not a compile."""
    schema = copy.deepcopy(schema)  # a fresh object: the engine has never seen it
    want, legacy = _profiled(__file__, _legacy_validate_schema, value, schema)
    got, engine = _profiled(_BD, llm_exec.validate_schema, value, schema)
    expected = ["validate_schema"] + [_LEGACY_TO_ENGINE[n] for n in legacy if n in _LEGACY_TO_ENGINE]
    assert engine == expected, collections.Counter(engine)
    assert got == want


def _bulk_downloader_calls(n, value, schema):
    """Names of the bulk_downloader functions entered by n repeat validate_schema calls."""
    names = []

    def profile(frame, event, arg):
        if event == "call" and _BD in frame.f_code.co_filename:
            names.append(frame.f_code.co_name)

    validate = llm_exec.validate_schema
    validate(value, schema)  # the first call interprets, the second compiles:
    validate(value, schema)  # repeat calls are what is counted
    gc_enabled = gc.isenabled()
    gc.disable()
    sys.setprofile(profile)
    try:
        for _ in range(n):
            validate(value, schema)
    finally:
        sys.setprofile(None)
        if gc_enabled:
            gc.enable()
    return names


@pytest.mark.parametrize("value", [
    pytest.param({"findings": [{"x": 1}], "severity": "low", "accessibility": []}, id="valid-full"),
    pytest.param({"findings": []}, id="valid-min"),
    pytest.param({"severity": "low"}, id="missing-required"),
    pytest.param({"findings": [], "severity": "critical"}, id="bad-enum"),
    pytest.param("no json object", id="not-object"),
])
def test_a_repeat_call_is_two_frames(value):
    """validate_schema + the compiled check: no per-call validator object, no
    per-property helper calls, no import -- exactly 2 frames, 100 calls -> 200."""
    names = _bulk_downloader_calls(100, value, UI)
    assert len(names) == 200, collections.Counter(names)
    assert names[0::2] == ["validate_schema"] * 100


@pytest.fixture
def fresh_checks(monkeypatch):
    """schema_runtime with an empty compiled-check table and last-schema slot."""
    from bulk_downloader import schema_runtime

    monkeypatch.setattr(schema_runtime, "_SCHEMA_CHECKS", {})
    monkeypatch.setattr(schema_runtime, "_last_schema_check", (None, None))
    return schema_runtime


def test_each_schema_object_compiles_once(fresh_checks, monkeypatch):
    schema_runtime = fresh_checks
    compiled = []
    real = schema_runtime.compile_schema

    def counting(schema):
        compiled.append(schema)
        return real(schema)

    monkeypatch.setattr(schema_runtime, "compile_schema", counting)
    a = {"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}}
    b = {"type": "array", "items": {"type": "string"}}
    for i in range(50):  # alternating: past the last-schema slot, into the id table
        assert llm_exec.validate_schema({"x": i}, a) == (True, "")
        assert llm_exec.validate_schema(["s", str(i)], b) == (True, "")
    assert llm_exec.validate_schema({"x": "1"}, a) == (False, "key x: expected integer")
    assert llm_exec.validate_schema(["s", 1], b) == (False, "item 1: expected string")
    assert len(compiled) == 2 and compiled[0] is a and compiled[1] is b
    twin = dict(a)  # equal content, another object: its own entry
    assert llm_exec.validate_schema({}, twin) == (False, "missing required key: x")
    assert len(compiled) == 2  # its first call is interpreted
    assert llm_exec.validate_schema({"x": True}, twin) == (False, "key x: expected integer, got boolean")
    assert len(compiled) == 3 and compiled[2] is twin


def test_a_repeat_call_skips_the_table(fresh_checks, monkeypatch):
    """The last schema's check is held in one slot: repeat calls on one schema object
    never look it up in the table; a switch of object looks up once."""
    schema_runtime = fresh_checks
    lookups = []

    class CountingTable(dict):
        def get(self, key, default=None):
            lookups.append(key)
            return super().get(key, default)

    monkeypatch.setattr(schema_runtime, "_SCHEMA_CHECKS", CountingTable())
    a, b = copy.deepcopy(FT), copy.deepcopy(UI)
    for schema, value in ((a, FT_OK), (a, FT_OK), (b, {"findings": []}), (b, {"findings": []})):
        llm_exec.validate_schema(value, schema)  # each object: interpreted, then compiled
    del lookups[:]
    for _ in range(100):
        assert llm_exec.validate_schema({"findings": []}, b) == (True, "")
    assert lookups == []
    assert llm_exec.validate_schema(FT_OK, a) == (True, "")
    assert lookups == [id(a)]


def test_the_compiled_table_is_bounded(fresh_checks):
    schema_runtime = fresh_checks
    table = schema_runtime._SCHEMA_CHECKS
    cap = schema_runtime._SCHEMA_CHECKS_MAX
    schemas = [{"type": "object", "required": [f"k{i}"]} for i in range(cap + 10)]
    for i, schema in enumerate(schemas):  # first calls: one uncompiled entry per object
        assert llm_exec.validate_schema({}, schema) == (False, f"missing required key: k{i}")
    assert 0 < len(table) <= cap
    for i, schema in enumerate(schemas):  # evicted or not, every schema answers for itself
        assert llm_exec.validate_schema({f"k{i}": 1}, schema) == (True, "")
    for i, schema in enumerate([dict(s) for s in schemas]):  # SchemaValidator compiles at once
        res = schema_runtime.SchemaValidator(schema).validate({})
        assert (res.valid, res.errors) == (False, [f"missing required key: k{i}"])
    assert 0 < len(table) <= cap


def test_the_validation_path_imports_no_serialization_backend():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        "import sys\n"
        f"sys.path.insert(0, {root!r})\n"
        "from bulk_downloader import llm_exec, llm_schemas\n"
        "ok = llm_exec.validate_schema({'findings': []}, llm_schemas.UI_SCREENSHOT_REVIEW)\n"
        "print(ok, sorted(m for m in ('msgspec', 'pydantic') if m in sys.modules))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=root)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "(True, '') []"


# ── faults surface ────────────────────────────────────────────────────────
def test_an_engine_fault_is_raised_not_masked(monkeypatch):
    from bulk_downloader import schema_runtime

    def fault(*args, **kwargs):
        raise RuntimeError("engine fault")

    # the engine entry of any revision: row 979's compiler, and the collect-all
    # validator it replaced (raising=False: a revision need not have both)
    monkeypatch.setattr(schema_runtime, "compile_schema", fault, raising=False)
    monkeypatch.setattr(schema_runtime, "validate_payload_against_schema", fault, raising=False)
    fresh = {"type": "object", "required": ["category"]}  # never validated
    # row 979 interprets a schema object's first call and compiles on its second
    assert llm_exec.validate_schema({"category": "auth"}, fresh) == (True, "")
    with pytest.raises(RuntimeError, match="engine fault"):
        llm_exec.validate_schema({"category": "auth"}, fresh)


# ── SchemaValidator / llm_schemas.validate: the same dialect ──────────────
def test_schema_validator_success_and_failure():
    """SchemaValidator validates schema contracts; errors carry the first failing check."""
    from bulk_downloader.schema_runtime import SchemaValidator

    schema = {
        "type": "object",
        "required": ["run_id", "category"],
        "properties": {
            "run_id": {"type": "integer"},
            "category": {"type": "string", "enum": ["telemetry", "event", "diagnostic"]},
            "rate": {"type": "number"},
        },
    }

    validator = SchemaValidator(schema)

    valid_res = validator.validate({"run_id": 979, "category": "telemetry", "rate": 99.4})
    assert valid_res.valid is True
    assert valid_res.errors == []
    assert valid_res.data["run_id"] == 979

    bad_res1 = validator.validate({"run_id": 979})
    assert (bad_res1.valid, bad_res1.errors) == (False, ["missing required key: category"])

    bad_res2 = validator.validate({"run_id": 979, "category": "invalid_category"})
    assert (bad_res2.valid, bad_res2.errors) == (
        False, ["key category: 'invalid_category' not in enum ['telemetry', 'event', 'diagnostic']"])

    bad_res3 = validator.validate({"run_id": "not_an_int", "category": "event"})
    assert (bad_res3.valid, bad_res3.errors) == (False, ["key run_id: expected integer"])


def test_legacy_schema_migration_bridge():
    """llm_schemas dicts validate through the runtime with llm_exec's verdicts."""
    from bulk_downloader.schema_runtime import validate_payload_against_schema

    valid_triage = {
        "category": "auth",
        "reason_code": "ERR_TOKEN_EXPIRED",
        "retryable": False,
        "suggested_action": "refresh_session",
        "confidence": 0.98,
    }
    res = validate_payload_against_schema(valid_triage, FT)
    assert (res.valid, res.errors) == (True, [])

    invalid_triage = {"category": "not_a_valid_category", "retryable": "not_a_bool"}
    bad_res = validate_payload_against_schema(invalid_triage, FT)
    assert (bad_res.valid, bad_res.errors) == (
        False, [f"key category: 'not_a_valid_category' not in enum {FT_ENUM}"])
    assert bad_res.errors == [llm_exec.validate_schema(invalid_triage, FT)[1]]


def test_llm_schemas_validate_fast_integration():
    ok, errors = llm_schemas.validate("failure_triage.v1", {
        "category": "auth",
        "reason_code": "EXPIRED",
        "retryable": False,
    })
    assert (ok, errors) == (True, [])
    assert llm_schemas.validate("failure_triage.v1", {"category": "invalid_category"}) == (
        False, [f"key category: 'invalid_category' not in enum {FT_ENUM}"])
    assert llm_schemas.validate("nope", {}) == (False, ["Unknown schema: nope"])


# ── serialization half ────────────────────────────────────────────────────
def test_runtime_metadata():
    """Metadata reports exactly the backends this environment has."""
    from bulk_downloader.schema_runtime import (
        get_serialization_runtime_info,
        is_msgspec_available,
        is_pydantic_available,
    )

    has_msgspec = importlib.util.find_spec("msgspec") is not None
    has_pydantic = importlib.util.find_spec("pydantic") is not None
    assert get_serialization_runtime_info() == {
        "backend": "msgspec" if has_msgspec else "stdlib_json",
        "msgspec_available": has_msgspec,
        "pydantic_v2_available": has_pydantic,
        "acceleration": "c_extension" if has_msgspec else "pure_python",
        "schema_version": 1,
        "fast_encoding_supported": True,
        "zero_copy_decoding": has_msgspec,
    }
    assert is_msgspec_available() is has_msgspec
    assert is_pydantic_available() is has_pydantic


def test_fast_dumps_and_loads_primitives():
    """Verify high-throughput encoding and decoding of JSON primitives and nested structures."""
    from bulk_downloader.schema_runtime import (
        decode_json,
        encode_json,
        fast_dumps,
        fast_loads,
    )

    data = {
        "task_id": 979,
        "action": "schema_validation_migration",
        "flags": [True, False, None],
        "metrics": {"latency_us": 12.5, "count": 1000},
    }

    encoded_str = fast_dumps(data)
    assert isinstance(encoded_str, str)
    assert fast_loads(encoded_str) == data

    encoded_bytes = encode_json(data)
    assert isinstance(encoded_bytes, bytes)
    assert decode_json(encoded_bytes) == data


def test_zero_copy_buffer_decoding():
    """Verify decode_json accepts bytes, bytearray, and memoryview."""
    from bulk_downloader.schema_runtime import decode_json, encode_json

    payload = {"status": "success", "rows": [1, 2, 3]}
    raw_bytes = encode_json(payload)
    assert decode_json(bytearray(raw_bytes)) == payload
    assert decode_json(memoryview(raw_bytes)) == payload


def test_fast_serialization_struct_and_dataclass():
    """Verify encoding of dataclasses and models with typed deserialization."""
    from bulk_downloader.schema_runtime import (
        EnterpriseModel,
        decode_json,
        encode_json,
        fast_dumps,
    )

    @dataclasses.dataclass
    class TransferMetric:
        channel: str
        bytes_transferred: int
        success: bool

    metric = TransferMetric(channel="cluster-lan", bytes_transferred=1048576, success=True)
    serialized = fast_dumps(metric)
    assert json.loads(serialized) == {"channel": "cluster-lan", "bytes_transferred": 1048576, "success": True}

    class WorkerConfig(EnterpriseModel):
        worker_id: str
        pool_size: int
        enabled: bool = True

    cfg = WorkerConfig(worker_id="g6", pool_size=16)
    cfg_json = encode_json(cfg)
    assert b"g6" in cfg_json

    restored = decode_json(cfg_json, target_type=WorkerConfig)
    assert restored.worker_id == "g6"
    assert restored.pool_size == 16
    assert restored.enabled is True


def test_stdlib_backend_round_trips_what_msgspec_does(monkeypatch):
    """The stdlib path (all of CI: no msgspec there) forced on any environment."""
    from bulk_downloader import schema_runtime as sr

    model_base = sr.EnterpriseModel  # built for the real backend before the patch
    monkeypatch.setattr(sr, "MSGSPEC_AVAILABLE", False)

    payload = {"status": "success", "rows": [1, 2, 3], "note": "naïve"}
    raw = json.dumps(payload).encode("utf-8")
    decoded = {kind: _outcome(sr.decode_json, buf) for kind, buf in (
        ("str", raw.decode("utf-8")), ("bytes", raw), ("bytearray", bytearray(raw)), ("memoryview", memoryview(raw)))}
    assert decoded == {kind: ("ok", payload) for kind in decoded}

    @dataclasses.dataclass
    class TransferMetric:
        channel: str
        bytes_transferred: int

    assert _outcome(lambda: json.loads(sr.fast_dumps(TransferMetric("lan", 7)))) == (
        "ok", {"channel": "lan", "bytes_transferred": 7})
    assert _outcome(sr.decode_json, b'{"channel": "lan", "bytes_transferred": 7}', target_type=TransferMetric) == (
        "ok", TransferMetric("lan", 7))
    assert _outcome(sr.decode_json, b"[1, 2]", target_type=list) == ("ok", [1, 2])

    class WorkerConfig(model_base):
        worker_id: str
        pool_size: int

    encoded = _outcome(sr.encode_json, WorkerConfig(worker_id="g6", pool_size=16))
    assert encoded[0] == "ok" and json.loads(encoded[1]) == {"worker_id": "g6", "pool_size": 16}
    restored = _outcome(sr.decode_json, encoded[1], target_type=WorkerConfig)
    assert restored[0] == "ok" and (restored[1].worker_id, restored[1].pool_size) == ("g6", 16)
    # what is neither dataclass nor model still goes to the caller's hook, or fails
    assert json.loads(sr.fast_dumps({"o": object()}, default=lambda o: "hooked")) == {"o": "hooked"}
    assert _outcome(sr.fast_dumps, {"o": object()})[:2] == ("raise", "TypeError")


def test_pydantic_v2_model_adapter():
    """Verify Pydantic-v2 compatible model API works across environments (native or fallback)."""
    from bulk_downloader.schema_runtime import EnterpriseModel

    class EventEnvelope(EnterpriseModel):
        event_name: str
        timestamp_ns: int
        payload: dict[str, Any]

    envelope = EventEnvelope.model_validate({
        "event_name": "worker.heartbeat",
        "timestamp_ns": 1726960000000,
        "payload": {"seat": "bd-agy-worker-g6", "status": "active"},
    })
    assert envelope.event_name == "worker.heartbeat"
    assert envelope.payload["seat"] == "bd-agy-worker-g6"

    dumped = envelope.model_dump()
    assert isinstance(dumped, dict)
    assert dumped["event_name"] == "worker.heartbeat"
    assert dumped["timestamp_ns"] == 1726960000000

    json_str = envelope.model_dump_json()
    assert isinstance(json_str, str)
    assert "worker.heartbeat" in json_str


def test_safe_json_fast_fallback():
    """Verify fallback gracefully handles complex custom objects that lack msgspec encoders."""
    from bulk_downloader.schema_runtime import decode_json, fast_dumps, fast_loads

    class CustomObject:
        def __init__(self, val: str):
            self.val = val

        def __str__(self):
            return f"Custom({self.val})"

    payload = {"custom": CustomObject("test_payload")}
    dumped = fast_dumps(payload, default=str)
    assert "Custom(test_payload)" in dumped
    restored = fast_loads(dumped)
    assert restored["custom"] == "Custom(test_payload)"
    # what msgspec rejects goes to stdlib json, which answers the same on either backend
    assert _outcome(fast_dumps, {"custom": object()}) == (
        "raise", "TypeError", "Object of type object is not JSON serializable")

    class Dumpable:  # not a msgspec type nor a dataclass: msgspec rejects it, stdlib encodes it
        def model_dump(self):
            return {"k": 1}

    assert _outcome(fast_dumps, {"m": Dumpable()}) == ("ok", '{"m": {"k": 1}}')
    got = _outcome(decode_json, memoryview(b"NaN"))  # msgspec: malformed; stdlib json: nan
    assert got[0] == "ok" and math.isnan(got[1]), got


def test_enterprise_model_is_built_once_on_first_use():
    from bulk_downloader import schema_runtime

    assert schema_runtime.EnterpriseModel is schema_runtime.EnterpriseModel
    assert schema_runtime.EnterpriseModel.__qualname__ == "EnterpriseModel"
    with pytest.raises(AttributeError, match="no_such_attribute"):
        schema_runtime.no_such_attribute
