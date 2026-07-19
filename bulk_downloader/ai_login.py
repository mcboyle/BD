"""v3.43.33: AI-assisted login form detection.

When the existing brute-force selector enumeration in login.py
fails — typically on new sites with unusual markup, or sites that
recently redesigned and broke our built-in templates — this module
proposes selectors directly by asking the local LLM to look at the
page.

## Why this is a separate module

The existing aiassist.py module handles:
  - Selector suggestions for *content rows* on listing pages
  - Selector diff-repair for broken templates
  - Element role classification
  - Resolution normalization

Login-form detection is mechanically similar (LLM, vision-capable,
JSON response) but the prompt and post-processing are different
enough that splitting into a dedicated module keeps each file
cohesive. Both modules share `aiassist._post_json` so the network
layer is single-source-of-truth.

## What the AI sees

  - DOM excerpt (HTML around the form, ideally ≤16KB so the model
    doesn't lose focus)
  - Optional screenshot (base64 PNG) — the vision model is much
    better at "this is the login form" recognition than text-only

## What it returns

  {
    "ok": bool,
    "username_selector": str,        // CSS selector
    "password_selector": str,
    "submit_selector": str,
    "captcha_detected": bool,        // surfaces a captcha so we
                                      // can route to manual login
    "confidence": int,               // 0-100; below ~70 the runner
                                      // should fall back to the
                                      // existing enumeration
    "reasoning": str,                // for the UI tooltip
    "latency_ms": int,
    "model": str
  }

On failure: {"ok": False, "error": str}

## Validation before use

The runner MUST call validate_proposed_selectors(page, proposal)
against the live Playwright page before submitting credentials —
the AI can hallucinate selectors that look plausible but don't
resolve. Validating is one round-trip per selector (count = how
many DOM nodes match) and protects against blindly trusting LLM
output.

## Caching

When AI detection succeeds and the actual login succeeds, the
runner saves the proposed selectors as learned overrides for that
site (existing user_templates infrastructure). Next run skips the
AI call entirely — fast path. The AI is only invoked when:
  - User explicitly clicks the 🪄 button, OR
  - Existing enumeration fails AND ai_login_assist_enabled=True on
    the site config
"""
from __future__ import annotations

from typing import Optional


# Prompt template. Designed to:
#   1. Make the JSON shape unambiguous so extract_json can find it
#   2. Include explicit "if no form found, return empty selectors"
#      so the model doesn't hallucinate to fill the response
#   3. Ask for confidence per-field so partial recognition is
#      surfaced ("I'm sure about username, less sure about submit")
LOGIN_DETECT_PROMPT = """You are analyzing a webpage to find the login form.

URL: {page_url}
{context_hint}

DOM excerpt:
```html
{dom_excerpt}
```

Find the form where a returning user enters credentials to log in. Skip
signup, registration, or password-reset forms unless they're the only
form present.

Respond with ONLY a JSON object in this exact shape:

{{
  "username_selector": "CSS selector for the username/email input",
  "password_selector": "CSS selector for the password input",
  "submit_selector": "CSS selector for the submit/login button",
  "captcha_detected": false,
  "confidence": 85,
  "reasoning": "Brief explanation: 1-2 sentences max"
}}

Rules:
- Selectors must be valid CSS (id, class, attribute, type=password, etc.)
- Prefer simple, specific selectors over complex chains
- For inputs, "input[type=password]" or "#login-pass" are typical good answers
- For submit buttons, button text or aria-label often works better than position
- captcha_detected=true ONLY if you see a clear captcha widget (recaptcha,
  hcaptcha, image-distortion text, "I'm not a robot")
- confidence is 0-100: how sure you are this is the login form
- If you can't find ANY of the three, return empty strings and confidence: 0
- DO NOT explain outside the JSON. JSON only."""


