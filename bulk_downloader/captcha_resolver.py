"""v3.43.39: Deeper captcha integration.

The existing captcha handling in runner.py covers Cloudflare
Turnstile only — sitekey extraction is Turnstile-specific, the
2captcha submission uses `method=turnstile` hardcoded, and there's
no support for reCAPTCHA v2/v3 or hCaptcha which are equally
common on the sites we scrape.

This module adds:

  1. **Type detection** — classify the visible captcha as Turnstile,
     reCAPTCHA v2, reCAPTCHA v3, hCaptcha, or unknown
  2. **Per-type solver dispatch** — each type has its own sitekey
     extraction, provider submission method, and token injection
     strategy
  3. **Stats per type and per provider** — feeds the existing
     `/captcha/stats` endpoint with much richer data so the user can
     see "Turnstile via 2captcha: 47/63 (74%), hCaptcha via
     capsolver: 12/14 (85%)"
  4. **Queue marker** — a job blocked on captcha gets `captcha_type`
     and `captcha_solver_state` fields in its job dict for UI display

## Why a separate module from runner

The existing handler is 80+ lines in runner.py with Turnstile-
specific logic. Adding 3 more captcha types means 4× the code, and
the runner is already 6,600+ lines. A dedicated module keeps the
captcha logic cohesive and unit-testable without spinning up a
real SiteRunner.

## Provider support matrix

  | Type        | 2captcha method  | capsolver type           |
  |-------------|------------------|--------------------------|
  | turnstile   | turnstile        | AntiTurnstileTaskProxyless |
  | recaptcha2  | userrecaptcha    | ReCaptchaV2TaskProxyless |
  | recaptcha3  | userrecaptcha    | ReCaptchaV3TaskProxyless |
  | hcaptcha    | hcaptcha         | HCaptchaTaskProxyless    |

## Token injection

Each captcha type has a different way to register the solved
token back into the page:

  - Turnstile: write to `<input name="cf-turnstile-response">`
    (or `cf-chl-widget-*`); trigger `window.turnstile.execute()`
    callbacks
  - reCAPTCHA v2: write to `<textarea name="g-recaptcha-response">`;
    if a `data-callback` attribute exists on the widget, fire it
  - reCAPTCHA v3: writeable via `grecaptcha.execute()` callback
    chain; injection requires a registered handler. Falls back
    to setting the hidden response field.
  - hCaptcha: write to `<textarea name="h-captcha-response">`;
    trigger `window.hcaptcha.execute()` callback if registered
"""
from __future__ import annotations

import time
from typing import Optional


# ── Type detection ────────────────────────────────────────────────────

# Each captcha type has signature DOM nodes — checked in order.
# First match wins, so order matters: most specific first.
_TYPE_SIGNATURES = [
    ("turnstile", [
        "iframe[src*='challenges.cloudflare.com']",
        "[data-sitekey][class*='turnstile']",
        ".cf-turnstile",
        "#cf-chl-widget",
        "div[id^='cf-chl-widget']",
    ]),
    ("hcaptcha", [
        "iframe[src*='hcaptcha.com']",
        ".h-captcha[data-sitekey]",
        ".h-captcha iframe",
    ]),
    ("recaptcha2", [
        ".g-recaptcha[data-sitekey]:not([data-size='invisible'])",
        "iframe[src*='recaptcha/api2/anchor']",
        ".g-recaptcha iframe",
    ]),
    ("recaptcha3", [
        # v3 widgets are invisible (data-size=invisible) or have
        # the v3 script tag — detect both
        ".g-recaptcha[data-size='invisible']",
        "script[src*='recaptcha/api.js?render=']",
    ]),
]


