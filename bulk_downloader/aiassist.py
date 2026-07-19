"""Phase 27: AI-assist module — self-hosted vision LLM integration.

Talks to an Ollama-compatible HTTP API to provide suggestions that
the human verifies. Never auto-applies AI output — the workflow is
always: AI proposes → human reviews in the teach panel → human
commits. Errors never propagate out of this module; callers get
{"ok": False, "error": "..."} and decide what to show.

Public API:
  ai_status() -> dict  — connectivity + model availability check
  suggest_selectors(html_excerpt, screenshot_b64, page_url, context_hint)
                       -> dict {"ok", "suggestions": [...]}
  classify_role(element_desc) -> dict {"ok", "role", "confidence"}
  normalize_resolution(filename) -> dict {"ok", "resolution", "label", ...}

Privacy: all calls go to the locally-configured Ollama endpoint
(http://localhost:11434 by default). No request ever reaches a
public cloud endpoint; the config validator rejects URLs whose
host resolves to a non-RFC1918 / non-loopback address.

Hardware notes: tested with qwen2.5vl:7b on a single T4 (16GB).
Vision calls take 4-12s; non-vision calls 1-3s. We cap timeouts at
60s for vision, 30s for text-only.
"""

import hashlib
import ipaddress
import json
import re
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


# ── Configuration (set from app_config / global config UI) ────────────
_config = {
    # v3.43.43: provider selector. "ollama" preserves the
    # historic behavior (LAN-only self-hosted). New choices:
    # "claude", "openai", "gemini" route through cloud APIs.
    "provider": "ollama",
    "endpoint": "http://localhost:11434",
    "model_vision": "qwen2.5vl:7b",  # for screenshot-based prompts
    "model_text":   "qwen2.5:7b",     # for text-only classify / extract
    # v3.43.43: API key for cloud providers. Accepts plaintext OR
    # an @cred:<id> token resolved via secrets_store. Empty for
    # ollama (which is keyless).
    "api_key": "",
    "enabled": False,                   # off by default — opt-in
}

# ── Latency / health tracking ────────────────────────────────────────
_health = {
    "last_check_at": 0,
    "last_ok": False,
    "last_error": "",
    "latency_ms": 0,
    "call_count": 0,
    "fail_count": 0,
    "recent_latencies": [],  # rolling [(ts, ms, kind, ok)] — capped at 50
}
_health_lock = threading.Lock()


from typing import Any, Dict, List, Optional, Tuple

def configure(endpoint: Optional[str] = None,
              model_vision: Optional[str] = None,
              model_text: Optional[str] = None,
              enabled: Optional[bool] = None,
              provider: Optional[str] = None,
              api_key: Optional[str] = None) -> None:
    """Update runtime configuration. Called from app startup + when the
    user changes settings via the global config UI."""
    if endpoint is not None: _config["endpoint"] = endpoint.rstrip("/")
    if model_vision is not None: _config["model_vision"] = model_vision
    if model_text is not None: _config["model_text"] = model_text
    if enabled is not None: _config["enabled"] = bool(enabled)
    # v3.43.43: provider + api_key
    if provider is not None:
        p = str(provider).strip().lower()
        if p in ("ollama", "claude", "openai", "gemini"):
            _config["provider"] = p
    if api_key is not None:
        _config["api_key"] = str(api_key)


def get_config() -> Dict[str, Any]:
    """Return a copy of the current config (for the global config UI)."""
    return dict(_config)


def validate_endpoint(url: str, provider: Optional[str] = None) -> Tuple[bool, str]:
    """Phase 27 privacy: reject endpoints whose host resolves to a public
    IP. Allows loopback, RFC1918 (10/8, 172.16/12, 192.168/16),
    link-local, and unique-local IPv6. .local / .lan / .home suffixes
    pass too (typical LAN domains).

    v3.43.43: only enforced for `provider="ollama"`. Cloud providers
    (claude/openai/gemini) are public APIs by design — the validator
    just sanity-checks the URL shape.

    Returns (ok, message). On `ok=False`, the saver should refuse to
    accept the URL."""
    if not url: return False, "endpoint cannot be empty"
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"unparseable URL: {e}"
    if parsed.scheme not in ("http", "https"):
        return False, "scheme must be http or https"
    host = parsed.hostname or ""
    if not host: return False, "missing host"
    # v3.43.43: cloud providers — skip the private-network requirement.
    # The user opted into a public API by selecting a cloud provider;
    # refusing api.anthropic.com would be absurd.
    # Defensive: provider may be passed as non-string (corrupted
    # config, test typo). Fall back to "ollama" rather than crash
    # on .lower().
    effective_provider_raw = provider if provider is not None \
                                else _config.get("provider")
    if not isinstance(effective_provider_raw, str):
        effective_provider = "ollama"
    else:
        effective_provider = effective_provider_raw.lower()
    if effective_provider != "ollama":
        # Sanity: must be https for cloud (HTTP would leak the API key)
        if parsed.scheme != "https" and host not in ("localhost", "127.0.0.1"):
            return False, ("cloud provider endpoints must use https "
                            "(http would leak the API key over the wire)")
        return True, f"allowed ({effective_provider} cloud endpoint)"
    # Ollama: keep the existing LAN-only enforcement
    # Allowlist common LAN suffixes (avoid DNS lookups for these)
    lower = host.lower()
    for suffix in (".local", ".lan", ".home", ".internal", ".intranet"):
        if lower.endswith(suffix): return True, "allowed (LAN suffix)"
    # localhost / loopback names
    if lower in ("localhost", "ip6-localhost"): return True, "loopback"
    # Resolve the host and check every returned address
    try:
        addrs = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except Exception as e:
        # Can't resolve → refuse rather than accidentally allow public
        return False, f"can't resolve host: {e}"
    if not addrs:
        return False, "host has no addresses"
    for a in addrs:
        ip = a[4][0]
        try:
            ip_obj = ipaddress.ip_address(ip)
        except Exception: continue
        if not (ip_obj.is_private or ip_obj.is_loopback
                or ip_obj.is_link_local or ip_obj.is_reserved):
            return False, (f"refusing public IP {ip} — for ollama, only "
                           "private/loopback endpoints are allowed. To use "
                           "a public AI service, switch provider to "
                           "claude / openai / gemini.")
    return True, "allowed (private IP)"


