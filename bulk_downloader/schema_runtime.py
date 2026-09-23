"""Row 979: Enterprise Schema Validation & Fast Serialization Runtime Migration (msgspec & pydantic-v2).

Two halves:

* Schema validation. ``validate_schema`` validates llm_exec's minimal schema
  dialect (type / required / properties{k: enum, type} / enum / array items; see
  llm_schemas) and is llm_exec's validator (llm_exec re-exports it). A schema
  object's first call is interpreted; from its second call on it is compiled once
  into a fail-fast check that makes the interpreter's checks in the interpreter's
  order, so verdicts and reasons are unchanged and a call costs only the check.
* Serialization. msgspec C-accelerated JSON encode/decode and a pydantic-v2 style
  model base when msgspec is installed, stdlib json otherwise. The optional
  backends are imported on first use, never by importing this module: llm_exec
  imports it, and validation needs neither.
"""
from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import json
import logging
import threading
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

# Module availability detection (find_spec only: nothing is imported here)
def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ModuleNotFoundError, ValueError, AttributeError):
        return False


MSGSPEC_AVAILABLE: bool = _module_available("msgspec")
PYDANTIC_AVAILABLE: bool = _module_available("pydantic")

T = TypeVar("T")

_msgspec_module: Any = None


def _msgspec() -> Any:
    """The msgspec module, imported on first use (callers check MSGSPEC_AVAILABLE)."""
    global _msgspec_module
    if _msgspec_module is None:
        _msgspec_module = importlib.import_module("msgspec")
    return _msgspec_module


def is_msgspec_available() -> bool:
    """Return True if the msgspec C-accelerated runtime is available."""
    return MSGSPEC_AVAILABLE


def is_pydantic_available() -> bool:
    """Return True if pydantic (v2) is available in the current environment."""
    return PYDANTIC_AVAILABLE


def get_serialization_runtime_info() -> dict[str, Any]:
    """Return capability and backend configuration metadata."""
    return {
        "backend": "msgspec" if MSGSPEC_AVAILABLE else "stdlib_json",
        "msgspec_available": MSGSPEC_AVAILABLE,
        "pydantic_v2_available": PYDANTIC_AVAILABLE,
        "acceleration": "c_extension" if MSGSPEC_AVAILABLE else "pure_python",
        "schema_version": 1,
        "fast_encoding_supported": True,
        "zero_copy_decoding": MSGSPEC_AVAILABLE,
    }


def _stdlib_default(enc_hook: Callable[[Any], Any] | None) -> Callable[[Any], Any]:
    """``json.dumps`` default for the stdlib path: dataclass instances and models
    encode as their fields (msgspec encodes both natively); anything else goes to
    the caller's hook."""

    def default(obj: Any) -> Any:
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return dataclasses.asdict(obj)
        dump = getattr(obj, "model_dump", None)
        if callable(dump) and not isinstance(obj, type):
            return dump()
        if enc_hook is not None:
            return enc_hook(obj)
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    return default


def _stdlib_encode(obj: Any, enc_hook: Callable[[Any], Any] | None) -> bytes:
    return json.dumps(obj, default=_stdlib_default(enc_hook), ensure_ascii=False).encode("utf-8")


def encode_json(
    obj: Any,
    enc_hook: Callable[[Any], Any] | None = None,
) -> bytes:
    """Encode an object into UTF-8 JSON bytes with high throughput.

    Uses msgspec C extension when available, with automatic fallback to stdlib json.
    """
    if MSGSPEC_AVAILABLE:
        msgspec = _msgspec()
        try:
            return msgspec.json.encode(obj, enc_hook=enc_hook)
        except (msgspec.EncodeError, TypeError):
            # what msgspec rejects (an object with model_dump, ...) stdlib json may
            # still encode: the stdlib path answers, and raises if it cannot either
            return _stdlib_encode(obj, enc_hook)
    return _stdlib_encode(obj, enc_hook)


def _build(target_type: Any, data: Any) -> Any:
    """``target_type`` from stdlib-decoded ``data``: models via ``model_validate``,
    dataclasses from their fields; any other target gets the decoded value."""
    validate = getattr(target_type, "model_validate", None)
    if callable(validate):
        return validate(data)
    if isinstance(target_type, type) and dataclasses.is_dataclass(target_type) and isinstance(data, dict):
        return target_type(**data)
    return data


