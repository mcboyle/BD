"""v3.43.43 regression tests — multi-provider AI.

Coverage:
  - Module surface: ai_provider exports, provider classes
  - Per-provider request shape (no network — assert what the
    request body WOULD be by intercepting _http_post)
  - Per-provider response parsing
  - error_kind classification across HTTP statuses
  - Factory (make_provider) with valid + invalid names
  - validate_endpoint provider-aware semantics
  - aiassist._call_model dispatches to the right provider
  - aiassist._resolve_api_key handles @cred and plaintext
  - app.py wiring: ai_provider/ai_api_key in _app_cfg, defaults,
    GET masks plaintext key, POST preserves on omit
  - UI: provider dropdown present + handler functions wired
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import json
from pathlib import Path
from unittest import mock


_REPO_ROOT = Path(__file__).resolve().parent.parent
_AI_PROV_PY = _REPO_ROOT / "bulk_downloader" / "ai_provider.py"
_AIASSIST_PY = _REPO_ROOT / "bulk_downloader" / "aiassist.py"
_AI_LOGIN_PY = _REPO_ROOT / "bulk_downloader" / "ai_login.py"
class _AppAggregateSrc:
    """app.py + every app_*.py blueprint module (PHASE 4 decomposition: route
    groups moved onto Flask blueprints); glob keeps source-coupled guards green
    across all current and future app.py route-group cuts."""
    def __init__(self, pkg_dir):
        self._paths = [pkg_dir / "app.py"] + sorted(pkg_dir.glob("app_*.py"))
    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self._paths)
_APP_PY = _AppAggregateSrc(_REPO_ROOT / "bulk_downloader")

def _APP_SRC():
    """app.py + extracted app_*.py modules (Phase 4 thin-core-shell; DECOMP-R2a kernels)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)
_APP_JS = _REPO_ROOT / "bulk_downloader" / "static" / "app.js"


# ── Module surface ────────────────────────────────────────────────────

def test_ai_provider_module_importable():
    import bulk_downloader.ai_provider  # noqa: F401


def test_required_exports():
    from bulk_downloader import ai_provider as ap
    for name in ("AIProvider", "OllamaProvider", "ClaudeProvider",
                  "OpenAIProvider", "GeminiProvider",
                  "GenerationResult", "HealthResult",
                  "make_provider", "list_provider_names",
                  "provider_info"):
        assert hasattr(ap, name), f"missing export: {name}"


def test_provider_names():
    from bulk_downloader.ai_provider import list_provider_names
    names = set(list_provider_names())
    assert names == {"ollama", "claude", "openai", "gemini"}


# ── make_provider factory ────────────────────────────────────────────

def test_make_provider_valid_names():
    from bulk_downloader.ai_provider import (
        make_provider, OllamaProvider, ClaudeProvider,
        OpenAIProvider, GeminiProvider)
    assert isinstance(make_provider("ollama"), OllamaProvider)
    assert isinstance(make_provider("claude"), ClaudeProvider)
    assert isinstance(make_provider("openai"), OpenAIProvider)
    assert isinstance(make_provider("gemini"), GeminiProvider)


def test_make_provider_unknown_returns_none():
    from bulk_downloader.ai_provider import make_provider
    assert make_provider("anthropic") is None  # close but wrong
    assert make_provider("") is None
    assert make_provider("bogus") is None


def test_make_provider_defensive_against_bad_input():
    """Non-string types must return None, not crash."""
    from bulk_downloader.ai_provider import make_provider
    for bad in (None, 12345, [], {}):
        assert make_provider(bad) is None


def test_make_provider_case_insensitive():
    from bulk_downloader.ai_provider import (
        make_provider, ClaudeProvider)
    assert isinstance(make_provider("CLAUDE"), ClaudeProvider)
    assert isinstance(make_provider(" claude "), ClaudeProvider)


# ── provider_info ────────────────────────────────────────────────────

