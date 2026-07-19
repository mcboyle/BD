"""Phase 9.0 -- Shared LLM execution contract.

ONE execution path for every LLM-assisted feature. No slice may invent its own
prompt transport, timeout behaviour, output parser, cache key, schema validation,
retry policy, error semantics, image handling, or secret handling -- they all call
`execute()` with an `LLMCallSpec` and read back an `LLMResult`.

The contract is deliberately conservative:
  * raw transport is delegated to `aiassist._call_model` (provider-agnostic);
  * output is parsed/validated against an optional light schema;
  * sensitive ("secret-like") input is blocked before it ever reaches the model
    (default: secrets NOT allowed);
  * image input is only forwarded when explicitly allowed;
  * input length is capped;
  * a deterministic `fallback` runs whenever the model path fails or is offline;
  * the result is ALWAYS advisory -- it cannot affect runtime or bypass a
    review/approval gate. There is intentionally no `approve()`/`apply()` here.

Mock-first (Phase 9.7): `execute(spec, _call=...)` accepts an injected transport so
the whole contract is testable without a live model.
"""

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

_log = logging.getLogger("bulk_downloader.llm_exec")

# Closed status taxonomy. `via` (model|fallback|none) is orthogonal to status:
# status records the MODEL-path outcome; via records where the usable value came
# from. ok == "a usable value was produced" (model success OR fallback success).
STATUSES = frozenset({
    "success",              # model returned valid, schema-passing output
    "timeout",              # model timed out
    "invalid_json",         # schema expected but no JSON could be parsed
    "schema_failure",       # JSON parsed but failed the expected schema
    "provider_unavailable", # provider offline / network / server error / missing
    "forbidden_input",      # secret-like input, or image when not allowed -- BLOCKED
    "input_too_long",       # input exceeded the contract's max length -- BLOCKED
    "model_error",          # other model/transport error (auth, quota, filter, ...)
    "invalid_spec",         # contract violation (e.g. missing prompt id/version) -- BLOCKED
})

_RETRYABLE = {"timeout", "model_error"}


@dataclass
class LLMCallSpec:
    """The full contract for a single LLM call (9.0)."""
    task_id: str
    prompt_id: str
    prompt_version: str
    input: str
    input_source: str = "internal"
    provider: Optional[str] = None          # None -> resolve from aiassist config
    model: Optional[str] = None             # None -> resolve from aiassist config
    capability: str = "text"                # "text" | "vision"
    max_input_chars: int = 20000
    max_output_tokens: int = 512
    timeout: float = 30.0
    retries: int = 0
    schema: Optional[Dict[str, Any]] = None # None -> output is raw text
    schema_version: str = ""                # 9.2: bumps independently of prompt text
    fallback: Optional[Callable[[], Any]] = None
    image_b64: Optional[str] = None
    image_allowed: bool = False
    secret_input_allowed: bool = False      # default: secrets BLOCKED
    advisory: bool = True                   # output is advisory-only
    affects_runtime: bool = False           # output may NOT change runtime by default
    review_required: bool = True            # 9.2: result requires human review by default
    temperature: float = 0.1
    # 9.4 cache controls (opt-in; off keeps the 9.0 transport behaviour unchanged)
    use_cache: bool = False
    config_hash: str = ""
    preproc_version: str = ""


@dataclass
class LLMResult:
    """Structured outcome of `execute()`."""
    ok: bool
    status: str
    task_id: str
    prompt_id: str
    prompt_version: str
    provider: str
    model: str
    input_hash: str
    value: Any = None
    raw_text: str = ""
    error: str = ""
    latency_ms: int = 0
    via: str = "none"                       # "model" | "fallback" | "cache" | "none"
    advisory: bool = True
    affects_runtime: bool = False
    attempts: int = 0
    # 9.2 emit/audit metadata
    schema_version: str = ""
    output_hash: str = ""
    review_required: bool = True
    parse_status: str = "n/a"               # "ok" | "invalid" | "n/a"
    schema_status: str = "n/a"              # "ok" | "failed" | "n/a"
    fallback_status: str = "none"           # "none" | "used" | "failed"
    timestamp: float = 0.0
    confidence: Optional[float] = None
    cache_hit: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