def decode_json(
    buf: bytes | bytearray | memoryview | str,
    target_type: Any = Any,
    dec_hook: Callable[[type, Any], Any] | None = None,
) -> Any:
    """Decode JSON bytes, bytearray, memoryview, or str into Python objects or typed structures.

    Performs zero-copy parsing where supported by msgspec.
    """
    if isinstance(buf, str):
        buf = buf.encode("utf-8")

    if MSGSPEC_AVAILABLE:
        msgspec = _msgspec()
        try:
            if target_type is Any:
                return msgspec.json.decode(buf, dec_hook=dec_hook)
            return msgspec.json.decode(buf, type=target_type, dec_hook=dec_hook)
        except Exception:
            if target_type is Any:
                # Attempt stdlib fallback if msgspec fails on corner case
                return json.loads(bytes(buf).decode("utf-8"))
            raise

    # json.loads takes str/bytes/bytearray, not memoryview: one bytes() view of all three
    data = json.loads(bytes(buf).decode("utf-8"))
    if target_type is Any:
        return data
    return _build(target_type, data)


def fast_dumps(
    obj: Any,
    default: Callable[[Any], Any] | None = None,
) -> str:
    """Serialize object to JSON string via high-throughput C acceleration."""
    return encode_json(obj, enc_hook=default).decode("utf-8")


def fast_loads(
    s: str | bytes | bytearray | memoryview,
    target_type: Any = Any,
) -> Any:
    """Deserialize JSON string or buffer via high-throughput C acceleration."""
    return decode_json(s, target_type=target_type)


def _enterprise_model() -> type:
    """The EnterpriseModel base for the installed backend: a msgspec Struct, else a
    pydantic v2 BaseModel, else a plain class. Built on first use (``__getattr__``)."""
    base = None
    if not MSGSPEC_AVAILABLE and PYDANTIC_AVAILABLE:
        base = getattr(importlib.import_module("pydantic"), "BaseModel", None)
    if MSGSPEC_AVAILABLE:
        msgspec = _msgspec()

        class EnterpriseModel(msgspec.Struct):
            """High-performance Enterprise Model base class providing Pydantic-v2 API compatibility."""

            @classmethod
            def model_validate(cls: type[T], obj: Any) -> T:
                """Validate and construct model instance from dict, bytes, or another instance."""
                if isinstance(obj, cls):
                    return obj
                if isinstance(obj, (bytes, bytearray, memoryview, str)):
                    return decode_json(obj, target_type=cls)
                if isinstance(obj, dict):
                    return msgspec.convert(obj, cls)
                raise ValueError(f"Cannot validate object of type {type(obj)} to {cls}")

            def model_dump(self, mode: str = "python") -> dict[str, Any]:
                """Serialize model fields to a plain Python dictionary."""
                return msgspec.to_builtins(self)

            def model_dump_json(self) -> str:
                """Serialize model fields to a JSON string."""
                return fast_dumps(self)

    elif base is not None:
        class EnterpriseModel(base):  # type: ignore
            """Enterprise Model base using native Pydantic v2."""
    else:
        class EnterpriseModel:  # type: ignore
            """Enterprise Model base fallback when neither msgspec nor pydantic is available."""

            def __init__(self, **kwargs: Any) -> None:
                for k, v in kwargs.items():
                    setattr(self, k, v)

            @classmethod
            def model_validate(cls: type[T], obj: Any) -> T:
                if isinstance(obj, cls):
                    return obj
                if isinstance(obj, (bytes, bytearray, memoryview, str)):
                    data = decode_json(obj)
                    return cls(**data)
                if isinstance(obj, dict):
                    return cls(**obj)
                raise ValueError(f"Cannot validate object of type {type(obj)} to {cls}")

            def model_dump(self, mode: str = "python") -> dict[str, Any]:
                if dataclasses.is_dataclass(self):
                    return dataclasses.asdict(self)
                return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

            def model_dump_json(self) -> str:
                return fast_dumps(self.model_dump())

    EnterpriseModel.__qualname__ = "EnterpriseModel"
    return EnterpriseModel


_model_lock = threading.Lock()


def __getattr__(name: str) -> Any:
    # PEP 562: EnterpriseModel is built on first use, so importing this module
    # (llm_exec does, for validate_schema) imports neither msgspec nor pydantic.
    if name != "EnterpriseModel":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    with _model_lock:
        model = globals().get(name)
        if model is None:
            model = globals()[name] = _enterprise_model()
    return model


@dataclasses.dataclass
class ValidationResult:
    """Outcome of schema validation run."""
    valid: bool
    data: Any = None
    errors: list[str] = dataclasses.field(default_factory=list)


# ── schema validation: llm_exec's dialect, compiled once per schema ───────
_PYTYPES = {
    "string": str, "number": (int, float), "integer": int,
    "boolean": bool, "array": list, "object": dict, "null": type(None),
}
_NUMERIC = ("integer", "number")
_SchemaCheck = Callable[[Any], tuple[bool, str]]