def list_available_models(provider: Optional[str] = None,
                          endpoint: Optional[str] = None,
                          api_key: Optional[str] = None) -> Dict[str, Any]:
    """v3.62.3: list the models a provider currently exposes — Ollama's
    pulled models (live /api/tags), or a cloud provider's model list.
    Powers the AI-settings model dropdown so the operator picks from
    what the endpoint actually has instead of typing a tag blind.

    Any argument left None falls back to the saved config, so this also
    works for the already-configured provider. Fails open (INV-003):
    on any problem returns {ok: False, models: [], error: ...} and the
    UI falls back to its built-in suggestion list.

    Read-only — never writes config."""
    prov = provider if provider is not None else _config.get("provider")
    prov = str(prov or "ollama").strip().lower()
    ep = (endpoint if endpoint is not None
          else _config.get("endpoint") or "")
    ep = str(ep).strip()
    # Endpoint sanity first — provider-aware (LAN-only for ollama).
    # Probing an arbitrary typed-in URL would be a privacy hole.
    ok, msg = validate_endpoint(ep, prov)
    if not ok:
        return {"ok": False, "models": [], "provider": prov,
                "error": msg}
    key = api_key if api_key else _resolve_api_key()
    try:
        from . import ai_provider
    except Exception as e:
        return {"ok": False, "models": [], "provider": prov,
                "error": f"ai_provider unavailable: {e}"}
    p = ai_provider.make_provider(prov, endpoint=ep, api_key=key or "")
    if p is None:
        return {"ok": False, "models": [], "provider": prov,
                "error": f"unknown provider {prov!r}"}
    try:
        models = p.list_models()
    except Exception as e:
        # list_models() is contractually fail-soft, but guard anyway
        return {"ok": False, "models": [], "provider": prov,
                "error": f"{type(e).__name__}: {e}"}
    models = [m for m in (models or []) if m]
    return {"ok": bool(models), "models": models, "provider": prov,
            "error": "" if models
                     else "no models available — endpoint may be "
                          "unreachable, or has none installed"}


# ── HTTP plumbing ─────────────────────────────────────────────────────
def _record_call(kind, ms, ok, error=""):
    """Update the rolling health metrics."""
    with _health_lock:
        _health["call_count"] += 1
        if not ok: _health["fail_count"] += 1
        _health["last_check_at"] = time.time()
        _health["last_ok"] = ok
        _health["last_error"] = error[:200] if error else ""
        _health["latency_ms"] = ms
        _health["recent_latencies"].append(
            {"ts": time.time(), "ms": ms, "kind": kind, "ok": ok})
        if len(_health["recent_latencies"]) > 50:
            _health["recent_latencies"] = _health["recent_latencies"][-50:]


def _ip_is_lan(ip: str) -> bool:
    """True if ip is loopback/private/link-local/reserved -- the same classes
    validate_endpoint accepts for the ollama LAN-only gate."""
    try:
        o = ipaddress.ip_address(ip)
    except Exception:
        return False
    return bool(o.is_private or o.is_loopback or o.is_link_local or o.is_reserved)