# ── secret detection (conservative; advisory) ────────────────────────────
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|access[_-]?token|"
               r"auth[_-]?token|bearer|client[_-]?secret|private[_-]?key)\b\s*[:=]\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"@cred:"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def looks_like_secret(text: Optional[str]) -> bool:
    """True if `text` contains an obvious credential/secret. Conservative by
    design -- meant to block accidental secret leakage into a prompt, not to be a
    full DLP scanner. Callers that genuinely need to send secrets must set
    `secret_input_allowed=True` on the spec."""
    if not text:
        return False
    return any(p.search(text) for p in _SECRET_PATTERNS)


# ── light schema validation (stdlib-only; no jsonschema dependency) ───────
_PYTYPES = {
    "string": str, "number": (int, float), "integer": int,
    "boolean": bool, "array": list, "object": dict, "null": type(None),
}


def _check_type(value: Any, t: str):
    py = _PYTYPES.get(t)
    if py is None:
        return True, ""
    if t in ("integer", "number") and isinstance(value, bool):
        return False, f"expected {t}, got boolean"
    if isinstance(value, py):
        return True, ""
    return False, f"expected {t}"


def validate_schema(value: Any, schema: Optional[Dict[str, Any]]):
    """Validate `value` against a minimal schema dialect:
      {"type":"object","required":[...],"properties":{k:{"type":...}}}
      {"type":"array","items":<schema>}
      {"type":"string"|"number"|"integer"|"boolean"|"null"}
    Returns (ok: bool, reason: str)."""
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
                ok, why = validate_schema(el, items)
                if not ok:
                    return False, f"item {i}: {why}"
        return True, ""
    if t:
        return _check_type(value, t)
    return True, ""


# ── provider/model resolution + input hash ───────────────────────────────
def _resolve_provider_model(spec: LLMCallSpec):
    provider = spec.provider
    model = spec.model
    if provider and model:
        return provider, model
    try:
        from . import aiassist
        cfg = aiassist._config
    except Exception:
        cfg = {}
    if not provider:
        provider = cfg.get("provider", "ollama")
    if not model:
        model = (cfg.get("model_vision", "") if spec.capability == "vision"
                 else cfg.get("model_text", ""))
    return provider, model


