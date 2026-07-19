"""dev_suite.integrations_diag -- AI/integration diagnostics

Split from the dev_suite.py monolith (v3.66.395, pure code motion; surface preserved
via dev_suite/__init__.py). See kb/decomp/dev_suite/.
"""


from __future__ import annotations
import os
import sys
import threading
from pathlib import Path
import re as _sec_re
import json as _cfg_json
import re as _cfg_re
import os as _dl_os
import re as _dl_re

from ._common import (
    _percentile)



# ── 29. Ollama / AI model inventory (D-51) ─────────────────────────

def ollama_inventory() -> dict:
    """AI model inventory — the configured vision/text models and,
    when the provider is reachable, the installed models, flagging any
    configured model that is not installed. A dev-surface reshape of
    aiassist.ai_status() (which issues the live provider call)."""
    try:
        from bulk_downloader import aiassist as _ai
    except Exception as e:
        return {"error": f"aiassist import failed: {e}"[:160]}
    try:
        st = _ai.ai_status()
    except Exception as e:
        return {"error": f"ai_status failed: {e}"[:160]}
    missing = st.get("missing_models") or []
    reachable = bool(st.get("ok"))
    enabled = bool(st.get("enabled"))
    if not enabled:
        verdict = "AI assist is disabled in config"
    elif not reachable:
        verdict = f"provider unreachable: {st.get('error', '?')}"[:160]
    elif missing:
        verdict = (f"{len(missing)} configured model(s) not "
                   f"installed: {missing}")
    else:
        verdict = "all configured models are installed"
    return {
        "provider": st.get("provider", "?"),
        "endpoint": st.get("endpoint", ""),
        "enabled": enabled,
        "reachable": reachable,
        "configured_models": st.get("configured_models") or [],
        "installed_models": st.get("installed_models") or [],
        "missing_models": missing,
        "latency_ms": st.get("latency_ms"),
        "verdict": verdict,
    }



# ── 57. prompt previewer + AI fallback-chain tracer (T7: D-52+D-55)─
#
# D-52 — prompt previewer. The AI prompts are named module-level
# string templates with {placeholder} fields (SELECTOR_PROMPT etc.
# in aiassist, LOGIN_DETECT_PROMPT in ai_login, REFINE_PROMPT in
# template_extractor). This tool catalogs each one — its placeholders,
# length — and renders a filled preview with sample values, so the
# operator sees exactly what would be sent to the model without
# triggering a real inference call. Read-only.
#
# D-55 — AI fallback-chain tracer. An AI call degrades through a
# fixed sequence of stages: AI disabled -> provider invalid ->
# provider unreachable -> vision model missing (text-only fallback)
# -> model installed and reachable. This tool reads the current
# config + a live ai_status() and reports WHICH STAGE the chain
# reaches right now — i.e. what an AI call would do given the present
# environment — without making one. It is a decision-tree evaluation,
# not a per-call runtime trace.

# Each entry: registry name -> (module, attribute, sample values for
# each placeholder). The sample values are short and obviously fake.
_PROMPT_REGISTRY = {
    "selector": (
        "bulk_downloader.aiassist", "SELECTOR_PROMPT",
        {"dom_excerpt": "<div class='dl'>Download 1080p</div>",
         "page_url": "https://example.test/video/1",
         "context_hint": "HINT: the download button is in the header"}),
    "classify": (
        "bulk_downloader.aiassist", "CLASSIFY_PROMPT",
        {"element_desc": "{'tag':'a','text':'Download','href':'/dl'}"}),
    "resolution": (
        "bulk_downloader.aiassist", "RESOLUTION_PROMPT",
        {"filename": "MyVideo.1080p.WEB-DL.mp4"}),
    "diff_repair": (
        "bulk_downloader.aiassist", "DIFF_REPAIR_PROMPT",
        {"broken_selectors": "['.old-row', '.old-trigger']",
         "working_selectors": "['.still-good']",
         "dom_excerpt": "<ul class='new-list'>...</ul>",
         "page_url": "https://example.test/list"}),
    "login_detect": (
        "bulk_downloader.ai_login", "LOGIN_DETECT_PROMPT",
        {"dom_excerpt": "<form id='login'><input name='user'></form>",
         "page_url": "https://example.test/login",
         "context_hint": ""}),
    "template_refine": (
        "bulk_downloader.template_extractor", "REFINE_PROMPT",
        {"current_template": "{'row':'.item'}",
         "candidates_summary": "3 candidate rows found",
         "html_excerpt": "<div class='item'>...</div>"}),
}