def _check_type(value: Any, t: str) -> tuple[bool, str]:
    py = _PYTYPES.get(t)
    if py is None:
        return True, ""
    if t in ("integer", "number") and isinstance(value, bool):
        return False, f"expected {t}, got boolean"
    if isinstance(value, py):
        return True, ""
    return False, f"expected {t}"


def _interpret_schema(value: Any, schema: Any) -> tuple[bool, str]:
    """The dialect's interpreter: llm_exec.validate_schema as it was before row 979,
    verbatim. It runs every schema the compiler does not take, so those keep their
    old answers -- and their old exceptions."""
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
                ok, why = _check_type(value[k], sub["type"])
                if not ok:
                    return False, f"key {k}: {why}"
        return True, ""
    if t == "array":
        if not isinstance(value, list):
            return False, "expected array"
        items = schema.get("items")
        if items:
            for i, el in enumerate(value):
                ok, why = _interpret_schema(el, items)
                if not ok:
                    return False, f"item {i}: {why}"
        return True, ""
    if t:
        return _check_type(value, t)
    return True, ""


def _accept(value: Any) -> tuple[bool, str]:
    return True, ""


def _compile_scalar(t: str | None) -> _SchemaCheck:
    py = _PYTYPES.get(t) if t else None
    if py is None:  # no type, or a name outside the table: the interpreter accepts
        return _accept
    if t in _NUMERIC:
        def check_number(value: Any) -> tuple[bool, str]:
            if isinstance(value, bool):
                return False, f"expected {t}, got boolean"
            if isinstance(value, py):
                return True, ""
            return False, f"expected {t}"
        return check_number

    def check_scalar(value: Any) -> tuple[bool, str]:
        if isinstance(value, py):
            return True, ""
        return False, f"expected {t}"
    return check_scalar


def _compile_object(schema: dict[str, Any]) -> _SchemaCheck | None:
    required = schema.get("required", [])
    if type(required) not in (list, tuple) or any(type(k) is not str for k in required):
        return None
    required = tuple(required)
    props = schema.get("properties")
    if props is not None and type(props) is not dict:
        return None
    checks = []
    for key, sub in (props or {}).items():
        if not isinstance(sub, dict):
            continue  # the interpreter skips these too
        if type(sub) is not dict:
            return None
        enum = sub["enum"] if "enum" in sub else None
        if "enum" in sub and enum is None:
            return None  # `x in None` raises: the interpreter's to raise
        st = sub["type"] if "type" in sub else None
        if st is not None and type(st) is not str:
            return None
        py = _PYTYPES.get(st) if st is not None else None
        if enum is not None or py is not None:
            checks.append((key, enum, py, st, st in _NUMERIC))
    prop_checks = tuple(checks)

    def check_object(value: Any) -> tuple[bool, str]:
        if not isinstance(value, dict):
            return False, "expected object"
        for key in required:
            if key not in value:
                return False, f"missing required key: {key}"
        for key, enum, py, st, numeric in prop_checks:
            if key in value:
                v = value[key]
                if enum is not None and v not in enum:
                    return False, f"key {key}: {v!r} not in enum {enum}"
                if py is not None:
                    if numeric and isinstance(v, bool):
                        return False, f"key {key}: expected {st}, got boolean"
                    if not isinstance(v, py):
                        return False, f"key {key}: expected {st}"
        return True, ""
    return check_object


def _compile_array(schema: dict[str, Any]) -> _SchemaCheck | None:
    items = schema.get("items")
    if items is not None and type(items) is not dict:
        return None
    item_check = _compile(items) if items else None
    if items and item_check is None:
        return None

    def check_array(value: Any) -> tuple[bool, str]:
        if not isinstance(value, list):
            return False, "expected array"
        if item_check is not None:
            for i, el in enumerate(value):
                ok, why = item_check(el)
                if not ok:
                    return False, f"item {i}: {why}"
        return True, ""
    return check_array


def _with_enum(enum: Any, typed: _SchemaCheck) -> _SchemaCheck:
    if typed is _accept:
        def check_enum(value: Any) -> tuple[bool, str]:
            if value not in enum:
                return False, f"{value!r} not in enum {enum}"
            return True, ""
        return check_enum

    def check_enum_then_type(value: Any) -> tuple[bool, str]:
        if value not in enum:
            return False, f"{value!r} not in enum {enum}"
        return typed(value)
    return check_enum_then_type


def _compile(schema: Any) -> _SchemaCheck | None:
    """Closures making the interpreter's checks, in its order, for a plain
    JSON-shaped schema; None for any other shape (the interpreter keeps it)."""
    if type(schema) is not dict:
        return None
    t = schema.get("type")
    if t is not None and type(t) is not str:
        return None
    if t == "object":
        check = _compile_object(schema)
    elif t == "array":
        check = _compile_array(schema)
    else:
        check = _compile_scalar(t)
    if check is None or "enum" not in schema:
        return check
    return _with_enum(schema["enum"], check)


