"""runner_browser -- Playwright context/launch/profile/stealth/warm

Extracted from runner.py (SiteRunner) @v3.66.401, PHASE 3 runner cut 4.
Mixin: methods reference self.* only; NO __init__. Import block derived by AST
free-name scan of the moved bodies. Cycle rule: nothing from .runner.
"""
import sys, time

from .detect import safe_dest

# vpn_runtime soft import (moved verbatim from runner.py; flat sibling).
try:
    from . import vpn_runtime
    _VPN_RUNTIME_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_browser] vpn_runtime import failed (degraded): {_e}\n")
    _VPN_RUNTIME_AVAILABLE = False


class BrowserMixin:
    def _pw_save(self,dl,final_path):
        """Fallback: let Playwright stream the download to disk."""
        dest=safe_dest(final_path)
        dl.save_as(str(dest))
        return dest.stat().st_size if dest.exists() else 0
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
            "--no-sandbox",
            "--disable-notifications","--disable-popup-blocking",
            "--disable-infobars","--no-default-browser-check","--no-first-run",
            "--disable-features=PushMessaging,Translate,AutomationControlled",
            # 9.2 supplement: hide the headless flag in chrome://flags state
            "--disable-blink-features=AutomationControlled",
        ]
        if not headless:
            args.append("--window-size=1366,800")
            # Autofill flags only on headed launches (takeover, manual login).
            # See login.py:open_manual_login_browser for the full rationale.
            args.append("--password-store=basic")
            args.append("--enable-features=AutofillEnableAccountWalletStorage,PasswordManagerEnabled")
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
        if headless is None: headless=bool(self.config.get("headless", True))
        if use_persistent is None:
            use_persistent=bool(self.config.get("use_persistent_profile",True))
        # 9.1: prefer system Chrome over bundled Chromium when available.
        # Fall back gracefully if Chrome isn't installed.
        channel=None
        if self.config.get("use_real_chrome",True):
            channel="chrome"
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
        from . import cloak as _cloak
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
            try:
                ctx,used_pw,backend=_cloak.open_persistent_context(
                    user_data_dir=str(user_data_dir),headless=headless,
                    args=args_val,user_agent=ua_val,config=self.config,
                    netns=netns,**extra)
                self._install_stealth(ctx)
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
                # Common on the playwright backend: "Chrome channel not
                # installed". Retry without channel (bundled Chromium for THIS
                # site only) rather than letting the whole worker die.
                msg=str(e)[:100]
                sys.stderr.write(f"  launch persistent (channel={channel}) failed: {msg}\n")
                if channel and "channel" in extra:
                    sys.stderr.write("  retrying without system Chrome channel\n")
                    extra.pop("channel",None)
                    try:
                        ctx,used_pw,backend=_cloak.open_persistent_context(
                            user_data_dir=str(user_data_dir),headless=headless,
                            args=args_val,user_agent=ua_val,config=self.config,
                            netns=netns,**extra)
                        self._install_stealth(ctx)
                        _cloak.log_choice(flow,backend,detail+" (bundled)")
                        return None,ctx,used_pw,backend
                    except Exception as e2:
                        sys.stderr.write(f"  launch persistent fallback failed: {str(e2)[:100]}\n")
                # Persistent failed entirely — fall through to non-persistent
        # Non-persistent path: caller will create its own context
        extra=dict(launch_kwargs)
        extra.pop("headless",None)
        args_val=extra.pop("args",None)
        try:
            browser,used_pw,backend=_cloak.launch_browser(
                headless=headless,args=args_val,config=self.config,
                netns=netns,**extra)
        except Exception as e:
            if channel and "channel" in extra:
                sys.stderr.write(f"  launch (channel={channel}) failed: {str(e)[:100]}; falling back to bundled\n")
                extra.pop("channel",None)
                browser,used_pw,backend=_cloak.launch_browser(
                    headless=headless,args=args_val,config=self.config,
                    netns=netns,**extra)
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
    def _apply_stealth_library_to_page(self, page):
        """v3.43.56: if `use_stealth_library` is set AND the
        playwright-stealth library is installed, apply its evasions
        on top of the built-in STEALTH_JS. The library operates per-
        page (not per-context) so this gets called every time we
        create a new page in the worker pool.

        Fail-open: any error is logged once and ignored. The page
        is still usable via the built-in stealth.
        """
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
