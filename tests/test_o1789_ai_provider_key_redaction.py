"""dev-console-misc C5 -- the Gemini API key rides in the URL query
(?key=<quote(key)>). A non-HTTP failure (scheme-less endpoint ->
ValueError "unknown url type: '<url>'") was surfaced as
f"{type(e).__name__}: {e}", i.e. with the key, into HealthResult.error ->
aiassist.ai_status -> /api/dev/ollama + /api/dev/ai_fallback. generate()
raised the same ValueError outright (Request() was built outside the try in
_http_post), breaking the never-raises contract. Offline: fake key,
scheme-less endpoint, no network."""

BD_GATE_SCOPE = "module"  # subject: ai_provider key redaction only
from urllib.parse import quote

import pytest

from bulk_downloader import ai_provider as ap

KEY = "FAKE/KEY+1SENTINEL"          # '/' and '+' -> quoted form differs
QKEY = quote(KEY, safe="")


def _no_key(text):
    assert KEY not in text and QKEY not in text, f"C5 key leaked: {text!r}"


def test_gemini_health_check_error_redacts_key():
    h = ap.GeminiProvider(endpoint="no-scheme-host", api_key=KEY).health_check()
    assert not h.ok
    _no_key(h.error)
    assert h.error.startswith("ValueError: unknown url type"), h.error
    assert "key=[REDACTED]" in h.error, h.error


def test_gemini_generate_does_not_raise_or_leak_key():
    g = ap.GeminiProvider(endpoint="no-scheme-host", api_key=KEY).generate(
        "x", timeout=2)
    assert not g.ok
    _no_key(g.error)
    assert "key=[REDACTED]" in g.error, g.error


def test_ai_status_surface_redacts_key(monkeypatch):
    from bulk_downloader import aiassist
    monkeypatch.setitem(aiassist._config, "enabled", True)
    monkeypatch.setattr(aiassist, "_get_provider",
                        lambda: ap.GeminiProvider(endpoint="no-scheme-host",
                                                  api_key=KEY))
    st = aiassist.ai_status()
    assert st["ok"] is False
    _no_key(st["error"])


@pytest.mark.parametrize("cls", [ap.OpenAIProvider, ap.GeminiProvider])
def test_error_without_key_passes_unchanged(cls):
    # negative control: redaction only rewrites the key, nothing else
    p = cls(endpoint="no-scheme-host", api_key="")
    h = p.health_check()
    assert not h.ok
    assert "[REDACTED]" not in (h.error or ""), h.error
    e = ap.OpenAIProvider(endpoint="no-scheme-host", api_key=KEY).health_check().error
    assert e == "ValueError: unknown url type: 'no-scheme-host/v1/models'", e


# r2 (lens REFUTE F1/F2): a key with CR/LF reaches http.client, whose
# "Invalid header value b'...\n'" escapes the control chars, so neither the
# raw nor the quoted form matched; OpenAI health_check skipped redaction.
# Endpoint 127.0.0.1:1 -- header validation fails before any connect.
BAD_KEYS = ["sk-FAKESENTINEL123\n", "sk-FAKE\rSENTINEL123",
            # http.client encodes header values latin-1: b'...\\xe9\\r...'
            "sk-FAKE\xe9\rSENTINEL123"]
LOOP = "http://127.0.0.1:1"


def _no_bad_key(text):
    assert "SENTINEL123" not in text and "sk-FAKE" not in text, \
        f"C5r2 key leaked: {text!r}"


@pytest.mark.parametrize("key", BAD_KEYS, ids=["trailing-LF", "embedded-CR", "latin1-CR"])
@pytest.mark.parametrize("cls", [ap.OpenAIProvider, ap.ClaudeProvider,
                                 ap.GeminiProvider])
@pytest.mark.parametrize("call", ["health_check", "generate"])
def test_control_char_key_never_in_error(cls, key, call):
    p = cls(endpoint=LOOP, api_key=key)
    r = p.health_check() if call == "health_check" else p.generate("x", timeout=2)
    assert not r.ok
    _no_bad_key(r.error or "")


def test_redact_key_matches_escaped_forms():
    p = ap.OpenAIProvider(endpoint=LOOP, api_key="sk-FAKE\rSENTINEL123")
    for form in ("Invalid header value b'Bearer sk-FAKE\\rSENTINEL123'",
                 "'sk-FAKE\\rSENTINEL123'", "sk-FAKE%0DSENTINEL123"):
        out = p._redact_key(form)
        _no_bad_key(out)
        assert "[REDACTED]" in out, out