def _pin_lan_endpoint(url: str):
    """F-CBD03-01: DNS-rebind defense for the ollama LAN-only path. Resolve the
    endpoint host NOW, refuse unless EVERY resolved address is LAN/loopback, and
    pin the connection to a validated IP (original Host header preserved). This
    closes the window between save-time validate_endpoint and the request, and
    the .local/.lan suffix trust (those are resolved + checked here too).
    Returns (connect_url, host_header_or_None). Raises RuntimeError on refusal."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port
    if not host:
        raise RuntimeError("ollama endpoint has no host")
    # IP literal -> validate directly, no DNS (nothing to rebind).
    try:
        ipaddress.ip_address(host)
        if not _ip_is_lan(host):
            raise RuntimeError(f"refusing non-LAN ollama IP {host}")
        return url, None
    except ValueError:
        pass
    # loopback names carry no rebind risk.
    if host.lower() in ("localhost", "ip6-localhost"):
        return url, None
    try:
        addrs = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception as e:
        raise RuntimeError(f"can't resolve ollama host {host}: {e}")
    ips = [a[4][0] for a in addrs if a and a[4]]
    if not ips:
        raise RuntimeError(f"ollama host {host} has no addresses")
    for ip in ips:
        if not _ip_is_lan(ip):
            raise RuntimeError(
                f"refusing ollama endpoint: {host} resolved to public IP {ip} "
                "(possible DNS rebind; ollama is LAN-only)")
    pin_ip = ips[0]
    netloc_ip = f"[{pin_ip}]" if ":" in pin_ip else pin_ip
    if port:
        netloc_ip = f"{netloc_ip}:{port}"
    pinned = parsed._replace(netloc=netloc_ip).geturl()
    return pinned, parsed.netloc


def _post_json(path, body, timeout=30):
    """LEGACY: Direct POST to the Ollama-style endpoint. Kept for
    backward compatibility with callers that explicitly want the
    Ollama wire format. New code should use `_call_model()` which
    is provider-aware.

    Returns parsed dict or raises with a friendly error."""
    url = _config["endpoint"].rstrip("/") + path
    data = json.dumps(body).encode("utf-8")
    # F-CBD03-01: pin the resolved LAN IP at request time (DNS-rebind defense).
    connect_url, host_hdr = _pin_lan_endpoint(url)
    headers = {"Content-Type": "application/json"}
    if host_hdr:
        headers["Host"] = host_hdr
    req = Request(connect_url, data=data, method="POST", headers=headers)
    started = time.time()
    try:
        resp = urlopen(req, timeout=timeout)
        raw = resp.read().decode("utf-8", "replace")
    except HTTPError as e:
        ms = int((time.time() - started) * 1000)
        body_err = ""
        try: body_err = e.read().decode("utf-8", "replace")[:300]
        except Exception: pass
        raise RuntimeError(f"HTTP {e.code} from {url}: {body_err or e.reason}") from None
    except URLError as e:
        ms = int((time.time() - started) * 1000)
        raise RuntimeError(f"can't reach {url}: {e.reason} "
                           "(is Ollama running on this endpoint?)") from None
    except Exception as e:
        raise RuntimeError(f"unexpected error talking to {url}: {e}") from None
    try:
        return json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"non-JSON response from {url}: {raw[:200]}") from None


# ── v3.43.43: Provider-aware inference dispatcher ────────────────────

def _resolve_api_key() -> str:
    """Resolve an @cred: token through secrets_store, or return
    plaintext as-is. Caller treats empty string as "not configured"."""
    raw = _config.get("api_key") or ""
    if not raw:
        return ""
    # NEW-5 (v3.66.43): a @cred: reference that fails to resolve must
    # NEVER be sent upstream as the literal "@cred:..." string — that
    # leaks the ref name to the AI provider's logs and gives the user an
    # opaque upstream auth error instead of "vault locked". Return "" so
    # the caller reports "AI not configured".
    if raw.startswith("@cred:"):
        try:
            from . import secrets_store
            resolved = secrets_store.resolve_password(raw)
        except Exception as e:
            import logging
            logging.getLogger("bulk_downloader.aiassist").warning(
                "AI API key is a @cred: ref but secrets_store raised: %s; "
                "treating as not configured", e)
            return ""
        if not resolved:
            import logging
            logging.getLogger("bulk_downloader.aiassist").warning(
                "AI API key @cred: ref resolves to empty (backend locked "
                "or key deleted); treating as not configured")
            return ""
        return resolved
    return raw  # plaintext is the legacy path


def _get_provider():
    """Build the provider instance from current config. Returns None
    when the configured provider name is invalid."""
    try:
        from . import ai_provider
    except Exception:
        return None
    return ai_provider.make_provider(
        _config.get("provider", "ollama"),
        endpoint=_config.get("endpoint", ""),
        api_key=_resolve_api_key(),
        model_vision=_config.get("model_vision", ""),
        model_text=_config.get("model_text", ""),
    )


def _call_model(prompt: str,
                  image_b64: Optional[str] = None,
                  max_tokens: int = 1024,
                  temperature: float = 0.1,
                  timeout: float = 60.0):
    """Provider-agnostic inference call. Returns the provider's
    GenerationResult object directly — callers check `.ok` and read
    `.text` / `.error` / `.error_kind`. Records latency on every
    call so the dashboard shows it consistently regardless of
    provider."""
    warm_once()  # CAP-1: pre-load the model on first inference use (one-shot)
    provider = _get_provider()
    if provider is None:
        # Build a synthetic failure result with same shape as a real one
        from . import ai_provider
        return ai_provider.GenerationResult(
            ok=False, provider=_config.get("provider", "?"),
            error="provider not available "
                    f"(name={_config.get('provider','?')!r})",
            error_kind="bad_request")
    result = provider.generate(prompt, image_b64=image_b64,
                                  temperature=temperature,
                                  max_tokens=max_tokens,
                                  timeout=timeout)
    return result


_warmed = False
_warm_lock = threading.Lock()


def warm_once() -> None:
    """CAP-1 (v3.66.656): warm the configured provider exactly ONCE per process,
    on first inference use. Idempotent, thread-safe, best-effort: a warm failure
    is swallowed and still counts as attempted (no retry storm hammering a down
    provider on every call) -- the inference that follows surfaces its own error.
    Wired into the two inference entry points (_call_model, ai_chat.chat); never
    called for a disabled provider."""
    global _warmed
    if _warmed:
        return
    with _warm_lock:
        if _warmed:
            return
        _warmed = True  # mark BEFORE calling: one attempt per process, win or lose
    try:
        warmup()
    except Exception:
        pass


def _reset_warm() -> None:
    """Test-only: clear the one-shot warm flag so a test can re-exercise it."""
    global _warmed
    with _warm_lock:
        _warmed = False


def warmup(timeout: float = 120.0) -> bool:
    """L19: pre-load the configured provider's model so the first inference call
    doesn't cold-load and time out. Best-effort; returns False if the provider
    is unavailable or has no warmup path (e.g. cloud providers stay warm)."""
    provider = _get_provider()
    if provider is None or not hasattr(provider, "warmup"):
        return False
    try:
        return bool(provider.warmup(timeout=timeout))
    except Exception:
        return False


# ── JSON extraction (robust against vision-model prose wrapping) ───────
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)
_BARE_JSON_RE   = re.compile(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}|\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\])", re.DOTALL)

def extract_json(text: Optional[str]) -> Optional[Any]:
    """Pull a JSON object/array out of an LLM response that may wrap it
    in markdown fences, prose, or both. Returns the parsed object or
    None if nothing parses."""
    if not text: return None
    text = text.strip()
    # Direct parse first
    try: return json.loads(text)
    except Exception: pass
    # Look for fenced JSON
    m = _FENCED_JSON_RE.search(text)
    if m:
        try: return json.loads(m.group(1))
        except Exception: pass
    # Last-resort: find any {...} that parses, scanning longest first
    candidates = _BARE_JSON_RE.findall(text)
    candidates.sort(key=len, reverse=True)
    for c in candidates:
        try: return json.loads(c)
        except Exception: continue
    return None


# ── Public API: ai_status ─────────────────────────────────────────────
def ai_status() -> Dict[str, Any]:
    """Quick health check. Hits /api/tags to list installed models +
    confirm the endpoint responds. Doesn't validate that our configured
    models are actually installed — that's a separate call. Returns:

    {ok, endpoint, enabled, configured_models, installed_models,
     latency_ms, error, provider, is_local}
    """
    if not _config["enabled"]:
        return {"ok": False, "enabled": False,
                "endpoint": _config["endpoint"],
                "provider": _config.get("provider", "ollama"),
                "error": "AI assist is disabled in global config"}
    # v3.43.43: dispatch the health check through the provider so
    # each one uses its native shape (Ollama /api/tags, Claude
    # tiny-ping, OpenAI /v1/models, Gemini /v1beta/models).
    provider = _get_provider()
    if provider is None:
        return {"ok": False, "enabled": True,
                "endpoint": _config["endpoint"],
                "provider": _config.get("provider", "?"),
                "error": f"unknown provider {_config.get('provider')!r}"}
    health = provider.health_check()
    _record_call("status", health.latency_ms, health.ok,
                   error=health.error)
    if not health.ok:
        return {"ok": False, "enabled": True,
                "endpoint": _config["endpoint"],
                "provider": provider.name,
                "latency_ms": health.latency_ms,
                "error": health.error[:300] if health.error else ""}
    installed = health.info.get("installed_models") or []
    configured = [_config["model_vision"], _config["model_text"]]
    # For cloud providers we don't always have a definitive
    # installed list — missing_models stays empty when we can't
    # verify.
    missing = []
    if installed:
        missing = [m for m in configured if m and m not in installed]
    return {
        "ok": True, "enabled": True,
        "endpoint": _config["endpoint"],
        "provider": provider.name,
        "is_local": provider.name == "ollama",
        "latency_ms": health.latency_ms,
        "installed_models": installed,
        "configured_models": configured,
        "missing_models": missing,
        "fixed_model_list": health.info.get("fixed_models")
                              or getattr(provider, "fixed_model_list", None),
    }


def get_health():
    """Return the rolling health snapshot for display in the UI."""
    with _health_lock:
        return {
            "call_count": _health["call_count"],
            "fail_count": _health["fail_count"],
            "last_ok": _health["last_ok"],
            "last_error": _health["last_error"],
            "last_check_at": _health["last_check_at"],
            "latency_ms": _health["latency_ms"],
            "recent_latencies": list(_health["recent_latencies"]),
        }


# ── Prompt templates ──────────────────────────────────────────────────
SELECTOR_PROMPT = """You are helping a user identify download elements on a video page.
The user took a screenshot of the page and extracted its DOM. Your job is to
suggest CSS selectors that would click the highest-quality download button.

