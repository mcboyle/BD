"""v3.43.43: Multi-provider AI adapter.

Originally aiassist.py only spoke Ollama. This module adds three
cloud providers — Anthropic Claude, OpenAI ChatGPT, and Google
Gemini — behind a unified adapter so the rest of the codebase
(aiassist.py, ai_login.py) doesn't have to care which one is
configured.

## Why providers as a module, not inline

Each cloud provider has a different API shape:
  - Ollama:  POST /api/generate {model, prompt, images:[b64], stream:false}
             → {response: "...", model: "..."}
  - Claude:  POST /v1/messages {model, max_tokens, messages:[{role,content}]}
             header: x-api-key, anthropic-version
             → {content: [{type:"text", text:"..."}], ...}
  - OpenAI:  POST /v1/chat/completions {model, messages, max_tokens}
             header: Authorization: Bearer
             → {choices: [{message: {content: "..."}}], ...}
  - Gemini:  POST /v1beta/models/{model}:generateContent {contents:[{parts:[...]}]}
             query: key=...
             → {candidates: [{content: {parts: [{text: "..."}]}}], ...}

Image attachments are even more divergent:
  - Ollama:  body.images = ["<base64>"]
  - Claude:  content = [{type:"image", source:{type:"base64", media_type, data}}, {type:"text", text}]
  - OpenAI:  content = [{type:"image_url", image_url:{url:"data:image/png;base64,..."}}, ...]
  - Gemini:  parts = [{inline_data:{mime_type:"image/png", data:"<base64>"}}, ...]

Per-provider classes hide all of that. The caller just gets back
a `GenerationResult` with `.text` and the rest of the codebase
operates the same regardless of which one is configured.

## Privacy semantics

Ollama is LAN-only (existing behavior — host must be RFC1918 or
loopback). Cloud providers are PUBLIC by definition — the privacy
guard only applies to ollama. The UI surfaces this clearly so the
user knows when they're sending data off-premises.

## API key storage

Cloud providers need credentials. Keys go through the existing
`secrets_store` module (v3.43.14, the vault). Stored as
"@cred:ai_<provider>_key" tokens that resolve to plaintext only
at use time. Plaintext keys are also accepted (for the user who
just wants something working in 30 seconds), but the UI nudges
toward vault storage.

## Error handling

Every call returns a result object — never raises. Cloud APIs
have many quirky failure modes (auth, quota, content filter,
rate limit, model not available, region block, brief network
blip) and we want each one surfaced consistently as
`{ok:False, error:"...", error_kind:"auth|quota|filter|..."}`
so the UI can color-code without parsing strings.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ── Result objects ───────────────────────────────────────────────────


@dataclass
class GenerationResult:
    """Outcome of a single inference call. .ok semantics same as
    everywhere else in the codebase; .text holds the model's
    response on success. error_kind is one of:
      auth | quota | rate_limit | filter | timeout |
      bad_request | server_error | network | unknown
    """
    ok: bool
    text: str = ""
    model: str = ""
    provider: str = ""
    latency_ms: int = 0
    error: str = ""
    error_kind: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)
    # AI-4: cost/token accounting. Token counts are extracted from the raw
    # provider response; cost_usd is computed from the operator-set per-model
    # rate table (None when no rate is configured -- BD never guesses a price).
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Optional[float] = None

    def __post_init__(self):
        # AI-4: derive token counts + cost from the raw response automatically so
        # every provider path records them without touching each return site.
        if self.raw and self.prompt_tokens == 0 and self.completion_tokens == 0:
            self.prompt_tokens, self.completion_tokens = _extract_usage(self.raw)
        if self.cost_usd is None and (self.prompt_tokens or self.completion_tokens):
            self.cost_usd = _cost_for(self.model, self.prompt_tokens,
                                      self.completion_tokens)


# ── AI-4: token/cost accounting helpers ──────────────────────────────
# Per-model rate table: {model: {"in_per_1k": float, "out_per_1k": float}}.
# Operator-supplied (wired from config); empty by default so cost stays None.
_MODEL_RATES: Dict[str, Dict[str, float]] = {}


def set_model_rates(rates: Optional[Dict[str, Dict[str, float]]]) -> None:
    """Replace the per-model USD rate table (used to compute GenerationResult.cost_usd)."""
    global _MODEL_RATES
    _MODEL_RATES = dict(rates or {})


def _extract_usage(raw: Any) -> "tuple[int, int]":
    """Best-effort (prompt_tokens, completion_tokens) from a raw provider response.

    Handles Ollama (prompt_eval_count/eval_count), Claude and OpenAI
    (usage.{input,output}_tokens / usage.{prompt,completion}_tokens). Unknown or
    missing shapes return (0, 0) -- never raises.
    """
    if not isinstance(raw, dict):
        return (0, 0)
    # Ollama: top-level counters
    if "prompt_eval_count" in raw or "eval_count" in raw:
        try:
            return (int(raw.get("prompt_eval_count") or 0),
                    int(raw.get("eval_count") or 0))
        except Exception:
            return (0, 0)
    usage = raw.get("usage")
    if isinstance(usage, dict):
        try:
            pt = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
            ct = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
            return (int(pt), int(ct))
        except Exception:
            return (0, 0)
    return (0, 0)


def _cost_for(model: str, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
    """USD cost for a call from the configured per-model rate, or None if unset."""
    rate = _MODEL_RATES.get(model or "")
    if not isinstance(rate, dict):
        return None
    try:
        in_1k = float(rate.get("in_per_1k", 0.0))
        out_1k = float(rate.get("out_per_1k", 0.0))
    except Exception:
        return None
    return (prompt_tokens / 1000.0) * in_1k + (completion_tokens / 1000.0) * out_1k


@dataclass
class HealthResult:
    ok: bool
    latency_ms: int = 0
    error: str = ""
    info: Dict[str, Any] = field(default_factory=dict)


# ── Provider base class ──────────────────────────────────────────────


class AIProvider:
    """Subclass contract:
      .name            — short id, e.g. "ollama" / "claude"
      .needs_api_key   — bool; cloud providers all True
      .default_endpoint — used when user doesn't configure one
      .default_models  — {"vision": "...", "text": "..."}
      .generate(...)   — abstract
      .health_check()  — abstract
      .list_models()   — optional; return None if not supported

    Subclasses should NEVER let an exception escape `generate` or
    `health_check`. Failures convert to `GenerationResult(ok=False,
    error_kind=...)` so the caller can color-code them.
    """
    name: str = ""
    needs_api_key: bool = False
    default_endpoint: str = ""
    default_models: Dict[str, str] = {}
    # Some providers (Claude) have a fixed model list — knowing it
    # lets the UI suggest a dropdown rather than a free-text field
    fixed_model_list: Optional[List[str]] = None

    def __init__(self, endpoint: str = "", api_key: str = "",
                  model_vision: str = "", model_text: str = ""):
        self.endpoint = (endpoint or self.default_endpoint).rstrip("/")
        self.api_key = api_key or ""
        self.model_vision = (model_vision or "").strip() or self.default_models.get("vision", "")
        self.model_text = (model_text or "").strip() or self.default_models.get("text", "")

    def generate(self, prompt: str, image_b64: Optional[str] = None,
                  temperature: float = 0.1, max_tokens: int = 1024,
                  timeout: float = 60.0) -> GenerationResult:
        raise NotImplementedError

    def health_check(self) -> HealthResult:
        raise NotImplementedError

    def list_models(self) -> Optional[List[str]]:
        return self.fixed_model_list

    # ── Shared HTTP helper ──────────────────────────────────────────

    def _http_post(self, url: str, body: Dict[str, Any],
                    headers: Dict[str, str],
                    timeout: float) -> tuple:
        """POST + read response. Returns (ok, status, parsed_json or raw_text, latency_ms).

        Never raises. Conversion to GenerationResult is the caller's
        job — different providers map HTTP codes to error_kinds
        differently.
        """
        data = json.dumps(body).encode("utf-8")
        all_headers = {"Content-Type": "application/json"}
        all_headers.update(headers)
        req = Request(url, data=data, method="POST",
                        headers=all_headers)
        started = time.time()
        try:
            resp = urlopen(req, timeout=timeout)
            raw = resp.read().decode("utf-8", "replace")
            ms = int((time.time() - started) * 1000)
            try:
                return True, resp.status, json.loads(raw), ms
            except Exception:
                return True, resp.status, raw, ms
        except HTTPError as e:
            ms = int((time.time() - started) * 1000)
            body_err = ""
            try:
                body_err = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            # Try to parse the error body as JSON for nicer surfacing
            try:
                err_json = json.loads(body_err)
                return False, e.code, err_json, ms
            except Exception:
                return False, e.code, body_err or e.reason, ms
        except URLError as e:
            ms = int((time.time() - started) * 1000)
            return False, 0, f"network: {e.reason}", ms
        except Exception as e:
            ms = int((time.time() - started) * 1000)
            return False, 0, f"{type(e).__name__}: {e}", ms

    def _classify_http_error(self, status: int) -> str:
        """Map an HTTP status code to one of the standard error_kind
        values. Subclasses can override for provider-specific quirks."""
        if status == 401 or status == 403:
            return "auth"
        if status == 429:
            return "rate_limit"
        if status == 408 or status == 504:
            return "timeout"
        if 400 <= status < 500:
            return "bad_request"
        if 500 <= status < 600:
            return "server_error"
        if status == 0:
            return "network"
        return "unknown"


# ── Ollama (existing) ────────────────────────────────────────────────


class OllamaProvider(AIProvider):
    """Self-hosted Ollama. Keeps the existing behavior of aiassist —
    LAN-only, no API key, /api/generate."""
    name = "ollama"
    needs_api_key = False
    default_endpoint = "http://localhost:11434"
    default_models = {
        # Real Ollama registry tags. The qwen2.5 size tags are already
        # Q4_K_M quants — there is no "-q4" suffix in the registry. The
        # vision model is the qwen2.5vl family, not "qwen2-vl".
        "vision": "qwen2.5vl:7b",
        "text": "qwen2.5:7b",
    }
    # L19: hold the model in memory between calls so an idle gap doesn't force
    # a cold-load (30-60s) that trips the request timeout. Ollama accepts a
    # duration string; "10m" comfortably covers interactive operator use.
    keep_alive = "10m"

    def generate(self, prompt, image_b64=None, temperature=0.1,
                  max_tokens=1024, timeout=60.0):
        model = self.model_vision if image_b64 else self.model_text
        body = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if image_b64:
            body["images"] = [image_b64]
        ok, status, resp, ms = self._http_post(
            self.endpoint + "/api/generate", body, {}, timeout)
        if not ok:
            err_str = resp if isinstance(resp, str) else json.dumps(resp)[:300]
            return GenerationResult(
                ok=False, provider=self.name, model=model,
                latency_ms=ms, error=err_str,
                error_kind=self._classify_http_error(status))
        if not isinstance(resp, dict):
            return GenerationResult(
                ok=False, provider=self.name, model=model,
                latency_ms=ms,
                error=f"non-JSON response: {str(resp)[:200]}",
                error_kind="server_error")
        text = resp.get("response") or ""
        return GenerationResult(
            ok=True, text=text, model=model, provider=self.name,
            latency_ms=ms, raw=resp)

    def health_check(self):
        try:
            started = time.time()
            resp = urlopen(Request(self.endpoint + "/api/tags"),
                             timeout=5)
            raw = resp.read().decode("utf-8", "replace")
            ms = int((time.time() - started) * 1000)
            data = json.loads(raw)
            return HealthResult(
                ok=True, latency_ms=ms,
                info={"installed_models": [
                    m.get("name", "")
                    for m in (data.get("models") or [])
                ]})
        except Exception as e:
            return HealthResult(ok=False,
                                  error=f"{type(e).__name__}: {e}")

    def list_models(self):
        h = self.health_check()
        if h.ok:
            return h.info.get("installed_models", [])
        return None

    def warmup(self, timeout=120.0):
        """Pre-load the text model into memory and hold it (keep_alive) so the
        first real call doesn't cold-load and time out (L19). Cheap: a 1-token
        empty generate. Best-effort; returns True if the load call succeeded."""
        body = {
            "model": self.model_text,
            "prompt": "",
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"num_predict": 1},
        }
        ok, _status, _resp, _ms = self._http_post(
            self.endpoint + "/api/generate", body, {}, timeout)
        return bool(ok)

    def _classify_http_error(self, status: int) -> str:
        """Ollama returns 404 when the requested model isn't pulled locally --
        distinct from a generic malformed request. Surface it as 'not_found' so
        the UI can say 'model not installed' (and the operator can pull it)."""
        if status == 404:
            return "not_found"
        return super()._classify_http_error(status)


# ── Claude (Anthropic) ───────────────────────────────────────────────


class ClaudeProvider(AIProvider):
    """Anthropic Claude. Uses /v1/messages with the x-api-key header.
    Image attachments go in the content array as type='image' blocks."""
    name = "claude"
    needs_api_key = True
    default_endpoint = "https://api.anthropic.com"
    default_models = {
        # Defaults match the most capable widely-available models
        # as of build time. User can override.
        "vision": "claude-sonnet-4-6",
        "text": "claude-haiku-4-5-20251001",
    }
    fixed_model_list = [
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ]

    def generate(self, prompt, image_b64=None, temperature=0.1,
                  max_tokens=1024, timeout=60.0):
        if not self.api_key:
            return GenerationResult(
                ok=False, provider=self.name,
                error="API key not configured",
                error_kind="auth")
        model = self.model_vision if image_b64 else self.model_text
        # Construct content blocks
        content: List[Dict[str, Any]] = []
        if image_b64:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    # We default to png — callers send screenshots,
                    # which are png in this codebase. Wrong mime
                    # type is non-fatal; Claude infers from data.
                    "media_type": "image/png",
                    "data": image_b64,
                },
            })
        content.append({"type": "text", "text": prompt})
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": content}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        ok, status, resp, ms = self._http_post(
            self.endpoint + "/v1/messages",
            body, headers, timeout)
        if not ok:
            err = self._extract_error_message(resp)
            kind = self._classify_http_error(status)
            # Claude returns 400 for content filter; differentiate
            if status == 400 and isinstance(resp, dict):
                err_type = ((resp.get("error") or {}).get("type") or "")
                if "policy" in err_type.lower() or "content" in err_type.lower():
                    kind = "filter"
            return GenerationResult(
                ok=False, provider=self.name, model=model,
                latency_ms=ms, error=err, error_kind=kind)
        if not isinstance(resp, dict):
            return GenerationResult(
                ok=False, provider=self.name, model=model,
                latency_ms=ms,
                error=f"non-JSON response: {str(resp)[:200]}",
                error_kind="server_error")
        # Find text blocks in the response.content array
        text_parts = []
        for block in (resp.get("content") or []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_parts.append(block.get("text") or "")
        text = "".join(text_parts)
        return GenerationResult(
            ok=True, text=text, model=model, provider=self.name,
            latency_ms=ms, raw=resp)

    def health_check(self):
        """Cheapest "is the API key + endpoint working" probe. Claude
        has no enumeration endpoint we can hit anonymously, so we send
        a tiny 1-token request."""
        if not self.api_key:
            return HealthResult(ok=False, error="no API key configured")
        # Tiny ping — uses the cheapest model + minimum tokens
        body = {
            "model": self.model_text or "claude-haiku-4-5-20251001",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "."}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        ok, status, resp, ms = self._http_post(
            self.endpoint + "/v1/messages", body, headers, 10.0)
        if not ok:
            return HealthResult(
                ok=False, latency_ms=ms,
                error=self._extract_error_message(resp))
        return HealthResult(ok=True, latency_ms=ms,
                              info={"fixed_models": self.fixed_model_list})

    def _extract_error_message(self, resp) -> str:
        if isinstance(resp, dict):
            err = resp.get("error") or {}
            if isinstance(err, dict):
                return f"{err.get('type','?')}: {err.get('message','')}"[:300]
            return json.dumps(resp)[:300]
        return str(resp)[:300]


# ── OpenAI (ChatGPT) ─────────────────────────────────────────────────


class OpenAIProvider(AIProvider):
    """OpenAI Chat Completions. Bearer auth, image_url content blocks."""
    name = "openai"
    needs_api_key = True
    default_endpoint = "https://api.openai.com"
    default_models = {
        "vision": "gpt-4o",
        "text": "gpt-4o-mini",
    }
    # Not enforced — user can type any model. We suggest the common
    # ones so the dropdown is helpful.
    fixed_model_list = [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "o1",
        "o1-mini",
    ]

    def generate(self, prompt, image_b64=None, temperature=0.1,
                  max_tokens=1024, timeout=60.0):
        if not self.api_key:
            return GenerationResult(
                ok=False, provider=self.name,
                error="API key not configured",
                error_kind="auth")
        model = self.model_vision if image_b64 else self.model_text
        # Content is a list when image is included, a plain string
        # when text-only — both shapes are accepted by Chat
        # Completions.
        if image_b64:
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{image_b64}",
                }},
            ]
        else:
            content = prompt
        body = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {"Authorization": "Bearer " + self.api_key}
        ok, status, resp, ms = self._http_post(
            self.endpoint + "/v1/chat/completions",
            body, headers, timeout)
        if not ok:
            err = self._extract_error_message(resp)
            kind = self._classify_http_error(status)
            # OpenAI puts content-filter errors under code 'content_filter'
            if isinstance(resp, dict):
                inner = (resp.get("error") or {})
                if isinstance(inner, dict):
                    code = (inner.get("code") or "").lower()
                    if "content_filter" in code or "content_policy" in code:
                        kind = "filter"
                    elif "insufficient_quota" in code:
                        kind = "quota"
            return GenerationResult(
                ok=False, provider=self.name, model=model,
                latency_ms=ms, error=err, error_kind=kind)
        if not isinstance(resp, dict):
            return GenerationResult(
                ok=False, provider=self.name, model=model,
                latency_ms=ms,
                error=f"non-JSON response: {str(resp)[:200]}",
                error_kind="server_error")
        choices = resp.get("choices") or []
        text = ""
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            text = msg.get("content") or ""
        return GenerationResult(
            ok=True, text=text, model=model, provider=self.name,
            latency_ms=ms, raw=resp)

    def health_check(self):
        """Hit /v1/models which is cheap and validates the key."""
        if not self.api_key:
            return HealthResult(ok=False, error="no API key configured")
        try:
            started = time.time()
            req = Request(
                self.endpoint + "/v1/models",
                headers={"Authorization": "Bearer " + self.api_key})
            resp = urlopen(req, timeout=10)
            raw = resp.read().decode("utf-8", "replace")
            ms = int((time.time() - started) * 1000)
            data = json.loads(raw)
            return HealthResult(
                ok=True, latency_ms=ms,
                info={"installed_models": [
                    m.get("id", "")
                    for m in (data.get("data") or [])
                ]})
        except HTTPError as e:
            ms = int((time.time() - started) * 1000)
            return HealthResult(
                ok=False, latency_ms=ms,
                error=f"HTTP {e.code}: {e.reason}")
        except Exception as e:
            return HealthResult(
                ok=False, error=f"{type(e).__name__}: {e}")

    def list_models(self):
        h = self.health_check()
        if h.ok:
            installed = h.info.get("installed_models", [])
            # Filter to GPT and o-series; OpenAI returns dozens of
            # fine-tuned + deprecated entries
            return [m for m in installed
                      if m.startswith(("gpt-", "o1", "o3"))]
        return self.fixed_model_list

    def _extract_error_message(self, resp) -> str:
        if isinstance(resp, dict):
            err = resp.get("error") or {}
            if isinstance(err, dict):
                msg = err.get("message", "")
                code = err.get("code", "")
                return f"[{code}] {msg}"[:300] if code else msg[:300]
        return str(resp)[:300]


# ── Gemini (Google) ──────────────────────────────────────────────────


class GeminiProvider(AIProvider):
    """Google Gemini. URL takes ?key=<API_KEY>, content uses 'parts'
    array with text + inline_data shapes."""
    name = "gemini"
    needs_api_key = True
    default_endpoint = "https://generativelanguage.googleapis.com"
    default_models = {
        "vision": "gemini-2.0-flash-exp",
        "text": "gemini-1.5-flash",
    }
    fixed_model_list = [
        "gemini-2.0-flash-exp",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "gemini-1.5-flash-8b",
    ]

    def generate(self, prompt, image_b64=None, temperature=0.1,
                  max_tokens=1024, timeout=60.0):
        if not self.api_key:
            return GenerationResult(
                ok=False, provider=self.name,
                error="API key not configured",
                error_kind="auth")
        model = self.model_vision if image_b64 else self.model_text
        parts: List[Dict[str, Any]] = []
        if image_b64:
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": image_b64,
                },
            })
        parts.append({"text": prompt})
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        # Gemini API key goes in the URL query string
        from urllib.parse import quote
        url = (self.endpoint
                 + f"/v1beta/models/{quote(model, safe='')}"
                 + f":generateContent?key={quote(self.api_key, safe='')}")
        ok, status, resp, ms = self._http_post(url, body, {}, timeout)
        if not ok:
            err = self._extract_error_message(resp)
            kind = self._classify_http_error(status)
            # Gemini returns 400 with status=INVALID_ARGUMENT for bad
            # content; 429 for rate; 403 for region block
            return GenerationResult(
                ok=False, provider=self.name, model=model,
                latency_ms=ms, error=err, error_kind=kind)
        if not isinstance(resp, dict):
            return GenerationResult(
                ok=False, provider=self.name, model=model,
                latency_ms=ms,
                error=f"non-JSON response: {str(resp)[:200]}",
                error_kind="server_error")
        # Walk candidates[0].content.parts[*].text
        candidates = resp.get("candidates") or []
        # Check for safety blocks first — these come back as
        # candidate with finishReason=SAFETY and no text
        if candidates and isinstance(candidates[0], dict):
            finish = candidates[0].get("finishReason", "")
            if finish in ("SAFETY", "RECITATION", "BLOCKED"):
                return GenerationResult(
                    ok=False, provider=self.name, model=model,
                    latency_ms=ms,
                    error=f"blocked: {finish}",
                    error_kind="filter")
        text_parts = []
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            content = cand.get("content") or {}
            for p in (content.get("parts") or []):
                if isinstance(p, dict) and "text" in p:
                    text_parts.append(p["text"] or "")
        text = "".join(text_parts)
        return GenerationResult(
            ok=True, text=text, model=model, provider=self.name,
            latency_ms=ms, raw=resp)

    def health_check(self):
        if not self.api_key:
            return HealthResult(ok=False, error="no API key configured")
        try:
            from urllib.parse import quote
            started = time.time()
            url = (self.endpoint
                     + f"/v1beta/models?key={quote(self.api_key, safe='')}")
            resp = urlopen(Request(url), timeout=10)
            raw = resp.read().decode("utf-8", "replace")
            ms = int((time.time() - started) * 1000)
            data = json.loads(raw)
            # models entries have name="models/gemini-1.5-flash"; strip
            installed = []
            for m in (data.get("models") or []):
                full = m.get("name") or ""
                if full.startswith("models/"):
                    installed.append(full[len("models/"):])
                else:
                    installed.append(full)
            return HealthResult(
                ok=True, latency_ms=ms,
                info={"installed_models": installed})
        except HTTPError as e:
            return HealthResult(
                ok=False, error=f"HTTP {e.code}: {e.reason}")
        except Exception as e:
            return HealthResult(
                ok=False, error=f"{type(e).__name__}: {e}")

    def list_models(self):
        h = self.health_check()
        if h.ok:
            return h.info.get("installed_models", [])
        return self.fixed_model_list

    def _extract_error_message(self, resp) -> str:
        if isinstance(resp, dict):
            err = resp.get("error") or {}
            if isinstance(err, dict):
                return f"{err.get('status','?')}: {err.get('message','')}"[:300]
        return str(resp)[:300]


# ── Factory ──────────────────────────────────────────────────────────


_PROVIDERS = {
    "ollama": OllamaProvider,
    "claude": ClaudeProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}


def make_provider(name: str, endpoint: str = "", api_key: str = "",
                    model_vision: str = "",
                    model_text: str = "") -> Optional[AIProvider]:
    """Construct a provider by short name. Returns None on unknown
    name (caller treats as 'not configured')."""
    if not isinstance(name, str):
        return None
    cls = _PROVIDERS.get(name.strip().lower())
    if cls is None:
        return None
    return cls(endpoint=endpoint, api_key=api_key,
                 model_vision=model_vision, model_text=model_text)


def list_provider_names() -> List[str]:
    return list(_PROVIDERS.keys())


def provider_info(name: str) -> Optional[Dict[str, Any]]:
    """Static metadata for a provider — used by the UI to decide
    which fields to show (e.g. hide endpoint for cloud, show API
    key for cloud, etc.)."""
    if not isinstance(name, str):
        return None
    cls = _PROVIDERS.get(name.strip().lower())
    if cls is None:
        return None
    return {
        "name": cls.name,
        "needs_api_key": cls.needs_api_key,
        "default_endpoint": cls.default_endpoint,
        "default_models": dict(cls.default_models),
        "fixed_model_list": list(cls.fixed_model_list) if cls.fixed_model_list else None,
        "is_local": cls.name == "ollama",
    }
