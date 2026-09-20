"""runner_browser -- Playwright context/launch/profile/stealth/warm

Extracted from runner.py (SiteRunner) @v3.66.401, PHASE 3 runner cut 4.
Mixin: methods reference self.* only; NO __init__. Import block derived by AST
free-name scan of the moved bodies. Cycle rule: nothing from .runner.
"""
import json, queue, re, sys, threading, time
import urllib.parse

# vpn_runtime soft import (moved verbatim from runner.py; flat sibling).
try:
    from . import vpn_runtime
    _VPN_RUNTIME_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_browser] vpn_runtime import failed (degraded): {_e}\n")
    _VPN_RUNTIME_AVAILABLE = False


# Row 899: adaptive-streaming manifest detection (HLS .m3u8 / DASH .mpd).
# Player pages fetch these via async Fetch/XHR that a static scraper never
# sees; only a live CDP Network listener catches them during playback setup.
_ADAPTIVE_MANIFEST_RE = re.compile(r"\.(m3u8|mpd)$", re.IGNORECASE)
# the manifest content types a server may answer with when the URL carries no
# extension (a signed playlist endpoint): the response, not the path, says so
_ADAPTIVE_MANIFEST_MIME = {
    "application/vnd.apple.mpegurl": "hls", "application/x-mpegurl": "hls",
    "audio/mpegurl": "hls", "audio/x-mpegurl": "hls",
    "application/dash+xml": "dash",
}


def _adaptive_manifest_kind(url):
    """Return "hls" for a URL whose PATH ends in .m3u8, "dash" for .mpd,
    else None. The extension must be in the path: a player URL that merely
    carries ``?next=master.m3u8`` in a query value is not a manifest."""
    from urllib.parse import urlsplit
    try:
        path = urlsplit(url or "").path
    except ValueError:
        return None
    m = _ADAPTIVE_MANIFEST_RE.search(path)
    if not m:
        return None
    return "hls" if m.group(1).lower() == "m3u8" else "dash"


def _adaptive_manifest_kind_from_mime(mime_type):
    base = (mime_type or "").split(";", 1)[0].strip().lower()
    return _ADAPTIVE_MANIFEST_MIME.get(base)


class AdaptiveManifestWatcher:
    """Groups ``Network.requestWillBeSent`` CDP events by ``requestId`` so a
    manifest reached through one or more HTTP redirects is reported with its
    full hop-by-hop chain, not just the final URL. Pure/testable with
    synthetic event dicts -- no browser required (mirrors
    ``session_capture.feed_cdp_event``'s redirect-joining approach, scoped
    down to only the adaptive-manifest question this row asks).
    """

    def __init__(self):
        self._chains = {}    # requestId -> [url, ...] legs seen so far
        self._entries = {}   # requestId -> the detection dict (updated as the chain resolves)
        self.manifests = []  # completed detections, in arrival order

    def _detect(self, rid, url, kind):
        """Record/refresh the detection for ``rid``: ``url`` is the FINAL
        resolved URL so far, the chain is every earlier hop (redirects share
        one CDP requestId). One entry per request: a manifest that redirects
        is reported once, with its resolved URL and the complete chain, not
        once per leg."""
        chain = self._chains.get(rid) or [url]
        entry = self._entries.get(rid)
        if entry is None:
            entry = {"url": url, "kind": kind, "redirect_chain": list(chain[:-1]), "request_id": rid}
            self._entries[rid] = entry
            self.manifests.append(entry)
        else:
            entry["url"] = url
            entry["kind"] = kind
            entry["redirect_chain"] = list(chain[:-1])
        return entry

    def feed_playwright_response(self, response):
        """Feed a Playwright ``response`` (context-level ``response`` event):
        the manifest question is answered by the final URL's path or the
        Content-Type, and the chain by ``request.redirected_from``."""
        try:
            request = response.request
            url = response.url
            headers = response.headers or {}
            status = int(response.status)
        except Exception:
            return None
        if 300 <= status < 400:
            return None                      # an intermediate hop: the final response carries the chain
        kind = _adaptive_manifest_kind(url) or _adaptive_manifest_kind_from_mime(headers.get("content-type"))
        chain = []
        hop = getattr(request, "redirected_from", None)
        root = request
        depth = 0
        while hop is not None and depth < 32:
            chain.insert(0, hop.url)
            if kind is None:
                kind = _adaptive_manifest_kind(hop.url)
            root = hop
            hop = getattr(hop, "redirected_from", None)
            depth += 1
        if kind is None:
            return None
        rid = f"pw:{id(root)}"
        self._chains[rid] = chain + [url]
        return self._detect(rid, url, kind)

    def feed(self, method, params):
        """Feed one raw CDP event. Returns the detection dict when this event
        makes (or resolves) an adaptive-streaming manifest request, else None:
        a ``Network.requestWillBeSent`` leg whose path is .m3u8/.mpd, a later
        redirect leg of such a request (the resolved URL, whatever it is
        called), or a ``Network.responseReceived`` whose mimeType is a
        manifest type."""
        params = params or {}
        rid = params.get("requestId")
        if method == "Network.requestWillBeSent":
            url = (params.get("request") or {}).get("url", "")
            chain = self._chains.setdefault(rid, [])
            chain.append(url)
            kind = _adaptive_manifest_kind(url)
            if kind is None and rid in self._entries and len(chain) > 1:
                kind = self._entries[rid]["kind"]          # the manifest resolved to a differently named URL
            if kind is None:
                return None
            return self._detect(rid, url, kind)
        if method == "Network.responseReceived":
            response = params.get("response") or {}
            kind = _adaptive_manifest_kind_from_mime(response.get("mimeType"))
            if kind is None:
                return None
            url = response.get("url") or (self._chains.get(rid) or [""])[-1]
            if rid not in self._chains:
                self._chains[rid] = [url]
            elif self._chains[rid][-1] != url:
                self._chains[rid].append(url)
            return self._detect(rid, url, kind)
        return None


# Row 904: same CDP event session_capture.py already listens for
# (Network.webSocketFrameReceived rides the Network domain that capture
# already enables); this is a second, independent listener for callers that
# want parsed JSON messages routed to a queue, not the raw capture log.
_CDP_WS_FRAME_RECEIVED = "Network.webSocketFrameReceived"
_WS_OPCODE_TEXT = 1          # RFC 6455: only a text frame carries a JSON message;
                             # 2 is binary (CDP base64-encodes payloadData), 8/9/10 are control frames