Rules:
- Prefer specific, brittle selectors over generic ones (e.g. `a[href$=".mp4"]:has-text("4K")` over `a.btn`).
- If multiple resolutions are visible, prefer the highest.
- NEVER suggest selectors that match navigation, ads, related videos, login, or "share" buttons.
- Output 3-5 candidates ranked by confidence.

DOM EXCERPT (truncated to relevant region):
{dom_excerpt}

PAGE URL: {page_url}
{context_hint}

Return JSON only, no prose:
{{
  "suggestions": [
    {{"selector": "...", "role": "row_selectors" or "trigger_selectors",
      "reasoning": "one short sentence", "confidence": 0-100}}
  ]
}}"""

CLASSIFY_PROMPT = """Classify the following web element by role.

Element description:
{element_desc}

Choose ONE role from this list:
- user_field      — input where the username/email goes
- pass_field      — input where the password goes
- submit_btn      — button that submits the login form
- row_selectors   — link or button that triggers the actual file download
- trigger_selectors — button that reveals/opens a download menu or quality picker
- ignore          — irrelevant (ads, navigation, social, etc.)

Return JSON only:
{{"role": "...", "confidence": 0-100, "reasoning": "one short sentence"}}"""

RESOLUTION_PROMPT = """Extract the resolution from this filename.