def prompt_preview(name=None):
    """D-52 — catalog the AI prompt templates and render filled
    previews with sample values (read-only). With no `name`, returns
    every prompt's metadata + preview; with a `name`, just that one.
    """
    import importlib
    import re as _re

    def _render_one(key):
        module, attr, samples = _PROMPT_REGISTRY[key]
        info = {"name": key, "module": module, "attribute": attr}
        try:
            mod = importlib.import_module(module)
            template = getattr(mod, attr, None)
        except Exception as e:
            info["error"] = f"could not load: {str(e)[:140]}"
            return info
        if not isinstance(template, str):
            info["error"] = f"{attr} is not a string template"
            return info
        placeholders = sorted(set(_re.findall(r"\{(\w+)\}", template)))
        info["placeholders"] = placeholders
        info["template_length"] = len(template)
        # fill with the registry's sample values; any placeholder
        # missing a sample gets an obvious <name> marker
        fill = {p: samples.get(p, f"<{p}>") for p in placeholders}
        info["sample_values"] = {p: fill[p] for p in placeholders}
        try:
            info["filled_preview"] = template.format(**fill)
            info["preview_length"] = len(info["filled_preview"])
        except Exception as e:
            info["error"] = f"format failed: {str(e)[:140]}"
        return info

    if name:
        if name not in _PROMPT_REGISTRY:
            return {"tool": "prompt_preview", "ok": False,
                    "error": f"unknown prompt {name!r}; known: "
                             f"{sorted(_PROMPT_REGISTRY)}"}
        return {"tool": "prompt_preview", "ok": True,
                "prompt": _render_one(name)}

    prompts = [_render_one(k) for k in sorted(_PROMPT_REGISTRY)]
    errored = [p["name"] for p in prompts if "error" in p]
    return {
        "tool": "prompt_preview",
        "ok": True,
        "prompt_count": len(prompts),
        "known_prompts": sorted(_PROMPT_REGISTRY),
        "prompts": prompts,
        "errored": errored,
        "verdict": (f"{len(prompts)} prompt template(s) cataloged"
                    + (f"; {len(errored)} failed to render"
                       if errored else "")),
    }



# The fallback chain, in order. Each stage is the condition that
# stops the chain there; the first matching stage is where an AI
# call lands right now.
_AI_FALLBACK_STAGES = [
    "ai_disabled",          # _config['enabled'] is False
    "provider_invalid",     # provider name doesn't resolve
    "provider_unreachable", # endpoint not answering
    "model_missing",        # a configured model is not installed
    "text_only",            # vision model missing, text model ok
    "fully_operational",    # everything present and reachable
]



def ai_fallback_trace():
    """D-55 — evaluate the AI fallback chain against the current
    config + live provider status, and report which stage a call
    would reach right now (read-only — makes no inference call,
    though ai_status() does issue one lightweight provider probe).
    """
    out = {"tool": "ai_fallback_trace", "ok": True,
           "stages": list(_AI_FALLBACK_STAGES)}
    try:
        from bulk_downloader import aiassist as _ai
    except Exception as e:
        return {"tool": "ai_fallback_trace", "ok": False,
                "error": f"aiassist import failed: {str(e)[:140]}"}

    try:
        cfg = _ai.get_config() or {}
    except Exception:
        cfg = {}
    try:
        st = _ai.ai_status() or {}
    except Exception as e:
        st = {"error": str(e)[:140]}

    enabled = bool(st.get("enabled", cfg.get("enabled")))
    reachable = bool(st.get("ok"))
    provider = st.get("provider") or cfg.get("provider") or "?"
    missing = list(st.get("missing_models") or [])
    configured = list(st.get("configured_models") or [])
    model_vision = cfg.get("model_vision") or ""
    model_text = cfg.get("model_text") or ""

    # walk the chain — first stopping condition wins
    if not enabled:
        stage, reason = ("ai_disabled",
                         "AI assist is disabled in config — every "
                         "AI caller short-circuits to its non-AI path")
    elif provider in ("?", "", None):
        stage, reason = ("provider_invalid",
                         f"provider name {provider!r} does not resolve")
    elif not reachable:
        stage, reason = ("provider_unreachable",
                         f"provider {provider!r} not answering: "
                         f"{st.get('error', 'unknown')}"[:160])
    elif missing and model_vision in missing and model_text in missing:
        stage, reason = ("model_missing",
                         f"no configured model installed "
                         f"(missing: {missing})")
    elif model_vision and model_vision in missing:
        stage, reason = ("text_only",
                         f"vision model {model_vision!r} not "
                         "installed — vision calls fall back to the "
                         "text-only path")
    elif missing:
        stage, reason = ("model_missing",
                         f"a configured model is not installed "
                         f"(missing: {missing})")
    else:
        stage, reason = ("fully_operational",
                         "AI enabled, provider reachable, all "
                         "configured models installed")

    out["reached_stage"] = stage
    out["reason"] = reason
    out["enabled"] = enabled
    out["provider"] = provider
    out["reachable"] = reachable
    out["configured_models"] = configured
    out["missing_models"] = missing
    out["operational"] = stage == "fully_operational"
    out["verdict"] = f"AI fallback chain stops at '{stage}': {reason}"
    return out



