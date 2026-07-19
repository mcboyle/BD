"""runner_telemetry -- events/log, mirror pick, error classify/handle/screenshot (owns _RETRY_DELAYS_BY_KIND)

Extracted from runner.py (SiteRunner) @v3.66.401, PHASE 3 runner cut 4.
Mixin: methods reference self.* only; NO __init__. Import block derived by AST
free-name scan of the moved bodies. Cycle rule: kernel from .runner_util,
nothing from .runner.
"""
import re, sys, threading, time

from .runner_util import _BD_TO_APPRISE_EVENT
from .db import db_log
from .constants import RETRY_DELAYS

# httpx soft import (moved verbatim from runner.py; flat sibling).
try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False


class TelemetryMixin:
    def _fmt_dur(self, sec):
        """Format seconds as a short human-readable duration string.
        Truncates to the largest meaningful unit: '90s' → '1m',
        '7200s' → '2h', '172800s' → '2d'. Used in status messages
        like 'next in 1h if it fails again'."""
        if sec >= 86400: return f"{sec//86400}d"
        if sec >= 3600:  return f"{sec//3600}h"
        if sec >= 60:    return f"{sec//60}m"
        return f"{sec}s"
    def log_event(self, kind, message, url=None, extra=None):
        """Append a structured event to the in-memory log. Mirrored to
        stderr so the existing console output is unchanged. Caller passes:
          kind     — short tag like 'state', 'login', 'download', 'js_error',
                     'network', 'retry', 'takeover'. Used for filtering.
          message  — human-readable one-liner
          url      — optional URL this event relates to (for per-URL view)
          extra    — optional dict of structured data (e.g. {bytes, status})

        The buffer is bounded (deque maxlen=500) so old events drop off
        automatically. Each event gets a monotonically-increasing seq id
        so the UI can request "all events after seq N" for incremental
        polling."""
        # Phase 42: atomic counter; safe across worker threads + auto-retry
        # scanner + teach commits, none of which hold a shared lock.
        seq = next(self._event_seq_counter)
        self._event_seq = seq
        ev = {
            "seq": seq,
            "ts": time.time(),
            "kind": kind,
            "message": str(message)[:500],
            "url": url,
            "extra": extra or {},
        }
        self._event_log.append(ev)
        # Mirror to stderr so console behavior is unchanged.
        # v3.43.24: include site_id in the prefix so multi-site logs are
        # filterable with grep. Format: [site_id][kind] {message}, with
        # URL appended when present. Previously the kind alone was
        # ambiguous when 3+ sites were active.
        prefix = f"  [{self.site_id}][{kind}]"
        if url:
            prefix += f" {url[:60]}:"
        sys.stderr.write(f"{prefix} {ev['message']}\n")
        # v3.43.34: push to SSE subscribers. No throttle key — every
        # event is meaningful (these are user-facing log entries, not
        # progress noise). The broker drops oldest when subscriber
        # queues are full, so a slow client doesn't back-pressure the
        # publisher.
        try:
            from . import sse_broker as _sse
            _sse.publish("event_log", {
                "site_id": self.site_id,
                "seq": seq,
                "ts": ev["ts"],
                "kind": kind,
                "message": ev["message"],
                "url": url,
            })
        except Exception:
            pass
        # v3.43.70: also fire apprise notification when this event kind
        # maps to one of the canonical apprise event types. The
        # dispatcher handles enable check, URL list, batching, and
        # rate limiting internally — we just call notify() and forget.
        # Fail-open: any exception is swallowed (notifications are
        # never allowed to break the worker).
        try:
            from . import notify_apprise as _napp
            apprise_event = _BD_TO_APPRISE_EVENT.get(kind)
            if apprise_event is not None:
                site_name = self.config.get("name", self.site_id)
                title = f"[{site_name}] {kind}"
                body = ev["message"]
                if url:
                    body = f"{url}\n{body}"
                _napp.get_dispatcher().notify(apprise_event, title, body,
                                              site_id=self.site_id, url=url)
        except Exception:
            pass
        # v3.43.71: also record into the Telegram bot's recent-events ring
        # so /recent has data. Fail-open — bot module may not be
        # importable.
        try:
            from . import tg_bot as _tg_bot
            _tg_bot.get_bot().record_event(
                kind, ev["message"], site_id=self.site_id)
        except Exception:
            pass
        return ev
    def get_events(self, after_seq=0, limit=200, url_filter=None, kind_filter=None):
        """Return events with seq > after_seq, optionally filtered by URL
        or kind. Used by /api/sites/<sid>/events for polling."""
        result = []
        # Snapshot the deque to avoid concurrent-modification issues. deque
        # iteration during append is technically safe in CPython but we'd
        # still see partial states.
        snapshot = list(self._event_log)
        for ev in snapshot:
            if ev["seq"] <= after_seq: continue
            if url_filter and ev.get("url") != url_filter: continue
            if kind_filter and ev["kind"] != kind_filter: continue
            result.append(ev)
            if len(result) >= limit: break
        return result
    def _install_event_listeners(self, page, url):
        """Phase 13.5/13.6: hook page-level Playwright events to capture
        JS errors and (optionally) network activity. Called from _process_one
        right after page creation. Listeners are GC'd with the page."""
        # JS error capture (always on — cheap and very high-signal for
        # debugging sites whose own JS broke). pageerror fires on uncaught
        # exceptions in the page's scripts.
        try:
            page.on("pageerror", lambda exc: self.log_event(
                "js_error", str(exc)[:300], url=url))
        except Exception:
            pass
        # Network request log (optional, controlled by per-site config).
        # Off by default because it can be very chatty; users opt in for
        # specific sites where direct extraction is failing.
        if self.config.get("log_network", False):
            try:
                def _on_response(response):
                    try:
                        # Only log non-image/font/css requests by default
                        # to keep noise down. Users can spot the actual
                        # video URL in this log.
                        rt = (response.request.resource_type or "").lower()
                        if rt in ("image", "font", "stylesheet"): return
                        ct = response.headers.get("content-type", "")
                        size = response.headers.get("content-length", "")
                        self.log_event("network",
                            f"{response.status} {response.request.method} {response.url[:120]}",
                            url=url,
                            extra={"status": response.status, "ct": ct[:60],
                                   "size": size, "rt": rt})
                    except Exception: pass
                page.on("response", _on_response)
            except Exception:
                pass

        # F9/F10 detect-side (live): observe which fingerprinting APIs the
        # page ACTUALLY INVOKES at runtime, and report them. Off by default
        # (config flag). This RECORDS that the page acted on the browser —
        # it wraps the fingerprinting getters to set a flag and then calls
        # through to the original, so return values are UNCHANGED. It does
        # not spoof, randomize, or evaluate which spoof would pass: that
        # would be evasion, which stays declined. Pure record-and-report.
        if self.config.get("detect_fingerprinting", False):
            try:
                page.add_init_script("""
                (() => {
                  const fp = (window.__bd_fp = window.__bd_fp || {});
                  const mark = (k) => { fp[k] = (fp[k] || 0) + 1; };
                  try {
                    const c = HTMLCanvasElement.prototype;
                    const td = c.toDataURL;
                    c.toDataURL = function(){ mark('canvas'); return td.apply(this, arguments); };
                    const ctx = CanvasRenderingContext2D.prototype;
                    const gi = ctx.getImageData;
                    ctx.getImageData = function(){ mark('canvas'); return gi.apply(this, arguments); };
                  } catch(e){}
                  try {
                    const w = WebGLRenderingContext.prototype;
                    const gp = w.getParameter;
                    w.getParameter = function(){ mark('webgl'); return gp.apply(this, arguments); };
                  } catch(e){}
                  try {
                    const desc = Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver');
                    Object.defineProperty(Navigator.prototype, 'webdriver', {
                      get(){ mark('webdriver_probe'); return desc ? desc.get.call(this) : undefined; }
                    });
                  } catch(e){}
                  try {
                    const A = window.AudioContext || window.webkitAudioContext;
                    if (A && A.prototype.createOscillator) {
                      const co = A.prototype.createOscillator;
                      A.prototype.createOscillator = function(){ mark('audio'); return co.apply(this, arguments); };
                    }
                  } catch(e){}
                })();
                """)
            except Exception:
                pass
    def _flush_fingerprint_observation(self, page, url):
        """Read back which fingerprinting APIs the page invoked (set by the
        init-script from _install_event_listeners) and log them. Safe to
        call even when detection is off — it no-ops if the marker is
        absent. Detect-and-report only."""
        if not self.config.get("detect_fingerprinting", False):
            return
        try:
            fp = page.evaluate("() => window.__bd_fp || {}")
        except Exception:
            return
        apis = sorted(k for k, v in (fp or {}).items() if v)
        if apis:
            self.log_event(
                "fingerprint_observed",
                f"page invoked fingerprinting APIs: {', '.join(apis)} "
                "— automation may be profiled; manual download recommended",
                url=url, extra={"apis": apis})
    def _parse_hm(self, s, default_min):
        """Parse 'HH:MM' to minutes-since-midnight. On error, return default."""
        try:
            h, m = str(s).split(":", 1)
            return int(h) * 60 + int(m)
        except Exception:
            return default_min
    def _extract_host(self, url):
        """Return just the hostname for log messages."""
        try:
            from urllib.parse import urlparse
            return urlparse(url).hostname or url[:30]
        except Exception:
            return url[:30]
    def _pick_fastest_mirror(self, file_url):
        """Phase 69 (v3.41.0): speculative mirror failover. Fire concurrent
        HEAD requests against the primary URL and all mirrors; return the
        first one that responds successfully (HTTP 200/206/302).

        Cancels remaining requests by closing their connections — httpx
        handles this via context manager exit. The race finishes within
        the slowest mirror's TTFB (typically <500ms) which is much
        cheaper than retrying-on-failure (which only switches after the
        primary times out or 5xxs).

        Opt-in via config['speculative_mirror_select'] (default False).
        Returns the chosen URL (which may be the original)."""
        if not self.config.get("speculative_mirror_select", False):
            return file_url
        mirrors = self._build_mirror_urls(file_url)
        if not mirrors:
            return file_url
        candidates = [file_url] + mirrors
        import concurrent.futures as _cf
        winner = {"url": file_url, "lock": threading.Lock()}

        def probe(url):
            # AUDIT FIX (v3.42.0): validate scheme before sending. While
            # the URLs come from user config (so SSRF requires write
            # access to config), an attacker who got write to
            # mirror_subdomains could redirect us at file://, gopher://,
            # internal IPs, etc. httpx is permissive about schemes by
            # default — restrict to http/https.
            try:
                from urllib.parse import urlparse as _up
                parsed = _up(url)
                scheme = (parsed.scheme or "").lower()
                if scheme not in ("http", "https"):
                    return
                # v3.66.551 (F-RUN03-04): the scheme allowlist alone let an http(s)
                # URL at an internal address through. Also refuse a non-global target
                # (loopback/private/link-local/CGNAT) via the single canonical SSRF
                # predicate before the HEAD -- same skip convention as a bad scheme.
                from bulk_downloader.provider_resolve_impl._common import (
                    _is_safe_public_host,
                )
                _ok, _why = _is_safe_public_host(parsed.hostname or "")
                if not _ok:
                    return
            except Exception:
                return
            try:
                # v3.66.390 (Track-K): fail-closed VPN proxy. A vpn_required
                # site with a down tunnel raises VPNRequiredError, caught by the
                # surrounding except -> this mirror candidate is skipped (no
                # clear-net HEAD); the default path applies its own fail-closed.
                proxy_url = self._download_proxy_url()
                client_kwargs = {"timeout": httpx.Timeout(5.0, connect=3.0)}
                if proxy_url: client_kwargs["proxy"] = proxy_url
                with httpx.Client(**client_kwargs) as cl:
                    # follow_redirects=False (F-RUN03-04): a public mirror could 302 us
                    # to an internal host; the winner logic already treats 301/302 as a
                    # live mirror, so not following loses nothing and closes the amplifier.
                    r = cl.head(url, follow_redirects=False)
                    if r.status_code in (200, 206, 302, 301):
                        with winner["lock"]:
                            # First success wins; ignore later responders
                            if winner["url"] == file_url and url != file_url:
                                winner["url"] = url
            except Exception:
                pass

        # Race them; first to set winner wins. 5s overall budget.
        with _cf.ThreadPoolExecutor(max_workers=len(candidates)) as ex:
            futures = [ex.submit(probe, u) for u in candidates]
            done, not_done = _cf.wait(futures, timeout=5.0,
                                       return_when=_cf.FIRST_COMPLETED)
            # Don't bother waiting on the rest; they'll finish or get
            # cancelled when the executor shuts down.
        if winner["url"] != file_url:
            self.log_event("mirror_speculative",
                f"Picked mirror {winner['url']} over primary {file_url}")
        return winner["url"]
    def _build_mirror_urls(self, file_url):
        """Generate alternate URLs to try when the primary CDN fails.

        Strategy: take each entry in `mirror_subdomains` (newline-separated)
        and substitute it for the first label of the URL's hostname. So if
        the URL is `https://cdn1.example.com/path` and the config has
        `cdn2,cdn3,videos`, we try cdn2.example.com, cdn3.example.com,
        videos.example.com.

        If the entry contains a dot, treat it as a complete hostname swap
        (`media.example.com` → entire host replaced).

        Returns a list of fully-qualified URLs (does NOT include the
        original). Empty list when no mirrors configured."""
        raw = (self.config.get("mirror_subdomains") or "").strip()
        if not raw: return []
        candidates = [x.strip() for line in raw.replace(",", "\n").splitlines()
                      for x in [line.strip()] if x.strip()]
        if not candidates: return []
        try:
            from urllib.parse import urlparse, urlunparse
            p = urlparse(file_url)
            if not p.hostname: return []
            host_parts = p.hostname.split(".")
            if len(host_parts) < 2: return []
            apex = ".".join(host_parts[1:])  # everything after the first label
            current_first = host_parts[0]
        except Exception: return []
        out = []
        seen = {p.hostname.lower()}
        for c in candidates:
            if "." in c:
                new_host = c.lower()  # full host given
            else:
                new_host = f"{c}.{apex}".lower()
            if new_host in seen: continue
            seen.add(new_host)
            # Reconstruct URL with new netloc, preserving port if any
            netloc = new_host + (f":{p.port}" if p.port else "")
            out.append(urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment)))
        return out
    def _classify_error(self,message):
        """Phase 6.3: classify a failure message into a retry category.
        Returns one of: 'permanent', 'rate_limit', 'transient', 'network'.
        Each category has its own retry policy in RETRY_DELAYS_BY_KIND.

        Heuristics (case-insensitive substring match):
          - permanent: 404, 403, 'not found', 'forbidden', 'auth required',
            'invalid url', 'expired link', 'gone', '410'
          - rate_limit: 429, 'rate limit', 'too many requests', 'try again'
          - network: 'connect', 'timeout', 'reset', 'dns', 'unreachable',
            'connection', 'eof'
          - transient: everything else (5xx, generic errors, anything we
            can't classify) — gets a moderate retry"""
        m=(message or "").lower()
        if any(k in m for k in ("404","403","not found","forbidden","auth required",
                                 "invalid url","expired","410"," gone")):
            return "permanent"
        if any(k in m for k in ("429","rate limit","too many request","try again later")):
            return "rate_limit"
        if any(k in m for k in ("connect","timeout","reset","dns","unreachable",
                                 "connection","eof","timed out")):
            return "network"
        return "transient"
    def _handle_failure(self,url,message,screenshot=""):
        """Central failure handler. Classifies the error message into one of
        four categories (permanent/rate_limit/network/transient) via
        _classify_error, then either:
          • marks the job failed (permanent errors, or retries exhausted)
            and fires the 'failed' webhook event, OR
          • marks pending with a retry_after timestamp using the category's
            backoff schedule

        Called from every download/page failure path. The category prefix
        ([permanent], [rate_limit], etc.) is added to the message so users
        can see at a glance why a URL is failing — and for permanent
        errors, that auto-retry won't help."""
        with self._lock: job=self.jobs.get(url,{}); retries=job.get("retries",0)
        max_ret=int(self.config.get("max_retries",2))
        # Phase 6.3: pick delay schedule based on error category
        kind=self._classify_error(message)
        schedule=self._RETRY_DELAYS_BY_KIND.get(kind,RETRY_DELAYS)
        # Permanent errors fail immediately — no retry.
        if kind=="permanent" or retries>=max_ret or not schedule:
            tag=f"[{kind}] " if kind!="transient" else ""
            self._update_job(url,"failed",tag+message,screenshot=screenshot)
            db_log(self.site_id,self.config.get("name","?"),url,"failed","",0,
                   tag+message,screenshot)
            # Phase 20: fire failure hook (webhook, HA notify, etc.) only
            # on terminal failure — retries are not failures yet.
            try:
                from .hooks import fire_event
                fire_event("failed", self.config, job={
                    "url": url, "message": tag+message,
                    "retries": retries, "error_kind": kind,
                })
            except Exception as e:
                sys.stderr.write(f"  hook: fire_event(failed) failed: {e}\n")
        else:
            delay=schedule[min(retries,len(schedule)-1)]
            ds=f"{delay//3600}h" if delay>=3600 else (f"{delay//60}m" if delay>=60 else f"{delay}s")
            tag=f"[{kind}] " if kind!="transient" else ""
            from . import admission as _adm
            self._update_job(url,"pending",
                             f"{tag}Retry {retries+1}/{max_ret} in {ds} — {message}",
                             retries=retries+1,
                             retry_after=_adm.next_eligible_retry(
                                 time.time()+delay, self.config))
    def _screenshot(self,page,url):
        """Save a viewport screenshot of `page` to a deterministic filename
        derived from `url`. Returns the relative path as a POSIX string
        (forward slashes) so it round-trips cleanly through JSON / JS /
        HTTP regardless of host OS. Returns empty string on any failure
        — screenshots are best-effort UX, never a blocker."""
        try:
            name=re.sub(r"[^\w]","_",url)[-60:]+".png"
            target=self._ss_dir/name
            page.screenshot(path=str(target),full_page=False)
            # Store with forward slashes so the path round-trips cleanly
            # through JSON, JavaScript, and HTTP regardless of OS. Without
            # this, Windows backslashes get reinterpreted as JS string
            # escapes (e.g. \7 → bell char) when injected into onclick.
            return target.as_posix()
        except Exception: return ""
