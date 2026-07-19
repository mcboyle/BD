"""T37 (Tier 4) — D-56 token/size estimator.

Char-based heuristic per provider. Read-only. Not a real tokenizer.
"""
import json

from bulk_downloader import dev_suite as ds


# ── _estimate_tokens — the heuristic ────────────────────────────────

def test_estimate_tokens_empty_string():
    assert ds._estimate_tokens("", "ollama") == 0


def test_estimate_tokens_ollama_4chars_per_token():
    # 16 chars / 4 = 4 tokens (rounded up)
    assert ds._estimate_tokens("a" * 16, "ollama") == 4


def test_estimate_tokens_rounds_up():
    # 5 chars / 4 = 1.25 → 2 tokens (operators need conservative budget)
    assert ds._estimate_tokens("a" * 5, "ollama") == 2


def test_estimate_tokens_claude_denser():
    # claude divisor is 3.5 → 8 chars yields more tokens than ollama
    n_claude = ds._estimate_tokens("a" * 8, "claude")
    n_ollama = ds._estimate_tokens("a" * 8, "ollama")
    assert n_claude >= n_ollama


def test_estimate_tokens_unknown_provider_defaults():
    # Unknown provider falls through to default divisor (4)
    n_unknown = ds._estimate_tokens("a" * 8, "made-up-provider")
    n_ollama = ds._estimate_tokens("a" * 8, "ollama")
    assert n_unknown == n_ollama


# ── token_estimate — public API ─────────────────────────────────────

def test_token_estimate_basic(clean_workdir):
    r = ds.token_estimate(text="hello world", provider="ollama")
    assert r["ok"] is True
    assert r["tool"] == "token_estimate"
    assert r["char_count"] == 11
    assert r["byte_count"] == 11
    assert r["est_tokens"] > 0
    assert r["provider"] == "ollama"


def test_token_estimate_per_provider_table(clean_workdir):
    r = ds.token_estimate(text="hello world", provider="ollama")
    for p in ("ollama", "claude", "openai", "gemini"):
        assert p in r["per_provider"]
        assert r["per_provider"][p] > 0


def test_token_estimate_utf8_bytes(clean_workdir):
    # Non-ASCII characters take more bytes than chars
    r = ds.token_estimate(text="café résumé", provider="ollama")
    assert r["byte_count"] > r["char_count"]
    # char_count counts unicode code points
    assert r["char_count"] == 11


def test_token_estimate_missing_input(clean_workdir):
    r = ds.token_estimate()
    assert r["ok"] is False
    assert "required" in r["verdict"]


def test_token_estimate_template_id_not_found(clean_workdir):
    r = ds.token_estimate(template_id="nonexistent-template")
    assert r["ok"] is False
    assert "nonexistent-template" in r["verdict"]


def test_token_estimate_non_string_input(clean_workdir):
    r = ds.token_estimate(text=12345)
    assert r["ok"] is False


def test_token_estimate_verdict_mentions_provider(clean_workdir):
    r = ds.token_estimate(text="x" * 100, provider="claude")
    assert "claude" in r["verdict"]
    assert "tokens" in r["verdict"]


def test_token_estimate_long_prompt(clean_workdir):
    long_text = "lorem ipsum dolor sit amet " * 1000  # ~27k chars
    r = ds.token_estimate(text=long_text, provider="ollama")
    assert r["ok"] is True
    assert r["est_tokens"] > 5000
    # Conservative — rounds up
    assert r["est_tokens"] >= r["char_count"] // 4


def test_token_estimate_is_json_serializable(clean_workdir):
    r = ds.token_estimate(text="hello", provider="ollama")
    json.dumps(r)