# ── 58. AI-call latency log + AI health history (T8: D-53 + D-57) ──
#
# aiassist already maintains the raw data: aiassist._record_call
# updates _health on every model call, and get_health() exposes a
# 50-entry recent_latencies ring (each {ts, ms, kind, ok}) plus
# call/fail counts and last-check fields. Building a second collector
# would duplicate that and add module-level state — forbidden. So
# both T8 tools are read-only VIEWS over aiassist.get_health():
#
# D-53 — AI-call latency log. Turns the flat recent_latencies ring
# into an analysed latency log: per-kind breakdown (suggest /
# classify / diff_repair / res / status), p50/p95/max, ok-vs-fail
# split, and slow-call flagging.
#
# D-57 — AI health history. Frames the rolling health snapshot as a
# history view: total/fail counts, failure rate, the recent-call
# timeline with ok/fail markers, and time since the last call.

def ai_latency_log(slow_ms=8000.0):
    """D-53 — analysed view of aiassist's recent AI-call latencies
    (read-only). `slow_ms` is the threshold for the slow-call flag.
    """
    try:
        slow_ms = float(slow_ms)
    except Exception:
        slow_ms = 8000.0
    try:
        from bulk_downloader import aiassist as _ai
        health = _ai.get_health()
    except Exception as e:
        return {"tool": "ai_latency_log", "ok": False,
                "error": f"could not read AI health: {str(e)[:140]}"}

    calls = list(health.get("recent_latencies") or [])
    if not calls:
        return {"tool": "ai_latency_log", "ok": True,
                "sampled_calls": 0, "by_kind": [], "slow_calls": [],
                "verdict": "no AI calls recorded yet"}

    # group by kind
    kinds: dict = {}
    for c in calls:
        k = c.get("kind") or "?"
        kinds.setdefault(k, []).append(c)

    by_kind = []
    for k, group in sorted(kinds.items()):
        ms_vals = sorted(float(c.get("ms") or 0) for c in group)
        ok_n = sum(1 for c in group if c.get("ok"))
        by_kind.append({
            "kind": k,
            "count": len(group),
            "ok": ok_n,
            "failed": len(group) - ok_n,
            "p50_ms": round(_percentile(ms_vals, 50), 1),
            "p95_ms": round(_percentile(ms_vals, 95), 1),
            "max_ms": round(ms_vals[-1], 1) if ms_vals else 0.0,
        })

    slow = [{"ts": c.get("ts"), "kind": c.get("kind"),
             "ms": round(float(c.get("ms") or 0), 1),
             "ok": bool(c.get("ok"))}
            for c in calls
            if float(c.get("ms") or 0) >= slow_ms]
    all_ms = sorted(float(c.get("ms") or 0) for c in calls)
    return {
        "tool": "ai_latency_log",
        "ok": True,
        "sampled_calls": len(calls),
        "slow_threshold_ms": slow_ms,
        "overall_p50_ms": round(_percentile(all_ms, 50), 1),
        "overall_p95_ms": round(_percentile(all_ms, 95), 1),
        "overall_max_ms": round(all_ms[-1], 1) if all_ms else 0.0,
        "by_kind": by_kind,
        "slow_calls": slow,
        "verdict": (f"{len(calls)} recent AI call(s) across "
                    f"{len(by_kind)} kind(s)"
                    + (f"; {len(slow)} over {slow_ms:.0f}ms"
                       if slow else "; none slow")),
    }



