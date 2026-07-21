"""runner_challenge -- captcha/turnstile detection + solve handoff

Extracted from runner.py (SiteRunner) @v3.66.399, PHASE 3 runner cut 3.
Mixin: methods reference self.* only; NO __init__. Import block derived by AST
free-name scan of the moved bodies (not the seams doc, which models module-top
imports only). Cycle rule: imports nothing from .runner.
"""
import collections, sys, threading, time

from .db import db_log


class ChallengeMixin:
    def _record_captcha_encounter(self, now=None):
        """Record one detected challenge and retain exactly 24 hours."""
        now = time.time() if now is None else float(now)
        encounters = getattr(self, "_captcha_encounters", None)
        if encounters is None:
            encounters = collections.deque()
            self._captcha_encounters = encounters
        encounters_lock = getattr(self, "_captcha_encounters_lock", None)
        if encounters_lock is None:
            encounters_lock = threading.Lock()
            self._captcha_encounters_lock = encounters_lock
        cutoff = now - 86400
        with encounters_lock:
            while encounters and float(encounters[0]) < cutoff:
                encounters.popleft()
            encounters.append(now)

    def _handle_captcha_check(self, page, url):
        """Phase 7.2: detect a visible captcha on the current page, try
        the Turnstile auto-solver (Phase 15.10), then the yt-dlp CDN
        fallback (Phase 61), then finally route to needs_review.

        v3.43.39: now type-aware. The needs_review job gets a
        `captcha_type` field so the UI can show "blocked: hCaptcha"
        instead of generic "captcha challenge".

        Returns True to continue normal processing (no captcha, or it
        was solved cleanly), False if the URL was handled (caller
        should return). Behavior identical to the inline block
        extracted from _process_one in v3.43.18."""
        if not self._has_captcha(page):
            return True
        self._record_captcha_encounter()
        # v3.43.39: detect type up-front for stat tracking + UI surface.
        # Falls back to "unknown" if the resolver can't classify.
        try:
            from . import captcha_resolver as _cr
            detected_type = _cr.detect_captcha_type(page) or "unknown"
        except Exception:
            detected_type = "unknown"
        # Try the auto-solver if configured.
        solved = self._try_captcha_solve(page)
        if solved:
            self.log_event("captcha",
                f"{detected_type} solved via API; continuing", url=url)
            time.sleep(1.5)
            if not self._has_captcha(page):
                return True  # widget gone, proceed normally
            # Token injected but page still showing captcha; wait for resolve
            time.sleep(3.0)
        if not self._has_captcha(page):
            return True
        # Phase 61: yt-dlp CDN fallback before marking needs_review.
        ok, msg, fn, sz = self._try_ytdlp_fallback(url, "captcha challenge")
        if ok:
            self._update_job(url, "done", msg, filename=fn or "", file_size=sz)
            db_log(self.site_id, self.config.get("name","?"), url, "done", fn or "", sz, msg)
            return False
        # C6 (8.4): gallery-dl fallback after yt-dlp -- covers sites gallery-dl
        # handles that yt-dlp doesn't. Opt-in per site (use_gallerydl_fallback).
        ok, msg, fn, sz = self._try_gallerydl_fallback(url, "captcha challenge")
        if ok:
            self._update_job(url, "done", msg, filename=fn or "", file_size=sz)
            db_log(self.site_id, self.config.get("name","?"), url, "done", fn or "", sz, msg)
            return False
        ss = self._screenshot(page, url)
        # v3.43.39: include the detected captcha type so the queue UI
        # can surface it as a badge. Falls back gracefully if the
        # resolver isn't available.
        captcha_msg = (f"Captcha challenge detected ({detected_type}) "
                        f"— use 🧠 Take over to solve it manually")
        self._update_job(url, "needs_review", captcha_msg,
            screenshot=ss, captcha_type=detected_type)
        db_log(self.site_id, self.config.get("name","?"), url, "needs_review",
               "", 0, f"captcha challenge: {detected_type}", ss)
        return False
    def _has_captcha(self,page,timeout_ms=500):
        """Phase 7.2: quick check for visible captcha widgets on the page.
        Returns True if any of the CAPTCHA_SELECTORS are visible. Short
        timeout because we're checking the rendered page state, not
        waiting for one to appear."""
        from .constants import CAPTCHA_SELECTORS
        for sel in CAPTCHA_SELECTORS:
            try:
                loc=page.locator(sel).first
                if loc.count()>0 and loc.is_visible(timeout=timeout_ms):
                    sys.stderr.write(f"  captcha detected via [{sel}]\n")
                    return True
            except Exception: continue
        return False
    def _try_turnstile_solve(self, page):
        """Backward-compat alias for _try_captcha_solve. The name
        is retained because Phase 7.2 + Phase 15.10 callsites
        reference it; new code should call _try_captcha_solve
        directly for clarity."""
        return self._try_captcha_solve(page)
    def _try_captcha_solve(self, page):
        """v3.43.39: type-aware captcha solving. Detects whether
        the visible captcha is Turnstile / reCAPTCHA v2 / v3 / hCaptcha,
        then dispatches to the configured provider with the right
        sitekey extraction and token injection for that type.

        Returns True when a token was successfully injected; False
        otherwise. Failures are logged but non-fatal — the caller
        falls through to manual takeover or yt-dlp fallback.

        Configured via:
          captcha_provider — "2captcha" (default) or "capsolver"
          captcha_api_key  — provider API key

        Stats land in `self._captcha_stats` (legacy total) AND in
        the new per-type breakdown via `self._captcha_resolver_stats`.
        Both are exposed via /api/sites/<sid>/captcha/stats."""
        api_key = (self.config.get("captcha_api_key") or "").strip()
        if not api_key:
            return False
        provider = (self.config.get("captcha_provider") or "2captcha").strip().lower()
        try:
            from . import captcha_resolver as _cr
        except Exception as e:
            self.log_event("captcha",
                f"captcha_resolver import failed: {str(e)[:80]}")
            return False
        # Step 1: detect type
        captcha_type = _cr.detect_captcha_type(page)
        if not captcha_type:
            # No captcha visible right now — nothing to solve.
            return False
        # Step 2: extract sitekey
        sitekey = _cr.extract_sitekey(page, captcha_type)
        if not sitekey:
            self.log_event("captcha",
                f"No sitekey found for {captcha_type}; cannot auto-solve")
            return False
        # Lazily initialize the per-type stats tracker
        if not hasattr(self, "_captcha_resolver_stats"):
            self._captcha_resolver_stats = _cr.CaptchaStats()
        page_url = page.url
        action = ""  # reCAPTCHA v3 only; runner doesn't track per-page
                      # action names yet, so we default to provider's
                      # fallback ("verify")
        self._captcha_stats["submitted"] += 1
        self._captcha_resolver_stats.record_submission(captcha_type, provider)
        started = time.time()
        self.log_event("captcha",
            f"Submitting {captcha_type} (sitekey {sitekey[:20]}…) "
            f"to {provider}")
        try:
            token = _cr.solve(provider, api_key, captcha_type,
                                sitekey, page_url, action=action)
        except _cr._ProviderError as e:
            msg = f"{captcha_type} solve via {provider} failed: {e}"
            self._captcha_stats["failed"] += 1
            self._captcha_stats["last_failure"] = msg[:200]
            self._captcha_stats["last_failure_at"] = time.time()
            is_timeout = "timeout" in str(e).lower()
            if is_timeout:
                self._captcha_stats["timeouts"] += 1
                self._captcha_resolver_stats.record_timeout(
                    captcha_type, provider)
            else:
                self._captcha_resolver_stats.record_failed(
                    captcha_type, provider, str(e))
            self.log_event("captcha", msg)
            return False
        except Exception as e:
            msg = f"solver error: {type(e).__name__}: {str(e)[:80]}"
            self._captcha_stats["failed"] += 1
            self._captcha_stats["last_failure"] = msg[:200]
            self._captcha_resolver_stats.record_failed(
                captcha_type, provider, msg)
            self.log_event("captcha", msg)
            return False
        # Step 3: inject the token
        injected = _cr.inject_token(page, captcha_type, token)
        if not injected:
            msg = f"token injection failed for {captcha_type}"
            self._captcha_stats["failed"] += 1
            self._captcha_resolver_stats.record_failed(
                captcha_type, provider, msg)
            self.log_event("captcha", msg)
            return False
        elapsed = time.time() - started
        solve_ms = int(elapsed * 1000)
        self._captcha_stats["solved"] += 1
        self._captcha_stats["total_solve_time_ms"] += solve_ms
        self._captcha_stats["last_solve_time_ms"] = solve_ms
        self._captcha_stats["last_success_at"] = time.time()
        self._captcha_resolver_stats.record_solved(
            captcha_type, provider, elapsed)
        self.log_event("captcha",
            f"{captcha_type} solved in {elapsed:.1f}s "
            f"({len(token)}-char token injected)")
        return True
    def _try_turnstile_solve_LEGACY(self, page):
        """Phase 15.10: solve a Cloudflare Turnstile challenge via 2captcha.

        Configured per-site via:
          captcha_provider — "2captcha" (default) or "capsolver"
          captcha_api_key  — provider API key

        Off when no key is configured. Returns True if a token was
        successfully injected, False otherwise. Failures are logged but
        non-fatal — caller falls through to manual takeover.

        How it works:
          1. Find the Turnstile widget's sitekey via DOM inspection
          2. POST {sitekey, pageurl, key} to provider's `/in.php`
          3. Poll provider's `/res.php` every ~5s until solved (or 120s timeout)
          4. Inject the token into the cf-turnstile-response hidden input
          5. Trigger any registered window.turnstile callbacks

        Note: as of late 2025, 2captcha's Turnstile bypass works on roughly
        70% of sites — Cloudflare actively rotates challenge variants. Treat
        this as a probabilistic helper, not a guaranteed bypass."""
        api_key = (self.config.get("captcha_api_key") or "").strip()
        if not api_key: return False
        provider = (self.config.get("captcha_provider") or "2captcha").strip().lower()
        # Find the Turnstile widget's sitekey. It lives in data-sitekey on
        # an element with class cf-turnstile, OR in the iframe src as a
        # query param (?sitekey=...).
        try:
            sitekey = page.evaluate("""
                () => {
                    const el = document.querySelector('[data-sitekey]');
                    if (el) return el.getAttribute('data-sitekey');
                    const iframe = document.querySelector("iframe[src*='challenges.cloudflare.com']");
                    if (iframe) {
                        try {
                            const u = new URL(iframe.src);
                            const k = u.searchParams.get('sitekey');
                            if (k) return k;
                        } catch (e) {}
                    }
                    return null;
                }
            """)
        except Exception as e:
            self.log_event("captcha", f"sitekey extraction failed: {str(e)[:80]}")
            return False
        if not sitekey:
            self.log_event("captcha", "No Turnstile sitekey found on page; cannot auto-solve")
            return False
        page_url = page.url
        # Phase 32: track this submission and time it. Stats are visible
        # via /api/sites/<sid>/captcha/stats so the operator can see the
        # success rate of the configured provider for this site.
        self._captcha_stats["submitted"] += 1
        _captcha_started = time.time()
        def _captcha_record_failure(msg):
            self._captcha_stats["failed"] += 1
            self._captcha_stats["last_failure"] = msg[:200]
            self._captcha_stats["last_failure_at"] = time.time()
        def _captcha_record_timeout(msg):
            self._captcha_stats["timeouts"] += 1
            self._captcha_stats["last_failure"] = msg[:200]
            self._captcha_stats["last_failure_at"] = time.time()
        self.log_event("captcha", f"Submitting Turnstile sitekey {sitekey[:20]}… to {provider}")
        try:
            import httpx as _httpx
            if provider == "2captcha":
                # Submit job
                r = _httpx.post("https://2captcha.com/in.php", data={
                    "key": api_key, "method": "turnstile",
                    "sitekey": sitekey, "pageurl": page_url, "json": "1",
                }, timeout=20)
                d = r.json()
                if d.get("status") != 1:
                    msg = f"2captcha submit failed: {d.get('request')}"
                    self.log_event("captcha", msg); _captcha_record_failure(msg); return False
                request_id = d["request"]
                # Poll for result. Typical solve time 15-45s.
                deadline = time.time() + 180
                while time.time() < deadline:
                    time.sleep(5)
                    r = _httpx.get("https://2captcha.com/res.php", params={
                        "key": api_key, "action": "get",
                        "id": request_id, "json": "1",
                    }, timeout=15)
                    d = r.json()
                    if d.get("status") == 1:
                        token = d["request"]
                        break
                    if d.get("request") != "CAPCHA_NOT_READY":
                        msg = f"2captcha error: {d.get('request')}"
                        self.log_event("captcha", msg); _captcha_record_failure(msg); return False
                else:
                    msg = "2captcha timed out after 180s"
                    self.log_event("captcha", msg); _captcha_record_timeout(msg); return False
            elif provider == "capsolver":
                # CapSolver — simpler /createTask + /getTaskResult flow
                r = _httpx.post("https://api.capsolver.com/createTask", json={
                    "clientKey": api_key,
                    "task": {"type": "AntiTurnstileTaskProxyLess",
                             "websiteURL": page_url, "websiteKey": sitekey},
                }, timeout=20)
                d = r.json()
                task_id = d.get("taskId")
                if not task_id:
                    msg = f"capsolver createTask failed: {d.get('errorDescription')}"
                    self.log_event("captcha", msg); _captcha_record_failure(msg); return False
                deadline = time.time() + 180
                while time.time() < deadline:
                    time.sleep(5)
                    r = _httpx.post("https://api.capsolver.com/getTaskResult",
                                    json={"clientKey": api_key, "taskId": task_id}, timeout=15)
                    d = r.json()
                    if d.get("status") == "ready":
                        token = d.get("solution", {}).get("token")
                        if token: break
                    if d.get("status") == "failed":
                        msg = f"capsolver failed: {d.get('errorDescription')}"
                        self.log_event("captcha", msg); _captcha_record_failure(msg); return False
                else:
                    msg = "capsolver timed out after 180s"
                    self.log_event("captcha", msg); _captcha_record_timeout(msg); return False
            else:
                msg = f"Unknown captcha provider: {provider}"
                self.log_event("captcha", msg); _captcha_record_failure(msg); return False
        except Exception as e:
            msg = f"solver API error: {str(e)[:120]}"
            self.log_event("captcha", msg); _captcha_record_failure(msg); return False
        # Inject the token. Two ways to deliver it: the hidden input AND
        # the window.turnstile callback registry. Belt-and-suspenders.
        try:
            page.evaluate(f"""
                (token) => {{
                    // Hidden input (covers most submit-form integrations)
                    const inp = document.querySelector("input[name='cf-turnstile-response']");
                    if (inp) {{ inp.value = token; }}
                    // Also try the JS callback path
                    if (window.turnstile && typeof window.turnstile.execute === 'function') {{
                        try {{
                            const widget = document.querySelector('[data-sitekey]');
                            if (widget && widget.dataset.callback && window[widget.dataset.callback]) {{
                                window[widget.dataset.callback](token);
                            }}
                        }} catch (e) {{}}
                    }}
                }}
            """, token)
            self.log_event("captcha", f"Token injected ({len(token)} chars)")
            # Phase 32: mark success + record solve time
            solve_ms = int((time.time() - _captcha_started) * 1000)
            self._captcha_stats["solved"] += 1
            self._captcha_stats["total_solve_time_ms"] += solve_ms
            self._captcha_stats["last_solve_time_ms"] = solve_ms
            self._captcha_stats["last_success_at"] = time.time()
            return True
        except Exception as e:
            msg = f"token injection failed: {str(e)[:80]}"
            self.log_event("captcha", msg); _captcha_record_failure(msg); return False
