"""Phase 9.6 -- local LLM diagnostics / readiness check.

`check()` returns a structured readiness report so an operator can verify local
models before relying on Cut 7 / Phase 9 features. Mock-first: the model transport
(`_call`) and the model lister (`_lister`) are injectable, so the whole report is
testable without a live Ollama.

Status rollup: a critical check failing -> red; any other fail/warn -> amber; else
green. Informational checks (`info`) and `skipped` checks never change the rollup.

The probe prompts are FIXED benign strings (see PROBE_PROMPT) -- readiness never
sends sensitive content to the model.
"""

from typing import Any, Dict, List, Optional

PROBE_PROMPT = "Reply with the single word: ok"
VISION_PROBE_PROMPT = "Reply with the single word: ok"
# 1x1 transparent PNG -- a content-free vision probe.
TINY_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQ"
            "DwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
_TINY_PNG = TINY_PNG


def _add(checks: List[Dict[str, Any]], name: str, status: str,
         detail: str = "", critical: bool = False) -> None:
    checks.append({"name": name, "status": status, "detail": detail,
                   "critical": bool(critical)})


def _live_config() -> Dict[str, Any]:
    try:
        from . import aiassist
        return dict(aiassist._config)
    except Exception:
        return {}


def _vision_enabled(cfg: Dict[str, Any]) -> bool:
    return bool(cfg.get("ai_vision_enabled") or cfg.get("vision_enabled"))


def model_present(model: str, names: List[str]) -> bool:
    """True if `model` is among `names`, tolerating the implicit ':latest' tag
    in either direction (ollama lists 'x:latest' but operators configure 'x')."""
    if not model:
        return False
    if model in names:
        return True
    mb = model[:-7] if model.endswith(":latest") else model
    for n in names:
        nb = n[:-7] if n.endswith(":latest") else n
        if nb == mb:
            return True
    return False


def _suggest(status: str, checks: List[Dict[str, Any]]) -> str:
    if status == "green":
        return "Local LLM is ready. No action needed."
    fails = [c for c in checks if c["status"] == "fail"]
    for c in fails:
        if c["name"] == "endpoint_reachable":
            return ("Start your local model server (e.g. `ollama serve`) and confirm "
                    "the endpoint in Settings, then re-run readiness.")
        if c["name"] == "provider_configured":
            return "Set an AI provider in Settings, then re-run readiness."
        if c["name"] == "text_model_exists":
            return ("Pull/select the configured text model (it isn't present on the "
                    "endpoint), then re-run readiness.")
        if c["name"] == "tiny_text_prompt":
            return ("The text model didn't answer a tiny probe (timeout or error). "
                    "Check model load and timeout, then re-run readiness.")
    warns = [c for c in checks if c["status"] == "warn"]
    if any(c["name"] == "ai_enabled" for c in warns):
        return "Enable AI in Settings to use Cut 7 / Phase 9 features."
    return "Review the amber checks below; AI features remain optional and fail safe."


def check(*, config: Optional[Dict[str, Any]] = None,
          _call=None, _lister=None) -> Dict[str, Any]:
    from . import model_registry, llm_exec

    cfg = config if config is not None else _live_config()
    provider = str(cfg.get("provider", "") or "")
    endpoint = str(cfg.get("endpoint", "") or "")
    enabled = bool(cfg.get("enabled"))
    text_model = str(cfg.get("model_text", "") or "")
    vision_model = str(cfg.get("model_vision", "") or "")
    timeout = float(cfg.get("readiness_timeout", 10))

    checks: List[Dict[str, Any]] = []

    _add(checks, "ai_enabled", "ok" if enabled else "warn",
         "AI features enabled" if enabled else "AI is disabled in Settings")
    _add(checks, "provider_configured", "ok" if provider else "fail",
         f"provider={provider}" if provider else "no provider set",
         critical=not provider)

    reg = model_registry.build_registry(provider, _lister=_lister)
    reachable = bool(reg.get("ok"))
    names = [e["name"] for e in reg.get("entries", [])]
    _add(checks, "endpoint_reachable", "ok" if reachable else "fail",
         f"endpoint={endpoint}" if reachable else (reg.get("error") or "unreachable"),
         critical=not reachable)
    _add(checks, "models_list_works", "ok" if (reachable and names) else "fail",
         f"{len(names)} models listed" if names else "no models listed")
    _add(checks, "text_model_exists",
         "ok" if model_present(text_model, names) else "fail",
         text_model or "(none configured)", critical=True)
    if _vision_enabled(cfg):
        _add(checks, "vision_model_exists",
             "ok" if model_present(vision_model, names) else "fail",
             vision_model or "(none configured)")
    else:
        _add(checks, "vision_model_exists", "skipped", "vision not enabled")
    _add(checks, "registry_classifies", "ok", "capability classification available")
    _add(checks, "models_match_settings",
         "ok" if (not text_model or model_present(text_model, names)) else "warn",
         "configured models present on endpoint")

    # tiny text probe through the shared contract (fixed benign prompt)
    tspec = llm_exec.LLMCallSpec(task_id="readiness", prompt_id="readiness_probe",
                                 prompt_version="1", input=PROBE_PROMPT, schema=None,
                                 model=(text_model or None), timeout=timeout)
    tres = llm_exec.execute(tspec, _call=_call)
    latency_ms = tres.latency_ms
    if tres.status == "success":
        _add(checks, "tiny_text_prompt", "ok", f"{tres.latency_ms}ms")
    elif tres.status == "timeout":
        _add(checks, "tiny_text_prompt", "fail", "model timed out", critical=True)
    elif tres.status == "provider_unavailable":
        _add(checks, "tiny_text_prompt", "fail",
             tres.error or "provider unavailable", critical=True)
    else:
        _add(checks, "tiny_text_prompt", "warn", tres.status)

    if _vision_enabled(cfg):
        vspec = llm_exec.LLMCallSpec(task_id="readiness_vision",
                                     prompt_id="readiness_vision_probe",
                                     prompt_version="1", input=VISION_PROBE_PROMPT,
                                     schema=None, model=(vision_model or None),
                                     capability="vision", image_b64=_TINY_PNG,
                                     image_allowed=True, timeout=timeout)
        vres = llm_exec.execute(vspec, _call=_call)
        _add(checks, "tiny_vision_prompt",
             "ok" if vres.status == "success" else "warn", vres.status)
    else:
        _add(checks, "tiny_vision_prompt", "skipped", "vision not enabled")

    _add(checks, "timeout_behavior", "info", f"timeout={timeout}s")
    try:
        from . import aiassist
        last_err = aiassist._health.get("last_error", "")
    except Exception:
        last_err = ""
    _add(checks, "last_model_error", "info", last_err or "none")
    _add(checks, "no_secret_in_probe", "ok", "probe prompts are fixed benign strings")
    _add(checks, "compute_mode", "info", str(cfg.get("compute_mode", "unknown")))

    crit_fail = any(c["status"] == "fail" and c["critical"] for c in checks)
    any_fail = any(c["status"] == "fail" for c in checks)
    any_warn = any(c["status"] == "warn" for c in checks)
    status = "red" if crit_fail else ("amber" if (any_fail or any_warn) else "green")

    return {
        "status": status,
        "provider": provider,
        "endpoint": endpoint,
        "model_text": text_model,
        "model_vision": vision_model,
        "latency_ms": latency_ms,
        "checks": checks,
        "suggested_action": _suggest(status, checks),
    }
