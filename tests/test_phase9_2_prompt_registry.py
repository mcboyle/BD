"""Phase 9.2 -- prompt registry and prompt versioning (RED-first).

Every LLM call carries a named prompt id + version; the registry is reviewable as
source (not scattered strings); each result emits a metadata record (prompt id,
version, schema version, provider, model, input/output hash, timestamp, parse /
schema / fallback status, review-required, confidence).
"""

from bulk_downloader import prompts
from bulk_downloader.prompts import (
    render, get, exists, list_prompts, schema_version_for,
)
from bulk_downloader.llm_exec import LLMCallSpec, execute, from_prompt, result_metadata


class _Fake:
    def __init__(self, ok=True, text="ok", error="", error_kind=""):
        self.ok = ok; self.text = text; self.error = error
        self.error_kind = error_kind; self.provider = "ollama"
        self.model = "bd-text-small"; self.latency_ms = 3


def _call_ok(text):
    def _c(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
        return _Fake(text=text)
    return _c


_NAMED = ("failure_triage", "template_review_summary", "redaction_secret_scan",
          "site_onboard_summary", "opv_evidence_summary", "ui_screenshot_review",
          "route_quality_review", "doc_drift_scan", "nl_saved_search",
          "filename_metadata_parse")


# ── registry catalogue ───────────────────────────────────────────────────
def test_named_prompts_registered():
    for pid in _NAMED:
        assert exists(pid), pid
        rec = get(pid)
        assert rec and rec.get("version") and rec.get("schema_version")


def test_get_unknown_returns_none():
    assert get("does_not_exist") is None
    assert exists("does_not_exist") is False


def test_render_fills_template():
    s = render("failure_triage", "1", message="403 Forbidden", status_code=403)
    assert "403" in s


def test_render_missing_prompt_raises():
    raised = False
    try:
        render("does_not_exist", "1")
    except Exception:
        raised = True
    assert raised


def test_schema_version_present():
    assert schema_version_for("failure_triage", "1")


def test_list_prompts_reviewable():
    items = list_prompts()
    ids = {i["id"] for i in items}
    assert "failure_triage" in ids
    for i in items:
        assert "id" in i and "version" in i and "schema_version" in i \
               and "review_required" in i


# ── execute requires prompt id + version (9.2 rule) ──────────────────────
def test_execute_requires_prompt_id():
    sink = []
    spec = LLMCallSpec(task_id="x", prompt_id="", prompt_version="1", input="hi", schema=None)
    res = execute(spec, _call=lambda *a, **k: (sink.append(1) or _Fake()))
    assert res.status == "invalid_spec"
    assert res.ok is False
    assert len(sink) == 0


def test_execute_requires_prompt_version():
    sink = []
    spec = LLMCallSpec(task_id="x", prompt_id="p", prompt_version="", input="hi", schema=None)
    res = execute(spec, _call=lambda *a, **k: (sink.append(1) or _Fake()))
    assert res.status == "invalid_spec"
    assert len(sink) == 0


# ── per-result metadata emission (9.2) ───────────────────────────────────
def test_result_emits_full_metadata():
    spec = LLMCallSpec(task_id="classify", prompt_id="classify_role", prompt_version="2",
                       input="x", schema={"type": "object", "required": ["role"]},
                       schema_version="3")
    res = execute(spec, _call=_call_ok('{"role":"download","confidence":0.7}'))
    md = result_metadata(res)
    for f in ("task_id", "prompt_id", "prompt_version", "schema_version", "provider",
              "model", "input_hash", "output_hash", "timestamp", "parse_status",
              "schema_status", "fallback_status", "review_required", "confidence",
              "status", "via"):
        assert f in md, f
    assert md["prompt_version"] == "2"
    assert md["schema_version"] == "3"
    assert md["parse_status"] == "ok"
    assert md["schema_status"] == "ok"
    assert md["fallback_status"] == "none"
    assert md["output_hash"]
    assert md["confidence"] == 0.7


def test_metadata_on_schema_failure_with_fallback():
    spec = LLMCallSpec(task_id="t", prompt_id="p", prompt_version="1", input="x",
                       schema={"type": "object", "required": ["role"]},
                       fallback=lambda: {"role": "unknown"})
    res = execute(spec, _call=_call_ok('{"label":"x"}'))
    md = result_metadata(res)
    assert md["schema_status"] == "failed"
    assert md["fallback_status"] == "used"
    assert res.value == {"role": "unknown"}


def test_output_hash_tracks_output():
    base = dict(task_id="t", prompt_id="p", prompt_version="1", input="x", schema=None)
    r1 = execute(LLMCallSpec(**base), _call=_call_ok("aaa"))
    r2 = execute(LLMCallSpec(**base), _call=_call_ok("bbb"))
    assert r1.output_hash and r2.output_hash
    assert r1.output_hash != r2.output_hash


# ── from_prompt builds a contract spec from the registry ─────────────────
def test_from_prompt_builds_spec():
    spec = from_prompt("failure_triage", "1",
                       input_vars={"message": "403 Forbidden", "status_code": 403})
    assert isinstance(spec, LLMCallSpec)
    assert spec.prompt_id == "failure_triage" and spec.prompt_version == "1"
    assert spec.schema_version
    assert "403" in spec.input


def test_from_prompt_unknown_raises():
    raised = False
    try:
        from_prompt("does_not_exist", "1")
    except Exception:
        raised = True
    assert raised


def test_from_prompt_latest_version_when_unset():
    spec = from_prompt("filename_metadata_parse", None,
                       input_vars={"filename": "Show.S01E02.1080p.mkv"})
    assert spec.prompt_version  # resolved to a concrete version
