"""Phase 9.10 -- AI chat handler ("Ask the model" scratchpad backend).

A thin, stateless wrapper over the shared contract for a dev/operator scratchpad.
The Flask route `POST /api/ai/chat` is a one-liner over `chat()`.

Hard rules (mirrored from the spec):
  * respects the AI master switch (disabled -> {ok:false, error:"AI disabled"},
    provider NOT called);
  * provider failure -> {ok:false, error} (fail-open; the route returns HTTP 200);
  * NO persistence, NO chat history, NO secrets/file/shell/tool access, NO repo
    context injection;
  * image only for a vision-capable model (enforced via the 9.1 registry);
  * max prompt length + timeout enforced; output is plain text (the route returns
    it as a string; the FE must render text, never dangerouslySetInnerHTML).
"""

from typing import Any, Dict, Optional

from . import model_registry
from .llm_exec import LLMCallSpec, execute

MAX_PROMPT_CHARS = 8000


def _live_config() -> Dict[str, Any]:
    try:
        from . import aiassist
        return dict(aiassist._config)
    except Exception:
        return {}


def _resp(ok, response="", model="", provider="", latency_ms=0,
          image_included=False, error=""):
    return {"ok": bool(ok), "response": response, "model": model,
            "provider": provider, "latency_ms": latency_ms,
            "image_included": bool(image_included), "error": error}


def chat(payload: Dict[str, Any], *, config: Optional[Dict[str, Any]] = None,
         _call=None) -> Dict[str, Any]:
    """Run one scratchpad turn. Always returns a response dict (never raises);
    the route serializes it with HTTP 200."""
    cfg = config if config is not None else _live_config()
    provider = str(cfg.get("provider", "") or "")

    if not cfg.get("enabled"):
        return _resp(False, provider=provider, error="AI disabled")

    # CAP-1: warm the provider on first use (one-shot). ai_chat.chat routes
    # through llm_exec.execute, not aiassist._call_model, so warm here too.
    try:
        from . import aiassist as _ai
        _ai.warm_once()
    except Exception:
        pass

    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return _resp(False, provider=provider, error="empty prompt")
    if len(prompt) > MAX_PROMPT_CHARS:
        return _resp(False, provider=provider,
                     error=f"prompt too long (>{MAX_PROMPT_CHARS} chars)")

    model = str(payload.get("model") or cfg.get("model_text", "") or "")
    system = str(payload.get("system") or "")
    image_b64 = payload.get("image_b64") or ""

    if image_b64:
        ok, why = model_registry.can_use(model, "vision")
        if not ok:
            return _resp(False, model=model, provider=provider,
                         image_included=False,
                         error="image requires a vision-capable model")

    full_input = (system + "\n\n" + prompt) if system else prompt
    spec = LLMCallSpec(
        task_id="ai_chat", prompt_id="ai_chat", prompt_version="1",
        input=full_input, schema=None, model=model,
        capability=("vision" if image_b64 else "text"),
        image_b64=(image_b64 or None), image_allowed=bool(image_b64),
        max_input_chars=MAX_PROMPT_CHARS + 4000,
        max_output_tokens=int(cfg.get("chat_max_tokens", 512)),
        timeout=float(cfg.get("chat_timeout", 30)),
        review_required=False,
    )
    res = execute(spec, _call=_call)
    if res.status == "success":
        return _resp(True, response=res.value or "", model=res.model,
                     provider=res.provider, latency_ms=res.latency_ms,
                     image_included=bool(image_b64))
    return _resp(False, model=res.model, provider=res.provider,
                 latency_ms=res.latency_ms, image_included=bool(image_b64),
                 error=res.error or res.status)