def test_provider_info_shape():
    from bulk_downloader.ai_provider import provider_info
    info = provider_info("claude")
    assert info["name"] == "claude"
    assert info["needs_api_key"] is True
    assert info["default_endpoint"].startswith("https://")
    assert "default_models" in info
    assert info["is_local"] is False


def test_provider_info_ollama_is_local():
    from bulk_downloader.ai_provider import provider_info
    info = provider_info("ollama")
    assert info["is_local"] is True
    assert info["needs_api_key"] is False
    # Default points at localhost
    assert "localhost" in info["default_endpoint"] or \
           "127.0.0.1" in info["default_endpoint"]


def test_provider_info_unknown_returns_none():
    from bulk_downloader.ai_provider import provider_info
    assert provider_info("anthropic") is None
    assert provider_info(None) is None


# ── Per-provider request shapes (no network) ─────────────────────────


def _capture_http_call(provider, method_args, image_b64=None):
    """Run provider.generate but intercept _http_post so we never
    actually hit the network. Returns the captured (url, body,
    headers, timeout) tuple."""
    captured = {}

    def fake_http(url, body, headers, timeout):
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers
        captured["timeout"] = timeout
        # Return a successful-shaped response so the caller's parse
        # logic can complete — we don't care about the parsed value
        # for these tests, only the REQUEST shape.
        return True, 200, _fake_response_for(provider.name), 50

    with mock.patch.object(provider, "_http_post",
                              side_effect=fake_http):
        provider.generate(*method_args, image_b64=image_b64)
    return captured


def _fake_response_for(provider_name):
    """Build a minimal response that each provider can parse without
    crashing."""
    if provider_name == "ollama":
        return {"response": "ok"}
    if provider_name == "claude":
        return {"content": [{"type": "text", "text": "ok"}]}
    if provider_name == "openai":
        return {"choices": [{"message": {"content": "ok"}}]}
    if provider_name == "gemini":
        return {"candidates": [{
            "content": {"parts": [{"text": "ok"}]},
        }]}
    return {}


def test_ollama_request_shape():
    from bulk_downloader.ai_provider import OllamaProvider
    p = OllamaProvider(model_text="qwen2.5:7b")
    cap = _capture_http_call(p, ("hello world",))
    assert cap["url"].endswith("/api/generate")
    assert cap["body"]["model"] == "qwen2.5:7b"
    assert cap["body"]["prompt"] == "hello world"
    assert cap["body"]["stream"] is False
    # No images field when no screenshot
    assert "images" not in cap["body"]


def test_ollama_request_includes_image():
    from bulk_downloader.ai_provider import OllamaProvider
    p = OllamaProvider(model_vision="qwen2.5vl:7b")
    cap = _capture_http_call(p, ("hi",), image_b64="ZmFrZQ==")
    assert cap["body"]["model"] == "qwen2.5vl:7b"
    assert cap["body"]["images"] == ["ZmFrZQ=="]


def test_claude_request_shape():
    from bulk_downloader.ai_provider import ClaudeProvider
    p = ClaudeProvider(api_key="sk-test",
                         model_text="claude-haiku-4-5-20251001")
    cap = _capture_http_call(p, ("hello",))
    assert cap["url"].endswith("/v1/messages")
    assert cap["headers"]["x-api-key"] == "sk-test"
    assert cap["headers"]["anthropic-version"]
    assert cap["body"]["model"] == "claude-haiku-4-5-20251001"
    # Content for text-only is a list with one text block
    msgs = cap["body"]["messages"]
    assert msgs[0]["role"] == "user"
    content = msgs[0]["content"]
    assert any(c.get("type") == "text" for c in content)


def test_claude_request_with_image():
    from bulk_downloader.ai_provider import ClaudeProvider
    p = ClaudeProvider(api_key="sk-test",
                         model_vision="claude-sonnet-4-6")
    cap = _capture_http_call(p, ("describe",), image_b64="ZmFrZQ==")
    content = cap["body"]["messages"][0]["content"]
    img_blocks = [c for c in content if c.get("type") == "image"]
    assert len(img_blocks) == 1
    assert img_blocks[0]["source"]["type"] == "base64"
    assert img_blocks[0]["source"]["data"] == "ZmFrZQ=="