def detect_captcha_type(page) -> Optional[str]:
    """Identify which captcha (if any) is visible. Returns:
      "turnstile", "recaptcha2", "recaptcha3", "hcaptcha", or None.

    Uses short timeouts (300ms per selector) so this is cheap even
    when nothing's visible — runs on every login attempt.
    """
    for cap_type, selectors in _TYPE_SIGNATURES:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                # For invisible widgets (recaptcha3) being in the DOM
                # is enough; for visible ones we want is_visible().
                if cap_type == "recaptcha3":
                    return cap_type
                try:
                    if loc.is_visible(timeout=300):
                        return cap_type
                except Exception:
                    # Some selectors match nodes that don't support
                    # is_visible (script tags); count > 0 is enough
                    # to flag them
                    return cap_type
            except Exception:
                continue
    return None


# ── Sitekey extraction (per type) ────────────────────────────────────

def extract_sitekey(page, captcha_type: str) -> Optional[str]:
    """Find the sitekey for the current captcha. Different types
    store it differently:

      - turnstile: data-sitekey on .cf-turnstile OR iframe URL param
      - hcaptcha: data-sitekey on .h-captcha
      - recaptcha2: data-sitekey on .g-recaptcha
      - recaptcha3: data-sitekey on .g-recaptcha[data-size=invisible]
                     OR the `render=` query param of the api.js script

    Returns the sitekey or None if extraction fails.
    """
    if not captcha_type:
        return None
    try:
        if captcha_type == "turnstile":
            return page.evaluate("""
                () => {
                    const el = document.querySelector('.cf-turnstile[data-sitekey]')
                                || document.querySelector('[data-sitekey][class*="turnstile"]');
                    if (el) return el.getAttribute('data-sitekey');
                    const iframe = document.querySelector("iframe[src*='challenges.cloudflare.com']");
                    if (iframe) {
                        try {
                            const u = new URL(iframe.src);
                            return u.searchParams.get('sitekey');
                        } catch (e) {}
                    }
                    return null;
                }
            """)
        if captcha_type == "hcaptcha":
            return page.evaluate("""
                () => {
                    const el = document.querySelector('.h-captcha[data-sitekey]');
                    if (el) return el.getAttribute('data-sitekey');
                    const iframe = document.querySelector("iframe[src*='hcaptcha.com']");
                    if (iframe) {
                        try {
                            const u = new URL(iframe.src);
                            return u.searchParams.get('sitekey');
                        } catch (e) {}
                    }
                    return null;
                }
            """)
        if captcha_type == "recaptcha2":
            return page.evaluate("""
                () => {
                    const el = document.querySelector('.g-recaptcha[data-sitekey]');
                    return el ? el.getAttribute('data-sitekey') : null;
                }
            """)
        if captcha_type == "recaptcha3":
            return page.evaluate("""
                () => {
                    const el = document.querySelector('.g-recaptcha[data-sitekey]');
                    if (el) return el.getAttribute('data-sitekey');
                    const sc = document.querySelector("script[src*='recaptcha/api.js?render=']");
                    if (sc) {
                        try {
                            const u = new URL(sc.src);
                            return u.searchParams.get('render');
                        } catch (e) {}
                    }
                    return null;
                }
            """)
    except Exception:
        return None
    return None


# ── Provider submission ──────────────────────────────────────────────

# 2captcha's submission methods per type. Used in the `method`
# field on /in.php.
_2CAPTCHA_METHODS = {
    "turnstile": "turnstile",
    "recaptcha2": "userrecaptcha",
    "recaptcha3": "userrecaptcha",  # plus version=v3
    "hcaptcha": "hcaptcha",
}

# capsolver's taskType per captcha type.
_CAPSOLVER_TASK_TYPES = {
    "turnstile": "AntiTurnstileTaskProxyless",
    "recaptcha2": "ReCaptchaV2TaskProxyless",
    "recaptcha3": "ReCaptchaV3TaskProxyless",
    "hcaptcha": "HCaptchaTaskProxyless",
}


class _ProviderError(Exception):
    """Raised when a provider call fails — carries a short reason
    so the runner can log it usefully."""
    pass


