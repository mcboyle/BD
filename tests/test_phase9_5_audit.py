"""Phase 9.5 -- LLM observability and audit (RED-first).

Each call emits metadata; report() aggregates per task. Raw secrets are never
logged; observability works when the provider is offline.
"""

import json

from bulk_downloader import llm_audit, llm_cache
from bulk_downloader.llm_exec import LLMCallSpec, execute


class _Fake:
    def __init__(self, ok=True, text="ok", error="", error_kind=""):
        self.ok = ok; self.text = text; self.error = error
        self.error_kind = error_kind; self.provider = "ollama"
        self.model = "bd-text-small"; self.latency_ms = 7


def _c(text):
    def _call(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
        return _Fake(text=text)
    return _call


def _c_fail(kind):
    def _call(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
        return _Fake(ok=False, error="boom", error_kind=kind)
    return _call


def _spec(**kw):
    base = dict(task_id="lane", prompt_id="p", prompt_version="1", input="hello",
                schema=None, provider="ollama", model="bd-text-small")
    base.update(kw)
    return LLMCallSpec(**base)


_OBJ = {"type": "object", "required": ["role"]}


def test_success_emits_metadata():
    llm_audit.clear()
    execute(_spec(), _call=_c("ok"))
    evs = llm_audit.events()
    assert len(evs) == 1
    assert evs[0]["status"] == "success"
    assert "input_hash" in evs[0] and "output_hash" in evs[0]


def test_timeout_schema_cachehit_each_emit():
    llm_audit.clear()
    llm_cache.clear()
    execute(_spec(schema=_OBJ), _call=_c_fail("timeout"))
    execute(_spec(schema=_OBJ), _call=_c('{"x":1}'))           # schema_failure
    execute(_spec(use_cache=True, input="cacheme"), _call=_c("a"))
    execute(_spec(use_cache=True, input="cacheme"), _call=_c("b"))  # cache hit
    statuses = [e["status"] for e in llm_audit.events()]
    assert "timeout" in statuses
    assert "schema_failure" in statuses
    assert any(e.get("cache_hit") for e in llm_audit.events())


def test_report_aggregates_counts_and_latency():
    llm_audit.clear()
    for _ in range(3):
        execute(_spec(task_id="lane1"), _call=_c("ok"))
    execute(_spec(task_id="lane1", schema=_OBJ, fallback=lambda: {"role": "u"}),
            _call=_c("{}"))                                    # schema_failure + fallback
    rep = llm_audit.report("lane1")
    t = rep["tasks"]["lane1"]
    assert t["success"] == 3
    assert t["schema_failure"] >= 1
    assert t["fallback"] >= 1
    assert "avg_latency_ms" in t and "p95_latency_ms" in t
    assert "review_required" in t


def test_raw_secret_not_logged():
    llm_audit.clear()
    secret = "sk-SECRETLOGTEST0123456789"
    execute(_spec(input=f"please use {secret} now"), _call=_c("x"))
    dump = json.dumps(llm_audit.events())
    assert secret not in dump
    assert "SECRETLOGTEST" not in dump


def test_observability_works_when_provider_offline():
    llm_audit.clear()
    execute(_spec(), _call=_c_fail("network"))
    evs = llm_audit.events()
    assert len(evs) == 1
    assert evs[0]["status"] == "provider_unavailable"
    rep = llm_audit.report()
    assert rep["total_events"] == 1
    assert rep["tasks"]["lane"]["model_unavailable"] is True


def test_cache_hit_rate_in_report():
    llm_audit.clear()
    llm_cache.clear()
    execute(_spec(task_id="L", use_cache=True, input="x"), _call=_c("a"))
    execute(_spec(task_id="L", use_cache=True, input="x"), _call=_c("b"))
    t = llm_audit.report("L")["tasks"]["L"]
    assert t["cache_hit"] >= 1
    assert "cache_hit_rate" in t


def test_last_error_recorded():
    llm_audit.clear()
    execute(_spec(task_id="E"), _call=_c_fail("server_error"))
    t = llm_audit.report("E")["tasks"]["E"]
    assert t["last_error"]   # non-empty contract error
