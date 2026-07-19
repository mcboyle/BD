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