def input_hash(spec: LLMCallSpec) -> str:
    """Stable identity for a call: provider | model | prompt_id | prompt_version |
    input. Used as the cache key (9.4) and audit key (9.5)."""
    provider, model = _resolve_provider_model(spec)
    h = hashlib.sha256()
    for part in (provider, model, spec.prompt_id, spec.prompt_version, spec.input or ""):
        h.update(str(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


# ── the one execution path ────────────────────────────────────────────────
def _default_call(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
    from . import aiassist
    return aiassist._call_model(prompt, image_b64=image_b64, max_tokens=max_tokens,
                                temperature=temperature, timeout=timeout)


def execute(spec: LLMCallSpec, *, _call: Optional[Callable[..., Any]] = None) -> LLMResult:
    """Run one LLM call under the shared contract. Always returns an LLMResult;
    never raises for normal failure modes (timeout, bad output, offline provider,
    forbidden input). `_call` is injectable for tests (mock-first)."""
    if _call is None:
        _call = _default_call

    provider, model = _resolve_provider_model(spec)
    ih = input_hash(spec)

    def _result(status, *, ok=False, value=None, raw_text="", error="",
                latency_ms=0, via="none", attempts=0):
        has_schema = spec.schema is not None
        if not has_schema:
            parse_status, schema_status = "n/a", "n/a"
        elif status == "invalid_json":
            parse_status, schema_status = "invalid", "n/a"
        elif status == "schema_failure":
            parse_status, schema_status = "ok", "failed"
        elif status == "success":
            parse_status, schema_status = "ok", "ok"
        else:
            parse_status, schema_status = "n/a", "n/a"
        fallback_status = ("used" if via == "fallback"
                           else ("failed" if "fallback error" in (error or "") else "none"))
        out_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest() if raw_text else ""
        conf = None
        if isinstance(value, dict) and "confidence" in value:
            try:
                conf = float(value["confidence"])
            except (TypeError, ValueError):
                conf = None
        res = LLMResult(
            ok=ok, status=status, task_id=spec.task_id, prompt_id=spec.prompt_id,
            prompt_version=spec.prompt_version, provider=provider, model=model,
            input_hash=ih, value=value, raw_text=raw_text, error=error,
            latency_ms=latency_ms, via=via, advisory=bool(spec.advisory),
            affects_runtime=bool(spec.affects_runtime), attempts=attempts,
            schema_version=spec.schema_version, output_hash=out_hash,
            review_required=bool(spec.review_required), parse_status=parse_status,
            schema_status=schema_status, fallback_status=fallback_status,
            timestamp=time.time(), confidence=conf, cache_hit=(via == "cache"),
        )
        try:
            from . import llm_audit
            llm_audit.record(result_metadata(res))
        except Exception:  # pragma: no cover - audit must never break a call
            pass
        return res

    def _with_fallback(status, error="", raw_text="", latency_ms=0, attempts=0):
        """A model-path failure: run the deterministic fallback if present."""
        if spec.fallback is not None:
            try:
                fv = spec.fallback()
                _record(spec.task_id, latency_ms, False)
                return _result(status, ok=True, value=fv, raw_text=raw_text,
                               error=error, latency_ms=latency_ms, via="fallback",
                               attempts=attempts)
            except Exception as e:  # pragma: no cover - defensive
                error = (error + f"; fallback error: {e}").strip("; ")
        _record(spec.task_id, latency_ms, False)
        return _result(status, ok=False, raw_text=raw_text, error=error,
                       latency_ms=latency_ms, via="none", attempts=attempts)

    # ── pre-flight guards: NEVER reach the model ──────────────────────────
    _ckey = None

    def _cache_store(value):
        if not spec.use_cache:
            return
        try:
            from . import llm_cache
            llm_cache.set(_ckey or llm_cache.cache_key(spec), value,
                          schema_version=spec.schema_version,
                          prompt_version=spec.prompt_version)
        except Exception:  # pragma: no cover - cache must never break a call
            pass

    if not (spec.prompt_id and spec.prompt_version):
        return _with_fallback("invalid_spec",
                              error="prompt_id and prompt_version are required")
    if spec.input is not None and len(spec.input) > spec.max_input_chars:
        return _with_fallback("input_too_long",
                              error=f"input {len(spec.input)} > max {spec.max_input_chars}")
    if spec.image_b64 and not spec.image_allowed:
        return _with_fallback("forbidden_input", error="image input not permitted")
    if not spec.secret_input_allowed and looks_like_secret(spec.input):
        return _with_fallback("forbidden_input", error="secret-like input blocked")

    # ── 9.4 cache read (opt-in): a hit skips the model but is re-validated ─
    if spec.use_cache:
        try:
            from . import llm_cache
            _ckey = llm_cache.cache_key(spec)
            _hit = llm_cache.get(_ckey)
        except Exception:  # pragma: no cover
            _hit = None
        if _hit is not None:
            cval = _hit.get("value")
            valid = True
            if spec.schema is not None:
                valid, _ = validate_schema(cval, spec.schema)
            if valid:
                # cache hit does NOT bypass review: review_required/advisory come
                # straight from the spec via _result.
                return _result("success", ok=True, value=cval, via="cache", attempts=0)
            # invalid cached value -> ignore it and fall through to the model

    # ── model path (with bounded retry on transient failures) ─────────────
    status = "model_error"
    error = ""
    raw_text = ""
    latency = 0
    attempts = 0
    for attempt in range(spec.retries + 1):
        attempts = attempt + 1
        try:
            cr = _call(spec.input,
                       image_b64=spec.image_b64 if spec.image_allowed else None,
                       max_tokens=spec.max_output_tokens,
                       temperature=spec.temperature,
                       timeout=spec.timeout)
        except Exception as e:
            status, error = "model_error", str(e)
            if attempt < spec.retries:
                continue
            break

        if cr is None or not hasattr(cr, "ok"):
            status = "provider_unavailable"
            error = getattr(cr, "error", "") if cr is not None else "no result"
            break

        latency = getattr(cr, "latency_ms", 0) or 0
        if cr.ok:
            raw_text = getattr(cr, "text", "") or ""
            status, error = "success", ""
            break

        ek = getattr(cr, "error_kind", "") or ""
        error = getattr(cr, "error", "") or ""
        if ek == "timeout":
            status = "timeout"
        elif ek in ("network", "server_error"):
            status = "provider_unavailable"
        else:
            status = "model_error"
        if status in _RETRYABLE and attempt < spec.retries:
            continue
        break

    if status != "success":
        return _with_fallback(status, error=error, raw_text=raw_text,
                              latency_ms=latency, attempts=attempts)

    # ── parse + validate the model output ─────────────────────────────────
    if spec.schema is None:
        _record(spec.task_id, latency, True)
        _cache_store(raw_text)
        return _result("success", ok=True, value=raw_text, raw_text=raw_text,
                       latency_ms=latency, via="model", attempts=attempts)

    from . import aiassist
    parsed = aiassist.extract_json(raw_text)
    if parsed is None:
        return _with_fallback("invalid_json", error="no parseable JSON in output",
                              raw_text=raw_text, latency_ms=latency, attempts=attempts)

    ok_schema, reason = validate_schema(parsed, spec.schema)
    if not ok_schema:
        return _with_fallback("schema_failure", error=reason, raw_text=raw_text,
                              latency_ms=latency, attempts=attempts)

    _record(spec.task_id, latency, True)
    _cache_store(parsed)
    return _result("success", ok=True, value=parsed, raw_text=raw_text,
                   latency_ms=latency, via="model", attempts=attempts)


def _record(task_id: str, latency_ms: int, ok: bool) -> None:
    """Best-effort funnel into the existing health metrics so every contract
    call is observable consistently (full audit is 9.5)."""
    try:
        from . import aiassist
        aiassist._record_call(task_id, int(latency_ms or 0), bool(ok))
    except Exception:  # pragma: no cover - metrics must never break a call
        pass


# ── 9.2 prompt-driven spec construction + result metadata ────────────────
def from_prompt(prompt_id, version=None, *, input=None, input_vars=None,
                schema=None, **overrides) -> LLMCallSpec:
    """Build an LLMCallSpec from a registered prompt (9.2). Renders the prompt
    template with `input_vars` (unless an explicit `input` is given), and attaches
    the registry's schema + schema_version + review-required. Raises ValueError for
    an unknown prompt/version -- a call MUST reference a known prompt."""
    from . import prompts
    rec = prompts.get(prompt_id, version)
    if rec is None:
        raise ValueError(f"unknown prompt {prompt_id!r} version {version!r}")
    ver = rec["version"]
    rendered = input if input is not None else prompts.render(prompt_id, ver,
                                                              **(input_vars or {}))
    sch = schema if schema is not None else prompts.schema_for(prompt_id, ver)
    fields = dict(
        task_id=overrides.pop("task_id", prompt_id),
        prompt_id=prompt_id,
        prompt_version=ver,
        input=rendered,
        schema=sch,
        schema_version=prompts.schema_version_for(prompt_id, ver),
        review_required=prompts.review_required_for(prompt_id, ver),
    )
    fields.update(overrides)
    return LLMCallSpec(**fields)


def result_metadata(result: LLMResult) -> Dict[str, Any]:
    """The per-call emit/audit record (9.2; aggregated by 9.5). Contains only
    hashes + status flags -- never raw prompt/input/secret values."""
    return {
        "task_id": result.task_id,
        "prompt_id": result.prompt_id,
        "prompt_version": result.prompt_version,
        "schema_version": result.schema_version,
        "provider": result.provider,
        "model": result.model,
        "input_hash": result.input_hash,
        "output_hash": result.output_hash,
        "timestamp": result.timestamp,
        "parse_status": result.parse_status,
        "schema_status": result.schema_status,
        "fallback_status": result.fallback_status,
        "review_required": result.review_required,
        "confidence": result.confidence,
        "status": result.status,
        "via": result.via,
        "latency_ms": result.latency_ms,
        "cache_hit": result.cache_hit,
        "error": (result.error or "")[:200],   # contract error only; never raw input/output
    }