def ai_health_history():
    """D-57 — the AI health snapshot framed as a history view
    (read-only): call/fail counts, failure rate, the recent-call
    ok/fail timeline, and time since the last call.
    """
    import time as _time
    try:
        from bulk_downloader import aiassist as _ai
        health = _ai.get_health()
    except Exception as e:
        return {"tool": "ai_health_history", "ok": False,
                "error": f"could not read AI health: {str(e)[:140]}"}

    call_count = int(health.get("call_count") or 0)
    fail_count = int(health.get("fail_count") or 0)
    last_check = float(health.get("last_check_at") or 0)
    recent = list(health.get("recent_latencies") or [])

    fail_rate = (round(100.0 * fail_count / call_count, 1)
                 if call_count else 0.0)
    age_s = (round(_time.time() - last_check, 1)
             if last_check else None)

    # compact ok/fail timeline of the recent ring, oldest first
    timeline = [{"ts": c.get("ts"), "kind": c.get("kind"),
                 "ok": bool(c.get("ok")),
                 "ms": round(float(c.get("ms") or 0), 1)}
                for c in recent]
    recent_fail_count = sum(1 for c in recent if not c.get("ok"))

    if call_count == 0:
        verdict = "no AI calls recorded — health history is empty"
    elif fail_count == 0:
        verdict = (f"{call_count} AI call(s), all succeeded; "
                   "last call OK")
    else:
        verdict = (f"{call_count} AI call(s), {fail_count} failed "
                   f"({fail_rate}% failure rate); last call "
                   + ("OK" if health.get("last_ok") else "FAILED"))

    return {
        "tool": "ai_health_history",
        "ok": True,
        "call_count": call_count,
        "fail_count": fail_count,
        "failure_rate_pct": fail_rate,
        "last_ok": bool(health.get("last_ok")),
        "last_error": health.get("last_error") or "",
        "last_check_at": last_check or None,
        "seconds_since_last_call": age_s,
        "last_latency_ms": health.get("latency_ms"),
        "recent_call_count": len(recent),
        "recent_fail_count": recent_fail_count,
        "recent_timeline": timeline,
        "verdict": verdict,
    }



# ── 59. vision-model test harness (T9: D-54) ───────────────────────
#
# Unlike the other AI dev tools, this one ACTUALLY ISSUES AN
# INFERENCE CALL — that's the point of a test harness. It exercises
# the vision pipeline end-to-end: generate a tiny synthetic PNG,
# send it through aiassist._call_model with image_b64=... to the
# configured vision model, capture latency, the raw response, and
# any error. Distinct from ai_status (text-only health probe) and
# ollama_inventory (just lists models) — this proves the vision path
# itself is wired up.
#
# Safe to call when AI is disabled / vision model missing / provider
# unreachable: every error is captured as a structured outcome,
# never raised into the response. The call IS recorded via aiassist's
# normal _record_call hook (kind="vision_test") so it shows up in
# D-53 and D-57.

# Minimal 1x1 transparent PNG (68 bytes, well-formed). Small enough
# that the network/encoding overhead doesn't dominate the timing,
# valid enough that any vision model will accept it.
_TEST_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNg"
    "AAAABQABDQotQAAAAABJRU5ErkJggg=="
)


_TEST_VISION_PROMPT = (
    "This is a test image. Reply with the single word 'OK' if you "
    "can see the image. No other output."
)