def test_claude_refuses_without_key():
    from bulk_downloader.ai_provider import ClaudeProvider
    p = ClaudeProvider(api_key="")
    result = p.generate("test")
    assert result.ok is False
    assert result.error_kind == "auth"


def test_openai_request_shape():
    from bulk_downloader.ai_provider import OpenAIProvider
    p = OpenAIProvider(api_key="sk-test", model_text="gpt-4o-mini")
    cap = _capture_http_call(p, ("hello",))
    assert cap["url"].endswith("/v1/chat/completions")
    assert cap["headers"]["Authorization"] == "Bearer sk-test"
    assert cap["body"]["model"] == "gpt-4o-mini"
    # Text-only: content is a plain string
    assert cap["body"]["messages"][0]["content"] == "hello"


def test_openai_request_with_image():
    from bulk_downloader.ai_provider import OpenAIProvider
    p = OpenAIProvider(api_key="sk-test", model_vision="gpt-4o")
    cap = _capture_http_call(p, ("describe",), image_b64="ZmFrZQ==")
    content = cap["body"]["messages"][0]["content"]
    # With image, content becomes a list of blocks
    assert isinstance(content, list)
    image_blocks = [c for c in content if c.get("type") == "image_url"]
    assert len(image_blocks) == 1
    assert "data:image/png;base64,ZmFrZQ==" in image_blocks[0]["image_url"]["url"]


def test_openai_refuses_without_key():
    from bulk_downloader.ai_provider import OpenAIProvider
    p = OpenAIProvider(api_key="")
    result = p.generate("test")
    assert result.ok is False
    assert result.error_kind == "auth"


def test_gemini_request_shape():
    from bulk_downloader.ai_provider import GeminiProvider
    p = GeminiProvider(api_key="AIzaTest", model_text="gemini-1.5-flash")
    cap = _capture_http_call(p, ("hello",))
    # Key goes in the query string for Gemini
    assert "key=AIzaTest" in cap["url"]
    assert "gemini-1.5-flash:generateContent" in cap["url"]
    parts = cap["body"]["contents"][0]["parts"]
    assert any(p_ and p_.get("text") for p_ in parts)


def test_gemini_request_with_image():
    from bulk_downloader.ai_provider import GeminiProvider
    p = GeminiProvider(api_key="AIzaTest",
                          model_vision="gemini-2.0-flash-exp")
    cap = _capture_http_call(p, ("describe",), image_b64="ZmFrZQ==")
    parts = cap["body"]["contents"][0]["parts"]
    inline = [p_ for p_ in parts if "inline_data" in p_]
    assert len(inline) == 1
    assert inline[0]["inline_data"]["data"] == "ZmFrZQ=="
    assert inline[0]["inline_data"]["mime_type"] == "image/png"


# ── Response parsing per provider ────────────────────────────────────


def test_ollama_extracts_response_text():
    from bulk_downloader.ai_provider import OllamaProvider
    p = OllamaProvider()
    with mock.patch.object(p, "_http_post",
        return_value=(True, 200, {"response": "hello world"}, 100)):
        r = p.generate("x")
    assert r.ok
    assert r.text == "hello world"


def test_claude_extracts_text_blocks():
    from bulk_downloader.ai_provider import ClaudeProvider
    p = ClaudeProvider(api_key="sk-test")
    fake = {"content": [
        {"type": "text", "text": "hello"},
        {"type": "text", "text": " world"},
    ]}
    with mock.patch.object(p, "_http_post",
        return_value=(True, 200, fake, 100)):
        r = p.generate("x")
    assert r.ok
    assert r.text == "hello world"


def test_openai_extracts_choices_message():
    from bulk_downloader.ai_provider import OpenAIProvider
    p = OpenAIProvider(api_key="sk-test")
    fake = {"choices": [{"message": {"content": "result here"}}]}
    with mock.patch.object(p, "_http_post",
        return_value=(True, 200, fake, 100)):
        r = p.generate("x")
    assert r.ok
    assert r.text == "result here"