Filename: {filename}

Return JSON only:
{{
  "resolution": "1080p|720p|2160p|...",
  "label": "1080|720|4K|8K|HD|SD|...",
  "width": 1920,
  "height": 1080,
  "confidence": 0-100
}}

If you can't determine the resolution, return resolution=null, label=null,
width=0, height=0, confidence=0. Do not guess wildly."""


# Phase 29: prompt for differential / repair-mode teach. The model
# gets a list of previously-working selectors that no longer match
# (the page got redesigned, IDs changed, classes were reshuffled,
# etc.) plus the current live DOM. Its job is to find structurally
# equivalent elements and suggest updated selectors.
DIFF_REPAIR_PROMPT = """A site you previously learned download selectors for has been redesigned.
Some of these selectors no longer match anything on the live page. Your job is to
look at the CURRENT DOM and propose new selectors that target the same kinds of
elements (download links, quality buttons, etc.) using the page's new structure.

PREVIOUSLY-WORKING SELECTORS that no longer match:
{broken_selectors}

PREVIOUSLY-WORKING SELECTORS that still work (KEEP these, use as anchors for context):
{working_selectors}

CURRENT DOM EXCERPT:
{dom_excerpt}

PAGE URL: {page_url}

Rules:
- For each BROKEN selector, propose ONE replacement that targets the same kind of element.
- If you can see a 4K/8K/highest-quality link in the new DOM that wasn't there before, prefer that.
- If a broken selector has no clear equivalent (the feature was removed), say so honestly.
- Prefer specific selectors (with text matches or attribute filters) over generic class selectors.