def submit_2captcha(api_key: str, captcha_type: str, sitekey: str,
                     page_url: str, action: str = "",
                     timeout_s: float = 180.0) -> str:
    """Submit a captcha solve job to 2captcha.com and poll until
    solved. Returns the token string. Raises _ProviderError on
    submit failure or solve timeout.

    `action` is required for reCAPTCHA v3 (the "action" the page
    uses with `grecaptcha.execute(sitekey, {action: 'login'})`).
    """
    if not api_key or not isinstance(api_key, str):
        raise _ProviderError("api_key not configured")
    # Defensive: non-hashable types (list, dict) crash `captcha_type
    # in _2CAPTCHA_METHODS` with TypeError. Normalize early.
    if not isinstance(captcha_type, str) or not captcha_type:
        raise _ProviderError(f"unsupported captcha type: {captcha_type!r}")
    if captcha_type not in _2CAPTCHA_METHODS:
        raise _ProviderError(f"unsupported captcha type: {captcha_type}")
    import httpx as _httpx
    submit_data = {
        "key": api_key,
        "method": _2CAPTCHA_METHODS[captcha_type],
        "sitekey": sitekey,
        "pageurl": page_url,
        "json": "1",
    }
    if captcha_type == "recaptcha3":
        submit_data["version"] = "v3"
        if action:
            submit_data["action"] = action
        submit_data["min_score"] = "0.5"
    try:
        r = _httpx.post("https://2captcha.com/in.php",
                        data=submit_data, timeout=20)
        d = r.json()
    except Exception as e:
        raise _ProviderError(f"submit failed: {type(e).__name__}: {e}")
    if d.get("status") != 1:
        raise _ProviderError(f"submit rejected: {d.get('request')}")
    request_id = d["request"]
    # Poll for result. 2captcha typical solve time 15-90s depending
    # on type. Turnstile is fastest; hCaptcha slowest.
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(5)
        try:
            r = _httpx.get("https://2captcha.com/res.php", params={
                "key": api_key, "action": "get",
                "id": request_id, "json": "1",
            }, timeout=15)
            d = r.json()
        except Exception as e:
            raise _ProviderError(f"poll failed: {type(e).__name__}: {e}")
        if d.get("status") == 1:
            return d["request"]
        # status=0 with request=CAPCHA_NOT_READY means keep polling
        req = d.get("request", "")
        if req == "CAPCHA_NOT_READY":
            continue
        # Any other status=0 response is a hard failure
        raise _ProviderError(f"solve failed: {req}")
    raise _ProviderError(f"timeout after {timeout_s}s")


def submit_capsolver(api_key: str, captcha_type: str, sitekey: str,
                      page_url: str, action: str = "",
                      timeout_s: float = 180.0) -> str:
    """Submit a captcha solve job to capsolver.com and poll until
    solved. Returns the token. Raises _ProviderError on failure.

    Capsolver API is different from 2captcha — JSON throughout,
    taskType per captcha kind, and tokens come back as
    `gRecaptchaResponse` / `token` depending on type."""
    if not api_key or not isinstance(api_key, str):
        raise _ProviderError("api_key not configured")
    # Defensive: non-string types crash dict membership check below.
    if not isinstance(captcha_type, str) or not captcha_type:
        raise _ProviderError(f"unsupported captcha type: {captcha_type!r}")
    if captcha_type not in _CAPSOLVER_TASK_TYPES:
        raise _ProviderError(f"unsupported captcha type: {captcha_type}")
    import httpx as _httpx
    task = {
        "type": _CAPSOLVER_TASK_TYPES[captcha_type],
        "websiteURL": page_url,
        "websiteKey": sitekey,
    }
    if captcha_type == "recaptcha3":
        task["pageAction"] = action or "verify"
        task["minScore"] = 0.5
    try:
        r = _httpx.post("https://api.capsolver.com/createTask",
                        json={"clientKey": api_key, "task": task},
                        timeout=20)
        d = r.json()
    except Exception as e:
        raise _ProviderError(f"submit failed: {type(e).__name__}: {e}")
    if d.get("errorId", 1) != 0:
        raise _ProviderError(
            f"submit rejected: {d.get('errorDescription') or d.get('errorCode')}")
    task_id = d.get("taskId")
    if not task_id:
        raise _ProviderError("submit returned no taskId")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(5)
        try:
            r = _httpx.post("https://api.capsolver.com/getTaskResult",
                            json={"clientKey": api_key, "taskId": task_id},
                            timeout=15)
            d = r.json()
        except Exception as e:
            raise _ProviderError(f"poll failed: {type(e).__name__}: {e}")
        if d.get("errorId", 1) != 0:
            raise _ProviderError(
                f"poll error: {d.get('errorDescription')}")
        status = d.get("status")
        if status == "ready":
            sol = d.get("solution") or {}
            # Different types return different fields; check all
            token = (sol.get("token")
                      or sol.get("gRecaptchaResponse")
                      or sol.get("captchaResponse"))
            if not token:
                raise _ProviderError("solution had no token field")
            return token
        if status == "processing":
            continue
        raise _ProviderError(f"unexpected status: {status}")
    raise _ProviderError(f"timeout after {timeout_s}s")