def test_gemini_extracts_candidate_parts():
    from bulk_downloader.ai_provider import GeminiProvider
    p = GeminiProvider(api_key="AIzaTest")
    fake = {"candidates": [{
        "content": {"parts": [{"text": "answer"}]},
    }]}
    with mock.patch.object(p, "_http_post",
        return_value=(True, 200, fake, 100)):
        r = p.generate("x")
    assert r.ok
    assert r.text == "answer"


def test_gemini_detects_safety_block():
    """A SAFETY finishReason from Gemini surfaces as error_kind=filter."""
    from bulk_downloader.ai_provider import GeminiProvider
    p = GeminiProvider(api_key="AIzaTest")
    fake = {"candidates": [{
        "finishReason": "SAFETY",
        "content": {"parts": []},
    }]}
    with mock.patch.object(p, "_http_post",
        return_value=(True, 200, fake, 100)):
        r = p.generate("x")
    assert r.ok is False
    assert r.error_kind == "filter"


# ── error_kind classification ────────────────────────────────────────


def test_http_401_is_auth():
    from bulk_downloader.ai_provider import ClaudeProvider
    p = ClaudeProvider(api_key="sk-test")
    with mock.patch.object(p, "_http_post",
        return_value=(False, 401,
            {"error": {"type": "authentication_error",
                       "message": "bad key"}}, 100)):
        r = p.generate("x")
    assert r.ok is False
    assert r.error_kind == "auth"


def test_http_429_is_rate_limit():
    from bulk_downloader.ai_provider import OpenAIProvider
    p = OpenAIProvider(api_key="sk-test")
    with mock.patch.object(p, "_http_post",
        return_value=(False, 429, {"error": {"code": "rate_limited"}}, 100)):
        r = p.generate("x")
    assert r.error_kind == "rate_limit"


def test_http_5xx_is_server_error():
    from bulk_downloader.ai_provider import GeminiProvider
    p = GeminiProvider(api_key="AIzaTest")
    with mock.patch.object(p, "_http_post",
        return_value=(False, 503, "service down", 100)):
        r = p.generate("x")
    assert r.error_kind == "server_error"


def test_network_failure_kind():
    from bulk_downloader.ai_provider import OllamaProvider
    p = OllamaProvider()
    with mock.patch.object(p, "_http_post",
        return_value=(False, 0, "network: connection refused", 100)):
        r = p.generate("x")
    assert r.error_kind == "network"


def test_openai_quota_error_kind():
    from bulk_downloader.ai_provider import OpenAIProvider
    p = OpenAIProvider(api_key="sk-test")
    with mock.patch.object(p, "_http_post",
        return_value=(False, 429,
            {"error": {"code": "insufficient_quota",
                       "message": "out of credits"}}, 100)):
        r = p.generate("x")
    # Quota should classify as quota, not just rate_limit
    assert r.error_kind == "quota"


# ── aiassist._call_model dispatcher ──────────────────────────────────


def test_call_model_dispatches_to_configured_provider():
    """_call_model uses the provider name from _config and builds
    a provider instance through make_provider."""
    from bulk_downloader import aiassist
    saved = aiassist.get_config()
    try:
        aiassist.configure(provider="claude", api_key="sk-test",
                            enabled=True)
        # Intercept the provider's generate so we don't hit network
        from bulk_downloader import ai_provider
        with mock.patch.object(ai_provider.ClaudeProvider,
                                "generate",
                                return_value=ai_provider.GenerationResult(
                                    ok=True, text="hi",
                                    provider="claude",
                                    model="claude-haiku-4-5-20251001",
                                    latency_ms=50)):
            result = aiassist._call_model("prompt")
        assert result.ok
        assert result.provider == "claude"
    finally:
        aiassist.configure(provider=saved.get("provider", "ollama"),
                            api_key=saved.get("api_key", ""),
                            enabled=saved.get("enabled", False))