Return JSON only:
{{
  "repairs": [
    {{
      "old_selector": "exact text of broken selector",
      "new_selector": "proposed replacement CSS selector",
      "role": "row_selectors" or "trigger_selectors",
      "reasoning": "one short sentence explaining the match",
      "confidence": 0-100
    }}
  ],
  "removed": ["selector for which no equivalent could be found", ...]
}}"""


# ── Public API: suggest_selectors ─────────────────────────────────────
def suggest_selectors(dom_excerpt: str,
                      screenshot_b64: Optional[str] = None,
                      page_url: str = "",
                      context_hint: str = "") -> Dict[str, Any]:
    """Ask the vision model to propose CSS selectors. dom_excerpt should
    be a few KB of HTML (the relevant region, not the whole page).
    screenshot_b64 is optional — text-only fallback used if the vision
    model isn't installed or the caller didn't capture a shot.

    Returns: {ok, suggestions: [{selector, role, reasoning, confidence}]}.
    """
    if not _config["enabled"]:
        return {"ok": False, "error": "AI assist is disabled"}
    started = time.time()
    # Truncate DOM to ~16k chars — vision models handle modest input
    # well; very long DOMs cause repetition and confusion.
    dom_excerpt = (dom_excerpt or "")[:16000]
    prompt = SELECTOR_PROMPT.format(
        dom_excerpt=dom_excerpt,
        page_url=page_url or "(not provided)",
        context_hint=("HINT: " + context_hint) if context_hint else "",
    )
    # v3.43.43: provider-aware. Dispatcher picks the right wire
    # format based on _config["provider"].
    result = _call_model(prompt, image_b64=screenshot_b64,
                          max_tokens=1024, temperature=0.1, timeout=60)
    ms = result.latency_ms
    if not result.ok:
        _record_call("suggest", ms, False, result.error)
        return {"ok": False, "error": result.error,
                "error_kind": result.error_kind,
                "provider": result.provider}
    text = result.text
    parsed = extract_json(text)
    if not parsed or not isinstance(parsed, dict) or "suggestions" not in parsed:
        _record_call("suggest", ms, False, "couldn't parse JSON")
        return {"ok": False, "error": "model returned unparseable response",
                "raw": text[:500], "latency_ms": ms,
                "provider": result.provider}
    # Sanitize the suggestion list — drop anything missing fields
    cleaned = []
    for s in (parsed.get("suggestions") or []):
        if not isinstance(s, dict): continue
        sel = (s.get("selector") or "").strip()
        if not sel: continue
        role = s.get("role") or "row_selectors"
        if role not in ("row_selectors", "trigger_selectors"):
            role = "row_selectors"
        try: conf = int(s.get("confidence", 50))
        except Exception: conf = 50
        cleaned.append({
            "selector": sel[:300],
            "role": role,
            "reasoning": (s.get("reasoning") or "")[:200],
            "confidence": max(0, min(100, conf)),
        })
    cleaned.sort(key=lambda x: x["confidence"], reverse=True)
    _record_call("suggest", ms, True)
    return {"ok": True, "suggestions": cleaned[:5], "latency_ms": ms,
            "model": result.model, "provider": result.provider}


# ── Public API: diff_repair (Phase 29) ────────────────────────────────
def diff_repair(broken_selectors: List[str],
                working_selectors: List[str],
                dom_excerpt: str,
                screenshot_b64: Optional[str] = None,
                page_url: str = "") -> Dict[str, Any]:
    """Given previously-working selectors that no longer match a redesigned
    page, ask the model to propose replacements that target the same kinds
    of elements in the new DOM.

    broken_selectors:  list of selector strings that failed
    working_selectors: list of selector strings that still match (context)
    dom_excerpt:       up to ~16k chars of the relevant page region
    screenshot_b64:    optional; enables vision-mode matching

    Returns: {ok, repairs: [{old_selector, new_selector, role, reasoning,
              confidence}], removed: [selectors with no equivalent]}.
    """
    # Empty input → no-op success. Doesn't require AI to be enabled.
    if not broken_selectors:
        return {"ok": True, "repairs": [], "removed": [],
                "note": "no broken selectors to repair"}
    if not _config["enabled"]:
        return {"ok": False, "error": "AI assist is disabled"}
    started = time.time()
    dom_excerpt = (dom_excerpt or "")[:16000]
    # Format selector lists as bulleted text for the prompt
    def _fmt(sels):
        if not sels: return "  (none)"
        return "\n".join(f"  - {s}" for s in sels[:20])
    prompt = DIFF_REPAIR_PROMPT.format(
        broken_selectors=_fmt(broken_selectors),
        working_selectors=_fmt(working_selectors),
        dom_excerpt=dom_excerpt,
        page_url=page_url or "(not provided)",
    )
    # v3.43.43: provider-aware
    result = _call_model(prompt, image_b64=screenshot_b64,
                          max_tokens=1024, temperature=0.1, timeout=60)
    ms = result.latency_ms
    if not result.ok:
        _record_call("diff_repair", ms, False, result.error)
        return {"ok": False, "error": result.error,
                "error_kind": result.error_kind,
                "provider": result.provider}
    parsed = extract_json(result.text)
    if not parsed or not isinstance(parsed, dict):
        _record_call("diff_repair", ms, False, "couldn't parse JSON")
        return {"ok": False, "error": "model returned unparseable response",
                "raw": result.text[:400], "latency_ms": ms,
                "provider": result.provider}
    # Clean up + filter the repairs list
    repairs = []
    broken_set = set(broken_selectors)
    for r in (parsed.get("repairs") or []):
        if not isinstance(r, dict): continue
        old = (r.get("old_selector") or "").strip()
        new = (r.get("new_selector") or "").strip()
        if not old or not new: continue
        if old not in broken_set:
            match = next((b for b in broken_set if old in b or b in old), None)
            if not match: continue
            old = match
        role = r.get("role", "row_selectors")
        if role not in ("row_selectors","trigger_selectors"):
            role = "row_selectors"
        try: conf = int(r.get("confidence", 50))
        except Exception: conf = 50
        repairs.append({
            "old_selector": old,
            "new_selector": new[:300],
            "role": role,
            "reasoning": (r.get("reasoning") or "")[:200],
            "confidence": max(0, min(100, conf)),
        })
    removed = [s for s in (parsed.get("removed") or []) if isinstance(s, str)]
    _record_call("diff_repair", ms, True)
    return {"ok": True, "repairs": repairs, "removed": removed,
            "latency_ms": ms, "model": result.model,
            "provider": result.provider}


# ── Public API: classify_role ─────────────────────────────────────────
def classify_role(element_desc: Dict[str, Any]) -> Dict[str, Any]:
    """Given a textual description of an element, classify its role.
    element_desc is a short string like:
        '<input type="password" name="pw" placeholder="Password">'
    Used for auto-confirming proposed picks during teach.
    """
    if not _config["enabled"]:
        return {"ok": False, "error": "AI assist is disabled"}
    prompt = CLASSIFY_PROMPT.format(element_desc=element_desc[:1000])
    # v3.43.43: provider-aware. No image — uses text model.
    result = _call_model(prompt, image_b64=None,
                          max_tokens=200, temperature=0.0, timeout=15)
    ms = result.latency_ms
    if not result.ok:
        _record_call("classify", ms, False, result.error)
        return {"ok": False, "error": result.error,
                "error_kind": result.error_kind,
                "provider": result.provider}
    parsed = extract_json(result.text)
    if not parsed or not isinstance(parsed, dict):
        _record_call("classify", ms, False, "couldn't parse JSON")
        return {"ok": False, "error": "model returned unparseable response",
                "latency_ms": ms, "provider": result.provider}
    role = parsed.get("role", "ignore")
    valid = ("user_field","pass_field","submit_btn","row_selectors",
             "trigger_selectors","ignore")
    if role not in valid: role = "ignore"
    try: conf = int(parsed.get("confidence", 50))
    except Exception: conf = 50
    _record_call("classify", ms, True)
    return {"ok": True, "role": role,
            "confidence": max(0, min(100, conf)),
            "reasoning": (parsed.get("reasoning") or "")[:200],
            "latency_ms": ms, "provider": result.provider}


# ── Public API: normalize_resolution ──────────────────────────────────
# Rule-based fast path for common patterns; falls back to the LLM for
# weird cases. Returns the SAME shape either way so callers don't care
# which path produced the answer.

_RES_LABELS = [
    (re.compile(r"7680\s*[x×]\s*4320|\b8k\b", re.I), {"resolution":"4320p", "label":"8K",   "width":7680, "height":4320}),
    (re.compile(r"5760\s*[x×]\s*3240|\b6k\b", re.I), {"resolution":"3240p", "label":"6K",   "width":5760, "height":3240}),
    (re.compile(r"3840\s*[x×]\s*2160|\b4k\b|\b2160p?\b|\buhd\b", re.I), {"resolution":"2160p", "label":"4K",   "width":3840, "height":2160}),
    (re.compile(r"2560\s*[x×]\s*1440|\b2k\b|\b1440p?\b|\bqhd\b", re.I), {"resolution":"1440p", "label":"2K",   "width":2560, "height":1440}),
    (re.compile(r"1920\s*[x×]\s*1080|\b1080p?\b|\bfhd\b", re.I), {"resolution":"1080p", "label":"1080", "width":1920, "height":1080}),
    (re.compile(r"1280\s*[x×]\s*720|\b720p?\b|\bhd\b", re.I), {"resolution":"720p",  "label":"720",  "width":1280, "height":720}),
    (re.compile(r"\b480p?\b|\bsd\b", re.I), {"resolution":"480p", "label":"SD", "width":854, "height":480}),
]

def normalize_resolution(filename: str) -> Dict[str, Any]:
    """Best-effort extraction. Fast path: regex. Fallback: LLM."""
    if not filename:
        return {"ok": True, "resolution": None, "label": None,
                "width": 0, "height": 0, "confidence": 0,
                "via": "empty"}
    for rx, info in _RES_LABELS:
        if rx.search(filename):
            return {"ok": True, **info, "confidence": 95, "via": "regex"}
    if not _config["enabled"]:
        return {"ok": True, "resolution": None, "label": None,
                "width": 0, "height": 0, "confidence": 0,
                "via": "no-match"}
    # LLM fallback. v3.43.43: provider-aware.
    prompt = RESOLUTION_PROMPT.format(filename=filename[:300])
    result = _call_model(prompt, image_b64=None,
                          max_tokens=200, temperature=0.0, timeout=15)
    ms = result.latency_ms
    if not result.ok:
        _record_call("res", ms, False, result.error)
        return {"ok": False, "error": result.error,
                "error_kind": result.error_kind,
                "via": "ai-fail", "provider": result.provider}
    parsed = extract_json(result.text)
    if not parsed:
        _record_call("res", ms, False, "couldn't parse JSON")
        return {"ok": False, "error": "no parseable JSON",
                "via": "ai-fail", "provider": result.provider}
    try: width = int(parsed.get("width", 0))
    except Exception: width = 0
    try: height = int(parsed.get("height", 0))
    except Exception: height = 0
    try: conf = int(parsed.get("confidence", 0))
    except Exception: conf = 0
    _record_call("res", ms, True)
    return {"ok": True,
            "resolution": parsed.get("resolution"),
            "label": parsed.get("label"),
            "width": width, "height": height,
            "confidence": max(0, min(100, conf)),
            "via": "ai", "latency_ms": ms,
            "provider": result.provider}


# ── 7.5a: filename -> metadata at scale ───────────────────────────────
# Broadens normalize_resolution's regex-fast-path / LLM-fallback shape to
# full title/season/episode/codec/resolution extraction, cached by filename
# hash. Advisory only: the deterministic regex validates the common case and
# the model is a best-effort fallback for the weird tail. Same return shape
# from every path so callers never branch on `via`. Side-effect-free; fails
# open; never persists or mutates anything.

FILENAME_META_PROMPT = """Extract structured metadata from this media filename.