def solve(provider: str, api_key: str, captcha_type: str,
            sitekey: str, page_url: str, action: str = "",
            timeout_s: float = 180.0) -> str:
    """Dispatch to the configured provider's solver. Returns the
    token. Raises _ProviderError on any failure.

    Used by the runner — single entry point so the runner doesn't
    have to know which provider is which.

    Defensive: non-string `provider` is rejected before reaching
    .strip() — a cfg corrupted to provider=int would otherwise
    crash with AttributeError."""
    if not isinstance(provider, str):
        # Empty / non-string → fall through to the default
        provider = "2captcha"
    provider = (provider or "2captcha").strip().lower()
    if provider == "2captcha":
        return submit_2captcha(api_key, captcha_type, sitekey,
                                 page_url, action=action,
                                 timeout_s=timeout_s)
    if provider == "capsolver":
        return submit_capsolver(api_key, captcha_type, sitekey,
                                  page_url, action=action,
                                  timeout_s=timeout_s)
    raise _ProviderError(f"unknown provider: {provider}")


# ── Token injection (per type) ──────────────────────────────────────

# Injection JS per type. Each function gets the token; the JS finds
# the right hidden field and any registered callback.
_INJECTION_JS = {
    "turnstile": """
        (token) => {
            // Set the hidden response field
            const inputs = document.querySelectorAll(
                'input[name="cf-turnstile-response"], input[name="cf-chl-widget-response"]');
            inputs.forEach(el => el.value = token);
            // Trigger registered callbacks via window.turnstile
            if (window.turnstile && typeof window.turnstile.execute === 'function') {
                try {
                    const wrappers = document.querySelectorAll('.cf-turnstile');
                    wrappers.forEach(w => {
                        const cb = w.getAttribute('data-callback');
                        if (cb && typeof window[cb] === 'function') {
                            try { window[cb](token); } catch (e) {}
                        }
                    });
                } catch (e) {}
            }
            return inputs.length;
        }
    """,
    "recaptcha2": """
        (token) => {
            const ta = document.querySelector('textarea[name="g-recaptcha-response"]');
            if (ta) {
                ta.value = token;
                // Some sites poll for the value rather than relying on
                // callback; setting it is usually enough
                ta.style.display = '';
            }
            // Fire registered callbacks
            const widget = document.querySelector('.g-recaptcha[data-callback]');
            if (widget) {
                const cb = widget.getAttribute('data-callback');
                if (cb && typeof window[cb] === 'function') {
                    try { window[cb](token); } catch (e) {}
                }
            }
            return ta ? 1 : 0;
        }
    """,
    "recaptcha3": """
        (token) => {
            // v3 has no hidden field by default; sites usually call
            // grecaptcha.execute() and pass the result. We override
            // grecaptcha.execute to return our token immediately so
            // the next site call gets the solved token.
            try {
                if (window.grecaptcha) {
                    const orig = window.grecaptcha.execute;
                    window.grecaptcha.execute = function() {
                        return Promise.resolve(token);
                    };
                }
            } catch (e) {}
            // Also set any hidden field the site might have created
            const ta = document.querySelector('textarea[name="g-recaptcha-response"]');
            if (ta) ta.value = token;
            return 1;
        }
    """,
    "hcaptcha": """
        (token) => {
            const ta = document.querySelector('textarea[name="h-captcha-response"]');
            if (ta) ta.value = token;
            // Fire registered hcaptcha callback if present
            const widget = document.querySelector('.h-captcha[data-callback]');
            if (widget) {
                const cb = widget.getAttribute('data-callback');
                if (cb && typeof window[cb] === 'function') {
                    try { window[cb](token); } catch (e) {}
                }
            }
            return ta ? 1 : 0;
        }
    """,
}


