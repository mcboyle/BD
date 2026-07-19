"""Phase 9.7 -- local LLM eval harness.

A fixture-driven harness that exercises the shared contract for each LLM task
across categories (known-good, bad/edge, malformed, timeout/offline,
redaction-sensitive, schema-invalid, low-confidence). All model outputs are
MOCKED, so `run()` works with no live Ollama; an optional live smoke is left to
the operator.

Each fixture asserts the contract's safety properties, not model cleverness:
  * a failure with a fallback yields the deterministic fallback value (path
    unchanged);
  * malformed / schema-invalid output is rejected (never used);
  * redaction-sensitive input never reaches the model;
  * timeout / offline fall back cleanly;
  * no result ever sets affects_runtime.
"""

from typing import Any, Dict, List

from . import llm_schemas
from .llm_exec import LLMCallSpec, execute

_SECRET = "api_key=sk-EVALSECRET0123456789"


class _Fake:
    def __init__(self, ok=True, text="", error="", error_kind=""):
        self.ok = ok; self.text = text; self.error = error
        self.error_kind = error_kind; self.provider = "ollama"
        self.model = "bd-text-small"; self.latency_ms = 1


# behavior: a model output string, or the markers "TIMEOUT" / "OFFLINE".
FIXTURES: List[Dict[str, Any]] = [
    # ── failure_triage ───────────────────────────────────────────────────
    {"name": "failure_triage/known-good", "category": "known-good",
     "task_id": "failure_triage", "prompt_id": "failure_triage", "version": "1",
     "schema": llm_schemas.FAILURE_TRIAGE, "input": "403 Forbidden",
     "behavior": '{"category":"auth","retryable":false}',
     "expect_status": "success", "expect_via": "model"},
    {"name": "failure_triage/bad-enum", "category": "schema-invalid",
     "task_id": "failure_triage", "prompt_id": "failure_triage", "version": "1",
     "schema": llm_schemas.FAILURE_TRIAGE, "input": "weird failure",
     "behavior": '{"category":"banana"}', "fallback": {"category": "unknown"},
     "expect_status": "schema_failure", "expect_via": "fallback"},
    {"name": "failure_triage/malformed-prose", "category": "malformed",
     "task_id": "failure_triage", "prompt_id": "failure_triage", "version": "1",
     "schema": llm_schemas.FAILURE_TRIAGE, "input": "x",
     "behavior": "It's probably an auth error, hard to say.",
     "fallback": {"category": "unknown"},
     "expect_status": "invalid_json", "expect_via": "fallback"},
    {"name": "failure_triage/malformed-no-fallback", "category": "malformed",
     "task_id": "failure_triage", "prompt_id": "failure_triage", "version": "1",
     "schema": llm_schemas.FAILURE_TRIAGE, "input": "x",
     "behavior": "no json at all", "expect_status": "invalid_json",
     "expect_value_none": True},
    {"name": "failure_triage/timeout", "category": "timeout-offline",
     "task_id": "failure_triage", "prompt_id": "failure_triage", "version": "1",
     "schema": llm_schemas.FAILURE_TRIAGE, "input": "x", "behavior": "TIMEOUT",
     "fallback": {"category": "unknown"},
     "expect_status": "timeout", "expect_via": "fallback"},
    {"name": "failure_triage/offline", "category": "timeout-offline",
     "task_id": "failure_triage", "prompt_id": "failure_triage", "version": "1",
     "schema": llm_schemas.FAILURE_TRIAGE, "input": "x", "behavior": "OFFLINE",
     "fallback": {"category": "unknown"},
     "expect_status": "provider_unavailable", "expect_via": "fallback"},
    # ── redaction_secret_scan ────────────────────────────────────────────
    {"name": "redaction_secret_scan/secret-input", "category": "redaction-sensitive",
     "task_id": "redaction_secret_scan", "prompt_id": "redaction_secret_scan",
     "version": "1", "schema": llm_schemas.REDACTION_SECRET_SCAN,
     "input": f"please scan this: {_SECRET}", "behavior": '{"secrets_found":[],"severity":"none"}',
     "secret": _SECRET, "expect_status": "forbidden_input"},
    # ── template_review_summary ──────────────────────────────────────────
    {"name": "template_review_summary/known-good", "category": "known-good",
     "task_id": "template_review_summary", "prompt_id": "template_review_summary",
     "version": "1", "schema": llm_schemas.TEMPLATE_REVIEW_SUMMARY,
     "input": "a template", "behavior": '{"summary":"looks ok","risk":"low"}',
     "expect_status": "success", "expect_via": "model"},
    {"name": "template_review_summary/bad-risk-enum", "category": "schema-invalid",
     "task_id": "template_review_summary", "prompt_id": "template_review_summary",
     "version": "1", "schema": llm_schemas.TEMPLATE_REVIEW_SUMMARY,
     "input": "a template", "behavior": '{"summary":"x","risk":"catastrophic"}',
     "fallback": {"summary": "review manually", "risk": "high"},
     "expect_status": "schema_failure", "expect_via": "fallback"},
    # ── ui_screenshot_review (bad/edge: missing required) ────────────────
    {"name": "ui_screenshot_review/missing-required", "category": "bad-edge",
     "task_id": "ui_screenshot_review", "prompt_id": "ui_screenshot_review",
     "version": "1", "schema": llm_schemas.UI_SCREENSHOT_REVIEW,
     "input": "a screenshot description", "behavior": '{"severity":"low"}',
     "fallback": {"findings": []}, "expect_status": "schema_failure",
     "expect_via": "fallback"},
    # ── opv_evidence_summary (text summary; schema-less success) ─────────
    {"name": "opv_evidence_summary/known-good", "category": "known-good",
     "task_id": "opv_evidence_summary", "prompt_id": "opv_evidence_summary",
     "version": "1", "schema": None, "input": "evidence text",
     "behavior": "Summary: three evidence items provided.",
     "expect_status": "success", "expect_via": "model"},
    # ── filename_metadata_parse (schema-less success) ────────────────────
    {"name": "filename_metadata_parse/known-good", "category": "known-good",
     "task_id": "filename_metadata_parse", "prompt_id": "filename_metadata_parse",
     "version": "1", "schema": None, "input": "Show.S01E02.1080p.mkv",
     "behavior": '{"title":"Show","season":1,"episode":2,"resolution":"1080p"}',
     "expect_status": "success", "expect_via": "model"},
    # ── nl_saved_search (low-confidence but schema-valid) ────────────────
    {"name": "nl_saved_search/low-confidence", "category": "low-confidence",
     "task_id": "nl_saved_search", "prompt_id": "nl_saved_search", "version": "1",
     "schema": {"type": "object", "required": ["filter"],
                "properties": {"confidence": {"type": "number"}}},
     "input": "videos from last week tagged news",
     "behavior": '{"filter":{"tag":"news"},"confidence":0.2}',
     "expect_status": "success", "expect_via": "model", "expect_confidence": 0.2},
]