Filename: {filename}

Return JSON only:
{{
  "title": "human-readable title or null",
  "season": 0,
  "episode": 0,
  "codec": "h264|h265|av1|vp9|null",
  "resolution": "1080p|720p|2160p|null",
  "label": "1080|720|4K|null",
  "width": 1920,
  "height": 1080,
  "confidence": 0-100
}}

Use null / 0 for anything you cannot determine. Do not guess wildly."""

# Codec aliases -> normalized name. Order doesn't matter (no overlap).
_CODEC_RX = [
    (re.compile(r"\b(?:x265|h\.?265|hevc)\b", re.I), "h265"),
    (re.compile(r"\b(?:x264|h\.?264|avc)\b", re.I), "h264"),
    (re.compile(r"\bav1\b", re.I), "av1"),
    (re.compile(r"\bvp9\b", re.I), "vp9"),
]
# SxxExx and the alternate NxNN form.
_EP_SXXEXX_RX = re.compile(r"\bS(\d{1,2})E(\d{1,3})\b", re.I)
_EP_NXNN_RX   = re.compile(r"\b(\d{1,2})x(\d{2,3})\b", re.I)
# Tokens that mark the boundary between a title and the technical tail.
_TITLE_CUT_RX = re.compile(
    r"\b(?:S\d{1,2}E\d{1,3}|\d{1,2}x\d{2,3}|19\d\d|20\d\d|"
    r"\d{3,4}p|x26[45]|h\.?26[45]|hevc|avc|av1|vp9|web[- ]?dl|webrip|"
    r"bluray|bdrip|hdrip|dvdrip|repack|proper)\b",
    re.I)

# Cache keyed by sha256(filename) -> result dict (advisory; process-local).
_FILENAME_META_CACHE: Dict[str, Dict[str, Any]] = {}


def _filename_cache_key(filename: str) -> str:
    return hashlib.sha256(filename.encode("utf-8", "replace")).hexdigest()


def _blank_meta(via: str) -> Dict[str, Any]:
    return {"ok": True, "title": None, "season": None, "episode": None,
            "codec": None, "resolution": None, "label": None,
            "width": 0, "height": 0, "confidence": 0, "via": via}


def _title_from_stem(filename: str) -> Optional[str]:
    """Best-effort human title: drop extension, normalize separators,
    cut at the first technical token, collapse whitespace."""
    stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", filename)   # strip extension
    stem = stem.replace("_", " ").replace(".", " ")
    m = _TITLE_CUT_RX.search(stem)
    if m:
        stem = stem[:m.start()]
    stem = re.sub(r"[-\[\]()]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" -")
    return stem or None


def normalize_filename(filename: str, *, _call=None) -> Dict[str, Any]:
    """Best-effort filename -> metadata. Fast path: regex (title/season/
    episode/codec/resolution). Fallback: LLM for opaque names. Cached by
    filename hash. `_call` is an injection seam for tests (defaults to the
    provider-backed `_call_model`)."""
    if not filename:
        return _blank_meta("empty")

    key = _filename_cache_key(filename)
    cached = _FILENAME_META_CACHE.get(key)
    if cached is not None:
        return dict(cached)

    # ── regex fast path ──────────────────────────────────────────────
    codec = None
    for rx, name in _CODEC_RX:
        if rx.search(filename):
            codec = name
            break

    season = episode = None
    m = _EP_SXXEXX_RX.search(filename) or _EP_NXNN_RX.search(filename)
    if m:
        try:
            season, episode = int(m.group(1)), int(m.group(2))
        except Exception:
            season = episode = None

    res = None
    for rx, info in _RES_LABELS:
        if rx.search(filename):
            res = info
            break

    # A structured signal (codec/episode/resolution) means we trust regex.
    if codec or episode is not None or res is not None:
        out = {
            "ok": True,
            "title": _title_from_stem(filename),
            "season": season, "episode": episode, "codec": codec,
            "resolution": (res or {}).get("resolution"),
            "label": (res or {}).get("label"),
            "width": (res or {}).get("width", 0),
            "height": (res or {}).get("height", 0),
            "confidence": 95, "via": "regex",
        }
        _FILENAME_META_CACHE[key] = dict(out)
        return out

    if not _config["enabled"]:
        out = _blank_meta("no-match")
        out["title"] = _title_from_stem(filename)
        _FILENAME_META_CACHE[key] = dict(out)
        return out

    # ── LLM fallback (opaque names only) ─────────────────────────────
    call = _call or _call_model
    prompt = FILENAME_META_PROMPT.format(filename=filename[:300])
    result = call(prompt, image_b64=None, max_tokens=256,
                  temperature=0.0, timeout=15)
    ms = getattr(result, "latency_ms", 0)
    if not getattr(result, "ok", False):
        _record_call("fname", ms, False, getattr(result, "error", ""))
        return {"ok": False, "error": getattr(result, "error", ""),
                "error_kind": getattr(result, "error_kind", ""),
                "via": "ai-fail", "provider": getattr(result, "provider", "?")}
    parsed = extract_json(getattr(result, "text", "") or "")
    if not parsed or not isinstance(parsed, dict):
        _record_call("fname", ms, False, "couldn't parse JSON")
        return {"ok": False, "error": "no parseable JSON",
                "via": "ai-fail", "provider": getattr(result, "provider", "?")}

    def _opt_int(v):
        try:
            return int(v)
        except Exception:
            return None

    try:
        conf = int(parsed.get("confidence", 0))
    except Exception:
        conf = 0
    out = {
        "ok": True,
        "title": parsed.get("title") or None,
        "season": _opt_int(parsed.get("season")),
        "episode": _opt_int(parsed.get("episode")),
        "codec": parsed.get("codec") or None,
        "resolution": parsed.get("resolution") or None,
        "label": parsed.get("label") or None,
        "width": (_opt_int(parsed.get("width")) or 0),
        "height": (_opt_int(parsed.get("height")) or 0),
        "confidence": max(0, min(100, conf)),
        "via": "ai", "latency_ms": ms,
        "provider": getattr(result, "provider", "?"),
    }
    _record_call("fname", ms, True)
    _FILENAME_META_CACHE[key] = dict(out)
    return out