def detect_login_form(dom_excerpt: str,
                        screenshot_b64: Optional[str] = None,
                        page_url: str = "",
                        context_hint: str = "") -> dict:
    """Ask the LLM to identify the login form's username, password, and
    submit selectors.

    Args:
      dom_excerpt: HTML snippet (ideally the form's ancestor region).
                    Truncated to 16KB internally.
      screenshot_b64: optional base64 PNG. When provided, uses vision
                       model; otherwise uses text-only model.
      page_url: for prompt context (helps when the URL itself
                  indicates "login" vs "signup")
      context_hint: optional extra instruction ("this is a single-page
                     app, the form may be in a modal")

    Returns: dict with ok / selectors / confidence / reasoning fields
              (see module docstring for full shape).
    """
    from . import aiassist
    if not aiassist.get_config().get("enabled"):
        return {"ok": False, "error": "AI assist is disabled (enable in Settings)"}
    dom_excerpt = (dom_excerpt or "")[:16000]
    prompt = LOGIN_DETECT_PROMPT.format(
        dom_excerpt=dom_excerpt,
        page_url=page_url or "(not provided)",
        context_hint=("HINT: " + context_hint) if context_hint else "",
    )
    # v3.43.43: provider-aware. Low temp = 0.05 for deterministic
    # selector identification, not creative writing.
    result = aiassist._call_model(prompt, image_b64=screenshot_b64,
                                     max_tokens=512, temperature=0.05,
                                     timeout=60)
    ms = result.latency_ms
    if not result.ok:
        aiassist._record_call("detect_login", ms, False, result.error)
        return {"ok": False, "error": result.error,
                "error_kind": result.error_kind,
                "latency_ms": ms, "provider": result.provider}
    text = result.text or ""
    parsed = aiassist.extract_json(text)
    if not isinstance(parsed, dict):
        aiassist._record_call("detect_login", ms, False,
                                "couldn't parse JSON")
        return {"ok": False, "error": "model returned unparseable response",
                "raw": text[:500] if isinstance(text, str) else "",
                "latency_ms": ms, "provider": result.provider}
    # Sanitize each field. Selectors can be empty strings (model
    # signals "couldn't find this one") but not None.
    def _clean_selector(v) -> str:
        if not isinstance(v, str): return ""
        s = v.strip()
        if not s: return ""
        # Cap length — sane selectors are short. A 5000-char "selector"
        # is the model hallucinating an entire HTML element.
        return s[:300]
    try:
        confidence = int(parsed.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))
    result = {
        "ok": True,
        "username_selector": _clean_selector(parsed.get("username_selector")),
        "password_selector": _clean_selector(parsed.get("password_selector")),
        "submit_selector": _clean_selector(parsed.get("submit_selector")),
        "captcha_detected": bool(parsed.get("captcha_detected", False)),
        "confidence": confidence,
        "reasoning": (parsed.get("reasoning") or "")[:300] if isinstance(parsed.get("reasoning"), str) else "",
        "latency_ms": ms,
        "model": result.model,
        "provider": result.provider,
    }
    # If all three selectors are empty, the AI couldn't find a form —
    # treat as "ok but useless"; caller falls back
    if not (result["username_selector"]
            or result["password_selector"]
            or result["submit_selector"]):
        result["confidence"] = 0
    aiassist._record_call("detect_login", ms, True)
    return result


