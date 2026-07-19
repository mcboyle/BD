"""Phase 9.3 -- structured output schemas (RED-first).

Machine-readable LLM output is schema-validated JSON; freeform prose is only ever
a human-facing summary field. Hard rule: invalid JSON / unknown enum / missing
required field / freeform-only / any schema failure == unusable -> fallback/review.
A rejected answer must not affect the deterministic output.
"""

from bulk_downloader import llm_schemas
from bulk_downloader.llm_schemas import FAILURE_TRIAGE, get_schema
from bulk_downloader.llm_exec import validate_schema, LLMCallSpec, execute, from_prompt


class _Fake:
    def __init__(self, ok=True, text="ok", error="", error_kind=""):
        self.ok = ok; self.text = text; self.error = error
        self.error_kind = error_kind; self.provider = "ollama"
        self.model = "bd-text-small"; self.latency_ms = 2


def _c(text):
    def _call(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
        return _Fake(text=text)
    return _call


def _spec(**kw):
    base = dict(task_id="t", prompt_id="failure_triage", prompt_version="1",
                input="x", schema=FAILURE_TRIAGE)
    base.update(kw)
    return LLMCallSpec(**base)


# ── reference schema catalogue ───────────────────────────────────────────
def test_reference_schemas_present():
    for n in ("failure_triage.v1", "redaction_secret_scan.v1",
              "template_review_summary.v1", "ui_screenshot_review.v1"):
        assert get_schema(n) is not None, n


# ── validator unit behaviour ─────────────────────────────────────────────
def test_enum_valid_accepted():
    ok, _ = validate_schema({"category": "auth"}, FAILURE_TRIAGE)
    assert ok is True


def test_unknown_enum_rejected():
    ok, why = validate_schema({"category": "banana"}, FAILURE_TRIAGE)
    assert ok is False and why


def test_missing_required_rejected():
    ok, _ = validate_schema({"reason_code": "x"}, FAILURE_TRIAGE)
    assert ok is False


def test_type_mismatch_rejected():
    ok, _ = validate_schema({"category": "auth", "retryable": "yes"}, FAILURE_TRIAGE)
    assert ok is False        # retryable must be boolean, not a string


def test_array_items_validated():
    schema = {"type": "array", "items": {"type": "object", "required": ["k"]}}
    ok, _ = validate_schema([{"k": 1}], schema)
    assert ok is True
    ok, _ = validate_schema([{"x": 1}], schema)
    assert ok is False


def test_top_level_enum():
    ok, _ = validate_schema("high", {"enum": ["low", "high"]})
    assert ok is True
    ok, _ = validate_schema("nope", {"enum": ["low", "high"]})
    assert ok is False


# ── hard rule through execute() ──────────────────────────────────────────
def test_freeform_only_rejected_via_execute():
    res = execute(_spec(), _call=_c("It's probably an auth error."))
    assert res.status == "invalid_json"


def test_unknown_enum_via_execute_falls_back():
    res = execute(_spec(fallback=lambda: {"category": "unknown"}),
                  _call=_c('{"category":"banana"}'))
    assert res.status == "schema_failure"
    assert res.via == "fallback"
    assert res.value == {"category": "unknown"}   # rejected answer does NOT win


def test_missing_required_via_execute_falls_back():
    res = execute(_spec(fallback=lambda: {"category": "unknown"}),
                  _call=_c('{"reason_code":"x"}'))
    assert res.status == "schema_failure"
    assert res.value == {"category": "unknown"}


def test_schema_valid_via_execute_accepted():
    res = execute(_spec(), _call=_c('{"category":"auth","retryable":false}'))
    assert res.status == "success"
    assert res.value["category"] == "auth"


def test_rejected_answer_does_not_affect_deterministic_output():
    # No fallback -> a schema-invalid answer yields ok=False and value None;
    # the caller's deterministic path is untouched (execute never mutates anything).
    res = execute(_spec(), _call=_c('{"category":"banana"}'))
    assert res.ok is False
    assert res.value is None
    assert res.advisory is True
    assert res.affects_runtime is False


# ── 9.2<->9.3 wiring: from_prompt attaches the reference schema ──────────
def test_from_prompt_attaches_reference_schema():
    spec = from_prompt("failure_triage", "1",
                       input_vars={"message": "403", "status_code": 403})
    assert spec.schema == FAILURE_TRIAGE