def inject_token(page, captcha_type: str, token: str) -> bool:
    """Inject the solved token into the page. Returns True if at
    least one target field was found and set."""
    if not (captcha_type and token):
        return False
    js = _INJECTION_JS.get(captcha_type)
    if not js:
        return False
    try:
        count = page.evaluate(js, token)
        return bool(count)
    except Exception:
        return False


# ── Stats tracking ───────────────────────────────────────────────────

class CaptchaStats:
    """Per-(type, provider) running stats. Used by the runner's
    existing _captcha_stats counter, augmented to track per-type
    success rates.

    Shape: {type: {provider: {submitted, solved, failed, timeouts}}}
    """
    def __init__(self):
        self._by_type_provider: dict = {}

    def record_submission(self, captcha_type: str, provider: str):
        key = self._key(captcha_type, provider)
        key.setdefault("submitted", 0)
        key["submitted"] += 1

    def record_solved(self, captcha_type: str, provider: str,
                        elapsed_s: float):
        key = self._key(captcha_type, provider)
        key.setdefault("solved", 0)
        key["solved"] += 1
        # Running average of solve time
        key.setdefault("total_solve_time_s", 0.0)
        key["total_solve_time_s"] += elapsed_s

    def record_failed(self, captcha_type: str, provider: str,
                        reason: str = ""):
        key = self._key(captcha_type, provider)
        key.setdefault("failed", 0)
        key["failed"] += 1
        if reason:
            key["last_failure"] = reason[:200]
            key["last_failure_at"] = time.time()

    def record_timeout(self, captcha_type: str, provider: str):
        key = self._key(captcha_type, provider)
        key.setdefault("timeouts", 0)
        key["timeouts"] += 1

    def _key(self, captcha_type: str, provider: str) -> dict:
        captcha_type = captcha_type or "unknown"
        provider = provider or "unknown"
        per_type = self._by_type_provider.setdefault(captcha_type, {})
        return per_type.setdefault(provider, {})

    def snapshot(self) -> dict:
        """Return a deep copy of stats with derived fields
        (success_rate, avg_solve_time) included for the UI."""
        out = {}
        for cap_type, providers in self._by_type_provider.items():
            type_data = {}
            for prov, raw in providers.items():
                entry = dict(raw)
                submitted = entry.get("submitted", 0)
                solved = entry.get("solved", 0)
                if submitted > 0:
                    entry["success_rate"] = round(
                        (solved / submitted) * 100.0, 1)
                else:
                    entry["success_rate"] = None
                if solved > 0:
                    entry["avg_solve_time_s"] = round(
                        entry.get("total_solve_time_s", 0.0) / solved, 1)
                else:
                    entry["avg_solve_time_s"] = None
                # Drop the running accumulator from the output —
                # only the derived field is interesting
                entry.pop("total_solve_time_s", None)
                type_data[prov] = entry
            out[cap_type] = type_data
        return out
