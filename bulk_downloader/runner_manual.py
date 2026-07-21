"""runner_manual -- manual-download session lifecycle (+ _ManualDownloadSession class)

Extracted from runner.py (SiteRunner) @v3.66.399, PHASE 3 runner cut 3.
Mixin: methods reference self.* only; NO __init__. The _ManualDownloadSession
top-level class moves here too (the 4 mixin methods construct it). Import block
derived by AST free-name scan of the moved bodies (the seams doc listed only
_ManualDownloadSession + sys; the class also needs the kernel fns + the httpx /
vpn_runtime conditionals). Cycle rule: imports the kernel from .runner_util,
NEVER from .runner.
"""
import sys, threading, queue

from playwright.sync_api import TimeoutError as PWTimeout
from .runner_util import _check_video_magic_bytes, resolve_url_attribute
from .runner_queue import job_status_writer

# httpx soft import (moved verbatim from runner.py; flat sibling). _HTTPX_AVAILABLE.
try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

# vpn_runtime soft import (moved verbatim). vpn_runtime + _VPN_RUNTIME_AVAILABLE.
try:
    from . import vpn_runtime
    _VPN_RUNTIME_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_manual] vpn_runtime import failed (degraded): {_e}\n")
    _VPN_RUNTIME_AVAILABLE = False


def _fire_capture_lifecycle(event, *args):
    """v3.66.468 WS3: fire a GATED capture-path lifecycle event.

    `before_capture(context, page, site_id)` once the live page is ready;
    `after_capture(artifact, site_id)` over the harvested capture artifact.
    No-ops unless allow_full_access is enabled (plugins.fire_lifecycle gates
    + per-hook quarantine/timeout/exception-isolates). Wrapped so a plugin
    error can never break a capture session. Returns the number of hooks that
    ran ok (0 when the gate is off or nothing is registered)."""
    try:
        from . import plugins as _pl
        return _pl.fire_lifecycle(event, *args)
    except Exception as _e:  # noqa: BLE001
        sys.stderr.write(f"  manual_dl: {event} lifecycle hook error: {_e}\n")
        return 0