def test_call_model_unknown_provider_returns_failure():
    from bulk_downloader import aiassist
    saved = aiassist.get_config()
    try:
        # Directly stuff a bogus value (configure() rejects unknowns)
        aiassist._config["provider"] = "bogus_provider_name"
        result = aiassist._call_model("test")
        assert result.ok is False
        assert "not available" in result.error.lower()
    finally:
        aiassist._config["provider"] = saved.get("provider", "ollama")


# ── API key resolution ──────────────────────────────────────────────


def test_resolve_api_key_plaintext():
    from bulk_downloader import aiassist
    saved = aiassist.get_config()
    try:
        aiassist.configure(api_key="sk-plain-test")
        assert aiassist._resolve_api_key() == "sk-plain-test"
    finally:
        aiassist.configure(api_key=saved.get("api_key", ""))


def test_resolve_api_key_empty():
    from bulk_downloader import aiassist
    saved = aiassist.get_config()
    try:
        aiassist.configure(api_key="")
        assert aiassist._resolve_api_key() == ""
    finally:
        aiassist.configure(api_key=saved.get("api_key", ""))


def test_resolve_api_key_cred_token():
    """@cred:label tokens go through secrets_store.resolve_password
    which returns the resolved value (or None)."""
    from bulk_downloader import aiassist
    saved = aiassist.get_config()
    try:
        aiassist.configure(api_key="@cred:openai_key")
        with mock.patch("bulk_downloader.secrets_store.resolve_password",
                          return_value="sk-resolved-from-vault"):
            assert aiassist._resolve_api_key() == "sk-resolved-from-vault"
    finally:
        aiassist.configure(api_key=saved.get("api_key", ""))


# ── validate_endpoint (provider-aware) ─────────────────────────────


def test_validate_endpoint_rejects_public_for_ollama():
    """Ollama keeps its LAN-only enforcement."""
    from bulk_downloader.aiassist import validate_endpoint
    ok, msg = validate_endpoint("https://api.openai.com",
                                  provider="ollama")
    assert ok is False
    assert "private" in msg.lower() or "public" in msg.lower()


def test_validate_endpoint_allows_public_for_cloud():
    """Cloud providers accept public https endpoints."""
    from bulk_downloader.aiassist import validate_endpoint
    for prov in ("claude", "openai", "gemini"):
        ok, msg = validate_endpoint("https://api.example.com",
                                      provider=prov)
        assert ok is True, f"{prov} rejected https: {msg}"


def test_validate_endpoint_rejects_http_for_cloud():
    """Cloud providers must use https — http would leak the key."""
    from bulk_downloader.aiassist import validate_endpoint
    ok, msg = validate_endpoint("http://api.openai.com",
                                  provider="openai")
    assert ok is False
    assert "https" in msg.lower()


def test_validate_endpoint_localhost_allowed_for_cloud_http():
    """An exception to the cloud-https rule: localhost is allowed
    (user can run a local proxy that re-encrypts upstream)."""
    from bulk_downloader.aiassist import validate_endpoint
    ok, _ = validate_endpoint("http://localhost:8080",
                                  provider="openai")
    assert ok is True


# ── App.py wiring ────────────────────────────────────────────────────


def test_app_cfg_has_ai_provider_default():
    src = _APP_SRC()
    assert '"ai_provider": "ollama"' in src
    assert '"ai_api_key"' in src


def test_global_config_endpoint_handles_ai_provider():
    src = _APP_SRC()
    # Both POST handlers (validation + persistence)
    assert '"ai_provider"' in src
    assert "validate_endpoint" in src
    # Must pass provider= kwarg to validator
    assert "provider=" in src


def test_global_config_masks_plaintext_key():
    """GET response must not return plaintext API key — it shows
    "<configured>" so the client can render a passive field."""
    src = _APP_PY.read_text(encoding="utf-8")
    assert "<configured>" in src
    # And the @cred prefix passes through unchanged (it's a
    # reference, not a secret)
    assert "@cred:" in src


