"""Phase-0 finish (sub-item D): unknown-model fast-fail clarity. An unknown
Ollama model already fast-fails (Ollama returns 404 immediately -- no hang; the
cold-load hang was Cut 0.3, the blank-model hang was 594). But a 404 mapped to
the generic 'bad_request' kind; Ollama's 404 specifically means 'model not
pulled', so classify it as 'not_found' so the UI can say so distinctly."""
from bulk_downloader import ai_provider


def test_ollama_404_classified_not_found():
    p = ai_provider.OllamaProvider()
    assert p._classify_http_error(404) == "not_found"
    # other codes still behave as the base class defines
    assert p._classify_http_error(401) == "auth"
    assert p._classify_http_error(500) == "server_error"


def test_ollama_unknown_model_fast_fails_with_clear_kind():
    p = ai_provider.OllamaProvider(model_text="does-not-exist")
    p._http_post = lambda url, body, headers, timeout: (
        False, 404, {"error": "model 'does-not-exist' not found, try pulling it first"}, 5)
    r = p.generate("hi")
    assert r.ok is False, "unknown model must fail (not hang or succeed)"
    assert r.error_kind == "not_found", f"expected not_found, got {r.error_kind}"
    assert "not found" in (r.error or "").lower()
