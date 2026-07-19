"""Phase 9.6 -- local LLM readiness check (RED-first, mock-first).

A readiness report (green/amber/red) the operator runs before using Cut 7 / Phase 9
features. Provider offline -> amber/red, never a crash; missing models detected;
tiny text probe success path; vision probe skipped for text-only; timeout handled;
probe prompts carry no sensitive content.
"""

from bulk_downloader import llm_readiness
from bulk_downloader.llm_readiness import check


class _Fake:
    def __init__(self, ok=True, text="ok", error="", error_kind=""):
        self.ok = ok; self.text = text; self.error = error
        self.error_kind = error_kind; self.provider = "ollama"
        self.model = "bd-text-small"; self.latency_ms = 5


def _call_ok(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
    return _Fake(text="ok")


def _call_timeout(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
    return _Fake(ok=False, error="t/o", error_kind="timeout")


def _call_offline(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
    return _Fake(ok=False, error="net", error_kind="network")


def _lister(models):
    return lambda: {"ok": bool(models), "models": models, "provider": "ollama",
                    "error": "" if models else "unreachable"}


def _cfg(**kw):
    base = {"enabled": True, "provider": "ollama", "endpoint": "http://127.0.0.1:11434",
            "model_text": "bd-text-small", "model_vision": "", "ai_vision_enabled": False}
    base.update(kw)
    return base


def _named(rep, name):
    return [c for c in rep["checks"] if c["name"] == name]


def test_all_good_is_green():
    rep = check(config=_cfg(), _call=_call_ok, _lister=_lister(["bd-text-small"]))
    assert rep["status"] == "green"
    assert rep["provider"] == "ollama"
    assert _named(rep, "endpoint_reachable")[0]["status"] == "ok"


def test_provider_offline_amber_or_red_no_crash():
    rep = check(config=_cfg(), _call=_call_offline, _lister=_lister([]))
    assert rep["status"] in ("amber", "red")
    assert "suggested_action" in rep


def test_missing_text_model_detected():
    rep = check(config=_cfg(model_text="not-pulled"), _call=_call_ok,
                _lister=_lister(["bd-text-small"]))
    assert _named(rep, "text_model_exists")[0]["status"] == "fail"
    assert rep["status"] in ("amber", "red")


def test_tiny_text_success_path():
    rep = check(config=_cfg(), _call=_call_ok, _lister=_lister(["bd-text-small"]))
    assert _named(rep, "tiny_text_prompt")[0]["status"] == "ok"


def test_tiny_vision_skipped_for_text_only():
    rep = check(config=_cfg(ai_vision_enabled=False), _call=_call_ok,
                _lister=_lister(["bd-text-small"]))
    v = _named(rep, "tiny_vision_prompt")
    assert v and v[0]["status"] == "skipped"


def test_vision_enabled_missing_model_flagged():
    rep = check(config=_cfg(ai_vision_enabled=True, model_vision="moondream"),
                _call=_call_ok, _lister=_lister(["bd-text-small"]))
    assert _named(rep, "vision_model_exists")[0]["status"] in ("fail", "warn")


def test_timeout_path_no_crash():
    rep = check(config=_cfg(), _call=_call_timeout, _lister=_lister(["bd-text-small"]))
    assert rep["status"] in ("amber", "red")
    assert _named(rep, "tiny_text_prompt")[0]["status"] in ("fail", "warn")


def test_no_sensitive_prompt_content():
    seen = []

    def cap(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
        seen.append(prompt)
        return _Fake(text="ok")

    check(config=_cfg(), _call=cap, _lister=_lister(["bd-text-small"]))
    assert seen
    for p in seen:
        assert llm_readiness.PROBE_PROMPT in p
        assert "sk-" not in p and "password" not in p.lower()


def test_registry_classifies_check_present():
    rep = check(config=_cfg(), _call=_call_ok, _lister=_lister(["bd-text-small"]))
    assert _named(rep, "registry_classifies")


def test_disabled_ai_is_amber():
    rep = check(config=_cfg(enabled=False), _call=_call_ok, _lister=_lister(["bd-text-small"]))
    assert rep["status"] in ("amber", "red")
    assert _named(rep, "ai_enabled")[0]["status"] != "ok"


def test_model_present_tolerates_latest_tag():
    # operator configured 'bd-text-small'; ollama lists 'bd-text-small:latest'
    rep = check(config=_cfg(model_text="bd-text-small"), _call=_call_ok,
                _lister=_lister(["bd-text-small:latest", "qwen2.5:0.5b"]))
    assert _named(rep, "text_model_exists")[0]["status"] == "ok"
    assert rep["status"] == "green"
    assert llm_readiness.model_present("bd-text-small", ["bd-text-small:latest"]) is True
    assert llm_readiness.model_present("x:latest", ["x"]) is True
    assert llm_readiness.model_present("absent", ["bd-text-small:latest"]) is False