def vision_test_harness(prompt=None, timeout=30.0):
    """D-54 — exercise the vision pipeline end-to-end against a tiny
    synthetic image. ACTUALLY MAKES AN INFERENCE CALL (the only dev
    tool that does). Returns a structured outcome whether the call
    succeeds, fails, times out, or AI is disabled — never raises.
    """
    import time as _time
    try:
        timeout = max(1.0, min(float(timeout), 120.0))
    except Exception:
        timeout = 30.0
    out = {"tool": "vision_test_harness", "ok": True,
           "called": False, "test_image_bytes": 68}
    try:
        from bulk_downloader import aiassist as _ai
    except Exception as e:
        return {"tool": "vision_test_harness", "ok": False,
                "called": False,
                "error": f"aiassist import failed: {str(e)[:140]}"}

    cfg = _ai.get_config() if hasattr(_ai, "get_config") else {}
    out["enabled"] = bool(cfg.get("enabled"))
    out["configured_vision_model"] = cfg.get("model_vision") or ""
    out["provider"] = cfg.get("provider") or "?"

    if not out["enabled"]:
        out["outcome"] = "skipped_ai_disabled"
        out["verdict"] = ("AI assist is disabled — vision pipeline "
                          "not exercised")
        return out
    if not out["configured_vision_model"]:
        out["outcome"] = "skipped_no_vision_model"
        out["verdict"] = "no vision model configured"
        return out

    full_prompt = (str(prompt).strip() if prompt
                   else _TEST_VISION_PROMPT)[:500]
    started = _time.time()
    try:
        result = _ai._call_model(
            full_prompt, image_b64=_TEST_PNG_B64,
            max_tokens=32, temperature=0.0, timeout=timeout)
    except Exception as e:
        out["called"] = True
        out["outcome"] = "exception"
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        out["latency_ms"] = int((_time.time() - started) * 1000)
        out["verdict"] = (f"vision call raised "
                          f"{type(e).__name__} — pipeline broken")
        return out

    out["called"] = True
    out["latency_ms"] = getattr(result, "latency_ms", None)
    out["model_used"] = getattr(result, "model", "") or ""
    out["call_ok"] = bool(getattr(result, "ok", False))
    if not out["call_ok"]:
        out["outcome"] = "model_failed"
        out["error"] = (getattr(result, "error", "") or "")[:200]
        out["error_kind"] = getattr(result, "error_kind", "") or ""
        out["verdict"] = (f"vision call failed "
                          f"({out['error_kind'] or 'unknown'}): "
                          f"{out['error']}")
        return out

    text = (getattr(result, "text", "") or "").strip()
    out["outcome"] = "success"
    out["response_text"] = text[:300]
    out["response_length"] = len(text)
    out["verdict"] = (
        f"vision call succeeded in {out['latency_ms']}ms; "
        f"model returned {out['response_length']} char(s)")
    return out



# ── T37 / D-56 — token / size estimator ────────────────────────────

