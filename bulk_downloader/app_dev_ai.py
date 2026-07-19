"""app_dev.ai -- 7 @dev_bp route handlers, sub-sliced from app_dev.py (Tier M, pure motion).

Handlers attach to the SHARED dev_bp (imported from .app_dev); the routing surface
(rule, methods, bare-name) is byte-identical -- test_route_map_invariant diffs EMPTY.
"""
from __future__ import annotations
from flask import Blueprint, jsonify, request
from .app_dev import (
    _check_csrf,
    _dev_mode_guard,
    dev_bp,
)


@dev_bp.route("/api/dev/ollama")
def api_dev_ollama():
    """AI / Ollama model inventory — configured vs installed."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.ollama_inventory())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/ai_fallback")
def api_dev_ai_fallback():
    """T7/D-55 — evaluate the AI fallback chain against the current
    config + live provider status, reporting which stage a call would
    reach right now (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.ai_fallback_trace())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/ai_latency")
def api_dev_ai_latency():
    """T8/D-53 — analysed view of aiassist's recent AI-call latencies:
    per-kind p50/p95/max, ok/fail split, slow-call flag (read-only).
    Optional ?slow_ms=."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.ai_latency_log(
            slow_ms=request.args.get("slow_ms", 8000.0)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/ai_health_history")
def api_dev_ai_health_history():
    """T8/D-57 — AI health snapshot as a history view: call/fail
    counts, failure rate, recent ok/fail timeline (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.ai_health_history())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/vision_test", methods=["POST"])
def api_dev_vision_test():
    """T9/D-54 — exercise the vision pipeline end-to-end. ACTUALLY
    issues a real AI call against the configured vision model with a
    tiny synthetic PNG. POST + CSRF (state-changing). Body
    {prompt?, timeout?}."""
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    try:
        from . import dev_suite as _ds
        body = request.json or {}
        return jsonify(_ds.vision_test_harness(
            prompt=body.get("prompt"),
            timeout=body.get("timeout", 30.0)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/token_estimate", methods=["POST"])
def api_dev_token_estimate():
    """T37/D-56 — character-based token estimate for a prompt.
    Body: {text?: str, template_id?: str, provider?: str}.
    POST for body size, read-only by contract (no state changes)."""
    guard = _dev_mode_guard()
    if guard: return guard
    body = request.json or {}
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.token_estimate(
            text=body.get("text"),
            provider=body.get("provider", "ollama"),
            template_id=body.get("template_id")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/model_pull_check")
def api_dev_model_pull_check():
    """T39/D-58 — verify Ollama-installed models against the
    configured/required set; print pull commands for any missing.
    Read-only — never invokes ollama pull."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        # No URL params — the function derives requested models from
        # OllamaProvider.default_models + _app_cfg.ai_models.
        return jsonify(_ds.model_pull_check())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
