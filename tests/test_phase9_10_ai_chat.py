"""Phase 9.10 -- AI chat backend (RED-first, mock-first).

Covers the handler contract: mocked success; AI disabled -> ok:false, provider NOT
called; provider .ok=false -> fail-open; empty/overlong prompt rejected; image with
text-only model rejected; timeout handled; no persistence.
"""

from bulk_downloader import ai_chat


class _Fake:
    def __init__(self, ok=True, text="hi there", error="", error_kind=""):
        self.ok = ok; self.text = text; self.error = error
        self.error_kind = error_kind; self.provider = "ollama"
        self.model = "bd-text-small"; self.latency_ms = 12


def _ok_call(sink):
    def _c(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
        sink.append(prompt)
        return _Fake()
    return _c


def _fail_call(kind):
    def _c(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
        return _Fake(ok=False, error="boom", error_kind=kind)
    return _c


def _cfg(**kw):
    base = {"enabled": True, "provider": "ollama", "endpoint": "http://127.0.0.1:11434",
            "model_text": "bd-text-small"}
    base.update(kw)
    return base


def test_mocked_success():
    sink = []
    out = ai_chat.chat({"prompt": "hello"}, config=_cfg(), _call=_ok_call(sink))
    assert out["ok"] is True
    assert out["response"] == "hi there"
    assert out["provider"] == "ollama"
    assert out["image_included"] is False
    assert len(sink) == 1


def test_ai_disabled_does_not_call_provider():
    sink = []
    out = ai_chat.chat({"prompt": "hello"}, config=_cfg(enabled=False), _call=_ok_call(sink))
    assert out["ok"] is False
    assert out["error"] == "AI disabled"
    assert len(sink) == 0


def test_provider_failure_fail_open():
    out = ai_chat.chat({"prompt": "hello"}, config=_cfg(), _call=_fail_call("server_error"))
    assert out["ok"] is False
    assert out["error"]                     # a non-empty error, but a normal dict (HTTP 200)


def test_empty_prompt_rejected():
    sink = []
    out = ai_chat.chat({"prompt": "   "}, config=_cfg(), _call=_ok_call(sink))
    assert out["ok"] is False and out["error"] == "empty prompt"
    assert len(sink) == 0


def test_overlong_prompt_rejected():
    sink = []
    out = ai_chat.chat({"prompt": "x" * (ai_chat.MAX_PROMPT_CHARS + 1)},
                       config=_cfg(), _call=_ok_call(sink))
    assert out["ok"] is False and "too long" in out["error"]
    assert len(sink) == 0


def test_image_with_text_only_model_rejected():
    sink = []
    out = ai_chat.chat({"prompt": "look", "image_b64": "iVBOR", "model": "qwen2.5:0.5b"},
                       config=_cfg(), _call=_ok_call(sink))
    assert out["ok"] is False
    assert "vision" in out["error"]
    assert len(sink) == 0


def test_image_with_vision_model_included():
    sink = []
    out = ai_chat.chat({"prompt": "look", "image_b64": "iVBOR", "model": "qwen2.5vl:7b"},
                       config=_cfg(), _call=_ok_call(sink))
    assert out["ok"] is True
    assert out["image_included"] is True
    assert len(sink) == 1


def test_timeout_handled():
    out = ai_chat.chat({"prompt": "hello"}, config=_cfg(), _call=_fail_call("timeout"))
    assert out["ok"] is False
    assert out["error"]                     # surfaced, not raised


def test_no_persistence_surface():
    # the handler must not expose any history/persistence API
    assert not hasattr(ai_chat, "history")
    assert not hasattr(ai_chat, "save")
    assert not hasattr(ai_chat, "store")


def test_route_wired_disabled_returns_200():
    # locks the /api/ai/chat wiring (route exists; respects the master switch)
    from bulk_downloader import app as appmod, aiassist
    saved = dict(aiassist._config)
    try:
        aiassist._config["enabled"] = False
        c = appmod.app.test_client()
        r = c.post("/api/ai/chat", json={"prompt": "hi"})
        assert r.status_code == 200
        b = r.get_json()
        assert b["ok"] is False and b["error"] == "AI disabled"
    finally:
        aiassist._config.clear()
        aiassist._config.update(saved)


def _provider_call(endpoint):
    # the real Ollama transport (urlopen + error_kind mapping), not a fake result
    from bulk_downloader.ai_provider import OllamaProvider
    p = OllamaProvider(endpoint=endpoint, model_text="bd-text-small")

    def _c(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
        return p.generate(prompt, max_tokens=max_tokens, temperature=temperature,
                          timeout=timeout)
    return _c


def test_cold_load_timeout_is_actionable():
    # F042: a model still loading accepts the connection but sends nothing until
    # the cap; the operator saw only the raw "TimeoutError: timed out".
    import socket
    srv = socket.socket()
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)  # kernel completes the handshake; nobody ever answers
        ep = "http://127.0.0.1:%d" % srv.getsockname()[1]
        out = ai_chat.chat({"prompt": "hello"}, config=_cfg(chat_timeout=0.5),
                           _call=_provider_call(ep))
    finally:
        srv.close()
    assert out["ok"] is False
    err = out["error"]
    assert "bd-text-small" in err and "within 0.5s" in err, err
    assert "still loading" in err and "send again" in err, err
    assert "TimeoutError" in err, err       # the raw cause stays visible


def test_unreachable_provider_not_called_loading():
    # negative control: a refused connection is "down", not "slow"
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    out = ai_chat.chat({"prompt": "hello"}, config=_cfg(chat_timeout=0.5),
                       _call=_provider_call("http://127.0.0.1:%d" % port))
    assert out["ok"] is False
    assert "network" in out["error"], out["error"]
    assert "still loading" not in out["error"], out["error"]