def categories() -> List[str]:
    return sorted({f["category"] for f in FIXTURES})


def _make_call(fx, sink):
    def _call(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
        sink.append(prompt)
        b = fx["behavior"]
        if b == "TIMEOUT":
            return _Fake(ok=False, error="t/o", error_kind="timeout")
        if b == "OFFLINE":
            return _Fake(ok=False, error="net", error_kind="network")
        return _Fake(ok=True, text=b)
    return _call


def _run_fixture(fx: Dict[str, Any]) -> Dict[str, Any]:
    sink: List[str] = []
    fb = fx.get("fallback")
    spec = LLMCallSpec(
        task_id=fx["task_id"], prompt_id=fx["prompt_id"], prompt_version=fx["version"],
        input=fx["input"], schema=fx.get("schema"),
        schema_version=fx.get("schema_version", ""),
        fallback=((lambda v=fb: v) if fb is not None else None),
    )
    res = execute(spec, _call=_make_call(fx, sink))
    model_called = len(sink) > 0

    ok = (res.status == fx["expect_status"])
    if fx.get("expect_via"):
        ok = ok and (res.via == fx["expect_via"])
    if fx.get("expect_value_none"):
        ok = ok and (res.value is None)
    if fx.get("expect_confidence") is not None:
        ok = ok and (res.confidence == fx["expect_confidence"])
    if fx["category"] == "redaction-sensitive":
        ok = ok and (model_called is False)
        sec = fx.get("secret")
        if sec:
            ok = ok and all(sec not in p for p in sink)
    if fb is not None and res.status != "success":
        ok = ok and (res.value == fb)            # deterministic fallback preserved
    ok = ok and (res.affects_runtime is False)   # never mutates runtime

    return {
        "name": fx["name"], "category": fx["category"], "task_id": fx["task_id"],
        "ok": bool(ok), "model_called": model_called, "status": res.status,
        "via": res.via,
        "detail": "" if ok else f"expected status={fx['expect_status']} "
                                 f"via={fx.get('expect_via')}, got status={res.status} via={res.via}",
    }


def run() -> Dict[str, Any]:
    """Run every fixture through the contract. Returns a summary + per-case rows."""
    cases = [_run_fixture(fx) for fx in FIXTURES]
    passed = sum(1 for c in cases if c["ok"])
    return {
        "total": len(cases),
        "passed": passed,
        "failed": len(cases) - passed,
        "categories": categories(),
        "cases": cases,
    }