def test_global_config_preserves_key_on_omit():
    """If the POST body doesn't include ai_api_key, the existing
    value is preserved (the field is wrapped in `if "ai_api_key" in
    data`)."""
    src = _APP_SRC()
    pos = src.find('if "ai_api_key" in data')
    assert pos > 0
    # And there's a "<configured>" sentinel check inside the handler
    body = src[pos:pos + 1000]
    assert "<configured>" in body


def test_aiassist_configure_accepts_new_kwargs():
    """The configure() function must accept provider= and api_key=."""
    from bulk_downloader import aiassist
    # Should not raise
    saved = aiassist.get_config()
    try:
        aiassist.configure(provider="claude", api_key="test")
        cfg = aiassist.get_config()
        assert cfg["provider"] == "claude"
        assert cfg["api_key"] == "test"
    finally:
        aiassist.configure(
            provider=saved.get("provider", "ollama"),
            api_key=saved.get("api_key", ""))


# ── UI wiring ────────────────────────────────────────────────────────


# ── Adversarial regression ──────────────────────────────────────────


def test_provider_generate_never_raises():
    """Every provider's generate() must convert downstream failures
    into a failed GenerationResult. The catch boundary is
    `_http_post` — it already returns `(False, 0, "...", ms)` on
    any URLError/HTTPError/generic exception, so generate() can
    rely on that contract.

    Here we exercise the FULL catch path: _http_post returns
    `(False, 0, "...", ms)` representing a network failure, and
    every provider must surface this as a clean GenerationResult."""
    from bulk_downloader.ai_provider import (
        OllamaProvider, ClaudeProvider, OpenAIProvider, GeminiProvider)
    providers = [
        OllamaProvider(),
        ClaudeProvider(api_key="sk-test"),
        OpenAIProvider(api_key="sk-test"),
        GeminiProvider(api_key="AIzaTest"),
    ]
    for p in providers:
        with mock.patch.object(p, "_http_post",
            return_value=(False, 0, "network: refused", 100)):
            r = p.generate("test")
        assert r.ok is False, f"{p.name} reported ok=True on network failure"
        assert r.error_kind in ("network", "unknown", "server_error"), (
            f"{p.name} returned unexpected error_kind: {r.error_kind}")


def test_provider_handles_non_dict_response():
    """If the upstream returns non-JSON (e.g. HTML 500 page), we
    must surface a clean error rather than crash on .get()."""
    from bulk_downloader.ai_provider import (
        OllamaProvider, ClaudeProvider, OpenAIProvider, GeminiProvider)
    for p in (OllamaProvider(),
                ClaudeProvider(api_key="sk-test"),
                OpenAIProvider(api_key="sk-test"),
                GeminiProvider(api_key="AIzaTest")):
        with mock.patch.object(p, "_http_post",
            return_value=(True, 200, "<html>error</html>", 100)):
            r = p.generate("test")
        assert r.ok is False
        assert "non-JSON" in r.error or r.error_kind == "server_error"


def test_call_model_with_corrupt_provider_value():
    """If somehow _config["provider"] is set to garbage (not a
    string), _call_model must return a failure, not crash."""
    from bulk_downloader import aiassist
    saved = aiassist.get_config()
    try:
        for bad in (12345, [], {}, None):
            aiassist._config["provider"] = bad
            result = aiassist._call_model("test")
            assert result.ok is False
    finally:
        aiassist._config["provider"] = saved.get("provider", "ollama")


def test_validate_endpoint_handles_non_string_provider():
    """Audit catch: validate_endpoint(provider=12345) crashed
    `.lower()` with AttributeError. Must fall back to 'ollama'."""
    from bulk_downloader.aiassist import validate_endpoint
    for bad in (12345, [], {}, object()):
        # Must not raise
        ok, msg = validate_endpoint("http://localhost:11434", provider=bad)
        # Falls back to ollama enforcement; localhost passes
        assert ok is True
