"""v3.66.670 -- AI-4: provider cost/token accounting.

ai_provider already records latency_ms on every GenerationResult. AI-4's gap is
that it logs NO token counts and NO cost. This cut adds:
  * prompt_tokens / completion_tokens on GenerationResult, extracted from the raw
    provider response across all three response shapes:
      - Ollama:  prompt_eval_count / eval_count
      - Claude:  usage.input_tokens / usage.output_tokens
      - OpenAI:  usage.prompt_tokens / usage.completion_tokens
  * cost_usd, computed from an operator-set per-model rate table (unset -> None).

Pure-unit: exercises the new _extract_usage / _cost_for helpers + the dataclass
fields directly, so no HTTP mocking is needed. Zero-arg tests.
"""
from __future__ import annotations

import pytest  # noqa: F401

from bulk_downloader import ai_provider as ap


def test_generationresult_has_token_and_cost_fields():
    r = ap.GenerationResult(ok=True)
    assert r.prompt_tokens == 0
    assert r.completion_tokens == 0
    assert r.cost_usd is None


def test_extract_usage_ollama_shape():
    pt, ct = ap._extract_usage({"prompt_eval_count": 120, "eval_count": 340})
    assert (pt, ct) == (120, 340)


def test_extract_usage_claude_shape():
    pt, ct = ap._extract_usage({"usage": {"input_tokens": 50, "output_tokens": 200}})
    assert (pt, ct) == (50, 200)


def test_extract_usage_openai_shape():
    pt, ct = ap._extract_usage(
        {"usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33}})
    assert (pt, ct) == (11, 22)


def test_extract_usage_missing_is_zero():
    assert ap._extract_usage({}) == (0, 0)
    assert ap._extract_usage(None) == (0, 0)


def test_cost_for_uses_configured_rates():
    ap.set_model_rates({"m1": {"in_per_1k": 0.01, "out_per_1k": 0.03}})
    try:
        # 1000 in @ 0.01/1k + 1000 out @ 0.03/1k = 0.04
        c = ap._cost_for("m1", 1000, 1000)
        assert c is not None and abs(c - 0.04) < 1e-9, c
        # unknown model -> None (no guessing)
        assert ap._cost_for("unknown", 1000, 1000) is None
    finally:
        ap.set_model_rates({})


def test_cost_for_no_rates_is_none():
    ap.set_model_rates({})
    assert ap._cost_for("m1", 1000, 1000) is None


def test_result_autopopulates_from_raw_ollama_response():
    """A provider building GenerationResult(raw=<ollama resp>) auto-records tokens,
    and cost_usd once a rate is set for that model."""
    ap.set_model_rates({"llava": {"in_per_1k": 0.0, "out_per_1k": 0.0}})
    try:
        r = ap.GenerationResult(
            ok=True, model="llava", provider="ollama",
            raw={"prompt_eval_count": 100, "eval_count": 250})
        assert r.prompt_tokens == 100 and r.completion_tokens == 250
        assert r.cost_usd == 0.0   # rate present (zero) -> a number, not None
        # No rate for this model -> tokens still counted, cost stays None.
        ap.set_model_rates({})
        r2 = ap.GenerationResult(
            ok=True, model="llava", provider="ollama",
            raw={"prompt_eval_count": 5, "eval_count": 7})
        assert r2.prompt_tokens == 5 and r2.completion_tokens == 7
        assert r2.cost_usd is None
    finally:
        ap.set_model_rates({})