_WS_URL_RE = re.compile(r"^https?://[^\s\"'<>]+$", re.IGNORECASE)
_WS_MAX_QUEUED_MESSAGES = 1000   # ring of raw messages kept for a consumer (oldest evicted)
_WS_MAX_QUEUED_URLS = 5000       # distinct media URLs kept between drains
_WS_URL_KEYS = ("url", "src", "href", "file", "media_url", "mediaurl", "playback_url", "stream_url",
                "manifest", "hls", "dash", "download", "source")


def extract_media_urls(message):
    """Every http(s) URL string carried by a parsed WebSocket message
    (recursively through objects/arrays), in document order, de-duplicated.
    A key from _WS_URL_KEYS is listed first so a consumer sees the field the
    app names as the media before incidental links."""
    named, others, seen = [], [], set()

    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k).lower())
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and _WS_URL_RE.match(node.strip()):
            url = node.strip()
            if url in seen:
                return
            seen.add(url)
            (named if key in _WS_URL_KEYS else others).append(url)
    walk(message)
    return named + others


class WebSocketFrameDispatcher:
    """Row904: routes JSON WebSocket frames captured over CDP into a plain
    ``queue.Queue``, decoupling the browser's own CDP event-delivery thread
    from whatever loop later consumes asynchronous state updates / media
    tokens. A frame whose payload is not a JSON object/array/value is
    dropped -- a queue consumer expecting parsed messages has nothing useful
    to do with raw text. A frame arriving after :meth:`close` is dropped too,
    so a CDP event racing the teardown can never enqueue into an
    already-abandoned queue."""

    def __init__(self, max_messages=_WS_MAX_QUEUED_MESSAGES, max_urls=_WS_MAX_QUEUED_URLS):
        # Bounded (measured, rb9 review): the dispatcher is installed on every
        # page of a persistent context and nothing in the runner's lifecycle is
        # obliged to drain it; an unbounded queue grew by ~106 MB over 200k
        # presence-style frames on real Chromium. Oldest messages are evicted,
        # counted in ``dropped``; the media URLs a message carried are lifted
        # out at arrival so a chatty channel cannot evict an announcement
        # before the consumer reads it.
        self.queue = queue.Queue(maxsize=max(1, int(max_messages)))
        self.dropped = 0
        self._max_urls = max(1, int(max_urls))
        self._urls = {}                 # url -> None, arrival order, de-duplicated
        self._client = None
        self._closed = False
        self._lock = threading.Lock()   # closed-check and enqueue are one step (a parse in flight cannot enqueue after close)

    def _on_frame(self, params):
        if self._closed:
            return
        response = params.get("response") or {}
        if response.get("opcode", _WS_OPCODE_TEXT) != _WS_OPCODE_TEXT:
            return                       # binary (base64) or control frame: not a JSON message
        payload = response.get("payloadData")
        if not isinstance(payload, str):
            return
        try:
            message = json.loads(payload)
        except ValueError:
            return
        if not isinstance(message, (dict, list)):
            return                       # a bare scalar is not a protocol message
        with self._lock:
            if self._closed:
                return
            for url in extract_media_urls(message):
                if url not in self._urls and len(self._urls) >= self._max_urls:
                    del self._urls[next(iter(self._urls))]
                    self.dropped += 1
                self._urls[url] = None
            if self.queue.full():
                try:
                    self.queue.get_nowait()
                    self.dropped += 1
                except queue.Empty:
                    pass
            self.queue.put_nowait(message)

    def drain(self):
        """Every queued message so far, in arrival order."""
        out = []
        while True:
            try:
                out.append(self.queue.get_nowait())
            except queue.Empty:
                return out

    def media_urls(self):
        """Media URLs carried by every message captured since the last call,
        in arrival order, de-duplicated -- taken from the arrival-time store,
        so an announcement survives even if its message was evicted from
        ``queue`` by later traffic. Drains the queue as well."""
        with self._lock:
            urls = list(self._urls)
            self._urls = {}
        self.drain()
        return urls

    def close(self):
        """Detach the CDP session (if any) and stop routing frames.

        Safe to call more than once -- the second call is a no-op. Setting
        ``_closed`` before detaching means a frame already in flight when
        ``close()`` runs is still dropped by ``_on_frame`` even if the
        underlying transport delivers it a moment after ``detach()``
        returns."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.detach()
        except Exception:
            return


class BrowserMixin:
    def _install_adaptive_manifest_capture(self, ctx):
        """Row 899: attach a live CDP ``Network`` listener to every page of
        ``ctx`` and watch for adaptive-streaming manifest requests
        (.m3u8/.mpd) during playback setup, with their redirect chains.
        Detections hand off to ``self.manifest_urls`` -- the queue a
        transport pipeline consumer reads -- via
        ``self._on_adaptive_manifest_detected``.

        Not exercised by the unit suite (needs a real browser/CDP session);
        the detection + redirect-chain logic it delegates to
        (``AdaptiveManifestWatcher.feed``) is unit-tested with synthetic
        events, same split as ``session_capture.capture_via_cdp`` /
        ``feed_cdp_event``. Caller still drives navigation/playback; this
        only wires the listener onto pages already open or opened later."""
        watcher = AdaptiveManifestWatcher()

        def _wire(page):
            try:
                client = ctx.new_cdp_session(page)
                client.send("Network.enable")
            except Exception as e:
                sys.stderr.write(
                    f"  [runner_browser] manifest CDP session failed: {str(e)[:100]}\n")
                return
            client.on("Network.requestWillBeSent", lambda params: (
                self._on_adaptive_manifest_detected(watcher.feed(
                    "Network.requestWillBeSent", params))))
            client.on("Network.responseReceived", lambda params: (
                self._on_adaptive_manifest_detected(watcher.feed(
                    "Network.responseReceived", params))))

        # Primary: the context-level Playwright response event covers every
        # page of the context from the moment of installation, with no
        # per-page wiring race (a CDP session opened from the "page" event
        # can miss the first requests of a page). The per-page CDP session
        # below stays for the raw-event path.
        try:
            ctx.on("response", lambda response: (
                self._on_adaptive_manifest_detected(watcher.feed_playwright_response(response))))
        except Exception as e:
            sys.stderr.write(
                f"  [runner_browser] manifest response listener not attached: {str(e)[:100]}\n")
        for page in getattr(ctx, "pages", None) or []:
            _wire(page)
        try:
            ctx.on("page", _wire)
        except Exception as e:
            sys.stderr.write(
                f"  [runner_browser] manifest page listener not attached: {str(e)[:100]}\n")
        return watcher

    def _maybe_install_adaptive_manifest_capture(self, ctx):
        """Production wiring: every persistent playback context gets the
        capture unless the site config says ``adaptive_manifest_capture:
        false``. The watcher handle is kept on the runner."""
        config = getattr(self, "config", None) or {}
        if config.get("adaptive_manifest_capture", True) is False:
            return None
        try:
            self._adaptive_manifest_watcher = self._install_adaptive_manifest_capture(ctx)
        except Exception as e:
            sys.stderr.write(f"  [runner_browser] manifest capture not installed: {str(e)[:100]}\n")
            return None
        return self._adaptive_manifest_watcher

    def drain_manifest_urls(self):
        """The transport pipeline's read side: hand over every manifest
        detected so far (resolved URL + redirect chain) and empty the queue.
        Snapshots each entry so a later redirect leg cannot rewrite what a
        consumer already took."""
        queued = getattr(self, "manifest_urls", None) or []
        self.manifest_urls = []
        return [dict(e, redirect_chain=list(e.get("redirect_chain") or [])) for e in queued]

    def _on_adaptive_manifest_detected(self, entry):
        """Handoff point for a detected adaptive-streaming manifest: queue
        it on ``self.manifest_urls`` for the transport pipeline to consume,
        and log it if this runner has an event log. ``entry`` is None for
        every non-manifest CDP event (the common case); a no-op then."""
        if entry is None:
            return
        if not hasattr(self, "manifest_urls"):
            self.manifest_urls = []
        if any(e is entry for e in self.manifest_urls):
            return                                   # a redirect leg refreshed an entry already queued
        for queued in self.manifest_urls:
            if queued["url"] == entry["url"]:
                # the CDP and Playwright paths saw the same request: keep one
                # entry, with the most complete chain
                if len(entry.get("redirect_chain") or ()) > len(queued.get("redirect_chain") or ()):
                    queued["redirect_chain"] = list(entry["redirect_chain"])
                return
        self.manifest_urls.append(entry)
        _log = getattr(self, "log_event", None)
        if _log is not None:
            hops = len(entry["redirect_chain"])
            _log("manifest",
                 f"{entry['kind']} manifest detected"
                 + (f" after {hops} redirect(s)" if hops else "")
                 + f": {entry['url'][:120]}",
                 extra={"kind": entry["kind"], "url": entry["url"],
                        "redirect_chain": entry["redirect_chain"]})

    def _watch_websocket_json(self, page):
        """Row904: attach a CDP ``Network`` session to ``page`` and route
        every JSON-parseable ``Network.webSocketFrameReceived`` payload into
        a :class:`WebSocketFrameDispatcher` queue, so a caller can poll for
        asynchronous state updates / media authorization tokens delivered
        over the page's own WebSocket connections without blocking on
        Playwright's own page event loop.

        Returns the dispatcher. Caller MUST call ``dispatcher.close()`` when
        done -- it detaches the CDP session and stops further routing."""
        dispatcher = WebSocketFrameDispatcher()
        client = page.context.new_cdp_session(page)
        dispatcher._client = client
        try:
            client.send("Network.enable")
            client.on(_CDP_WS_FRAME_RECEIVED, dispatcher._on_frame)
        except Exception:
            # a session created but never fully wired is torn down here, not leaked
            dispatcher.close()
            raise
        return dispatcher

    def _maybe_install_websocket_json_capture(self, ctx):
        """Production wiring: every page of a persistent playback context
        (open now or opened later) gets a WebSocket JSON dispatcher unless
        the site config says ``websocket_capture: false``. Dispatchers are
        kept on ``self._websocket_dispatchers``; ``drain_websocket_media_urls``
        is the read side; ``_close_websocket_capture`` tears them down."""
        config = getattr(self, "config", None) or {}
        if config.get("websocket_capture", True) is False:
            return None
        dispatchers = getattr(self, "_websocket_dispatchers", None)
        if dispatchers is None:
            dispatchers = self._websocket_dispatchers = []

        def _wire(page):
            try:
                dispatchers.append(self._watch_websocket_json(page))
            except Exception as e:
                sys.stderr.write(f"  [runner_browser] websocket capture not installed: {str(e)[:100]}\n")

        for page in getattr(ctx, "pages", None) or []:
            _wire(page)
        try:
            ctx.on("page", _wire)
        except Exception:
            pass
        return dispatchers

    def drain_websocket_media_urls(self):
        """The consumer: media URLs carried by every JSON WebSocket message
        captured so far across the context's pages (de-duplicated)."""
        urls, seen = [], set()
        for dispatcher in list(getattr(self, "_websocket_dispatchers", None) or []):
            for url in dispatcher.media_urls():
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
        return urls

    def _close_websocket_capture(self):
        for dispatcher in list(getattr(self, "_websocket_dispatchers", None) or []):
            dispatcher.close()
        self._websocket_dispatchers = []

    def _pw_save(self,dl,final_path):
        """Fallback: let Playwright stream the download to disk.

        Returns (size_on_disk, bytes_transferred_this_call). The browser
        fetched the whole file, so the two are equal here -- unlike the httpx
        path, which can rename a resumed or already-complete file into place
        without moving a byte.
        """
        # ``_do_download`` already atomically reserved ``final_path`` and
        # records that exact path on success.  A second ``safe_dest`` probe here
        # could pick a suffix after the reservation, leaving the browser bytes
        # at one path and the done/history row at another.
        dest=final_path
        dl.save_as(str(dest))
        size = dest.stat().st_size if dest.exists() else 0
        return size, size
    def _context_options(self, headless=True):
        """Phase 7.1: build browser_context kwargs from the site's
        fingerprint config. Falls back to safe defaults when fields are
        missing. Always sets accept_downloads=True since we always need
        the download path to work.

        `headless` controls whether the fingerprint viewport is applied:
          - headless=True (worker): apply fingerprint viewport for anti-
            detection. Playwright renders to a virtual viewport at the
            fingerprint's dimensions.
          - headless=False (manual login / takeover): SKIP the viewport
            override and set no_viewport=True. With a fixed virtual
            viewport, the page renders at fingerprint size (e.g. 3840x2160
            for 4K) but Chrome's actual window is much smaller, so
            Playwright scale-fits the content — visually a "huge zoom in"
            on text fields. For headed mode, Chrome's actual window size
            should drive the viewport."""
        fp=self.config.get("fingerprint") or {}
        opts={"accept_downloads":True}
        if fp.get("user_agent"): opts["user_agent"]=fp["user_agent"]
        if headless and fp.get("viewport_w") and fp.get("viewport_h"):
            try: opts["viewport"]={"width":int(fp["viewport_w"]),"height":int(fp["viewport_h"])}
            except Exception: pass
        elif not headless:
            # Track Chrome's actual window size — no virtual viewport.
            opts["no_viewport"] = True
        if fp.get("timezone"): opts["timezone_id"]=fp["timezone"]
        if fp.get("locale"): opts["locale"]=fp["locale"]
        return opts
    def _launch_args(self, headless=True):
        """Common chromium launch args. Suppresses notifications, popups,
        infobars, and other automation-blocking prompts. The
        --disable-blink-features=AutomationControlled flag is the second
        most-checked stealth tell after navigator.webdriver — without it
        Cloudflare detects automation in the first request.

        For headed launches we also pass --window-size so Chrome opens
        at a sensible desktop size (1366x800) rather than its tiny default.

        v3.43.14: non-headless launches (manual takeover, manual
        download, manual login) get Chromium's password manager and
        autofill enabled. Headless workers don't — there's no user
        present to interact with autofill prompts."""
        args = [
            "--no-sandbox", "--disable-notifications", "--disable-popup-blocking",
            "--disable-infobars", "--no-default-browser-check", "--no-first-run",
            "--disable-features=PushMessaging,Translate,AutomationControlled",
            "--disable-blink-features=AutomationControlled",
        ]
        from .browser_sentinel import get_chromium_memory_flags as _gcmf
        args.extend(_gcmf())
        if not headless:
            args.append("--window-size=1366,800")
            args.append("--password-store=basic")
            args.append("--enable-features=AutofillEnableAccountWalletStorage,PasswordManagerEnabled")
        # Row 927: V8 heap bounding flags (bounds RSS memory growth past 750MB)
        # v3.43.14: password manager and autofill enabled on headed launches
        # v3.66.468 WS2: operator-supplied unpacked chromium extensions.
        # `chromium_extensions: [dir, ...]` -> --disable-extensions-except +
        # --load-extension (Chromium needs both together; persistent-context
        # only, which BD uses). Non-list / missing / non-existent dirs are
        # inert -- never a bare flag, never a crash.
        ext = self.config.get("chromium_extensions") if isinstance(self.config, dict) else None
        # Accept a list/tuple OR a comma-separated string (the latter is what the
        # gui-safe per-site editor stores via a text control), so editing the
        # field in the GUI can't silently corrupt a list into a dead string.
        if isinstance(ext, str):
            ext = [p for p in (s.strip() for s in ext.split(",")) if p]
        if isinstance(ext, (list, tuple)):
            from pathlib import Path
            dirs = []
            for e in ext:
                try:
                    p = Path(str(e)).expanduser()
                except Exception:  # noqa: BLE001
                    continue
                if p.is_dir() and str(p) not in dirs:
                    dirs.append(str(p))
            # INTEROP-GOV-1: when governance is enabled, an extension loads ONLY
            # if the interop_registry permits it (registered + risk-acknowledged +
            # enabled) AND its live content hash still matches the pinned
            # provenance -- an un-acked or silently-changed extension is refused.
            # Default-OFF: with the toggle absent, dirs are unchanged (EXT-1
            # behavior). The registry read is per-launch and few dirs, so cheap.
            if self.config.get("interop_governance_enabled", False):
                from . import interop_registry as _ir
                dirs = [d for d in dirs
                        if _ir.is_permitted("chromium_extension", d, _ir.dir_sha256(d))]
            if dirs:
                csv = ",".join(dirs)
                args.append(f"--disable-extensions-except={csv}")
                args.append(f"--load-extension={csv}")
        return args
    def _manual_profile_dir(self):
        """Phase 41.6: dedicated profile dir for manual login / manual teach
        sessions. Separate from worker profiles so:
          • A password manager extension installed once persists across
            future manual login + teach windows
          • Worker cookies / state don't bleed into the manual session
            (the workers run their OWN profiles for each URL/site)
          • Two workers can run concurrently in their own profiles while a
            manual session is also open in its own profile

        Located at ./profiles/<site_id>/manual/.

        Same stale-singleton-lock cleanup as _profile_dir."""
        from pathlib import Path
        d = Path("profiles") / self.site_id / "manual"
        d.mkdir(parents=True, exist_ok=True)
        for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            p = d / lock
            try:
                if p.is_symlink() or p.exists(): p.unlink()
            except Exception: pass
        return str(d.resolve())
    def _profile_dir(self, worker_idx=None):
        """Phase 9.3 / 19.fix: persistent profile dir.

        OLD behavior: one dir per site at ./profiles/<site_id>/. This breaks
        when max_concurrent>1 because Chrome enforces a SingletonLock per
        profile dir — only one process can open it at a time. The second
        worker's launch_persistent_context fails with "Target page, context
        or browser has been closed". The same failure also hits when a
        previous browser process didn't fully release the lock yet.

        NEW behavior: each worker gets its own subdir. profiles/<site_id>/main
        for the default/single-worker case (preserves existing trust cookies
        from upgrades), profiles/<site_id>/wN for additional workers. We
        also proactively delete any stale SingletonLock / SingletonCookie /
        SingletonSocket files at startup so a crashed previous run doesn't
        block the next launch."""
        from pathlib import Path
        if worker_idx is None or worker_idx == 0:
            d = Path("profiles") / self.site_id / "main"
        else:
            d = Path("profiles") / self.site_id / f"w{worker_idx}"
        d.mkdir(parents=True, exist_ok=True)
        # Clean stale singleton locks. These only matter if Chrome crashed
        # last time — they're symlinks/files that newer Chrome refuses to
        # overwrite. Safe to delete unconditionally; Chrome recreates them.
        for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            p = d / lock
            try:
                if p.is_symlink() or p.exists(): p.unlink()
            except Exception: pass
        return str(d.resolve())
    def _apply_persistent_cookie_file(self, ctx):
        """Apply a configured, usable cookie jar to a persistent context."""
        cookie_file = self.config.get("cookie_file")
        if not cookie_file:
            return
        try:
            from pathlib import Path
            cookie_path = Path(str(cookie_file)).expanduser()
            if not cookie_path.is_file() or cookie_path.stat().st_size == 0:
                return
            loaded, _detail = self.set_cookies_from_file(str(cookie_path))
        except Exception as e:
            sys.stderr.write(
                f"  persistent cookie file load failed: {str(e)[:100]}\n")
            return
        # An unreadable file leaves the existing profile usable. Failure to
        # apply a loaded jar instead invalidates this newly opened context;
        # let the launch owner dispose of it and choose the fallback.
        jar = self.cookies if loaded else ()
        if jar:
            ctx.add_cookies(jar)
    def _record_channel_fallback(self,flow,channel,error,recovered):
        """Row 723: surface a real-Chrome -> bundled-Chromium degradation in the
        SITE'S RUN RECORD, not only in the service log. The site asked for a
        real Chrome binary (`use_real_chrome` -> Playwright channel="chrome",
        which only Google Chrome satisfies) and this host does not have one, so
        the run happened on bundled Chromium with a different fingerprint. A2:
        a capability that degrades silently is indistinguishable from one that
        worked, so the run record has to say so. Also files the note in cloak's
        ledger for callers that own a record but not this mixin."""
        from . import cloak as _cloak
        note=_cloak.note_channel_fallback(
            site_id=self.site_id,flow=str(flow),channel=str(channel),
            error=str(error),recovered=bool(recovered))
        verdict=("ran on bundled Chromium instead" if recovered
                 else "and the bundled-Chromium retry ALSO failed")
        _log=getattr(self,"log_event",None)
        if _log is None:
            # No event log on this caller. The note is still in cloak's ledger,
            # so the degradation is recorded and drainable -- it is never
            # dropped, which is the whole point of row 723.
            sys.stderr.write(f"  [browser] channel fallback (unrecorded sink): "
                             f"{note['channel']} {verdict}\n")
            return note
        _log("browser",
            f"real Chrome unavailable (channel={channel}); {verdict} "
            f"-- {note['error']}",
            extra={"requested_channel":note["channel"],
                   "recovered":note["recovered"],
                   "flow":note["flow"],
                   "error":note["error"],
                   "degraded":True})
        return note
    def _surface_pending_channel_fallbacks(self):
        """Drain degradations recorded by flows that have no runner (login
        submit, session replay, capture) and put them in THIS site's run
        record. Returns how many were surfaced. Called by the runner after
        each login flow it owns and at every browser launch (the keeper's
        login has no runner at hand; its note waits for the site's next
        launch). A caller with no event log surfaces nothing and DRAINS
        nothing -- the note stays for a real owner."""
        from . import cloak as _cloak
        _log=getattr(self,"log_event",None)
        if _log is None:
            return 0
        try:
            notes=_cloak.drain_channel_fallbacks(self.site_id)
        except Exception:
            return 0
        for n in notes:
            verdict=("ran on bundled Chromium instead" if n.get("recovered")
                     else "and the bundled-Chromium retry ALSO failed")
            _log("browser",
                f"real Chrome unavailable (channel={n.get('channel')}) in "
                f"{n.get('flow')}; {verdict} -- {n.get('error','')}",
                extra={"requested_channel":n.get("channel"),
                       "recovered":bool(n.get("recovered")),
                       "flow":n.get("flow"),
                       "error":n.get("error",""),
                       "degraded":True})
        return len(notes)
    def _launch_browser(self,headless=None,use_persistent=None,worker_idx=None,profile_override=None,netns=None):
        """Phase 9 / v3.66.141: unified browser launcher routed through the
        shared cloak wrapper so every runner flow honours the configured
        backend (cloakbrowser|playwright). Returns a 4-tuple
        ``(browser, ctx, pw, backend)``:
          (None, ctx, pw, backend)     persistent context (no separate browser
                                       handle; ctx survives across app runs)
          (browser, None, pw, backend) caller creates its own context via
                                       browser.new_context(...) — used by the
                                       worker which spawns one ctx per URL.
        ``pw`` is the started Playwright instance on the ``playwright`` backend
        (caller must ``pw.stop()`` after closing) and ``None`` on the
        ``cloakbrowser`` backend (the context/browser stops its own).

        `worker_idx` is forwarded to _profile_dir so each worker uses its
        own profile directory (Chrome's SingletonLock prevents two
        processes from opening the same dir simultaneously). None or 0
        means "main" — backward compatible with the single-worker case.

        `profile_override` (Phase 41.6): when provided, overrides the
        worker profile dir. Used by manual login/teach sessions to point
        at the dedicated manual profile (so password manager extensions
        survive across sessions without polluting worker profiles).

        `netns` (F5 Phase 2, v3.66.701): when the worker holds a per-capture
        network namespace, it is threaded to the cloak wrapper, which launches
        the browser THROUGH the 699 shim so the browser process itself lives
        inside the namespace (Playwright spawns Chromium itself -- there is no
        argv for the caller to wrap). None -> byte-identical prior launch.

        Caller decides which form it wants by passing `use_persistent`."""
        # Row 723: a login flow (keeper-driven, or any owner without a run
        # record) may have left this site's degradation in cloak's ledger;
        # it belongs in the record before this launch's own events.
        try:
            self._surface_pending_channel_fallbacks()
        except Exception:
            pass
        if headless is None: headless=bool(self.config.get("headless", True))
        self._launched_headless = headless  # row923: the asset filter keys off this
        if use_persistent is None:
            use_persistent=bool(self.config.get("use_persistent_profile",True))
        channel=None
        if self.config.get("use_real_chrome",False):
            channel="chrome"
        from . import cloak as _cloak
        launch_config=dict(self.config)
        if channel and _cloak.resolve_backend(self.config,use_default=False) is None: launch_config["browser_backend"]="playwright"
        # Build launch options
        launch_kwargs={"headless":headless,"args":self._launch_args(headless=headless)}
        if channel: launch_kwargs["channel"]=channel
        # Phase 15.8: per-site proxy. Format: "scheme://[user:pass@]host:port".
        # SOCKS5 (Tor, WARP), HTTP, and HTTPS proxies are all supported by
        # Playwright. Same proxy gets passed to curl_cffi/httpx for direct
        # downloads via _http_download. Free option: Cloudflare WARP via
        # `warp-cli connect` on the host then proxy="socks5://127.0.0.1:40000"
        # (the default WARP socks port).
        proxy_url = (self.config.get("proxy") or "").strip()
        # v3.43.60: if no explicit proxy is set, ask vpn_runtime whether this
        # site has a tunnel configured. Returns None if no VPN configured (use
        # system network). Explicit proxy wins to preserve the v3.43.0 behavior.
        if not proxy_url and _VPN_RUNTIME_AVAILABLE:
            try:
                vpn_proxy = vpn_runtime.playwright_proxy_for_site(self.site_id)
                if vpn_proxy:
                    launch_kwargs["proxy"] = vpn_proxy
                    sys.stderr.write(f"  using VPN tunnel proxy {vpn_proxy['server']}\n")
            except vpn_runtime.VPNRequiredError as e:
                # Site requires VPN but it's not available — fail fast rather than leak.
                sys.stderr.write(f"  ERROR: VPN required for {self.site_id}: {e}\n")
                raise
            except Exception as e:
                sys.stderr.write(f"  vpn proxy resolution raised (continuing): {e}\n")
        if proxy_url:
            try:
                from urllib.parse import urlparse
                pp = urlparse(proxy_url)
                # Playwright wants {server, username, password} not a URL.
                # Strip credentials from server but keep them in username/password.
                proxy_dict = {"server": f"{pp.scheme}://{pp.hostname}{':'+str(pp.port) if pp.port else ''}"}
                if pp.username: proxy_dict["username"] = pp.username
                if pp.password: proxy_dict["password"] = pp.password
                launch_kwargs["proxy"] = proxy_dict
                sys.stderr.write(f"  using proxy {pp.scheme}://{pp.hostname} (creds={'yes' if pp.username else 'no'})\n")
            except Exception as e:
                sys.stderr.write(f"  proxy config parse error (ignored): {e}\n")
        flow = (f"manual download[{self.site_id}]" if profile_override
                else f"worker[{self.site_id}/{worker_idx or 0}]")
        if use_persistent:
            # 9.3 path: persistent context survives across runs of the app.
            # Phase 41.6: profile_override points manual download/teach
            # sessions at a dedicated profile dir.
            # v3.66.141: launch via the shared cloak wrapper (honours the
            # configured backend; owns the Playwright lifecycle).
            user_data_dir = profile_override if profile_override else self._profile_dir(worker_idx)
            ctx_kwargs=dict(self._context_options(headless=headless))
            ctx_kwargs.update(launch_kwargs)
            # v3.66.465: plugin config providers may layer per-site launch
            # knobs (proxy/headless/user_agent/args/viewport). Inert unless a
            # provider is registered AND changes a value from the base config.
            try:
                from . import plugins as _pl
                _eff=_pl.resolve_site_config(self.site_id,self.config)
                for _k in ("proxy","headless","user_agent","args","viewport"):
                    if _k in _eff and _eff.get(_k)!=self.config.get(_k):
                        ctx_kwargs[_k]=_eff[_k]
                # GATED full-access hook: mutate launch kwargs in place. No-op
                # unless allow_full_access is enabled.
                _pl.fire_lifecycle("before_launch",ctx_kwargs,self.site_id)
            except Exception:
                pass
            extra=dict(ctx_kwargs)
            extra.pop("headless",None)
            args_val=extra.pop("args",None)
            ua_val=extra.pop("user_agent",None)
            detail="persistent "+("manual profile" if profile_override else "profile")
            ctx = used_pw = None
            try:
                ctx,used_pw,backend=_cloak.open_persistent_context(
                    user_data_dir=str(user_data_dir),headless=headless,
                    args=args_val,user_agent=ua_val,config=launch_config,
                    netns=netns,**extra)
                self._apply_persistent_cookie_file(ctx)
                self._install_stealth(ctx)
                self._maybe_install_adaptive_manifest_capture(ctx)
                self._maybe_install_websocket_json_capture(ctx)
                # v3.66.465: GATED full-access after_context hook. Live ctx +
                # first page (if any). No-op unless allow_full_access is on.
                try:
                    from . import plugins as _pl
                    _pg=(ctx.pages[0] if getattr(ctx,"pages",None) else None)
                    _pl.fire_lifecycle("after_context",ctx,_pg,self.site_id)
                except Exception:
                    pass
                _cloak.log_choice(flow,backend,detail)
                return None,ctx,used_pw,backend
            except Exception as e:
                # A successful launch can still fail during cookie/stealth
                # setup. Release that pair before opening its replacement.
                try:
                    try:
                        if ctx is not None:
                            ctx.close()
                    finally:
                        if used_pw is not None:
                            used_pw.stop()
                except Exception:
                    pass  # Cleanup failure must not prevent the fallback.
                # Common on the playwright backend: "Chrome channel not
                # installed". Retry without channel (bundled Chromium for THIS
                # site only) rather than letting the whole worker die.
                msg=str(e)[:100]
                sys.stderr.write(f"  launch persistent (channel={channel}) failed: {msg}\n")
                if channel and "channel" in extra:
                    sys.stderr.write("  retrying without system Chrome channel\n")
                    extra.pop("channel",None)
                    ctx = used_pw = None
                    try:
                        ctx,used_pw,backend=_cloak.open_persistent_context(
                            user_data_dir=str(user_data_dir),headless=headless,
                            args=args_val,user_agent=ua_val,config=launch_config,
                            netns=netns,**extra)
                        self._apply_persistent_cookie_file(ctx)
                        self._install_stealth(ctx)
                        self._maybe_install_adaptive_manifest_capture(ctx)
                        self._maybe_install_websocket_json_capture(ctx)
                        _cloak.log_choice(flow,backend,detail+" (bundled)")
                        self._record_channel_fallback(flow,channel,msg,True)
                        return None,ctx,used_pw,backend
                    except Exception as e2:
                        try:
                            try:
                                if ctx is not None:
                                    ctx.close()
                            finally:
                                if used_pw is not None:
                                    used_pw.stop()
                        except Exception:
                            pass
                        sys.stderr.write(f"  launch persistent fallback failed: {str(e2)[:100]}\n")
                        self._record_channel_fallback(flow,channel,str(e2)[:100],False)
                # Persistent failed entirely — fall through to non-persistent
        # Non-persistent path: caller will create its own context
        extra=dict(launch_kwargs)
        extra.pop("headless",None)
        args_val=extra.pop("args",None)
        try:
            browser,used_pw,backend=_cloak.launch_browser(
                headless=headless,args=args_val,config=launch_config,
                netns=netns,**extra)
        except Exception as e:
            if channel and "channel" in extra:
                sys.stderr.write(f"  launch (channel={channel}) failed: {str(e)[:100]}; falling back to bundled\n")
                extra.pop("channel",None)
                try:
                    browser,used_pw,backend=_cloak.launch_browser(
                        headless=headless,args=args_val,config=launch_config,
                        netns=netns,**extra)
                except Exception as e2:
                    self._record_channel_fallback(flow,channel,str(e2)[:100],False)
                    raise
                self._record_channel_fallback(flow,channel,str(e)[:100],True)
            else:
                raise
        _cloak.log_choice(flow,backend,"non-persistent")
        return browser,None,used_pw,backend
    def _install_stealth(self,ctx):
        """Phase 9.2: install the stealth init script on this context. Runs
        before every page's own scripts on every navigation. Uses the
        BrowserContext-level add_init_script so we don't have to remember
        to install per-page."""
        # EME detection recorder: always installed, independent of the stealth
        # toggle. Detection only (records requestMediaKeySystemAccess, calls
        # through) -- never circumvents. See eme_detect.py.
        try:
            from .eme_detect import EME_INIT_JS
            ctx.add_init_script(EME_INIT_JS)
        except Exception as e:
            sys.stderr.write(f"  eme recorder install failed: {str(e)[:80]}\n")
        if not self.config.get("use_stealth",True): return
        try:
            from .constants import STEALTH_JS
            ctx.add_init_script(STEALTH_JS)
        except Exception as e:
            sys.stderr.write(f"  stealth install failed: {str(e)[:80]}\n")
    # row923: telemetry is judged by HOST (third-party trackers) or by a
    # path SEGMENT (a first-party /collect, /beacon, /analytics, /telemetry
    # endpoint) -- never by a bare substring of the whole URL
    # ("/collections/12" is a document, not a beacon) -- and only on the
    # resource types a tracker uses. Documents, API calls (xhr/fetch),
    # media and manifests are never subject to the telemetry rule.
    # Stylesheets are NEVER aborted (round 4): CSS decides what is VISIBLE,
    # and every gate/selector path in this product keys off visibility
    # (interstitial._visible_first, is_visible counts, trigger selectors,
    # clearance) -- a CSS-hidden duplicate control must stay hidden. A
    # site's own CSS often comes from a CDN host, so no first-party rule
    # can make aborting it safe either.
    _ASSET_FILTER_BLOCKED_TYPES = frozenset({"image", "font"})
    _TELEMETRY_TYPES = frozenset({"script", "ping", "beacon", "other", "eventsource"})
    _TELEMETRY_HOSTS = (
        "google-analytics.com", "googletagmanager.com", "doubleclick.net",
        "segment.io", "mixpanel.com",
    )
    _TELEMETRY_PATH_RE = re.compile(r"/(?:analytics|telemetry|beacon|collect)(?:/|$)")

    def _install_browser_asset_filter(self, page, *, headless=None):
        """Abort disposable browser assets without touching page data or media.

        Installed only on HEADLESS worker pages: the operator's headed
        manual/teach window (runner_manual) must render exactly what the site
        serves. ``headless`` defaults to the mode this runner launched with
        (``_launch_browser``); a runner whose browser was launched elsewhere
        (a sub-crawler, an alternate harness) falls back to its own
        ``config["headless"]`` -- the same default ``_launch_browser`` uses --
        so a headless worker is never silently left unfiltered.
        """
        if not self.config.get("browser_asset_filter", True):
            return
        if headless is None:
            headless = getattr(self, "_launched_headless", None)
        if headless is None:
            headless = bool(self.config.get("headless", True))
        if headless is not True:
            return
        blocked_types = self._ASSET_FILTER_BLOCKED_TYPES
        telemetry_types = self._TELEMETRY_TYPES
        telemetry_hosts = self._TELEMETRY_HOSTS
        telemetry_path_re = self._TELEMETRY_PATH_RE

        def first_party(host):
            # the page's own site (or a sub/parent domain of it) serves its
            # player and UI bundles from wherever it likes -- a first-party
            # /analytics/app.js is application code, not a tracker
            page_url = str(getattr(page, "url", "") or "")
            page_host = (urllib.parse.urlsplit(page_url.lower()).hostname or "") if page_url else ""
            if not page_host or not host:
                return None  # unknown: the path rule does not judge scripts
            return host == page_host or host.endswith("." + page_host) or page_host.endswith("." + host)

        def filter_request(route, request):
            resource_type = str(getattr(request, "resource_type", "") or "")
            if resource_type in blocked_types:
                route.abort()
                return
            if resource_type in telemetry_types:
                url = str(getattr(request, "url", "") or "").lower()
                parsed = urllib.parse.urlsplit(url)
                host = parsed.hostname or ""
                if any(host == h or host.endswith("." + h) for h in telemetry_hosts):
                    route.abort()
                    return
                if telemetry_path_re.search(parsed.path or ""):
                    # a script is judged by its path only when it is
                    # THIRD-PARTY to the page; pings/beacons/other always
                    if resource_type != "script" or first_party(host) is False:
                        route.abort()
                        return
            route.continue_()

        page.route("**/*", filter_request)
    def _apply_stealth_library_to_page(self, page):
        """v3.43.56: if `use_stealth_library` is set AND the
        playwright-stealth library is installed, apply its evasions
        on top of the built-in STEALTH_JS. The library operates per-
        page (not per-context) so this gets called every time we
        create a new page in the worker pool.

        Fail-open: any error is logged once and ignored. The page
        is still usable via the built-in stealth.
        """
        self._install_browser_asset_filter(page)
        try:
            from . import stealth as _stealth
            applied, detail = _stealth.apply_to_page(page, self.config)
            if applied and not getattr(self, "_stealth_library_logged", False):
                sys.stderr.write(
                    f"  stealth-library: {detail} (one-time log)\n")
                self._stealth_library_logged = True
        except Exception as e:
            sys.stderr.write(
                f"  stealth-library: unexpected error: "
                f"{type(e).__name__}: {str(e)[:80]}\n")
    @staticmethod
    def _spa_settlement_script():
        """Row 914: JS installed on a page to detect SPA route transitions
        (``history.pushState``/``replaceState``) and expose a settlement
        barrier that only flips true once the DOM has gone quiet (no
        MutationObserver activity for ``quietMs``) since the LATEST
        transition -- so an extractor waiting on it never reads a route's
        DOM before its async render has actually finished, and never reads
        a stale route's DOM if another navigation fired in the meantime.

        Exposes: ``window.__bd_spa_nav_count`` (increments per transition),
        ``window.__bd_spa_settled`` (bool), ``window.__bd_spa_settled_nav``
        (the nav_count value the current ``settled=true`` applies to).
        """
        return """
(function(){
  if (window.__bd_spa_hooked) return true;
  window.__bd_spa_hooked = true;
  window.__bd_spa_nav_count = 0;
  window.__bd_spa_settled = true;
  window.__bd_spa_settled_nav = 0;
  var quietMs = 150;
  // a DOM that never goes quiet (spinner, ticker, animation loop) must not
  // starve the barrier: at most maxSettleMs after the latest transition
  // the route is declared settled whatever the observer still sees
  var maxSettleMs = 1500;
  var timer = null;
  var navStartedAt = Date.now();
  function settleNow(navAt){
    window.__bd_spa_settled = true;
    window.__bd_spa_settled_nav = navAt;
  }
  function scheduleSettle(){
    if (timer) { clearTimeout(timer); timer = null; }
    var navAtSchedule = window.__bd_spa_nav_count;
    var elapsed = Date.now() - navStartedAt;
    if (elapsed >= maxSettleMs) { settleNow(navAtSchedule); return; }
    timer = setTimeout(function(){
      timer = null;
      settleNow(navAtSchedule);
    }, Math.min(quietMs, maxSettleMs - elapsed));
  }
  function markUnsettled(){
    window.__bd_spa_nav_count += 1;
    window.__bd_spa_settled = false;
    navStartedAt = Date.now();
    scheduleSettle();
  }
  var origPush = history.pushState;
  var origReplace = history.replaceState;
  history.pushState = function(){
    var r = origPush.apply(this, arguments);
    markUnsettled();
    return r;
  };
  history.replaceState = function(){
    var r = origReplace.apply(this, arguments);
    markUnsettled();
    return r;
  };
  window.addEventListener('popstate', markUnsettled);
  var observer = new MutationObserver(function(mutations){
    if (mutations.length === 0) return;
    // past the settle window of the latest transition, mutations are the
    // page's own life (tickers, players), not a route still mounting
    if (Date.now() - navStartedAt >= maxSettleMs) return;
    window.__bd_spa_settled = false;
    scheduleSettle();
  });
  // via add_init_script this runs at document start, when neither
  // documentElement nor body exists yet: observe() would throw and the
  // barrier would silently degrade to navigation-only (no mutation
  // settlement at all). Attach to the root as soon as there is one.
  function attach(){
    var root = document.documentElement || document.body;
    if (!root) return false;
    observer.observe(root, {
      childList: true, subtree: true, attributes: true, characterData: true,
    });
    window.__bd_spa_observer = observer;
    return true;
  }
  if (!attach()) {
    var probe = setInterval(function(){ if (attach()) clearInterval(probe); }, 0);
    document.addEventListener('DOMContentLoaded', function(){ attach(); clearInterval(probe); }, {once: true});
  }
  return true;
})()
"""

    def _install_spa_settlement_hooks(self, page):
        """Arm the pushState/replaceState + MutationObserver settlement
        barrier on ``page``. ``add_init_script`` covers future navigations;
        the immediate ``evaluate`` also covers a page that has already
        loaded (matches affordance_learning.py's operator-activity hook)."""
        script = self._spa_settlement_script()
        try:
            page.add_init_script(script=script)
            return bool(page.evaluate(script))
        except Exception:
            return False

    def _settle_after_navigation(self, page, timeout_ms=5000):
        """The worker's post-``goto`` hook (runner.py): arm the barrier on
        this page (idempotent) and wait for the DOM to settle since the
        latest route transition. Off with config ``spa_settlement`` false.
        Never raises; a timeout returns False and the extractor proceeds
        with what is there (bounded wait, same as before this row)."""
        if not self.config.get("spa_settlement", True):
            return None
        if not self._install_spa_settlement_hooks(page):
            return False
        return self._wait_for_spa_settlement(page, timeout_ms=timeout_ms)

    def _wait_for_spa_settlement(self, page, timeout_ms=5000):
        """Block until the DOM has settled since the LATEST route
        transition (``__bd_spa_settled_nav`` catches up to
        ``__bd_spa_nav_count``), or return False on timeout. Comparing the
        nav count (not just the settled flag) is what prevents an
        extractor from reading a stale route's DOM during a fast flurry of
        navigations."""
        try:
            page.wait_for_function(
                "window.__bd_spa_settled === true && "
                "window.__bd_spa_settled_nav === window.__bd_spa_nav_count",
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False

    def _warm_session(self, page):
        """Phase 15.7: visit configured warmup URLs before deep-linking
        to a video page. The first request to a deep URL with no cookies
        and no referrer is the #1 Cloudflare flag — humans browse from
        the homepage and click through. Warmup makes us look like that.

        Configurable via per-site `warmup_urls` (newline or comma
        separated list of relative paths or full URLs) and `warmup_every`
        (seconds; 0 = every URL, default 1800 = every 30min).

        Skipped if:
          - No warmup_urls configured
          - We warmed within the last warmup_every seconds
          - The site already has cookies from a successful login (we're
            already known to the server)"""
        import random as _rnd
        from .settlement import wait_for_settlement
        warmup_raw = (self.config.get("warmup_urls") or "").strip()
        if not warmup_raw: return
        every = int(self.config.get("warmup_every", 1800) or 1800)
        if every > 0 and (time.time() - self._last_warmup_at) < every:
            return  # warmed recently, skip
        urls = [u.strip() for line in warmup_raw.replace(",", "\n").splitlines()
                for u in [line.strip()] if u.strip()]
        if not urls: return
        # Pick 1-3 random URLs from the list — visiting all of them every
        # cycle would itself be patterned. Random subset feels more human.
        sample_count = min(len(urls), _rnd.randint(1, 3))
        sample = _rnd.sample(urls, sample_count)
        # If a URL is relative, prepend the login_url's origin
        try:
            from urllib.parse import urlparse, urljoin
            base = self.config.get("login_url") or ""
            origin = ""
            if base:
                p = urlparse(base)
                if p.scheme and p.netloc:
                    origin = f"{p.scheme}://{p.netloc}"
        except Exception: origin = ""
        for u in sample:
            if not u.startswith("http") and origin:
                u = urljoin(origin + "/", u.lstrip("/"))
            elif not u.startswith("http"):
                continue  # can't resolve, skip
            try:
                self.log_event("warmup", f"Visiting {u[:80]}", url=u)
                page.goto(u, wait_until="domcontentloaded", timeout=20000)
                # Row 926 / O989: readiness is event-driven -- in-flight requests
                # at zero and the DOM quiet for 250ms -- before the page is
                # "read". The reading pause below is pacing, not readiness,
                # and stays. A page that never settles just proceeds after the
                # timeout (fail-soft: warmup is best effort).
                settled = wait_for_settlement(page, timeout=5.0)
                self.log_event("warmup", f"Settled: {settled.reason} in {settled.duration_ms:.0f}ms "
                                         f"({settled.requests_seen} requests, {settled.long_polls_ignored} long-polls ignored)", url=u)
                # Random scroll to look like reading
                scroll_y = _rnd.randint(200, 800)
                try: page.mouse.wheel(0, scroll_y)
                except Exception: pass
                time.sleep(_rnd.uniform(2.5, 6.0))
            except Exception as e:
                self.log_event("warmup", f"Warmup visit failed: {str(e)[:80]}", url=u)
                # If a warmup URL fails, don't waste time on the rest —
                # the site is probably blocking us regardless.
                return
        self._last_warmup_at = time.time()
        self.log_event("warmup", f"Warmed up via {sample_count} URL(s)")


from .browser_sentinel import (  # noqa: E402
    runner_maybe_recycle_browser as _rmrb,
    runner_check_browser_rss as _rcbr,
)
BrowserMixin.maybe_recycle_browser = _rmrb
BrowserMixin.check_browser_rss = _rcbr