class _ManualDownloadSession:
    """Owns a Playwright session for the duration of a manual download
    takeover. All ops are dispatched via a command queue to the worker
    thread that owns the pw/browser/ctx objects."""

    def __init__(self, runner, target_url, teach_base_url):
        self._runner = runner
        self.target_url = target_url
        self._teach_base_url = teach_base_url
        self._cmd_q = queue.Queue()
        self._error = None
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._thread = threading.Thread(
            target=self._run, daemon=True,
            name=f"manual-dl-{runner.site_id[:8]}",
        )
        self._thread.start()
        # Block until the browser is fully open or we hit an error
        self._ready.wait(timeout=45)

    @property
    def ready(self):
        return self._ready.is_set() and self._error is None and not self._closed.is_set()

    @property
    def error(self):
        return self._error

    def _launch(self):
        """Open browser+context+page. Only called from the worker thread.

        Phase 41.6: when manual_use_persistent_profile is on (default),
        uses the runner's dedicated manual profile dir. This lets the user
        install password manager extensions once and have them persist
        across both manual login and manual teach sessions. The profile
        is isolated from worker profiles (separate dir) so it doesn't
        affect normal download flow."""
        runner = self._runner
        use_persist = bool(runner.config.get("manual_use_persistent_profile", True))
        if use_persist:
            manual_dir = runner._manual_profile_dir()
            browser, ctx, pw, backend = runner._launch_browser(
                headless=False, use_persistent=True,
                profile_override=manual_dir)
            sys.stderr.write(f"  manual_dl: using persistent profile at {manual_dir}\n")
        else:
            browser, ctx, pw, backend = runner._launch_browser(headless=False, use_persistent=False)
        if ctx is None:
            ctx = browser.new_context(**runner._context_options(headless=False))
            runner._install_stealth(ctx)
        if runner.cookies:
            try: ctx.add_cookies(runner.cookies)
            except Exception as e:
                sys.stderr.write(f"  manual_dl: add_cookies failed: {e}\n")
        # For persistent ctx, ctx.pages already contains a blank page —
        # reuse it if present; else create one
        page = ctx.pages[0] if (use_persist and ctx.pages) else ctx.new_page()
        # v3.43.56: apply playwright-stealth library if configured
        runner._apply_stealth_library_to_page(page)
        # Install recorder + teach overlay BEFORE navigation so they
        # survive the page load
        try:
            from .learn import install_recorder, install_teach_overlay
            install_recorder(page)
            # v3.43.44: pass the per-site URL fingerprint so the
            # teach panel's scoreCandidates() can boost CDN/path
            # patterns we've seen succeed before.
            fp = runner.config.get("url_fingerprint") or {}
            install_teach_overlay(page, runner.site_id,
                                  base_url=self._teach_base_url,
                                  fingerprint=fp)
        except Exception as e:
            sys.stderr.write(f"  manual_dl: install_recorder/overlay (pre-nav) failed: {e}\n")
        try:
            page.goto(self.target_url, wait_until="domcontentloaded", timeout=30000)
        except PWTimeout:
            pass  # leave browser open even if slow
        except Exception as e:
            sys.stderr.write(f"  manual_dl: nav failed (browser still open): {e}\n")
        # Re-install on the live page so the navigation didn't wipe them
        try:
            from .learn import install_recorder, install_teach_overlay
            install_recorder(page)
            fp = runner.config.get("url_fingerprint") or {}
            install_teach_overlay(page, runner.site_id,
                                  base_url=self._teach_base_url,
                                  fingerprint=fp)
        except Exception as e:
            sys.stderr.write(f"  manual_dl: install_recorder/overlay (post-nav) failed: {e}\n")
        # v3.66.468 WS3: before_capture -- live ctx+page just became ready for
        # a capture run. GATED (no-op unless allow_full_access).
        _fire_capture_lifecycle("before_capture", ctx, page, runner.site_id)
        return browser, ctx, page, pw

    def _run(self):
        """Owner thread for the manual download Playwright session.

        Starts Playwright, opens the browser via _launch, signals ready,
        then enters a command loop. Commands are received via self._cmd_q
        as (cmd_name, payload, response_queue) tuples. Recognized commands:

          - 'verify'           — test the user's picked selectors against the
                                 live DOM; returns match details + extracted URL.
          - 'test_download'    — extract the URL, then fetch 2 MB and inspect
                                 magic bytes to confirm it's real video.
          - 'finalize'         — harvest recordings + cookies, then exit.
          - 'commit'           — snapshot cookies only, then exit (Teach Mode).
          - 'cancel'           — exit without harvesting.
          - 'snapshot_cookies' — return current cookies, don't exit.

        Between commands the loop runs a gentle liveness probe to detect
        a closed browser window. Three consecutive failed probes ends the
        session. One-off probe failures are tolerated because Playwright's
        .pages access can transiently raise during navigation without the
        browser actually being dead.

        On any unhandled exception the error is stored on self._error and
        self._ready is set, so a constructor blocked on _ready.wait()
        doesn't hang indefinitely.

        Cleanup order matters: ctx.close() FIRST (it flushes persistent
        profile state to disk), then browser.close(), then pw.stop()."""
        pw = browser = ctx = page = None
        try:
            browser, ctx, page, pw = self._launch()
            self._ready.set()
            sys.stderr.write(f"  manual_dl: session thread ready ({self._thread.name})\n")
            # Phase 41.4: track ctx liveness failures across ticks. Some
            # Playwright/Chrome combos transiently raise on .pages — one
            # failure isn't proof the browser died. Require 3 consecutive
            # failures before declaring death. This prevents the
            # "browser opens, closes 2 seconds later" symptom Matt saw on
            # Windows Server when the bundled Chromium has slow startup.
            liveness_misses = 0
            while True:
                try:
                    cmd, payload, response_q = self._cmd_q.get(timeout=2)
                except queue.Empty:
                    # Idle tick — gentle liveness probe. browser.is_connected()
                    # is more reliable than ctx.pages — pages can transiently
                    # be empty during navigation without the browser being dead.
                    alive = True
                    try:
                        # browser may be None when use_persistent=True returns
                        # the ctx directly. For manual_dl we always pass
                        # use_persistent=False, so browser is set.
                        if browser is not None:
                            alive = browser.is_connected()
                        else:
                            # Fall back to ctx.pages but tolerate failures
                            _ = ctx.pages
                    except Exception as e:
                        alive = False
                        sys.stderr.write(
                            f"  manual_dl: liveness probe raised ({type(e).__name__}: {str(e)[:80]}) "
                            f"[miss {liveness_misses+1}/3]\n")
                    if not alive:
                        liveness_misses += 1
                        if liveness_misses >= 3:
                            sys.stderr.write(
                                "  manual_dl: browser disconnected (3 consecutive probes failed) — "
                                f"ending session\n")
                            break
                    else:
                        liveness_misses = 0
                    continue
                # Got a real command — reset liveness counter
                liveness_misses = 0
                if cmd == "finalize":
                    try:
                        from .learn import harvest_recordings
                        from .cookies import pw_to_json
                        harvest = harvest_recordings(ctx)
                    except Exception as e:
                        sys.stderr.write(f"  manual_dl: harvest failed: {e}\n")
                        harvest = {"clicks": [], "inputs": []}
                    try:
                        cookies = pw_to_json(ctx.cookies())
                    except Exception as e:
                        sys.stderr.write(f"  manual_dl: cookie read failed: {e}\n")
                        cookies = []
                    # v3.66.468 WS3: after_capture -- let gated plugins
                    # read/annotate the harvested artifact in place. No-op
                    # unless allow_full_access.
                    _artifact = {"cookies": cookies, "recordings": harvest,
                                 "site_id": self._runner.site_id}
                    _fire_capture_lifecycle("after_capture", _artifact,
                                            self._runner.site_id)
                    cookies = _artifact.get("cookies", cookies)
                    harvest = _artifact.get("recordings", harvest)
                    response_q.put(("ok", cookies, harvest))
                    break
                elif cmd == "verify":
                    # payload is the picks dict
                    picks = payload
                    try:
                        from .detect import find_best_download
                        live_page = ctx.pages[-1] if ctx.pages else None
                        if not live_page:
                            response_q.put(("err", "No page in context"))
                            continue
                        best = find_best_download(live_page, custom="", learned=picks, runner=self)
                        if not best:
                            response_q.put(("err",
                                "No element matched the picked selectors on this page"))
                            continue
                        # v3.43.0: also extract the URL the worker would actually
                        # use. Three paths in priority order:
                        #   1. url_attribute (per-selector via resolve_url_attribute)
                        #   2. element's own href
                        #   3. "(click-and-capture)" sentinel (caller will run test_download)
                        matched_sel = best.get("_learned_sel","")
                        url_attr = resolve_url_attribute(
                            picks.get("url_attribute"),
                            picks.get("row_selectors") or [],
                            matched_sel,
                        )
                        extracted_url = ""
                        url_via = ""
                        try:
                            if url_attr:
                                v = best["locator"].get_attribute(url_attr)
                                if v:
                                    # Resolve relative against current page
                                    if not v.startswith(("http://","https://")):
                                        from urllib.parse import urljoin as _uj
                                        v = _uj(live_page.url, v)
                                    extracted_url = v
                                    url_via = url_attr
                            if not extracted_url:
                                # Fallback: read href off the element directly
                                v = best["locator"].get_attribute("href")
                                if v and not v.startswith("javascript:"):
                                    if not v.startswith(("http://","https://")):
                                        from urllib.parse import urljoin as _uj
                                        v = _uj(live_page.url, v)
                                    extracted_url = v
                                    url_via = "href"
                        except Exception as _e:
                            pass
                        response_q.put(("ok", {
                            "match_text": best.get("text","")[:120],
                            "score": best.get("score",0),
                            "via_learned": best.get("_via_learned",False),
                            "selector": matched_sel,
                            "extracted_url": extracted_url,
                            "url_via": url_via,
                            "url_attr_used": url_attr,
                        }))
                    except Exception as e:
                        response_q.put(("err", f"Verify error: {str(e)[:120]}"))
                elif cmd == "test_download":
                    # v3.43.0: verify the picks against the live page AND
                    # actually pull 1 MB from the resulting URL to confirm
                    # it's a real video file. Payload is the picks dict.
                    # Returns a detail dict with extracted_url, http_status,
                    # content_type, content_length, bytes_fetched, magic_ok,
                    # magic_kind ("mp4"|"mkv"|"webm"|"unknown").
                    picks = payload
                    try:
                        from .detect import find_best_download
                        from .cookies import pw_to_json
                        live_page = ctx.pages[-1] if ctx.pages else None
                        if not live_page:
                            response_q.put(("err", "No page in context")); continue
                        best = find_best_download(live_page, custom="", learned=picks, runner=self)
                        if not best:
                            response_q.put(("err",
                                "No element matched the picked selectors")); continue
                        # Extract URL same way as verify
                        matched_sel = best.get("_learned_sel","")
                        url_attr = resolve_url_attribute(
                            picks.get("url_attribute"),
                            picks.get("row_selectors") or [],
                            matched_sel,
                        )
                        extracted_url = ""
                        try:
                            if url_attr:
                                v = best["locator"].get_attribute(url_attr)
                                if v:
                                    if not v.startswith(("http://","https://")):
                                        from urllib.parse import urljoin as _uj
                                        v = _uj(live_page.url, v)
                                    extracted_url = v
                            if not extracted_url:
                                v = best["locator"].get_attribute("href")
                                if v and not v.startswith("javascript:"):
                                    if not v.startswith(("http://","https://")):
                                        from urllib.parse import urljoin as _uj
                                        v = _uj(live_page.url, v)
                                    extracted_url = v
                        except Exception:
                            pass
                        if not extracted_url:
                            # No URL to test — this means click-and-capture would
                            # be used. Tell the client that's the path; not
                            # a failure, but we can't pre-verify the bytes.
                            response_q.put(("ok", {
                                "kind": "click_and_capture",
                                "selector": matched_sel,
                                "match_text": best.get("text","")[:120],
                                "note": "This selector has no URL attribute — worker will click and capture the download. Can't pre-test bytes; commit and run the URL to verify end-to-end.",
                            }))
                            continue
                        # Snapshot cookies from the live page context
                        try:
                            cookies = pw_to_json(ctx.cookies())
                        except Exception:
                            cookies = []
                        # Fetch up to 2 MB. Use httpx with the live page's
                        # User-Agent + cookies. Don't follow redirects to
                        # internal hosts — call the SSRF guard.
                        from . import app as _app
                        if not _app._is_url_public(extracted_url):
                            response_q.put(("err",
                                f"URL points to non-public host: {extracted_url[:120]}"))
                            continue
                        ua = (self._runner.config.get("fingerprint") or {}).get("user_agent") or \
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                        headers = {"User-Agent": ua, "Referer": live_page.url,
                                   "Range": "bytes=0-2097151"}
                        cookie_jar = {c["name"]: c["value"] for c in cookies}
                        result = {
                            "kind": "fetch",
                            "extracted_url": extracted_url,
                            "selector": matched_sel,
                            "match_text": best.get("text","")[:120],
                            "url_attr_used": url_attr,
                        }
                        if not _HTTPX_AVAILABLE:
                            response_q.put(("err",
                                "httpx not installed — cannot test download. Run: "
                                "pip install httpx"))
                            continue
                        # v3.66.392 (VPN-CONTROLPLANE): route the download-test
                        # fetch through the same fail-closed VPN proxy as the
                        # payload path -- a vpn_required site whose tunnel is
                        # down must not be probed on the clear interface.
                        try:
                            _cp_proxy = self._runner._download_proxy_url()
                        except Exception as _cpe:
                            if _VPN_RUNTIME_AVAILABLE and isinstance(
                                    _cpe, vpn_runtime.VPNRequiredError):
                                response_q.put(("err",
                                    "VPN required for this site but the tunnel is "
                                    "down/unavailable -- refusing clear-interface "
                                    "download test"))
                                continue
                            raise
                        try:
                            with httpx.Client(timeout=30.0, follow_redirects=True,
                                              proxy=_cp_proxy) as cl:
                                r = cl.get(extracted_url, headers=headers, cookies=cookie_jar)
                                result["http_status"] = r.status_code
                                result["content_type"] = r.headers.get("Content-Type","")
                                # Content-Range: bytes 0-N/total — total is the full size
                                cr = r.headers.get("Content-Range","")
                                if "/" in cr:
                                    try: result["content_length"] = int(cr.rsplit("/",1)[1])
                                    except Exception: pass
                                if "content_length" not in result:
                                    try: result["content_length"] = int(r.headers.get("Content-Length","0"))
                                    except Exception: result["content_length"] = 0
                                body = r.content[:2*1024*1024]
                                result["bytes_fetched"] = len(body)
                                kind = _check_video_magic_bytes(body)
                                result["magic_kind"] = kind
                                result["magic_ok"] = (kind != "unknown")
                        except httpx.HTTPError as e:
                            result["http_error"] = f"{type(e).__name__}: {str(e)[:200]}"
                            result["magic_ok"] = False
                            result["magic_kind"] = "error"
                        response_q.put(("ok", result))
                    except Exception as e:
                        response_q.put(("err", f"Test download error: {str(e)[:160]}"))
                elif cmd == "commit":
                    # payload is (picks, raw_events) — just harvest cookies + close
                    try:
                        from .cookies import pw_to_json
                        cookies = pw_to_json(ctx.cookies())
                    except Exception:
                        cookies = []
                    response_q.put(("ok", cookies))
                    break
                elif cmd == "cancel":
                    response_q.put(("ok",))
                    break
                elif cmd == "snapshot_cookies":
                    try:
                        from .cookies import pw_to_json
                        response_q.put(("ok", pw_to_json(ctx.cookies())))
                    except Exception as e:
                        response_q.put(("err", str(e)[:200]))
        except Exception as e:
            self._error = f"{type(e).__name__}: {str(e)[:200]}"
            sys.stderr.write(f"  manual_dl: session thread error: {self._error}\n")
            self._ready.set()
        finally:
            # v3.36.8: close ctx FIRST, then browser, then pw. For persistent
            # profile sessions (Phase 41.6), `browser` is None and `ctx` is the
            # persistent context — ctx.close() is the call that flushes its
            # storage state to the profile dir. Without this, password-manager
            # extension state, cookies set during teach, etc. could be lost.
            try:
                if ctx is not None: ctx.close()
            except Exception: pass
            try:
                if browser: browser.close()
            except Exception: pass
            try:
                if pw: pw.stop()
            except Exception: pass
            self._closed.set()
            sys.stderr.write("  manual_dl: session thread exited\n")

    def _send(self, cmd, payload=None, timeout=30):
        """Dispatch a command and wait for the response. Returns the
        full response tuple from the worker (("ok", ...) or ("err", ...))."""
        if self._closed.is_set() or not self._ready.is_set():
            return ("err", "session not active")
        if self._error:
            return ("err", self._error)
        rq = queue.Queue()
        try:
            self._cmd_q.put((cmd, payload, rq))
        except Exception as e:
            return ("err", f"queue put failed: {e}")
        try:
            return rq.get(timeout=timeout)
        except queue.Empty:
            return ("err", f"{cmd} timed out")

    def finalize(self, timeout=30):
        """Harvest recordings + cookies, close session. Returns
        (ok, message, cookies, harvest)."""
        result = self._send("finalize", timeout=timeout)
        self._closed.wait(timeout=10)
        if result[0] == "ok":
            cookies, harvest = result[1], result[2]
            return True, f"captured {len(cookies)} cookies", cookies, harvest
        return False, result[1], [], {"clicks": [], "inputs": []}

    def verify(self, picks, timeout=15):
        """Test the user's picked selectors against the live DOM.
        Returns (ok, detail_dict)."""
        result = self._send("verify", picks, timeout=timeout)
        if result[0] == "ok":
            return True, result[1]
        return False, {"error": result[1]}

    def test_download(self, picks, timeout=60):
        """v3.43.0: dry-run a real fetch of the URL the picks would
        resolve to. Confirms HTTP 200 + recognized video magic bytes
        before commit. Returns (ok, detail_dict). Detail includes
        extracted_url, http_status, content_type, content_length,
        magic_ok, magic_kind, and either match_text or http_error."""
        result = self._send("test_download", picks, timeout=timeout)
        if result[0] == "ok":
            return True, result[1]
        return False, {"error": result[1]}

    def commit(self, timeout=15):
        """Snapshot cookies and close the session for a Teach Mode commit.
        Returns (ok, cookies)."""
        result = self._send("commit", timeout=timeout)
        self._closed.wait(timeout=10)
        if result[0] == "ok":
            return True, result[1]
        return False, []

    def cancel(self, timeout=10):
        """Close the session without harvesting anything."""
        if self._closed.is_set(): return
        self._send("cancel", timeout=timeout)
        self._closed.wait(timeout=10)

    def snapshot_cookies(self, timeout=10):
        """Read cookies without closing the session."""
        result = self._send("snapshot_cookies", timeout=timeout)
        if result[0] == "ok": return result[1]
        return None