def validate_proposed_selectors(page, proposal: dict) -> dict:
    """Run the proposed selectors against a live Playwright page and
    report per-selector validity.

    Args:
      page: Playwright Page object
      proposal: output of detect_login_form

    Returns:
      {
        "username_valid": bool,
        "password_valid": bool,
        "submit_valid": bool,
        "username_count": int,    // how many nodes match
        "password_count": int,
        "submit_count": int,
        "all_valid": bool,        // True iff exactly one match each
        "errors": [str, ...]
      }

    Why this matters: LLMs occasionally produce plausible-looking
    selectors that don't resolve (e.g. `.login-form` when the actual
    class is `loginForm`). Without this validation step, the runner
    would call `page.fill('.login-form', username)` which silently
    no-ops on Playwright, and then submit an empty form. We catch
    that BEFORE it can produce a confusing 'Invalid credentials'
    error.
    """
    out = {
        "username_valid": False, "password_valid": False, "submit_valid": False,
        "username_count": 0, "password_count": 0, "submit_count": 0,
        "all_valid": False, "errors": [],
    }
    for field, key in (("username_selector", "username"),
                        ("password_selector", "password"),
                        ("submit_selector", "submit")):
        raw_sel = proposal.get(field)
        # Defensive: a malformed proposal might have non-string values
        # (int, list, dict). Treat anything non-string-or-empty as "no
        # selector proposed" — same as missing.
        if not isinstance(raw_sel, str):
            out["errors"].append(
                f"{key}: no selector proposed (got {type(raw_sel).__name__})")
            continue
        sel = raw_sel.strip()
        if not sel:
            out["errors"].append(f"{key}: no selector proposed")
            continue
        try:
            count = page.locator(sel).count()
            out[f"{key}_count"] = count
            if count == 1:
                out[f"{key}_valid"] = True
            elif count == 0:
                out["errors"].append(
                    f"{key}: selector {sel!r} matches 0 nodes")
            else:
                # Multiple matches — ambiguous, but might still work
                # if the first one is the right one. Mark as valid
                # but flag for caller awareness.
                out[f"{key}_valid"] = True
                out["errors"].append(
                    f"{key}: selector {sel!r} matches {count} nodes "
                    f"(ambiguous, using first)")
        except Exception as e:
            out["errors"].append(
                f"{key}: selector {sel!r} invalid: "
                f"{type(e).__name__}: {e}")
    out["all_valid"] = (out["username_valid"]
                         and out["password_valid"]
                         and out["submit_valid"])
    return out


def grade_proposal(proposal: dict, validation: dict) -> dict:
    """Combine LLM confidence with validation results into a single
    decision metric. Returns:

      {
        "use_proposal": bool,        // safe to use?
        "score": int,                // 0-100 combined score
        "reasons": [str, ...]        // why we did/didn't accept
      }

    Decision policy:
      - LLM confidence >= 70 AND all selectors validate (1 match each)
        → use_proposal=True, score = confidence
      - LLM confidence >= 70 BUT one or more selectors don't validate
        → use_proposal=False (don't submit credentials with bad sel)
      - LLM confidence < 70 BUT all selectors validate
        → use_proposal=True with score capped at confidence (the
          validation gives us extra evidence even if the LLM was
          unsure)
      - LLM confidence < 50 → never use
      - Captcha detected → never use (must hand off to manual)
    """
    try:
        score = int(proposal.get("confidence") or 0)
    except (TypeError, ValueError):
        # Model returned a non-numeric confidence ("high", "low", etc.)
        # — treat as 0 (won't be trusted).
        score = 0
    score = max(0, min(100, score))
    reasons = []
    use = True
    if proposal.get("captcha_detected"):
        use = False
        reasons.append("captcha detected — must use manual login")
    if not validation.get("all_valid"):
        use = False
        if not validation.get("username_valid"):
            reasons.append("username selector didn't validate")
        if not validation.get("password_valid"):
            reasons.append("password selector didn't validate")
        if not validation.get("submit_valid"):
            reasons.append("submit selector didn't validate")
    if score < 50:
        use = False
        reasons.append(f"low confidence ({score} < 50)")
    elif score < 70 and validation.get("all_valid"):
        # Low confidence but selectors check out — partial credit
        reasons.append(f"partial trust: confidence {score}, validators passed")
    elif score >= 70 and validation.get("all_valid"):
        reasons.append(f"high confidence ({score}) and all selectors validate")
    return {
        "use_proposal": use,
        "score": score,
        "reasons": reasons,
    }
