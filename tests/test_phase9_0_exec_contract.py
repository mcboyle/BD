"""Phase 9.0 -- shared LLM execution contract (RED-first, mock-first per 9.7).

All LLM-assisted features must use ONE shared execution path. This suite pins the
contract: structured success/failure, timeout, invalid-JSON, schema-failure,
provider-unavailable, forbidden secret-input, image-gating, input-length cap,
deterministic fallback when the model is offline, and the standing invariants
(output is advisory-only and can never bypass a review/approval gate).

Mock-first: a fake `_call` is injected so no live model is needed.
"""

import json

from bulk_downloader import llm_exec
from bulk_downloader.llm_exec import LLMCallSpec, execute, input_hash


# ── fake transport (mimics ai_provider.GenerationResult enough for execute) ──
class _Fake:
    def __init__(self, ok=True, text="", error="", error_kind="",
                 provider="ollama", model="bd-text-small", latency_ms=5):
        self.ok = ok
        self.text = text
        self.error = error
        self.error_kind = error_kind
        self.provider = provider
        self.model = model
        self.latency_ms = latency_ms


def _counting_call(result, sink):
    """Return a `_call` that records each invocation and returns `result`
    (or, if `result` is a list, the next item per call)."""
    def _call(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
        sink.append({"prompt": prompt, "image_b64": image_b64,
                     "max_tokens": max_tokens, "timeout": timeout})
        if isinstance(result, list):
            return result[min(len(sink) - 1, len(result) - 1)]
        return result
    return _call


def _spec(**kw):
    base = dict(task_id="classify", prompt_id="classify_role", prompt_version="1",
                input="role of <button>Download</button>",
                provider="ollama", model="bd-text-small")
    base.update(kw)
    return LLMCallSpec(**base)


_OBJ_SCHEMA = {"type": "object", "required": ["role"],
               "properties": {"role": {"type": "string"}}}


# ── 1. success path ──────────────────────────────────────────────────────
def test_success_json_schema():
    sink = []
    spec = _spec(schema=_OBJ_SCHEMA)
    res = execute(spec, _call=_counting_call(_Fake(text='{"role":"download"}'), sink))
    assert res.ok is True
    assert res.status == "success"
    assert res.via == "model"
    assert res.value == {"role": "download"}
    assert res.task_id == "classify"
    assert res.prompt_id == "classify_role"
    assert res.prompt_version == "1"
    assert res.input_hash
    assert res.advisory is True
    assert res.affects_runtime is False
    assert len(sink) == 1


def test_no_schema_returns_raw_text():
    sink = []
    spec = _spec(schema=None, task_id="summary")
    res = execute(spec, _call=_counting_call(_Fake(text="a plain summary"), sink))
    assert res.ok is True
    assert res.status == "success"
    assert res.value == "a plain summary"
    assert res.raw_text == "a plain summary"


# ── 2. input hash determinism ───────────────────────────────────────────
def test_input_hash_deterministic_and_sensitive():
    h1 = input_hash(_spec(input="alpha"))
    h2 = input_hash(_spec(input="alpha"))
    h3 = input_hash(_spec(input="beta"))
    assert h1 == h2
    assert h1 != h3
    # provider/model/prompt-version are part of the identity
    assert input_hash(_spec(input="alpha", prompt_version="2")) != h1
    assert input_hash(_spec(input="alpha", provider="claude")) != h1


# ── 3. invalid-JSON path ─────────────────────────────────────────────────
def test_invalid_json_without_fallback_fails():
    sink = []
    spec = _spec(schema=_OBJ_SCHEMA)
    res = execute(spec, _call=_counting_call(_Fake(text="I think it's a download button."), sink))
    assert res.ok is False
    assert res.status == "invalid_json"
    assert res.value is None


def test_invalid_json_with_fallback_recovers():
    sink = []
    spec = _spec(schema=_OBJ_SCHEMA, fallback=lambda: {"role": "unknown"})
    res = execute(spec, _call=_counting_call(_Fake(text="no json here"), sink))
    assert res.ok is True
    assert res.status == "invalid_json"
    assert res.via == "fallback"
    assert res.value == {"role": "unknown"}


# ── 4. schema-failure path ───────────────────────────────────────────────
def test_schema_failure_path():
    sink = []
    spec = _spec(schema=_OBJ_SCHEMA)
    res = execute(spec, _call=_counting_call(_Fake(text='{"label":"x"}'), sink))
    assert res.ok is False
    assert res.status == "schema_failure"


def test_schema_failure_with_fallback():
    spec = _spec(schema=_OBJ_SCHEMA, fallback=lambda: {"role": "unknown"})
    res = execute(spec, _call=_counting_call(_Fake(text='{"label":"x"}'), []))
    assert res.ok is True
    assert res.status == "schema_failure"
    assert res.via == "fallback"
    assert res.value == {"role": "unknown"}


# ── 5. timeout path + retry ──────────────────────────────────────────────
def test_timeout_retries_then_fails():
    sink = []
    spec = _spec(schema=_OBJ_SCHEMA, retries=2)
    res = execute(spec, _call=_counting_call(_Fake(ok=False, error_kind="timeout"), sink))
    assert res.status == "timeout"
    assert res.ok is False
    assert res.attempts == 3          # 1 + 2 retries
    assert len(sink) == 3


def test_timeout_with_fallback_recovers():
    spec = _spec(schema=_OBJ_SCHEMA, retries=1, fallback=lambda: {"role": "unknown"})
    res = execute(spec, _call=_counting_call(_Fake(ok=False, error_kind="timeout"), []))
    assert res.ok is True
    assert res.status == "timeout"
    assert res.via == "fallback"


# ── 6. provider-unavailable + deterministic fallback (model offline) ─────
def test_provider_unavailable_fallback_still_works():
    spec = _spec(schema=_OBJ_SCHEMA, fallback=lambda: {"role": "unknown"})
    res = execute(spec, _call=_counting_call(_Fake(ok=False, error_kind="network"), []))
    assert res.ok is True
    assert res.status == "provider_unavailable"
    assert res.via == "fallback"
    assert res.value == {"role": "unknown"}


def test_call_returning_none_is_provider_unavailable():
    sink = []
    spec = _spec(schema=_OBJ_SCHEMA)
    res = execute(spec, _call=_counting_call(None, sink))
    assert res.status == "provider_unavailable"
    assert res.ok is False


def test_server_error_maps_to_provider_unavailable():
    spec = _spec(schema=_OBJ_SCHEMA)
    res = execute(spec, _call=_counting_call(_Fake(ok=False, error_kind="server_error"), []))
    assert res.status == "provider_unavailable"


# ── 7. forbidden secret-input (never sent to the model) ──────────────────
def test_forbidden_secret_input_not_sent():
    sink = []
    spec = _spec(input="here is my api_key=sk-ABCDEF0123456789 and password: hunter2")
    res = execute(spec, _call=_counting_call(_Fake(text='{"role":"x"}'), sink))
    assert res.status == "forbidden_input"
    assert res.ok is False
    assert len(sink) == 0             # the model was NEVER called


def test_secret_input_allowed_override_sends():
    sink = []
    spec = _spec(input="api_key=sk-ABCDEF0123456789", schema=None,
                 secret_input_allowed=True)
    res = execute(spec, _call=_counting_call(_Fake(text="ok"), sink))
    assert len(sink) == 1             # explicit opt-in lets it through
    assert res.status == "success"


# ── 8. image gating ──────────────────────────────────────────────────────
def test_image_not_allowed_blocks():
    sink = []
    spec = _spec(image_b64="iVBORw0KGgo=", image_allowed=False)
    res = execute(spec, _call=_counting_call(_Fake(text='{"role":"x"}'), sink))
    assert res.status == "forbidden_input"
    assert len(sink) == 0


def test_image_allowed_forwards_image():
    sink = []
    spec = _spec(image_b64="iVBORw0KGgo=", image_allowed=True,
                 capability="vision", schema=None)
    res = execute(spec, _call=_counting_call(_Fake(text="ok"), sink))
    assert len(sink) == 1
    assert sink[0]["image_b64"] == "iVBORw0KGgo="


# ── 9. input-length cap ──────────────────────────────────────────────────
def test_input_too_long_blocks():
    sink = []
    spec = _spec(input="x" * 100, max_input_chars=10)
    res = execute(spec, _call=_counting_call(_Fake(text='{"role":"x"}'), sink))
    assert res.status == "input_too_long"
    assert len(sink) == 0


# ── 10. advisory invariants / no gate bypass ─────────────────────────────
def test_advisory_invariants():
    res = execute(_spec(schema=_OBJ_SCHEMA),
                  _call=_counting_call(_Fake(text='{"role":"x"}'), []))
    # output is advisory and cannot affect runtime by default
    assert res.advisory is True
    assert res.affects_runtime is False
    # the contract module exposes no "approve"/"apply" authority path
    assert not hasattr(llm_exec, "approve")
    assert not hasattr(llm_exec, "apply")
    # even an explicit affects_runtime spec stays advisory (bounded, never authority)
    res2 = execute(_spec(schema=_OBJ_SCHEMA, affects_runtime=True),
                   _call=_counting_call(_Fake(text='{"role":"x"}'), []))
    assert res2.advisory is True


# ── 11. status taxonomy is a stable, closed set ──────────────────────────
def test_status_taxonomy_constant():
    expected = {"success", "timeout", "invalid_json", "schema_failure",
                "provider_unavailable", "forbidden_input", "input_too_long",
                "model_error"}
    assert expected.issubset(set(llm_exec.STATUSES))


# ── 12. config resolution when provider/model unset ──────────────────────
def test_resolves_provider_model_from_config_when_unset():
    from bulk_downloader import aiassist
    saved = dict(aiassist._config)
    try:
        aiassist._config["provider"] = "claude"
        aiassist._config["model_text"] = "claude-test-model"
        spec = _spec(provider=None, model=None, schema=None)
        res = execute(spec, _call=_counting_call(_Fake(text="ok", provider="claude",
                                                       model="claude-test-model"), []))
        assert res.provider == "claude"
        assert res.model == "claude-test-model"
        # and that resolution feeds the hash identity
        assert input_hash(spec) == input_hash(_spec(provider="claude",
                                                     model="claude-test-model",
                                                     schema=None))
    finally:
        aiassist._config.clear()
        aiassist._config.update(saved)