def compile_schema(schema: Any) -> _SchemaCheck:
    """``check(value) -> (ok, reason)`` for ``schema``: compiled closures for plain
    JSON-shaped schemas, the interpreter for any other shape -- the same answers
    (and exceptions) either way."""
    compiled = _compile(schema)
    if compiled is not None:
        return compiled

    def interpret(value: Any) -> tuple[bool, str]:
        return _interpret_schema(value, schema)
    return interpret


# id(schema) -> (schema, compiled check), or (schema, None) for an object used once
_SCHEMA_CHECKS: dict[int, tuple[Any, _SchemaCheck | None]] = {}
_SCHEMA_CHECKS_MAX = 256
_last_schema_check: tuple[Any, Any] = (None, None)


def schema_check(schema: Any) -> _SchemaCheck:
    """The compiled check of ``schema``, built once per schema object.

    Keyed on ``id(schema)``; each entry holds its schema, so no other object can
    have that id while the entry exists. The table is bounded (cleared when full).
    Schemas are treated as immutable once used -- llm_schemas' rule: a shipped
    schema is never edited in place."""
    hit = _SCHEMA_CHECKS.get(id(schema))
    if hit is not None and hit[1] is not None:
        return hit[1]
    check = compile_schema(schema)
    if len(_SCHEMA_CHECKS) >= _SCHEMA_CHECKS_MAX:
        _SCHEMA_CHECKS.clear()
    _SCHEMA_CHECKS[id(schema)] = (schema, check)
    return check


def validate_schema(value: Any, schema: dict[str, Any] | None) -> tuple[bool, str]:
    """Validate `value` against a minimal schema dialect:
      {"type":"object","required":[...],"properties":{k:{"type":...}}}
      {"type":"array","items":<schema>}
      {"type":"string"|"number"|"integer"|"boolean"|"null"}
    Returns (ok: bool, reason: str) -- the first failing check.

    This is llm_exec.validate_schema (row 979). A schema object's first call is
    answered by the interpreter, so a schema used once never pays for a compile;
    its second call compiles it. The last schema's check is held in one tuple, so
    a repeat call is this frame plus the compiled check."""
    global _last_schema_check
    if not schema:
        return True, ""
    last = _last_schema_check
    if last[0] is schema:
        return last[1](value)
    hit = _SCHEMA_CHECKS.get(id(schema))
    if hit is None:
        if len(_SCHEMA_CHECKS) >= _SCHEMA_CHECKS_MAX:
            _SCHEMA_CHECKS.clear()
        _SCHEMA_CHECKS[id(schema)] = (schema, None)
        return _interpret_schema(value, schema)
    check = hit[1] if hit[1] is not None else schema_check(schema)
    _last_schema_check = (schema, check)
    return check(value)


class SchemaValidator:
    """Unified schema validation engine supporting JSON schemas and EnterpriseModels.

    A dict schema is llm_exec's dialect (see validate_schema), compiled once and
    fail-fast: ``errors`` holds the first failing check, in llm_exec's words."""

    def __init__(self, schema: dict[str, Any] | type[Any]) -> None:
        self.schema = schema
        self._check = None if isinstance(schema, type) else schema_check(schema)

    def validate(self, payload: Any) -> ValidationResult:
        """Validate payload against configured schema specification."""
        if self._check is None:
            # Model / Struct / Dataclass validation
            try:
                if hasattr(self.schema, "model_validate"):
                    val = self.schema.model_validate(payload)
                    return ValidationResult(valid=True, data=val, errors=[])
                if MSGSPEC_AVAILABLE and issubclass(self.schema, _msgspec().Struct) and isinstance(payload, dict):
                    val = _msgspec().convert(payload, self.schema)
                    return ValidationResult(valid=True, data=val, errors=[])
                if isinstance(payload, dict):
                    val = self.schema(**payload)
                    return ValidationResult(valid=True, data=val, errors=[])
                return ValidationResult(valid=False, errors=[f"Cannot validate {type(payload)} to {self.schema}"])
            except (TypeError, ValueError, AttributeError) as e:
                return ValidationResult(valid=False, errors=[str(e)])

        ok, reason = self._check(payload)
        if ok:
            return ValidationResult(valid=True, data=payload, errors=[])
        return ValidationResult(valid=False, data=payload, errors=[reason])


def validate_payload_against_schema(
    payload: Any,
    schema: dict[str, Any] | type[Any],
) -> ValidationResult:
    """Validate a payload dictionary or structure against a schema."""
    validator = SchemaValidator(schema)
    return validator.validate(payload)