class ManualMixin:
    def start_manual_download(self,target_url):
        """Phase 41.3: open a non-headless Chromium at `target_url` with
        the site's cookies loaded and the click recorder + teach overlay
        installed, OWNED BY A DEDICATED THREAD.

        Why a thread: Playwright's sync API is thread-bound. Earlier
        versions launched the browser on the Flask request handler thread
        and stashed (pw, browser, ctx) tuples on self for later requests
        to read. That worked only on Werkzeug versions that reused
        threads — newer Werkzeug spawns a fresh thread per request, the
        original thread terminates immediately after the response is
        sent, and Playwright's subprocess gets garbage-collected →
        browser closes within a second of opening. This is the same fix
        we applied to manual-login in Phase 19.fix.
        """
        if getattr(self,"_manual_download_session",None):
            sys.stderr.write(
                f"  manual_dl: start refused — session already exists for site {self.site_id}\n")
            return False,"Already a manual download in progress; finish or cancel that first"
        if not target_url or not target_url.startswith("http"):
            sys.stderr.write(
                f"  manual_dl: start refused — bad url: {target_url!r}\n")
            return False,"target_url must be an http(s) URL"
        sys.stderr.write(f"  manual_dl: start_manual_download called for {target_url}\n")
        try:
            session = _ManualDownloadSession(self, target_url, self._teach_base_url())
        except Exception as e:
            sys.stderr.write(f"  manual_dl: session construction raised: {type(e).__name__}: {e}\n")
            return False, f"Couldn't open browser: {str(e)[:120]}"
        # Wait for the session to either become ready or error out
        if not session.ready:
            err = session.error or "session failed to ready within 45s"
            sys.stderr.write(f"  manual_dl: session never became ready: {err[:200]}\n")
            return False, f"Couldn't open browser: {err[:140]}"
        self._manual_download_session = session
        self._login_status = f"⏳ Manual download takeover for {target_url[:60]}..."
        sys.stderr.write(f"  download takeover started: {target_url}\n")
        return True, "Manual download takeover started"
    def finish_manual_download(self):
        """User clicked 'Done' on the download takeover. Harvest the
        recorded clicks (via session's dedicated thread, since playwright
        is thread-bound), classify them, merge learned selectors into
        the site config, mark the URL as done in the queue."""
        session = getattr(self,"_manual_download_session",None)
        if not session: return False,"No pending manual download"
        self._manual_download_session = None
        target_url = session.target_url
        ok, msg, new_cookies, harvest = session.finalize(timeout=30)
        if not ok:
            return False, msg

        # Persist refreshed cookies, if the user logged in during takeover
        try:
            if new_cookies:
                self.set_cookies(new_cookies)
                cf=self.config.get("cookie_file","")
                if cf:
                    try:
                        from .cookies import save_cookies_to_file
                        save_cookies_to_file(cf,new_cookies)
                    except (OSError, ValueError) as e:
                        self.log.warning("post-takeover cookie save to %s failed: %s", cf, e)
        except Exception as e:
            self.log.debug("post-takeover cookie persist failed: %s", e)

        # Classify and persist learned download patterns
        learned_count=0
        try:
            from .learn import classify_download, merge_learned
            sels=classify_download(harvest)
            learned_count=sum(1 for v in sels.values() if v)
            if learned_count and not self._override_suppresses_persist():
                merge_learned(self.config,sels,kind="download")
                try:
                    from . import app as _app
                    if self.site_id in _app.s_cfg:
                        _app.s_cfg[self.site_id]=self.config
                        _app.s_meta[self.site_id]=_app._build_meta(self.config)
                        _app._save_sites_config()
                    self._persist_learned_to_draft()  # B2: persist toggle ON -> draft
                except Exception as e:
                    self.log.error("download takeover persist failed: %s", e)
                sys.stderr.write(f"  download takeover: learned {learned_count} role(s): "
                    f"{', '.join(k for k,v in sels.items() if v)}\n")
        except Exception as e:
            self.log.error("download takeover classify failed: %s", e)

        # Mark the target URL as done so it doesn't come back as needs_review
        try:
            self._update_job(target_url,"done",
                             f"Manual download — learned {learned_count} role(s)",
                             filename="(manual)")
        except Exception: pass

        # Phase 41.2: clear the auto_teach state and re-enqueue any URLs
        # that were waiting for selectors
        if learned_count:
            self._auto_teach_logged = False
            with job_status_writer(self) as mark_status_changed:
                changed = False
                for u, j in self.jobs.items():
                    if j.get("auto_teach_seen") and j.get("status") == "needs_review":
                        j["auto_teach_seen"] = False
                        j["status"] = "pending"
                        j["message"] = "Queued after teach completion"
                        try: self._url_queue.put_nowait(u)
                        except Exception: pass
                        changed = True
                if changed:
                    mark_status_changed()
            # Phase 41.5: now that pending URLs exist and selectors are
            # learned, spawn workers. start() is idempotent if already
            # running; if idle, it'll spawn fresh workers.
            try: self.start()
            except Exception as e:
                self.log.warning("post-teach start() failed: %s", e)

        msg=f"Manual download finished, learned {learned_count} role(s)"
        self._login_status="✓ "+msg
        return True,msg
    def cancel_manual_download(self):
        """User clicked Cancel. Close the session, no learning."""
        session = getattr(self,"_manual_download_session",None)
        if not session: return False,"No pending manual download"
        self._manual_download_session = None
        target_url = session.target_url
        try:
            session.cancel(timeout=10)
        except Exception as e:
            self.log.debug("manual download cancel error (browser may already be gone): %s", e)
        # Clear the auto_teach state so retry is clean
        try:
            with job_status_writer(self) as mark_status_changed:
                if target_url in self.jobs:
                    j = self.jobs[target_url]
                    if j.get("auto_teach_seen"):
                        j["auto_teach_seen"] = False
                        j["status"] = "pending"
                        j["message"] = "Cancelled — retry to resume teach flow"
                        try: self._url_queue.put_nowait(target_url)
                        except Exception: pass
                        mark_status_changed()
            self._auto_teach_logged = False
        except Exception: pass
        self._login_status="✗ Manual download cancelled"
        return True,"Cancelled"
    def is_awaiting_manual_download(self):
        return getattr(self,"_manual_download_session",None) is not None