def _estimate_tokens(text: str, provider: str) -> int:
    """Char-based heuristic per provider's published rule. This is
    NOT a real tokenizer — it's the operator's rough budget tool.

    Rules (approximate, per official provider docs at time of writing):
      ollama (LLaMA-family BPE-ish): ~4 chars/token
      claude:                       ~3.5 chars/token
      openai (cl100k_base):         ~4 chars/token
      gemini:                       ~4 chars/token
    """
    if not text:
        return 0
    divisors = {
        "ollama": 4.0,
        "claude": 3.5,
        "openai": 4.0,
        "gemini": 4.0,
    }
    div = divisors.get(provider, 4.0)
    # Round UP — operators care about staying under a budget, not
    # being precise. 1 char input still costs 1 token.
    n = len(text)
    return int((n + div - 1) // div) if div > 0 else n



def token_estimate(text=None, provider="ollama",
                    template_id=None):
    """T37 / D-56 — estimate tokens + bytes for a prompt. Read-only.

    Either `text` (a raw prompt string) or `template_id` (a key into
    the configured prompt registry, if one exists) must be provided.

    Returns {tool, ok, provider, char_count, byte_count, est_tokens,
    per_provider{}, verdict}.
    """
    out = {
        "tool": "token_estimate",
        "ok": True,
        "provider": provider,
        "char_count": 0,
        "byte_count": 0,
        "est_tokens": 0,
        "per_provider": {},
        "verdict": "",
    }
    if template_id and not text:
        # Try to pull a template from the prompt registry, if one
        # exists. There isn't a stable one yet — fall through to error
        # if not findable.
        try:
            from bulk_downloader import app as _app
            prompts = _app._app_cfg.get("ai_prompts") or {}
            text = prompts.get(template_id)
        except Exception:
            text = None
        if not text:
            out["ok"] = False
            out["verdict"] = (
                f"template_id '{template_id}' not found in ai_prompts")
            return out
    if not isinstance(text, str):
        out["ok"] = False
        out["verdict"] = "text or template_id required"
        return out
    out["char_count"] = len(text)
    out["byte_count"] = len(text.encode("utf-8"))
    out["est_tokens"] = _estimate_tokens(text, provider)
    # Cross-provider table — handy when comparing budgets
    for p in ("ollama", "claude", "openai", "gemini"):
        out["per_provider"][p] = _estimate_tokens(text, p)
    out["verdict"] = (
        f"~{out['est_tokens']} tokens for {provider} "
        f"({out['char_count']} chars, {out['byte_count']} bytes). "
        f"Heuristic only — not a real tokenizer.")
    return out


# ── T39 / D-58 — Ollama model-pull helper ──────────────────────────

def model_pull_check(*, models=None, endpoint=None):
    """T39 / D-58 — verify whether the configured/requested Ollama
    models are installed locally. Read-only — never invokes
    `ollama pull` (that's an operator action on the host). Returns
    the exact pull commands for any missing models.

    DANGER constraint honoured: registry tags come from
    OllamaProvider.default_models or the operator's _app_cfg, NEVER
    hardcoded in this function.

    Args:
      models: optional list of explicit model tags to verify. If
              omitted, derive from OllamaProvider.default_models +
              any tags in _app_cfg.ai_models (best-effort).
      endpoint: optional base URL override for the Ollama API.

    Returns {tool, ok, endpoint, installed[], requested[],
    missing[], extra[], pull_commands[], verdict}.
    """
    out = {
        "tool": "model_pull_check",
        "ok": True,
        "endpoint": "",
        "installed": [],
        "requested": [],
        "missing": [],
        "extra": [],
        "pull_commands": [],
        "verdict": "",
    }
    # Derive requested set
    requested: list = []
    if isinstance(models, list):
        requested = [str(m) for m in models if isinstance(m, str) and m]
    if not requested:
        # Pull from OllamaProvider's defaults — NOT a hardcoded list
        try:
            from bulk_downloader import ai_provider as _ap
            defaults = _ap.OllamaProvider.default_models or {}
            for v in defaults.values():
                if isinstance(v, str) and v and v not in requested:
                    requested.append(v)
        except Exception:
            pass
        # Plus anything the operator configured
        try:
            from bulk_downloader import app as _app
            cfg_models = _app._app_cfg.get("ai_models") or {}
            if isinstance(cfg_models, dict):
                for v in cfg_models.values():
                    if isinstance(v, str) and v and v not in requested:
                        requested.append(v)
        except Exception:
            pass
    out["requested"] = sorted(set(requested))
    if not out["requested"]:
        out["ok"] = False
        out["verdict"] = ("no models requested and no defaults "
                          "available; pass models= explicitly")
        return out
    # Probe live Ollama. Endpoint override (positional or _app_cfg) wins
    # over OllamaProvider.default_endpoint.
    ep = endpoint
    if not ep:
        try:
            from bulk_downloader import app as _app
            ep = (_app._app_cfg.get("ai_endpoint")
                   or _app._app_cfg.get("ollama_endpoint"))
        except Exception:
            ep = None
    if not ep:
        try:
            from bulk_downloader import ai_provider as _ap
            ep = _ap.OllamaProvider.default_endpoint
        except Exception:
            ep = "http://localhost:11434"
    out["endpoint"] = ep
    # Best-effort: instantiate the provider and call list_models. If
    # the endpoint is down we just report missing for all requested.
    installed: list = []
    try:
        from bulk_downloader import ai_provider as _ap
        prov = _ap.OllamaProvider(endpoint=ep)
        result = prov.list_models()
        if isinstance(result, list):
            installed = [str(m) for m in result]
    except Exception:
        installed = []
    out["installed"] = sorted(set(installed))
    out["missing"] = sorted(set(out["requested"]) - set(installed))
    out["extra"] = sorted(set(installed) - set(out["requested"]))
    # Pull commands — quoted so colons + tags survive the shell.
    out["pull_commands"] = [
        f"ollama pull '{m}'" for m in out["missing"]
    ]
    if not installed:
        out["verdict"] = (
            f"could not reach Ollama at {ep}; "
            f"{len(out['requested'])} model(s) requested, "
            f"presence unknown")
    elif out["missing"]:
        out["verdict"] = (
            f"{len(out['missing'])}/{len(out['requested'])} "
            f"model(s) missing: {', '.join(out['missing'])}")
    else:
        out["verdict"] = (
            f"all {len(out['requested'])} requested model(s) "
            f"installed at {ep}")
    return out
