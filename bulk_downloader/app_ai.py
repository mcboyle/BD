"""ai API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/ai views moved onto a Flask Blueprint.
Endpoint labels gain a "ai." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

ai_bp = Blueprint("ai", __name__)


@ai_bp.route("/api/ai/status")
def api_ai_status():
    """Reachability + model-installed check. Used by the global config
    UI to show a live status indicator and by the teach panel's 🪄
    button to decide whether to enable itself."""
    from . import ai_boot_status, aiassist
    payload = dict(aiassist.ai_status())
    payload["boot_readiness"] = ai_boot_status.read_status()
    return jsonify(payload)
@ai_bp.route("/api/ai/health")
def api_ai_health():
    """Rolling call stats (counts + recent latencies)."""
    from . import aiassist
    return jsonify(aiassist.get_health())
@ai_bp.route("/api/ai/models", methods=["POST"])
def api_ai_models():
    """v3.62.3: list the models the selected provider currently
    exposes, so the AI-settings UI can offer a dropdown instead of a
    free-text model field. Body: {provider, endpoint, api_key?}.

    The api_key is optional — a blank value, or the masked sentinel
    the UI shows for an already-saved key, falls back to the stored
    key. Read-only: never writes config."""
    from . import aiassist
    body = request.json or {}
    api_key = str(body.get("api_key") or "").strip()
    if api_key == "<configured>":
        api_key = ""   # masked placeholder — use the saved key
    return jsonify(aiassist.list_available_models(
        provider=body.get("provider"),
        endpoint=body.get("endpoint"),
        api_key=api_key or None,
    ))
@ai_bp.route("/api/ai/suggest_selectors", methods=["POST"])
def api_ai_suggest_selectors():
    """Body: {dom_excerpt, screenshot_b64?, page_url?, context_hint?}.
    Returns {ok, suggestions, latency_ms, model}."""
    from . import aiassist
    body = request.json or {}
    return jsonify(aiassist.suggest_selectors(
        dom_excerpt=body.get("dom_excerpt", ""),
        screenshot_b64=body.get("screenshot_b64"),
        page_url=body.get("page_url", ""),
        context_hint=body.get("context_hint", ""),
    ))
@ai_bp.route("/api/ai/classify", methods=["POST"])
def api_ai_classify():
    """Body: {element_desc}. Returns {ok, role, confidence}."""
    from . import aiassist
    body = request.json or {}
    return jsonify(aiassist.classify_role(body.get("element_desc", "")))
@ai_bp.route("/api/ai/normalize_resolution", methods=["POST"])
def api_ai_normalize_resolution():
    """Body: {filename}. Returns the normalized resolution shape."""
    from . import aiassist
    body = request.json or {}
    return jsonify(aiassist.normalize_resolution(body.get("filename", "")))
@ai_bp.route("/api/ai/diff_repair", methods=["POST"])
def api_ai_diff_repair():
    """Phase 29: differential teach. Body: {broken_selectors,
    working_selectors, dom_excerpt, screenshot_b64?, page_url?}.
    Returns proposed replacements that the user then verifies in the
    teach panel before committing."""
    from . import aiassist
    body = request.json or {}
    return jsonify(aiassist.diff_repair(
        broken_selectors=body.get("broken_selectors") or [],
        working_selectors=body.get("working_selectors") or [],
        dom_excerpt=body.get("dom_excerpt", ""),
        screenshot_b64=body.get("screenshot_b64"),
        page_url=body.get("page_url", ""),
    ))
@ai_bp.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    """Phase 9.10: stateless "Ask the model" scratchpad. Body:
    {prompt, model?, system?, image_b64?}. Returns
    {ok, response, model, provider, latency_ms, image_included, error}.

    Thin wrapper over the shared LLM execution contract. Respects the AI master
    switch, fails open on provider errors (always HTTP 200), enforces prompt
    length / vision-model-for-image / timeout, and persists nothing."""
    from . import ai_chat
    body = request.json or {}
    return jsonify(ai_chat.chat(body))

def register_routes(app) -> int:
    app.register_blueprint(ai_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("ai."))

