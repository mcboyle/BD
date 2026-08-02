"""runner_extractors -- per-site extractors + ytdlp/deep-detect fallback

Extracted from runner.py (SiteRunner) @v3.66.402, PHASE 3 runner cut 5.
Mixin: methods reference self.* only; NO __init__. Import block derived by AST
free-name scan of the moved bodies (the seams doc omitted all 6 conditional
soft-import blocks). Cycle rule: kernel from .runner_util, nothing from .runner.
The adapter soft-import blocks are DUPLICATED here (the core dispatch in
runner.py still references the same flags); flat-sibling imports are idempotent.
"""
import os, re, subprocess, sys
from datetime import datetime

# Cut 665 (2.5): a yt-dlp --limit-rate value must be a bare number with an
# optional K/M/G suffix (e.g. "2M", "500K", "1048576", "4.2M"). Anything else
# (empty, a flag-looking string, shell metacharacters) is dropped so a config
# value can never smuggle a flag or metacharacter into yt-dlp's option surface.
_RATE_RE = re.compile(r"^\d+(\.\d+)?[KMGkmg]?$")

from .runner_util import DEFAULT_MIN_RESOLUTION
from .db import db_log
from .detect import find_best_download, fmt_bytes
from .fname import resolve_filename_template, format_duration_for_filename
# F5 (v3.66.689): per-capture netns isolation for the subprocess download
# fallbacks. The engine shipped @686; this routes yt-dlp/gallery-dl launches
# through it (capture_netns bracket + netns_exec_argv wrap).
from . import netns_isolation

# tier_probe soft import (moved verbatim from runner.py; flat sibling).
try:
    from . import tier_probe as _tier_probe
    _TIER_PROBE_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_extractors] tier_probe import failed (degraded): {_e}\n")
    _tier_probe = None
    _TIER_PROBE_AVAILABLE = False

# extractors_aylo soft import.
try:
    from . import extractors_aylo as _aylo
    _AYLO_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_extractors] extractors_aylo import failed (degraded): {_e}\n")
    _aylo = None
    _AYLO_AVAILABLE = False

# extractors_vixen soft import.
try:
    from . import extractors_vixen as _vixen
    _VIXEN_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_extractors] extractors_vixen import failed (degraded): {_e}\n")
    _vixen = None
    _VIXEN_AVAILABLE = False

# extractors_dl8 soft import.
try:
    from . import extractors_dl8 as _dl8
    _DL8_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_extractors] extractors_dl8 import failed (degraded): {_e}\n")
    _dl8 = None
    _DL8_AVAILABLE = False

# extractors_jsonapi soft import.
try:
    from . import extractors_jsonapi as _jsonapi
    _JSONAPI_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_extractors] extractors_jsonapi import failed (degraded): {_e}\n")
    _jsonapi = None
    _JSONAPI_AVAILABLE = False

# vpn_runtime soft import.
try:
    from . import vpn_runtime
    _VPN_RUNTIME_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_extractors] vpn_runtime import failed (degraded): {_e}\n")
    _VPN_RUNTIME_AVAILABLE = False


def _socks_remote_dns(proxy_url):
    """Track-K (A1): rewrite a bare ``socks5://`` proxy to ``socks5h://`` so a
    subprocess (yt-dlp) resolves the target host *through* the tunnel instead of
    on the clear interface. Any other scheme (http/https/socks5h/explicit) is
    returned verbatim. Empty/None -> "" (no proxy)."""
    p = (proxy_url or "").strip()
    if p.startswith("socks5://"):
        return "socks5h://" + p[len("socks5://"):]
    return p


def _ssrf_guarded_http_get(inner):
    """v3.66.765 (SSRF-REM, defense-in-depth): wrap an INJECTED http_get with the
    canonical pre-fetch SSRF host check.

    deep_detect's DEFAULT http_get is fully guarded (pre-fetch + redirect hook +
    IP-pinned transport). The runner instead injects a raw ``_client.get(url)``
    closure and relied only on a downstream resolver guard. This wraps that
    closure so a page-derived URL to private / loopback / link-local (169.254/16)
    / CGNAT (100.64/10) space is refused BEFORE the fetch, at the injection edge,
    independent of the downstream guard. Imports are function-local and target
    bulk_downloader.provider_resolve, which runner_extractors already imports (no
    new import edge)."""
    from urllib.parse import urlparse
    from bulk_downloader.provider_resolve import _is_safe_public_host, SSRFBlocked

    def _guarded(url):
        ok, reason = _is_safe_public_host(urlparse(url).hostname or "")
        if not ok:
            raise SSRFBlocked("injected http_get SSRF guard: %s" % reason)
        return inner(url)

    return _guarded


_PLUGIN_DIR_CONFIG_KEY = {
    "ytdlp_plugin": "ytdlp_plugin_dirs",
    "gallerydl_plugin": "gallerydl_plugin_dirs",
}


def _permitted_plugin_dirs(kind, config):
    """INTEROP-GH-1 (v3.66.655): resolve the external plugin dirs for ``kind``
    (``ytdlp_plugin`` / ``gallerydl_plugin``) and, when interop governance is on,
    keep ONLY the dirs the interop_registry permits.

    Mirrors the chromium_extension gate (runner_browser) and the jd_plugin gate
    (runner_integrations): the ``ytdlp_plugin`` / ``gallerydl_plugin`` kinds were
    defined in the GOV-1 keystone but had no load-time consumer; this is that
    consumer. Semantics match the other two:

      * Governance OFF (``interop_governance_enabled`` absent/false) -> the
        operator-configured dirs pass through unchanged.
      * Governance ON -> a dir loads only if it is registered + risk-acknowledged
        + enabled AND its live content hash still matches the pinned provenance
        (``is_permitted(kind, dir, dir_sha256(dir))``) -- an un-acked or silently
        changed plugin dir is dropped.

    The config source is a plain list key (``ytdlp_plugin_dirs`` /
    ``gallerydl_plugin_dirs``), resolved off ``config`` -- no new BD_-prefixed env
    var. No dirs configured (the default for every existing site) -> returns
    ``[]`` -> the cmd builders emit no plugin flag -> zero behavior change. One
    registry read per fallback launch over a few dirs; cheap.
    """
    key = _PLUGIN_DIR_CONFIG_KEY.get(kind)
    raw = config.get(key) if key else None
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    dirs = [str(d) for d in raw if d]
    if not config.get("interop_governance_enabled", False):
        return dirs
    from . import interop_registry as _ir  # function-local: no tracked edge
    return [d for d in dirs if _ir.is_permitted(kind, d, _ir.dir_sha256(d))]


def _build_ytdlp_cmd(*, ytdlp, dl_dir, url, proxy_url=None, cookie_file="",
                     min_res=0, plugin_dirs=(), concurrent_fragments=0,
                     rate_limit="", netns=None):
    """Pure builder for the yt-dlp fallback CLI (unit-testable, no side effects).

    Track-K (A1): when ``proxy_url`` is in effect (an explicit per-site proxy or
    the site's VPN tunnel SOCKS url) it is threaded as ``--proxy`` using
    ``socks5h://`` so the subprocess does not leak DNS on the clear interface.
    ``proxy_url`` None/empty -> no ``--proxy`` arg (degrade open, unchanged).

    INTEROP-GH-1 (v3.66.655): ``plugin_dirs`` is the list of external yt-dlp
    plugin directories ALREADY gated by ``_permitted_plugin_dirs`` (provenance +
    risk-ack + live-pin, when interop governance is on). Each is threaded as its
    own ``--plugin-dirs DIR`` occurrence. Empty (the default for every site that
    has not configured plugin dirs) -> no flag -> byte-identical to the prior
    cmd. The gate lives in the caller, not here, so this stays a pure builder."""
    import os as _os
    cmd = [ytdlp, "--no-progress", "--no-warnings",
           "--no-playlist", "--restrict-filenames",
           "--output", _os.path.join(dl_dir, "%(title)s-%(id)s.%(ext)s")]
    proxy = _socks_remote_dns(proxy_url)
    if proxy:
        cmd += ["--proxy", proxy]
    # yt-dlp wants Netscape-format cookies; only a maintained .txt is usable.
    if cookie_file and cookie_file.endswith(".txt") and _os.path.exists(cookie_file):
        cmd += ["--cookies", cookie_file]
    if min_res > 0:
        cmd += ["-f", f"best[height>={min_res}]/best"]
    # Cut 665 (2.2): segment-parallel HLS/DASH. yt-dlp downloads one fragment at
    # a time by default; thread --concurrent-fragments only when the operator
    # configures N>1. N<=1 -> omit -> byte-identical cmd for unconfigured sites.
    if concurrent_fragments and int(concurrent_fragments) > 1:
        cmd += ["--concurrent-fragments", str(int(concurrent_fragments))]
    # Cut 665 (2.5): per-download bandwidth cap. Threaded only when the value is
    # a clean rate string (see _RATE_RE); anything else -> unlimited, byte-
    # identical prior cmd, and no chance of smuggling a flag as the value.
    if rate_limit and _RATE_RE.match(str(rate_limit).strip()):
        cmd += ["--limit-rate", str(rate_limit).strip()]
    # INTEROP-GH-1: external plugin dirs BEFORE the '--' terminator so a dir can
    # never be smuggled into the positional/URL slot. yt-dlp 2026.03.17 flag.
    for d in plugin_dirs:
        cmd += ["--plugin-dirs", d]
    # F-RUN01-02: terminate options with a bare '--' so a URL beginning with '-'
    # is treated as a positional target, never smuggled into yt-dlp's own flag
    # surface (e.g. --exec). A legitimate URL never starts with '-'.
    cmd.append("--")
    cmd.append(url)
    # F5 (v3.66.689): when a per-capture netns is active, confine the whole
    # subprocess by prefixing ``ip netns exec <ns>`` (LAST, so the wrap encloses
    # the fully-built argv). netns None/empty -> byte-identical prior cmd.
    if netns:
        cmd = netns_isolation.netns_exec_argv(netns, cmd)
    return cmd


def _build_gallerydl_cmd(*, gallerydl, dl_dir, url, proxy_url=None,
                         cookie_file="", min_res=0, plugin_dirs=(), netns=None):
    """Pure builder for the gallery-dl fallback CLI (unit-testable, no side
    effects). The gallery-dl analogue of ``_build_ytdlp_cmd``:

      * ``-d DIR`` sets the download destination;
      * ``proxy_url`` (explicit per-site proxy or the site's VPN SOCKS url) is
        threaded as ``--proxy`` via ``socks5h://`` so the subprocess resolves
        DNS through the tunnel, not on the clear interface (None/empty -> no
        proxy, degrade open);
      * a maintained Netscape ``.txt`` cookie file is passed via ``--cookies``;
      * ``min_res`` is a documented NO-OP here -- gallery-dl has no simple
        height filter (its ``--filter`` takes per-extractor metadata
        expressions whose keys vary by site), so rather than fabricate a filter
        we keep the parameter for signature parity with ``_build_ytdlp_cmd``;
      * the option list is terminated with a bare ``--`` (mirrors F-RUN01-02)
        so a URL beginning with ``-`` is always a positional target, never
        smuggled into gallery-dl's own flag surface. Verified: ``gallery-dl --
        --version`` treats ``--version`` as an (unsupported) URL, not the flag.
    """
    import os as _os
    cmd = [gallerydl, "-d", dl_dir]
    proxy = _socks_remote_dns(proxy_url)
    if proxy:
        cmd += ["--proxy", proxy]
    if cookie_file and cookie_file.endswith(".txt") and _os.path.exists(cookie_file):
        cmd += ["--cookies", cookie_file]
    # INTEROP-GH-1 (v3.66.655): external extractor dirs (already gated by
    # _permitted_plugin_dirs) via gallery-dl 1.32.5's ``-X`` / ``--extractors
    # PATH`` (action="append"). Placed before the '--' terminator, same
    # smuggle-safety as the yt-dlp builder. Empty -> byte-identical prior cmd.
    for d in plugin_dirs:
        cmd += ["-X", d]
    cmd.append("--")
    cmd.append(url)
    # F5 (v3.66.689): netns confinement wrap (see _build_ytdlp_cmd). None -> no-op.
    if netns:
        cmd = netns_isolation.netns_exec_argv(netns, cmd)
    return cmd


class ExtractorsMixin:
    def _try_ytdlp_fallback(self, url, fail_reason=""):
        """Phase 61 (v3.38.x): yt-dlp fallback layer. When the normal
        Playwright-based download flow fails on a URL, optionally try
        yt-dlp as a last resort before marking the URL needs_review/failed.

        Many sites have working yt-dlp extractors even when their HTML
        changes — yt-dlp gets updated extractor logic continuously, and
        works for thousands of sites via pattern matching against
        m3u8/MPD/progressive URLs in page sources.

        Controlled by config['use_ytdlp_fallback'] (default False, opt-in
        per site). Cookies from the site's cookie file are passed to
        yt-dlp via --cookies so authenticated content works.

        Returns (ok: bool, message: str, filename: str|None, size: int).
        - ok=True: yt-dlp downloaded successfully. Caller should mark done.
        - ok=False: yt-dlp couldn't handle it either. Caller proceeds with
          its original failure path."""
        if not self.config.get("use_ytdlp_fallback", False):
            return (False, "ytdlp_fallback disabled", None, 0)
        import shutil as _sh
        ytdlp = _sh.which("yt-dlp") or _sh.which("youtube-dl")
        if not ytdlp:
            return (False, "yt-dlp not installed on PATH", None, 0)
        dl_dir = (self.config.get("download_dir") or "").strip()
        if not dl_dir:
            return (False, "no download_dir configured", None, 0)
        try:
            os.makedirs(dl_dir, exist_ok=True)
        except Exception:
            return (False, "couldn't create download_dir", None, 0)
        # Track-K (A1): resolve the fail-closed download proxy up front, BEFORE
        # spawning yt-dlp. A vpn_required site whose tunnel is down/killed raises
        # VPNRequiredError -> fail closed here (return, no subprocess) rather than
        # egress the payload + DNS on the clear interface via the subprocess. An
        # explicit per-site proxy or the tunnel SOCKS url is threaded as --proxy
        # (socks5h, remote DNS); None == degrade open (site not required / no VPN).
        try:
            proxy_url = self._download_proxy_url()
        except Exception as e:
            if _VPN_RUNTIME_AVAILABLE and isinstance(e, vpn_runtime.VPNRequiredError):
                return (False,
                        f"vpn required for {self.site_id}, tunnel unavailable "
                        f"-- failing closed (yt-dlp not run): {e}", None, 0)
            raise
        cookie_file = (self.config.get("cookie_file") or "").strip()
        # Quality preference (Phase 67 — uses min_resolution as a hint)
        min_res = int(float(self.config.get("min_resolution", DEFAULT_MIN_RESOLUTION) or 0))  # v3.66.527: float() -> non-API fractional truncates, no ValueError
        plugin_dirs = _permitted_plugin_dirs("ytdlp_plugin", self.config)
        # Cut 665: download-engine tuning (2.2 segment-parallel, 2.5 bandwidth
        # cap). Both inert by default; the builder validates/omits, so a bad
        # config value degrades open (fragments=1, unlimited) rather than raising.
        try:
            concurrent_fragments = int(float(self.config.get("ytdlp_concurrent_fragments", 0) or 0))
        except (TypeError, ValueError):
            concurrent_fragments = 0
        rate_limit = (self.config.get("download_rate_limit") or "").strip()
        # F5 (v3.66.689): confine the yt-dlp subprocess to a per-capture netns
        # when the site opts in. capture_netns yields None (no isolation, path
        # unchanged) unless netns_isolation is enabled; on opt-in it creates +
        # tears down the ns and, if the ns is unavailable and the posture is
        # fail-closed (default), raises NetnsRequiredError so we return WITHOUT
        # spawning an un-isolated subprocess -- mirrors the VPN fail-closed guard.
        try:
            with netns_isolation.capture_netns(self.config, "dl", url) as ns:
                cmd = _build_ytdlp_cmd(ytdlp=ytdlp, dl_dir=dl_dir, url=url,
                                       proxy_url=proxy_url, cookie_file=cookie_file,
                                       min_res=min_res, plugin_dirs=plugin_dirs,
                                       concurrent_fragments=concurrent_fragments,
                                       rate_limit=rate_limit, netns=ns)
                self.log_event("ytdlp", f"Trying yt-dlp fallback for {fail_reason or 'failed URL'}", url=url)
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, encoding="utf-8")
                except subprocess.TimeoutExpired:
                    return (False, "yt-dlp timed out after 10 minutes", None, 0)
                except Exception as e:
                    return (False, f"yt-dlp exec failed: {e}", None, 0)
                if r.returncode != 0:
                    err = (r.stderr or r.stdout or "")[-200:]
                    return (False, f"yt-dlp exit {r.returncode}: {err}", None, 0)
                # Parse the output for the resulting filename. yt-dlp prints
                # "[download] Destination: PATH" or "[download] FILENAME has already been downloaded"
                stdout = r.stdout or ""
                filename = None
                # yt-dlp reports two different outcomes on the same happy path,
                # and the caller could not tell them apart: "Destination:" is a
                # real transfer, "has already been downloaded" moved nothing.
                # `size` is os.path.getsize either way, so the old return
                # asserted "Downloaded via yt-dlp fallback" for a download that
                # did not happen.
                fetched = True
                for line in stdout.splitlines():
                    if "[download] Destination:" in line:
                        filename = line.split("Destination:", 1)[1].strip()
                        break
                    if "has already been downloaded" in line:
                        filename = line.split("[download]", 1)[1].split(" has already")[0].strip()
                        fetched = False
                        break
                size = 0
                if filename and os.path.exists(filename):
                    try: size = os.path.getsize(filename)
                    except Exception: pass
                msg = ("Downloaded via yt-dlp fallback" if fetched
                       else "Already present (yt-dlp reported no download)")
                return (True, msg, filename, size, size if fetched else 0)
        except netns_isolation.NetnsRequiredError as e:
            return (False,
                    f"netns isolation required for {self.site_id}, unavailable "
                    f"-- failing closed (yt-dlp not run): {e}", None, 0, 0)

    def _try_gallerydl_fallback(self, url, fail_reason=""):
        """C6 (8.4): gallery-dl fallback layer. Tried AFTER the yt-dlp fallback
        (and before marking needs_review) for URLs neither BD's Playwright path
        nor yt-dlp handled. gallery-dl covers a set of sites yt-dlp does not.

        Opt-in via ``config['use_gallerydl_fallback']`` (default False, per
        site). Same fail-closed VPN discipline as the yt-dlp fallback: a
        vpn_required site whose tunnel is down returns WITHOUT spawning the
        subprocess. Cookies from the site's ``.txt`` cookie file are passed.

        Returns ``(ok, message, filename|None, size)`` -- same contract as
        ``_try_ytdlp_fallback`` so ``runner_challenge`` can chain them.
        """
        if not self.config.get("use_gallerydl_fallback", False):
            return (False, "gallerydl_fallback disabled", None, 0)
        import shutil as _sh
        gallerydl = _sh.which("gallery-dl")
        if not gallerydl:
            return (False, "gallery-dl not installed on PATH", None, 0)
        dl_dir = (self.config.get("download_dir") or "").strip()
        if not dl_dir:
            return (False, "no download_dir configured", None, 0)
        try:
            os.makedirs(dl_dir, exist_ok=True)
        except Exception:
            return (False, "couldn't create download_dir", None, 0)
        # Fail-closed proxy resolution up front (see _try_ytdlp_fallback).
        try:
            proxy_url = self._download_proxy_url()
        except Exception as e:
            if _VPN_RUNTIME_AVAILABLE and isinstance(e, vpn_runtime.VPNRequiredError):
                return (False,
                        f"vpn required for {self.site_id}, tunnel unavailable "
                        f"-- failing closed (gallery-dl not run): {e}", None, 0)
            raise
        cookie_file = (self.config.get("cookie_file") or "").strip()
        min_res = int(float(self.config.get("min_resolution", DEFAULT_MIN_RESOLUTION) or 0))
        plugin_dirs = _permitted_plugin_dirs("gallerydl_plugin", self.config)
        # F5 (v3.66.689): per-capture netns confinement (see _try_ytdlp_fallback).
        # capture_netns yields None when the site does not opt in (path unchanged);
        # on opt-in it brackets the subprocess and, when the ns is unavailable and
        # the posture is fail-closed (default), raises NetnsRequiredError so we
        # return WITHOUT spawning an un-isolated subprocess.
        try:
            with netns_isolation.capture_netns(self.config, "dl", url) as ns:
                cmd = _build_gallerydl_cmd(gallerydl=gallerydl, dl_dir=dl_dir, url=url,
                                           proxy_url=proxy_url, cookie_file=cookie_file,
                                           min_res=min_res, plugin_dirs=plugin_dirs, netns=ns)
                self.log_event("gallerydl",
                               f"Trying gallery-dl fallback for {fail_reason or 'failed URL'}",
                               url=url)
                # Snapshot dl_dir contents so we can identify what gallery-dl produced
                # (it prints downloaded paths, but the format is less stable than
                # yt-dlp's 'Destination:' line -- diffing the tree is more robust).
                before = set()
                try:
                    for root, _dirs, files in os.walk(dl_dir):
                        for fn in files:
                            before.add(os.path.join(root, fn))
                except Exception:
                    pass
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True,
                                       timeout=600, encoding="utf-8")
                except subprocess.TimeoutExpired:
                    return (False, "gallery-dl timed out after 10 minutes", None, 0, 0)
                except Exception as e:
                    return (False, f"gallery-dl exec failed: {e}", None, 0, 0)
                if r.returncode != 0:
                    err = (r.stderr or r.stdout or "")[-200:]
                    return (False, f"gallery-dl exit {r.returncode}: {err}", None, 0, 0)
                # Newly-written file (largest new file wins if several -- the media,
                # not a sidecar .json metadata file).
                filename = None
                size = 0
                try:
                    new_files = []
                    for root, _dirs, files in os.walk(dl_dir):
                        for fn in files:
                            p = os.path.join(root, fn)
                            if p not in before:
                                try:
                                    new_files.append((os.path.getsize(p), p))
                                except Exception:
                                    pass
                    if new_files:
                        new_files.sort(reverse=True)
                        size, filename = new_files[0]
                except Exception:
                    pass
                if filename is None:
                    # gallery-dl reported success but produced no new file (e.g. an
                    # already-downloaded archive hit). Treat as a soft miss so the
                    # caller keeps its own failure path.
                    return (False, "gallery-dl produced no new file", None, 0, 0)
                return (True, "Downloaded via gallery-dl fallback", filename, size, size)
        except netns_isolation.NetnsRequiredError as e:
            return (False,
                    f"netns isolation required for {self.site_id}, unavailable "
                    f"-- failing closed (gallery-dl not run): {e}", None, 0)

    def _try_deep_detect_fallback(self, page, url, learned_dl):
        """v3.66.6 — Backlog #7 wiring. When the primary scrape path
        returns no candidates (find_best_download → None), snapshot the
        page HTML and run deep_detect against it as a last-ditch
        attempt. deep_detect's offline analyzer knows about 14 candidate
        sources (resolution cards, HLS manifests, DASH MPDs, JSON-LD,
        state blobs, player configs, two-step POST workflows) that the
        legacy detect.py pipeline doesn't.

        v3.66.12 (roadmap P0) — switched from offline `deep_detect()` to
        `deep_detect_live()` so the runner benefits from the four
        v3.66.10 features that previously bypassed it:
          • probe_parallelism (concurrent HEAD probes)
          • manifest-following (HLS/DASH variant resolution)
          • URL canonicalization (dedup across surfaces)
          • signed-URL detection (skip pre-expired URLs)

        The original concern with live mode was unbounded latency
        ("would issue extra HTTP requests on every failed scrape").
        That risk is now bounded by `max_total_probe_time=8.0s`. To
        roll back without code changes, set `runner_use_live_dd:
        false` on the per-site config (defaults to true).

        Behavior:
          • Opt-in via per-site config: `deep_detect_fallback` (bool).
            Default off — runner conservatism. The site-level
            `template_auto_detect_mode='deep'` flag also implies it.
          • Per-site `runner_use_live_dd` (bool, default True) chooses
            between live and offline deep_detect.
          • On success, returns a `best`-shaped dict that the caller
            can use as-if `find_best_download` had returned it.
          • Persists discovered row_selectors / trigger_selectors into
            `learned_dl` so subsequent pages on the same site use the
            fast path. Same persistence shape as the existing learned
            block.
          • Silent on failure. Any exception in deep_detect is logged
            but doesn't escape — the caller falls through to the
            existing no-button-found handling.

        Returns:
            dict | None — `best`-shaped if a candidate was found.
        """
        # Per-site opt-in gate. Mode='deep' is the explicit site-setup
        # signal; `deep_detect_fallback` is the per-site toggle.
        mode = (self.config.get("template_auto_detect_mode") or "").lower()
        if not (self.config.get("deep_detect_fallback")
                or mode == "deep"):
            return None
        try:
            from . import deep_detect as _dd
        except Exception as e:
            sys.stderr.write(
                f"  deep-detect: import failed ({type(e).__name__}: {e}); "
                "fallback disabled\n")
            return None
        # Snapshot the current page. Playwright's `content()` returns
        # the serialized DOM, which is what deep_detect's parsers
        # expect. base_url comes from page.url so relative links
        # resolve correctly.
        try:
            html = page.content()
            base_url = page.url or url
        except Exception as e:
            sys.stderr.write(
                f"  deep-detect: page snapshot failed ({type(e).__name__}); "
                "fallback skipped\n")
            return None
        if not html or len(html) < 200:
            return None
        # Run the analyzer. v3.66.12: default to live mode (which
        # gets probe parallelism, manifest-follow, signed-URL
        # detection, URL canon). Off-switch: per-site config
        # `runner_use_live_dd: false` reverts to offline mode for
        # rollback without code changes.
        use_live = bool(self.config.get("runner_use_live_dd", True))
        try:
            if use_live:
                # Build a short-lived httpx client for live-mode
                # probes. Same pattern as other one-shot HTTP calls
                # in the runner (lines 871, 1355, 8441, etc.) —
                # client lifetime scoped to this call, no module-
                # level shared client. Probe budget is tight enough
                # that even a worst-case timeout (8s) is well below
                # the runner's per-job ceiling.
                import httpx as _httpx
                # Pass through the site's cookies + UA + referer so
                # the probes see the same authn the runner does.
                # Without this, signed URLs / paywalled CDNs would
                # 403 the probe and the candidate would be dropped.
                #
                # Cookies go on the client (httpx encodes them into
                # the Cookie: header per-request); other headers go
                # on every probe via `probe_headers`.
                try:
                    cookies_dict = {
                        c["name"]: c["value"]
                        for c in page.context.cookies()
                        if c.get("name") and "value" in c
                    }
                except Exception:
                    cookies_dict = {}
                ua = (self.config.get("user_agent")
                      or "Mozilla/5.0")
                probe_headers = {
                    "User-Agent": ua,
                    "Referer": base_url,
                    "Accept": "*/*",
                }
                # v3.66.392 (VPN-CONTROLPLANE): route the deep-detect live
                # probe through the same fail-closed VPN proxy as the payload
                # path -- a vpn_required site whose tunnel is down must not be
                # probed on the clear interface (skip deep-detect, return None).
                try:
                    _cp_proxy = self._download_proxy_url()
                except Exception as _cpe:
                    if _VPN_RUNTIME_AVAILABLE and isinstance(
                            _cpe, vpn_runtime.VPNRequiredError):
                        sys.stderr.write(
                            "  deep-detect: VPN required + tunnel down -- "
                            "skipping clear-interface probe\n")
                        return None
                    raise
                with _httpx.Client(
                        timeout=_httpx.Timeout(
                            connect=5.0, read=8.0,
                            write=5.0, pool=5.0),
                        cookies=cookies_dict,
                        proxy=_cp_proxy,
                        follow_redirects=True) as http:
                    # v3.66.15 (P5): read learned.deep_detect from
                    # the site config so deep_detect can apply its
                    # site-memory bias. Lazy import keeps the runner
                    # decoupled from learn.py at module-load time.
                    try:
                        from .learn import deep_detect_site_memory \
                            as _dd_site_memory
                        _site_mem = _dd_site_memory(self.config)
                    except Exception:
                        _site_mem = None
                    # v3.66.48: operator-controllable provider
                    # resolution. Default OFF — when enabled, deep_detect
                    # will call out to provider APIs (Vimeo/YouTube/
                    # Brightcove/Wistia/JWPlayer) to resolve embeds into
                    # direct candidates. This activates the C4-B
                    # YouTube signatureCipher decipher (player-js / yt-dlp
                    # backends), the C2 JWPlayer signing_callback, and the
                    # P5-2b honeypot-score wire. Off by default because it
                    # issues extra outbound requests to third-party
                    # provider endpoints and, for the player-js cipher
                    # backend, reproduces YouTube's signature transform —
                    # operators must opt in knowingly.
                    _resolve_providers = bool(
                        self.config.get("resolve_providers", False))
                    _signing_cb = None
                    _http_get = None
                    if _resolve_providers:
                        if not getattr(self, "_resolve_providers_warned",
                                       False):
                            sys.stderr.write(
                                "  deep-detect: WARNING — "
                                "resolve_providers=True for site "
                                f"{self.site_id!r}: provider resolution "
                                "will issue outbound requests to "
                                "third-party provider APIs and may invoke "
                                "the YouTube signatureCipher decipher "
                                "(BD_YOUTUBE_CIPHER). Signature "
                                "obfuscation only — no DRM/paywall/"
                                "age-gate bypass. Disable by removing "
                                "resolve_providers from the site config.\n")
                            self._resolve_providers_warned = True
                        # C2: build the per-site JWPlayer signing callback
                        # from config (fail-safe to None on bad config).
                        try:
                            from .provider_resolve import \
                                build_signing_callback as _bsc
                            _signing_cb = _bsc(self.config)
                        except Exception:
                            _signing_cb = None
                        # The provider resolvers need an http_get with the
                        # (url) -> (status, headers, body) contract.
                        # deep_detect_live forwards http_get but does NOT
                        # adapt its httpx `http` client, so build the
                        # adapter here from the same client (shares the
                        # site's cookies/headers/redirect policy). The
                        # SSRF guard is applied inside resolve_provider_embed;
                        # v3.66.765 also wraps it here (defense-in-depth) so a
                        # page-derived private/loopback URL is refused at the
                        # injection edge, not only downstream.
                        def _raw_http_get(_u, _client=http):  # noqa: E731
                            _r = _client.get(_u)
                            return (_r.status_code, dict(_r.headers),
                                    _r.content)
                        _http_get = _ssrf_guarded_http_get(_raw_http_get)
                    report = _dd.deep_detect_live(
                        html, base_url=base_url,
                        http=http,
                        probe_headers=probe_headers,
                        probe_parallelism=4,
                        max_total_probe_time=8.0,
                        follow_manifests=True,
                        poll_async_workflows=False,
                        # Sniff attachments is the default; explicit
                        # here so it's obvious the runner wants this.
                        sniff_attachments=True,
                        site_memory=_site_mem,  # P5
                        resolve_providers=_resolve_providers,  # v3.66.48
                        signing_callback=_signing_cb,          # v3.66.48 (C2)
                        http_get=_http_get,                    # v3.66.48
                    )
            else:
                # Off-switch path: offline analyzer only. Same
                # behaviour as pre-v3.66.12. Kept for rollback.
                # v3.66.15 (P5): also threaded through here so the
                # offline path benefits from learning when callers
                # explicitly disable live mode.
                try:
                    from .learn import deep_detect_site_memory \
                        as _dd_site_memory
                    _site_mem = _dd_site_memory(self.config)
                except Exception:
                    _site_mem = None
                report = _dd.deep_detect(html, base_url=base_url,
                                          site_memory=_site_mem)
        except Exception as e:
            sys.stderr.write(
                f"  deep-detect: analyzer raised "
                f"{type(e).__name__}: {str(e)[:120]}\n")
            return None
        # v3.66.15 (P5): record the outcome into the site config's
        # learned.deep_detect block. Side-effecting, so wrapped in
        # try/except — a failure to record is not a runner failure.
        # The caller (`app.py`) persists site config to disk on the
        # next save; this is consistent with how merge_learned works
        # for login/download blocks.
        try:
            from .learn import record_deep_detect_outcome \
                as _dd_record
            _dd_record(self.config, report, base_url=base_url)
            # T11 (v3.66.264): also persist the CURRENT pending
            # auto-submit / post-reveal approval candidates so the SPA's
            # per-site approval gate has a source (the candidates are
            # otherwise ephemeral — only decisions used to survive). Same
            # report, same once-per-run cadence; markers only (F2).
            from .learn import record_pending_approvals \
                as _dd_pending
            _dd_pending(self.config, report, base_url=base_url)
        except Exception as e:
            sys.stderr.write(
                f"  deep-detect: record outcome failed "
                f"({type(e).__name__}: {str(e)[:80]})\n")
        # F7: buckets is canonical; tolerate flat shape from older
        # callers/mocks during the migration window.
        candidates = ((report.get("buckets") or {}).get("accepted")
                      or report.get("download_candidates") or [])
        if not candidates:
            sys.stderr.write(
                "  deep-detect: no candidates found in fallback\n")
            return None
        # Surface a disclaimer line to stderr if the page tripped
        # blocker markers. Doesn't change behavior, but operators
        # tailing logs deserve to see it.
        disc = (report.get("blockers") or {}).get("warnings") or []
        if disc:
            sys.stderr.write(
                f"  deep-detect: blocker markers present: "
                f"{'; '.join(str(w)[:80] for w in disc[:2])}\n")
        # Strategy: pick the highest-scored candidate that has a real
        # click_selector. Convert to a `learned`-shaped dict and re-run
        # find_best_download — this reuses all the scoring + size +
        # hash-hint plumbing rather than re-implementing it here.
        # URL-only candidates (HLS variants, JSON-LD links, DASH reps,
        # state-blob URLs, player-config URLs) fall outside this path
        # by design — the runner's DOM-driven download model needs a
        # locator to click, and these surfaces produce URLs without a
        # corresponding DOM element. If the operator sees this fallback
        # consistently skip URL-only finds on a given site, the right
        # answer is to add direct-URL handling at a higher level (e.g.
        # a manifest-aware downloader), not to fake a click here.
        clickable = [c for c in candidates
                     if c.get("click_selector") and c.get("url")]
        if not clickable:
            url_only = [c for c in candidates if c.get("url")]
            if url_only:
                # Surface the missed opportunity so it's visible in logs.
                top = max(url_only, key=lambda c: c.get("score", 0))
                sys.stderr.write(
                    f"  deep-detect: {len(candidates)} candidate(s) found, "
                    f"{len(url_only)} URL-only (no click target); "
                    f"top URL-only is {top.get('source_type')} "
                    f"score={top.get('score')} "
                    f"({top.get('found_in')}) — runner can't consume\n")
            else:
                sys.stderr.write(
                    f"  deep-detect: {len(candidates)} candidate(s) found "
                    "but none had a usable click_selector or URL\n")
            return None
        clickable.sort(key=lambda c: c.get("score", 0), reverse=True)
        new_selectors = []
        for c in clickable[:5]:  # cap to top 5 — runner tries in order
            sel = c.get("click_selector")
            if sel and sel not in new_selectors:
                new_selectors.append(sel)
        if not new_selectors:
            return None
        sys.stderr.write(
            f"  deep-detect: trying {len(new_selectors)} new selector(s) "
            f"({clickable[0].get('quality_label') or '?'})\n")
        # Build a `learned`-shaped dict and re-run find_best_download.
        # Honor url_attribute if the candidate exposes one — defaults
        # to "href" otherwise.
        url_attr = clickable[0].get("url_attribute") or "href"
        retry_learned = {
            "row_selectors": new_selectors,
            "trigger_selectors": (learned_dl or {}).get(
                "trigger_selectors") or [],
            "url_attribute": url_attr,
        }
        try:
            best = find_best_download(
                page,
                self.config.get("dl_selector", "").strip(),
                learned=retry_learned,
                runner=self,
            )
        except Exception as e:
            sys.stderr.write(
                f"  deep-detect: retry find_best_download raised "
                f"{type(e).__name__}: {str(e)[:120]}\n")
            return None
        if not best:
            sys.stderr.write(
                "  deep-detect: selectors did not match in the DOM after "
                "all (page may have changed since snapshot)\n")
            return None
        # Tag the result so the caller can distinguish a deep-detect
        # save from a regular learned hit, and persist the selectors
        # so the next scrape on this site uses the fast path.
        best["_via_deep_detect"] = True
        # P5-2b: carry the resolved candidate's honeypot score onto the
        # runner-facing `best` so the completion-time db_log can stamp it
        # on the history row (the resolve->runner->db_log hop). The score
        # is only present when the scorer flagged this candidate (drop/
        # downscore zone); a clean candidate leaves it unset and the
        # column stays NULL -- no behaviour change for the common case.
        _hp = clickable[0].get("_honeypot_score")
        if _hp is not None:
            best["_honeypot_score"] = _hp
            _hpr = clickable[0].get("_honeypot_reason")
            if _hpr is not None:
                best["_honeypot_reason"] = _hpr
        self._persist_deep_detect_selectors(
            new_selectors, url_attr, clickable[0])
        return best
    def _persist_deep_detect_selectors(self, selectors, url_attr,
                                       top_candidate):
        """Merge deep_detect-discovered selectors into the site's
        learned config. Idempotent — selectors already present don't
        get duplicated. Selectors get appended (not prepended) to
        preserve any existing operator-tuned ordering.

        v3.66.6 — Backlog #7 wiring.
        """
        try:
            learned = self.config.setdefault("learned", {})
            dl = learned.setdefault("download", {})
            row_sels = dl.setdefault("row_selectors", [])
            for s in selectors:
                if s not in row_sels:
                    row_sels.append(s)
            # Only set url_attribute if missing — don't clobber an
            # operator-tuned value.
            if not dl.get("url_attribute"):
                dl["url_attribute"] = url_attr
            # Audit trail for the operator: what deep_detect saw.
            audit = dl.setdefault("deep_detect_log", [])
            audit.append({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "selectors_added": list(selectors),
                "quality_label": top_candidate.get("quality_label"),
                "score": top_candidate.get("score"),
                "source_type": top_candidate.get("source_type"),
            })
            # Cap audit log at 20 entries
            if len(audit) > 20:
                dl["deep_detect_log"] = audit[-20:]
        except Exception as e:
            sys.stderr.write(
                f"  deep-detect: selector persistence failed "
                f"({type(e).__name__}: {e})\n")
    def _try_jsonapi_extractor(self, url: str) -> bool:
        """v3.43.68: extract via HereSphere/DeoVR JSON API and download.

        Returns True on full success (download completed). Returns
        False on any failure — caller falls through to the rest of
        the dispatch chain.

        Unlike the Aylo/Vixen extractors, this one runs BEFORE the
        Playwright browser even opens — the API endpoint can be
        queried with just cookies + UA. That's faster and cheaper.

        Required config:
          - `use_jsonapi: True`
          - `jsonapi_url`: full endpoint base, e.g.
             `https://members.<site>.com/heresphere`
             or `https://api.naughtyapi.com/heresphere` for split-API
             sites.

        Optional config:
          - `jsonapi_protocol`: "heresphere" or "deovr" (auto-detect
             if blank — runtime sniffs the response shape)
          - `jsonapi_id_regex`: regex with capture group for scene-id
             extraction from the user's URL. Default tries common
             shapes (last numeric segment, ?id= / ?scene= / ?v=,
             slugified last segment).
          - `jsonapi_prefer_codec`: "h264" or "h265" — DeoVR only.
             Default empty (prefer h265 at equal tier — smaller files).
        """
        if not _JSONAPI_AVAILABLE or _jsonapi is None:
            return False

        api_base = (self.config.get("jsonapi_url") or "").strip()
        if not api_base:
            return False

        # Parse quality preference into integer cascade
        qpref_raw = self.config.get("quality_preference", "best") or "best"
        qpref: list = []
        for p in qpref_raw.split(","):
            p = p.strip()
            if not p:
                continue
            if p.lower() in ("best", "highest", "max"):
                qpref.append("best")
            else:
                try:
                    qpref.append(int(p.rstrip("p")))
                except ValueError:
                    pass

        protocol = (self.config.get("jsonapi_protocol") or "").strip().lower()
        id_regex = (self.config.get("jsonapi_id_regex") or "").strip()
        prefer_codec = (self.config.get("jsonapi_prefer_codec") or "").strip().lower()
        timeout_s = float(self.config.get("jsonapi_timeout_s", 15.0) or 15.0)

        user_agent = self.config.get("user_agent", "") or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # Cookies — convert Playwright list-of-dicts to flat dict
        cookies_dict: dict = {}
        try:
            for c in (self.cookies or []):
                if isinstance(c, dict) and "name" in c and "value" in c:
                    cookies_dict[c["name"]] = c["value"]
        except Exception:
            cookies_dict = {}

        self._update_job(url, "running", "JsonAPI: querying...")
        try:
            result = _jsonapi.fetch_and_extract(
                api_base, url,
                quality_pref=qpref, protocol=protocol,
                cookies=cookies_dict or None, user_agent=user_agent,
                timeout_s=timeout_s, id_regex=id_regex,
                prefer_codec=prefer_codec,
            )
        except Exception as e:
            sys.stderr.write(f"  jsonapi: fetch_and_extract raised {e}\n")
            return False

        if not result.ok or result.source is None:
            self.log_event(
                "jsonapi_extract_failed",
                f"{result.error}: {(result.error_detail or '')[:120]}",
                url=url,
            )
            return False

        # Build filename
        dl_dir_str = (self.config.get("download_dir") or "").strip()
        if not dl_dir_str:
            sys.stderr.write("  jsonapi: no download_dir configured\n")
            return False
        try:
            os.makedirs(dl_dir_str, exist_ok=True)
        except Exception as e:
            sys.stderr.write(f"  jsonapi: mkdir failed: {e}\n")
            return False

        from .detect import safe_dest
        src = result.source
        ext = ".mp4"
        title_root = result.title or url.rsplit("/", 1)[-1].split("?", 1)[0]
        now = datetime.now()
        # Performer list joined with ", " for the {performer} variable
        performer_str = ", ".join(result.performers) if result.performers else ""
        ctx_vars = {
            "site": self.config.get("name", "site"),
            "title": result.title or "",
            "filename": title_root,
            "stem": title_root,
            "ext": ext,
            "resolution": f"{src.height}p" if src.height else "",
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H-%M-%S"),
            "datetime": now.strftime("%Y-%m-%d_%H-%M-%S"),
            "performer": performer_str,
            "artist": performer_str,
            "studio": result.studio or self.config.get("name", ""),
            "year": (result.date_released[:4] if result.date_released
                     else now.strftime("%Y")),
            "upload_date": result.date_released,
            "duration": format_duration_for_filename(result.duration_sec),
            "quality": f"{src.height}p" if src.height else "",
            "extractor": f"jsonapi-{result.protocol}",
        }
        tpl = (self.config.get("filename_template", "")
               or "{filename}{ext}").strip()
        rendered = resolve_filename_template(tpl, ctx_vars)
        if not rendered:
            rendered = title_root + ext
        elif not os.path.splitext(rendered)[1]:
            rendered = rendered + ext
        output_filename = safe_dest(rendered)
        output_path = os.path.join(dl_dir_str, output_filename)
        try:
            os.makedirs(os.path.dirname(output_path) or dl_dir_str,
                        exist_ok=True)
        except Exception:
            pass

        # The page URL is also the referer for the CDN fetch
        referer = url

        self._update_job(
            url, "running",
            f"JsonAPI {result.protocol}: downloading {src.height}p "
            f"{src.codec or ('HLS' if src.is_hls else 'MP4')}...",
        )

        # This function's two arms converge on ONE `done` db_log, so the
        # transport has to travel as a variable. A hardcoded "segmented" there
        # would label every direct-MP4 JsonAPI download as a stream and make
        # L12 PASS on a transfer that never touched ffmpeg.
        transfer_mode = None
        if src.is_hls:
            transfer_mode = "segmented"
            try:
                from . import hls_downloader as _hls
            except ImportError:
                sys.stderr.write("  jsonapi: hls_downloader unavailable\n")
                return False
            if not _hls.is_available():
                sys.stderr.write("  jsonapi: ffmpeg not on PATH\n")
                return False

            def _progress(p):
                pct = ""
                if result.duration_sec > 0:
                    pct_v = min(100, int(100.0 * p.get("seconds", 0)
                                         / result.duration_sec))
                    pct = f" {pct_v}%"
                self._update_job(
                    url, "running",
                    f"JsonAPI HLS{pct} • {fmt_bytes(p.get('bytes', 0))}",
                )

            dl_result = _hls.download(
                src.url, output_path,
                user_agent=user_agent, referer=referer,
                progress_callback=_progress,
                cancel_check=lambda: self._stop.is_set(),
            )
            if not dl_result.ok:
                self.log_event(
                    "jsonapi_hls_failed",
                    f"hls failed: {dl_result.error}", url=url,
                )
                try:
                    if os.path.exists(output_path):
                        os.remove(output_path)
                except Exception:
                    pass
                return False
            downloaded_size = dl_result.bytes_written
        else:
            transfer_mode = "http"
            ok = self._do_direct_http_download(
                page_url=url, file_url=src.url,
                output_path=output_path, referer=referer,
            )
            if not ok:
                self.log_event(
                    "jsonapi_mp4_failed", "direct http failed", url=url,
                )
                return False
            try:
                downloaded_size = os.path.getsize(output_path)
            except OSError:
                downloaded_size = 0

        # Embed metadata — this is where JsonAPI shines because the
        # protocol exposes performers/studio/release-date natively.
        try:
            self._embed_metadata_if_mp4(
                output_path,
                title=result.title,
                performer=performer_str,
                site_name=self.config.get("name", ""),
                upload_date=result.date_released,
                source_url=url,
                thumbnail_url=result.thumbnail_url,
                quality=f"{src.height}p" if src.height else "",
                duration_sec=result.duration_sec,
                extractor_name=f"jsonapi-{result.protocol}",
            )
        except Exception as e:
            sys.stderr.write(f"  jsonapi: metadata embed raised {e}\n")

        file_size_on_disk = self._size_on_disk_after_tagging(
            output_path, downloaded_size)

        # Mark done
        self._update_job(
            url, "done",
            f"JsonAPI {result.protocol} {src.height}p "
            f"({fmt_bytes(downloaded_size)})",
            filename=output_filename, file_size=file_size_on_disk,
        )
        db_log(self.site_id, self.config.get("name", "?"), url, "done",
               output_filename, file_size_on_disk,
               f"jsonapi={result.protocol} tier={src.height} "
               f"avail={result.available_heights}", bytes_fetched=downloaded_size,
               transfer_mode=transfer_mode, file_path=output_path)
        self.log_event(
            "jsonapi_done",
            f"{src.height}p via {result.protocol} "
            f"(avail: {result.available_heights}, "
            f"performers: {len(result.performers)}, studio: {result.studio!r})",
            url=url,
        )
        return True
    def _try_vixen_extractor(self, url: str, page) -> bool:
        """v3.43.67: extract via Vixen __NEXT_DATA__ / <video src> and
        download.

        Returns True on full success, False on any failure (caller
        falls through to find_best_download).

        Three-path strategy:
          1. <script id="__NEXT_DATA__"> → walk for video record
          2. <video src=...> regex on rendered HTML
          3. GraphQL POST (placeholder — not implemented v3.43.67)

        After picking a URL, if `tier_probe_enabled` is True (and the
        URL has a `mp4_<N>/` segment) it gets handed to tier-probe to
        climb to the highest available tier.

        Like the Aylo handler, this builds the filename via the user's
        template, dispatches to HLS or direct download appropriately,
        and embeds MP4 metadata at the end.
        """
        if not _VIXEN_AVAILABLE or _vixen is None:
            return False

        # Parse quality preference
        qpref_raw = self.config.get("quality_preference", "best") or "best"
        qpref: list = []
        for p in qpref_raw.split(","):
            p = p.strip()
            if not p:
                continue
            if p.lower() in ("best", "highest", "max"):
                qpref.append("best")
            else:
                try:
                    qpref.append(int(p.rstrip("p")))
                except ValueError:
                    pass

        self._update_job(url, "running", "Vixen: extracting...")

        try:
            result = _vixen.extract_from_page(page, quality_pref=qpref)
        except Exception as e:
            sys.stderr.write(f"  vixen: extract_from_page raised {e}\n")
            return False

        if not result.ok:
            self.log_event(
                "vixen_extract_failed",
                f"vixen extract: {result.error}",
                url=url,
            )
            sys.stderr.write(
                f"  vixen: extract failed: {result.error} — "
                f"{(result.error_detail or '')[:120]}\n"
            )
            return False

        if not result.url:
            return False

        # If tier-probe is enabled AND the URL has a mp4_<N>/ segment,
        # try to upgrade. This is the natural pairing: __NEXT_DATA__
        # may give us a 480p URL even when 2160p is available; tier-
        # probe climbs the ladder. (For HLS URLs tier-probe is a no-op
        # — the manifest itself handles variant selection.)
        upgraded_url = result.url
        upgraded_tier = result.tier
        if not result.is_hls and self.config.get("tier_probe_enabled", False):
            try:
                # Force the vixen_network pattern if no per-site one
                # is configured — Vixen URLs all follow `mp4_<N>/`.
                if not self.config.get("tier_probe_pattern"):
                    self.config = dict(self.config)
                    self.config["tier_probe_pattern"] = "vixen_network"
                upgraded_url = self._probe_for_higher_tier(
                    result.url, referer=url)
                # Re-extract the tier from the (possibly upgraded) URL
                upgraded_tier = _vixen._tier_from_url(upgraded_url) or result.tier
            except Exception as e:
                sys.stderr.write(f"  vixen: tier-probe raised {e}\n")
                upgraded_url = result.url

        # Build output filename via the user's template
        dl_dir_str = (self.config.get("download_dir") or "").strip()
        if not dl_dir_str:
            sys.stderr.write("  vixen: no download_dir configured\n")
            return False
        try:
            os.makedirs(dl_dir_str, exist_ok=True)
        except Exception as e:
            sys.stderr.write(f"  vixen: mkdir failed: {e}\n")
            return False

        from .detect import safe_dest
        ext = ".mp4"  # HLS gets remuxed to MP4 by ffmpeg
        title_root = result.title or url.rsplit("/", 1)[-1].split("?", 1)[0]
        now = datetime.now()
        ctx_vars = {
            "site": self.config.get("name", "site"),
            "title": result.title or "",
            "filename": title_root,
            "stem": title_root,
            "ext": ext,
            "resolution": f"{upgraded_tier}p" if upgraded_tier else "",
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H-%M-%S"),
            "datetime": now.strftime("%Y-%m-%d_%H-%M-%S"),
            "performer": "",
            "artist": "",
            "studio": self.config.get("name", ""),
            "year": now.strftime("%Y"),
            "upload_date": "",
            "duration": format_duration_for_filename(result.duration_sec),
            "quality": f"{upgraded_tier}p" if upgraded_tier else "",
            "extractor": "vixen",
        }
        tpl = (self.config.get("filename_template", "")
               or "{filename}{ext}").strip()
        rendered = resolve_filename_template(tpl, ctx_vars)
        if not rendered:
            rendered = title_root + ext
        elif not os.path.splitext(rendered)[1]:
            rendered = rendered + ext
        output_filename = safe_dest(rendered)
        output_path = os.path.join(dl_dir_str, output_filename)
        try:
            os.makedirs(os.path.dirname(output_path) or dl_dir_str,
                        exist_ok=True)
        except Exception:
            pass

        # User-Agent + cookies from the live page (Akamai-signed URLs
        # care about UA + cookies matching the page that minted them)
        try:
            user_agent = page.evaluate("() => navigator.userAgent")
        except Exception:
            user_agent = self.config.get("user_agent", "") or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        referer = url

        self._update_job(
            url, "running",
            f"Vixen: downloading {upgraded_tier}p"
            f"{' HLS' if result.is_hls else ' MP4'} (via {result.via})...",
        )

        # Two arms, one `done` db_log -- see the note in _try_jsonapi_extractor.
        transfer_mode = None
        if result.is_hls:
            transfer_mode = "segmented"
            try:
                from . import hls_downloader as _hls
            except ImportError:
                sys.stderr.write("  vixen: hls_downloader unavailable\n")
                return False
            if not _hls.is_available():
                sys.stderr.write("  vixen: ffmpeg not on PATH\n")
                return False

            def _progress(p):
                pct = ""
                if result.duration_sec > 0:
                    pct_v = min(100, int(100.0 * p.get("seconds", 0)
                                         / result.duration_sec))
                    pct = f" {pct_v}%"
                self._update_job(
                    url, "running",
                    f"Vixen HLS{pct} • {fmt_bytes(p.get('bytes', 0))}",
                )

            dl_result = _hls.download(
                upgraded_url, output_path,
                user_agent=user_agent, referer=referer,
                progress_callback=_progress,
                cancel_check=lambda: self._stop.is_set(),
            )
            if not dl_result.ok:
                self.log_event(
                    "vixen_hls_failed",
                    f"hls failed: {dl_result.error}", url=url,
                )
                try:
                    if os.path.exists(output_path):
                        os.remove(output_path)
                except Exception:
                    pass
                return False
            downloaded_size = dl_result.bytes_written
        else:
            transfer_mode = "http"
            ok = self._do_direct_http_download(
                page_url=url, file_url=upgraded_url,
                output_path=output_path, referer=referer,
            )
            if not ok:
                self.log_event(
                    "vixen_mp4_failed", "direct http failed", url=url,
                )
                return False
            try:
                downloaded_size = os.path.getsize(output_path)
            except OSError:
                downloaded_size = 0

        # Embed metadata
        try:
            self._embed_metadata_if_mp4(
                output_path,
                title=result.title,
                performer="",
                site_name=self.config.get("name", ""),
                upload_date="",
                source_url=url,
                thumbnail_url=result.thumbnail_url,
                quality=f"{upgraded_tier}p" if upgraded_tier else "",
                duration_sec=result.duration_sec,
                extractor_name="vixen",
            )
        except Exception as e:
            sys.stderr.write(f"  vixen: metadata embed raised {e}\n")

        file_size_on_disk = self._size_on_disk_after_tagging(
            output_path, downloaded_size)

        # Mark done
        self._update_job(
            url, "done",
            f"Vixen {upgraded_tier}p {'HLS' if result.is_hls else 'MP4'} "
            f"({fmt_bytes(downloaded_size)}) via {result.via}",
            filename=output_filename, file_size=file_size_on_disk,
        )
        db_log(self.site_id, self.config.get("name", "?"), url, "done",
               output_filename, file_size_on_disk,
               f"vixen={result.via} tier={upgraded_tier} "
               f"avail={result.available_tiers}", bytes_fetched=downloaded_size,
               transfer_mode=transfer_mode, file_path=output_path)
        self.log_event(
            "vixen_done",
            f"{upgraded_tier}p via {result.via} "
            f"(avail: {result.available_tiers})",
            url=url,
        )
        return True
    def _try_dl8_extractor(self, url: str, page) -> bool:
        """v3.43.69: parse <dl8-video> and download.

        Two strategies under one method:

        (A) Standard dl8 path: walk <dl8-video> <source> elements,
            pick best per quality_preference, direct-download.

        (B) Badoink prediction: when the host is a Badoink-family
            site AND the <dl8-video> sources look like trailer URLs,
            generate member-area URL candidates by substituting tier
            suffixes, HEAD-probe each one, take the first 200 OK.
            This avoids navigating to the member area at all — the
            URLs are guessable from public trailer pages.

        Returns True on full success. Returns False on any failure —
        runner falls through to find_best_download. Fail-open.
        """
        if not _DL8_AVAILABLE or _dl8 is None:
            return False

        # Parse quality preference
        qpref_raw = self.config.get("quality_preference", "best") or "best"
        qpref: list = []
        for p in qpref_raw.split(","):
            p = p.strip()
            if not p:
                continue
            if p.lower() in ("best", "highest", "max"):
                qpref.append("best")
            else:
                try:
                    qpref.append(int(p.rstrip("p")))
                except ValueError:
                    pass

        predict_badoink = self.config.get(
            "dl8_predict_badoink_filenames", True)

        self._update_job(url, "running", "dl8: parsing...")

        try:
            result = _dl8.extract_from_page(
                page, quality_pref=qpref, predict_badoink=predict_badoink,
            )
        except Exception as e:
            sys.stderr.write(f"  dl8: extract_from_page raised {e}\n")
            return False

        if not result.ok:
            self.log_event(
                "dl8_extract_failed",
                f"dl8 extract: {result.error}",
                url=url,
            )
            sys.stderr.write(
                f"  dl8: extract failed: {result.error} — "
                f"{(result.error_detail or '')[:120]}\n"
            )
            return False

        # User-Agent + cookies for downloads
        try:
            user_agent = page.evaluate("() => navigator.userAgent")
        except Exception:
            user_agent = self.config.get("user_agent", "") or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        referer = url

        # Cookies dict for httpx-based HEAD probes (Badoink path)
        try:
            cookies_list = page.context.cookies()
        except Exception:
            cookies_list = []
        cookies_dict: dict = {}
        for c in (cookies_list or []):
            if isinstance(c, dict) and "name" in c and "value" in c:
                cookies_dict[c["name"]] = c["value"]

        # ── Path B: Badoink prediction ────────────────────────────
        chosen_url = result.url
        chosen_tier = result.tier
        if result.via == "badoink_predict" and result.badoink_candidates:
            self._update_job(
                url, "running",
                f"dl8: HEAD-probing {len(result.badoink_candidates)} "
                "Badoink candidates...",
            )
            picked = _dl8.probe_badoink_candidates(
                result.badoink_candidates,
                user_agent=user_agent, referer=referer,
                cookies=cookies_dict or None,
            )
            if picked is None:
                self.log_event(
                    "dl8_badoink_no_match",
                    f"all {len(result.badoink_candidates)} HEAD probes "
                    f"failed; falling through",
                    url=url,
                )
                return False
            chosen_url = picked.url
            chosen_tier = picked.tier
            self.log_event(
                "dl8_badoink_picked",
                f"tier {picked.tier} via {picked.suffix}",
                url=url,
            )

        if not chosen_url:
            return False

        # Build output path via filename template
        dl_dir_str = (self.config.get("download_dir") or "").strip()
        if not dl_dir_str:
            sys.stderr.write("  dl8: no download_dir configured\n")
            return False
        try:
            os.makedirs(dl_dir_str, exist_ok=True)
        except Exception as e:
            sys.stderr.write(f"  dl8: mkdir failed: {e}\n")
            return False

        from .detect import safe_dest
        ext = ".mp4"
        title_root = result.title or url.rsplit("/", 1)[-1].split("?", 1)[0]
        now = datetime.now()
        ctx_vars = {
            "site": self.config.get("name", "site"),
            "title": result.title or "",
            "filename": title_root,
            "stem": title_root,
            "ext": ext,
            "resolution": f"{chosen_tier}p" if chosen_tier else "",
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H-%M-%S"),
            "datetime": now.strftime("%Y-%m-%d_%H-%M-%S"),
            "performer": "",
            "artist": "",
            "studio": self.config.get("name", ""),
            "year": now.strftime("%Y"),
            "upload_date": "",
            "duration": "",
            "quality": f"{chosen_tier}p" if chosen_tier else "",
            "extractor": "dl8",
        }
        tpl = (self.config.get("filename_template", "")
               or "{filename}{ext}").strip()
        rendered = resolve_filename_template(tpl, ctx_vars)
        if not rendered:
            rendered = title_root + ext
        elif not os.path.splitext(rendered)[1]:
            rendered = rendered + ext
        output_filename = safe_dest(rendered)
        output_path = os.path.join(dl_dir_str, output_filename)
        try:
            os.makedirs(os.path.dirname(output_path) or dl_dir_str,
                        exist_ok=True)
        except Exception:
            pass

        self._update_job(
            url, "running",
            f"dl8: downloading {chosen_tier}p (via {result.via})...",
        )

        # dl8 URLs are always progressive MP4 — direct-download path
        ok = self._do_direct_http_download(
            page_url=url, file_url=chosen_url,
            output_path=output_path, referer=referer,
        )
        if not ok:
            self.log_event(
                "dl8_mp4_failed", "direct http failed", url=url,
            )
            return False

        try:
            downloaded_size = os.path.getsize(output_path)
        except OSError:
            downloaded_size = 0

        # Embed metadata
        try:
            self._embed_metadata_if_mp4(
                output_path,
                title=result.title,
                performer="",
                site_name=self.config.get("name", ""),
                upload_date="",
                source_url=url,
                thumbnail_url=result.poster,
                quality=f"{chosen_tier}p" if chosen_tier else "",
                duration_sec=0,
                extractor_name="dl8",
            )
        except Exception as e:
            sys.stderr.write(f"  dl8: metadata embed raised {e}\n")

        file_size_on_disk = self._size_on_disk_after_tagging(
            output_path, downloaded_size)

        # Mark done
        self._update_job(
            url, "done",
            f"dl8 {chosen_tier}p ({fmt_bytes(downloaded_size)}) "
            f"via {result.via}",
            filename=output_filename, file_size=file_size_on_disk,
        )
        db_log(self.site_id, self.config.get("name", "?"), url, "done",
               output_filename, file_size_on_disk,
               f"dl8={result.via} tier={chosen_tier} "
               f"avail={result.available_tiers}", bytes_fetched=downloaded_size,
               file_path=output_path)
        self.log_event(
            "dl8_done",
            f"{chosen_tier}p via {result.via} "
            f"(avail: {result.available_tiers})",
            url=url,
        )
        return True
    def _try_aylo_extractor(self, url: str, page) -> bool:
        """v3.43.66: extract via Aylo flashvars and download.

        Returns True on full success (download completed, job marked
        done). Returns False on any failure — caller falls through to
        the standard teach-based find_best_download path.

        Two-phase:
          1. Pull page.content(), regex-extract `flashvars_<id>`,
             walk `mediaDefinitions[]`, pick best variant per the
             site's `quality_preference`
          2. Dispatch to HLS (via _hls.download) if variant is HLS,
             or to httpx via _do_direct_http_download if MP4

        On variant pick: respects the site's `quality_preference`
        cascade. Special config `aylo_force_format` ("hls" or "mp4")
        restricts to one format. Special `aylo_force_mp4` shortcut
        same as `aylo_force_format=mp4`.

        Also embeds MP4 metadata if v3.43.64's mp4_metadata module is
        available — the flashvars block carries title and duration.
        """
        if not _AYLO_AVAILABLE or _aylo is None:
            return False

        # Quality preference cascade
        qpref_raw = self.config.get("quality_preference", "best") or "best"
        qpref: list = []
        for p in qpref_raw.split(","):
            p = p.strip()
            if not p:
                continue
            if p.lower() in ("best", "highest", "max"):
                qpref.append("best")
            else:
                try:
                    qpref.append(int(p.rstrip("p")))
                except ValueError:
                    pass

        # Force format
        force_fmt = (self.config.get("aylo_force_format", "") or "").strip().lower()
        if not force_fmt and self.config.get("aylo_force_mp4"):
            force_fmt = "mp4"
        if force_fmt not in ("hls", "mp4", ""):
            force_fmt = ""

        self._update_job(url, "running", "Aylo: extracting flashvars...")

        # Extract from the live page (HTML already loaded by caller)
        try:
            result = _aylo.extract_from_page(
                page, quality_pref=qpref, force_format=force_fmt,
            )
        except Exception as e:
            sys.stderr.write(f"  aylo: extract_from_page raised {e}\n")
            return False

        if not result.ok:
            self.log_event(
                "aylo_extract_failed",
                f"flashvars: {result.error} ({result.error_detail[:120]})",
                url=url,
            )
            sys.stderr.write(
                f"  aylo: extract failed: {result.error} — "
                f"{(result.error_detail or '')[:120]}\n"
            )
            return False

        if self.config.get("aylo_premium_only", False) and \
           result.variant and not result.variant.is_premium:
            self.log_event(
                "aylo_skip_nonpremium",
                "non-premium variant skipped (aylo_premium_only=true)",
                url=url,
            )
            return False

        variant = result.variant
        if variant is None or not variant.url:
            return False

        # Build filename context from the extractor metadata.
        # The runner already has the teach-path filename rendering
        # logic in `_process_one`; we reuse the same template + ctx
        # shape so users get consistent naming.
        dl_dir_str = (self.config.get("download_dir") or "").strip()
        if not dl_dir_str:
            sys.stderr.write("  aylo: no download_dir configured\n")
            return False
        try:
            os.makedirs(dl_dir_str, exist_ok=True)
        except Exception as e:
            sys.stderr.write(f"  aylo: mkdir failed: {e}\n")
            return False

        from .detect import safe_dest
        from pathlib import Path as _PathLib
        ext = ".mp4"  # HLS gets remuxed to mp4
        title_root = result.title or url.rsplit("/", 1)[-1].split("?", 1)[0]
        now = datetime.now()
        ctx_vars = {
            "site": self.config.get("name", "site"),
            "title": result.title or "",
            "filename": title_root,
            "stem": title_root,
            "ext": ext,
            "resolution": f"{variant.quality}p" if variant.quality else "",
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H-%M-%S"),
            "datetime": now.strftime("%Y-%m-%d_%H-%M-%S"),
            "performer": "",
            "artist": "",
            "studio": self.config.get("name", ""),
            "year": now.strftime("%Y"),
            "upload_date": "",
            "duration": format_duration_for_filename(result.duration_sec),
            "quality": f"{variant.quality}p" if variant.quality else "",
            "extractor": "aylo",
        }
        tpl = (self.config.get("filename_template", "")
               or "{filename}{ext}").strip()
        rendered = resolve_filename_template(tpl, ctx_vars)
        if not rendered:
            rendered = title_root + ext
        elif not os.path.splitext(rendered)[1]:
            rendered = rendered + ext
        output_filename = safe_dest(rendered)
        output_path = os.path.join(dl_dir_str, output_filename)
        try:
            os.makedirs(os.path.dirname(output_path) or dl_dir_str,
                        exist_ok=True)
        except Exception:
            pass

        # User-Agent + Referer for the CDN fetch. Aylo Akamai-signed
        # URLs care about UA matching the page that minted them. Pull
        # the live UA from the page.
        try:
            user_agent = page.evaluate("() => navigator.userAgent")
        except Exception:
            user_agent = self.config.get("user_agent", "") or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        referer = url

        # Cookies from the active context for httpx / ffmpeg
        try:
            cookies_list = page.context.cookies()
        except Exception:
            cookies_list = []
        cookies_dict: dict = {}
        for c in (cookies_list or []):
            if isinstance(c, dict) and "name" in c and "value" in c:
                cookies_dict[c["name"]] = c["value"]

        # Dispatch
        self._update_job(
            url, "running",
            f"Aylo: downloading {variant.quality}p {variant.format.upper()}...",
        )

        # Two arms, one `done` db_log -- see the note in _try_jsonapi_extractor.
        transfer_mode = None
        if variant.format == "hls":
            transfer_mode = "segmented"
            try:
                from . import hls_downloader as _hls
            except ImportError:
                sys.stderr.write("  aylo: hls_downloader unavailable\n")
                return False
            if not _hls.is_available():
                sys.stderr.write(
                    "  aylo: ffmpeg not on PATH; HLS download unavailable\n"
                )
                return False

            def _progress(p):
                pct = ""
                if result.duration_sec > 0:
                    pct_v = min(100, int(100.0 * p.get("seconds", 0)
                                         / result.duration_sec))
                    pct = f" {pct_v}%"
                self._update_job(
                    url, "running",
                    f"Aylo HLS{pct} • {fmt_bytes(p.get('bytes', 0))}",
                )

            dl_result = _hls.download(
                variant.url, output_path,
                user_agent=user_agent, referer=referer,
                progress_callback=_progress,
                cancel_check=lambda: self._stop.is_set(),
            )
            if not dl_result.ok:
                self.log_event(
                    "aylo_hls_failed",
                    f"hls failed: {dl_result.error}", url=url,
                )
                try:
                    if os.path.exists(output_path):
                        os.remove(output_path)
                except Exception:
                    pass
                return False
            downloaded_size = dl_result.bytes_written
        else:
            transfer_mode = "http"
            # MP4 direct download. Apply tier-probe in case the user
            # has it enabled (rare on Aylo since the flashvars already
            # gave us the best variant, but harmless).
            probed_url = self._probe_for_higher_tier(variant.url, referer=referer)
            ok = self._do_direct_http_download(
                page_url=url, file_url=probed_url,
                output_path=output_path, referer=referer,
            )
            if not ok:
                self.log_event(
                    "aylo_mp4_failed", "direct http failed", url=url,
                )
                return False
            try:
                downloaded_size = os.path.getsize(output_path)
            except OSError:
                downloaded_size = 0

        # v3.43.64: embed metadata
        try:
            self._embed_metadata_if_mp4(
                output_path,
                title=result.title,
                performer="",  # flashvars doesn't expose performer
                site_name=self.config.get("name", ""),
                upload_date="",
                source_url=url,
                thumbnail_url=result.thumbnail_url,
                quality=f"{variant.quality}p" if variant.quality else "",
                duration_sec=result.duration_sec,
                extractor_name="aylo",
            )
        except Exception as e:
            sys.stderr.write(f"  aylo: metadata embed raised {e}\n")

        file_size_on_disk = self._size_on_disk_after_tagging(
            output_path, downloaded_size)

        # Mark done
        self._update_job(
            url, "done",
            f"Aylo {variant.quality}p {variant.format.upper()} "
            f"({fmt_bytes(downloaded_size)})",
            filename=output_filename, file_size=file_size_on_disk,
        )
        db_log(self.site_id, self.config.get("name", "?"), url, "done",
               output_filename, file_size_on_disk,
               f"aylo={variant.format} quality={variant.quality}p "
               f"avail=[{','.join(result.available_qualities[:5])}]",
               bytes_fetched=downloaded_size,
               transfer_mode=transfer_mode, file_path=output_path)
        self.log_event(
            "aylo_done",
            f"{variant.quality}p {variant.format} via flashvars "
            f"(avail: {', '.join(result.available_qualities[:8])})",
            url=url,
        )
        return True
    def _probe_for_higher_tier(self, url: str, *, referer: str = "") -> str:
        """v3.43.65: speculatively probe higher-tier variants of `url`.

        Returns the URL of the highest 200-OK variant, or `url` unchanged
        on any failure. Gated by `tier_probe_enabled` per site config.

        This is called AFTER find_best_download has chosen `<video src>`
        but BEFORE the download begins, so we can swap in the higher
        tier without spending a single byte of bandwidth on the lower
        one. The HEAD probes themselves are cheap (~1KB each).

        Never raises — the tier_probe module is entirely fail-open. On
        any error (no library, bad pattern, network down, signed-URL
        rejection) we just return the original URL and log a one-line
        event so the user can see what was attempted.
        """
        if not url:
            return url
        if not self.config.get("tier_probe_enabled", False):
            return url
        if not _TIER_PROBE_AVAILABLE or _tier_probe is None:
            return url
        pattern_cfg = (self.config.get("tier_probe_pattern") or "").strip()
        if not pattern_cfg:
            return url
        # Resolve via known patterns (so a config value of
        # "vixen_network" expands to the regex), but allow custom
        # regex pass-through.
        pattern = _tier_probe.resolve_pattern(pattern_cfg)
        # Parse the optional ladder override
        ladder_raw = (self.config.get("tier_probe_ladder") or "").strip()
        ladder = None
        if ladder_raw:
            try:
                ladder = [int(x.strip()) for x in ladder_raw.split(",")
                          if x.strip().isdigit()]
                if not ladder:
                    ladder = None
            except Exception:
                ladder = None
        timeout_s = float(self.config.get("tier_probe_timeout_s", 5.0) or 5.0)
        max_attempts = int(self.config.get("tier_probe_max_attempts", 6) or 6)
        user_agent = self.config.get("user_agent", "") or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        # Pass the site's cookies as a flat dict for httpx. cookies
        # comes from cookies/<sid>.json in Playwright format; convert.
        cookies_dict = {}
        try:
            for c in (self.cookies or []):
                if isinstance(c, dict) and "name" in c and "value" in c:
                    cookies_dict[c["name"]] = c["value"]
        except Exception:
            cookies_dict = {}
        try:
            result = _tier_probe.probe_higher_tiers(
                url, pattern,
                user_agent=user_agent, referer=referer,
                cookies=cookies_dict or None,
                ladder=ladder, timeout_s=timeout_s,
                max_attempts=max_attempts,
            )
        except Exception as e:
            sys.stderr.write(f"  tier_probe: raised {type(e).__name__}: {e}\n")
            return url
        # Log a single-line event so the user can see the outcome
        try:
            if result.probed:
                probed_str = " ".join(
                    f"{t}:{s}" for t, s in result.probed_tiers
                ) or "—"
                if result.chosen_tier > result.original_tier:
                    self.log_event(
                        "tier_promoted",
                        f"tier {result.original_tier} -> {result.chosen_tier} "
                        f"({probed_str})",
                        url=url,
                    )
                elif result.error:
                    self.log_event(
                        "tier_probe_noop",
                        f"kept tier {result.original_tier}: "
                        f"{result.error} ({probed_str})",
                        url=url,
                    )
                else:
                    self.log_event(
                        "tier_probe_noop",
                        f"kept tier {result.original_tier} "
                        f"(no higher available: {probed_str})",
                        url=url,
                    )
        except Exception:
            pass
        return result.url
    def _run_pre_scrape_action(self, page) -> None:
        """v3.43.65: run a per-site action BEFORE scraping the <video>
        element, to coax the player into loading the highest-quality
        variant.

        Config:
          - `pre_scrape_action` (dict, optional): describes the click
             sequence. Three keys, all string CSS selectors:
               selector:  initial element to click (opens menu)
               wait_for:  element to wait for after the click
               select:    element inside the opened menu to click

        On any error this is a no-op — the runner continues with
        whatever quality the player picked on its own. Errors are
        logged but never propagated.

        Use cases from the recon survey:
          - Brazzers/Aylo: quality menu button -> 1080p option
          - AdultTime: cog icon -> resolution submenu -> highest
          - Vixen: settings -> auto/highest toggle
        """
        action = self.config.get("pre_scrape_action")
        if not action or not isinstance(action, dict):
            return
        selector = (action.get("selector") or "").strip()
        wait_for = (action.get("wait_for") or "").strip()
        select = (action.get("select") or "").strip()
        if not selector:
            return  # Need at least the trigger selector
        try:
            page.locator(selector).first.click(timeout=3000)
        except Exception as e:
            sys.stderr.write(
                f"  pre_scrape_action: selector click failed: "
                f"{type(e).__name__}: {str(e)[:80]}\n"
            )
            return
        if wait_for:
            try:
                page.locator(wait_for).first.wait_for(timeout=3000)
            except Exception as e:
                sys.stderr.write(
                    f"  pre_scrape_action: wait_for failed: "
                    f"{type(e).__name__}: {str(e)[:80]}\n"
                )
                # Continue anyway — maybe the menu opened but the
                # wait_for selector was wrong.
        if select:
            try:
                page.locator(select).first.click(timeout=3000)
            except Exception as e:
                sys.stderr.write(
                    f"  pre_scrape_action: select click failed: "
                    f"{type(e).__name__}: {str(e)[:80]}\n"
                )
                return
        # Give the player a moment to swap the src attribute
        try:
            page.wait_for_timeout(500)
        except Exception:
            pass
        try:
            self.log_event("pre_scrape_action_ok",
                           "ran pre-scrape quality menu action")
        except Exception:
            pass
    def _try_plugin_extractor(self, url: str) -> bool:
        """PLUGIN-DISPATCH (v3.66.691): run a registered plugin ``@extractor``
        for this site, if any, and download its result.

        The registry (``plugins.register_extractor`` -> ``_extractors``) was
        populated by every plugin type (exec/node/py-bridge, and GH-2's yt-dlp
        shim) but never invoked -- this is the missing dispatch. Contract:
        ``fn(url, context) -> {"video_url", ...} | {} | None``; ``{}``/``None``
        (or no ``video_url``) means 'not applicable' -> return False so the
        caller falls through unchanged. Gate is simply "an extractor is
        registered for this site" (naturally opt-in; a site with none registered
        pays one dict lookup). Returns True iff the download completed.

        HLS/DASH results (``is_hls``) are routed through ``hls_downloader``
        (695) -- the same ffmpeg path jsonapi/vixen use; ffmpeg absent -> fall
        through.
        """
        try:
            from . import plugins as _P
        except ImportError:
            return False
        fn = _P.get_extractor(self.site_id)
        if fn is None:
            return False
        try:
            result = fn(url, {"site_id": self.site_id, "config": self.config})
        except Exception as e:
            self.log_event("plugin_extract_failed",
                           f"plugin extractor for {self.site_id} raised: {e}", url=url)
            return False
        if not isinstance(result, dict):
            return False
        video_url = (result.get("video_url") or "").strip()
        if not video_url:
            return False           # {} / None / no video_url -> fall through
        is_hls = bool(result.get("is_hls"))
        dl_dir = (self.config.get("download_dir") or "").strip()
        if not dl_dir:
            return False
        try:
            os.makedirs(dl_dir, exist_ok=True)
        except Exception:
            return False
        import re as _re
        from pathlib import Path as _Path
        from .detect import safe_dest
        title = (result.get("title")
                 or url.rsplit("/", 1)[-1].split("?", 1)[0] or "download")
        title = _re.sub(r"[^\w.\- ]", "_", str(title))[:180] or "download"
        if is_hls:
            ext = ".mp4"           # ffmpeg remuxes the HLS/DASH manifest to MP4
        else:
            ext = (result.get("ext") or "").strip()
            if ext and not ext.startswith("."):
                ext = "." + ext
            if not ext:
                ext = ".mp4"
        output_path = str(safe_dest(_Path(dl_dir) / (title + ext)))
        # 695 (v3.66.695): route an HLS/DASH manifest from a plugin @extractor
        # through hls_downloader (the same ffmpeg path jsonapi/vixen use)
        # instead of falling through. ffmpeg absent / hls_downloader missing ->
        # fall through (False), matching the jsonapi HLS contract.
        if is_hls:
            try:
                from . import hls_downloader as _hls
            except ImportError:
                sys.stderr.write("  plugin_extractor: hls_downloader unavailable\n")
                return False
            if not _hls.is_available():
                sys.stderr.write("  plugin_extractor: ffmpeg not on PATH\n")
                return False
            self._update_job(url, "running",
                             f"Extracting HLS via plugin [{self.site_id}]...")
            try:
                dl_result = _hls.download(
                    video_url, output_path, referer=url,
                    cancel_check=lambda: self._stop.is_set())
            except Exception as e:
                sys.stderr.write(f"  plugin_extractor: hls path raised {e}\n")
                return False
            if not dl_result.ok:
                self.log_event("plugin_hls_failed",
                               f"hls failed: {dl_result.error}", url=url)
                try:
                    if os.path.exists(output_path):
                        os.remove(output_path)
                except Exception:
                    pass
                return False
            return True
        self._update_job(url, "running", f"Extracting via plugin [{self.site_id}]...")
        try:
            ok = self._do_direct_http_download(
                page_url=url, file_url=video_url, output_path=output_path, referer=url)
        except Exception as e:
            sys.stderr.write(f"  plugin_extractor: http path raised {e}\n")
            return False
        return bool(ok)

    def _try_library_extractor(self, url: str) -> bool:
        """v3.43.63: attempt a library-extractor download for `url`.

        Returns True if a download completed successfully and job state
        was updated. Returns False if the extractor isn't applicable
        (URL host not registered / library not installed) OR if the
        extract attempt failed. Caller falls through to JD/qB/teach
        on False.

        Two-stage:
          1. extractors.extract(site_id, url, quality_preference) -> ExtractResult
          2. If is_hls: hls_downloader.download(...). Else: existing
             _http_download path with the direct URL.

        On success, embeds metadata into the file via metadata_tagger
        (v3.43.64) if available; otherwise the file is left as-is.
        """
        try:
            from . import extractors as _ex
        except ImportError:
            return False
        # Match URL → site_id
        site_id = _ex.is_supported_url(url)
        if not site_id:
            sys.stderr.write("  library_extractor: url not in registry\n")
            return False
        if not _ex.is_available(site_id):
            # Library not installed; opt-in feature with graceful fall-through.
            return False

        # Quality preference: split the comma-separated config string into
        # the same list we use elsewhere.
        qpref_raw = self.config.get("quality_preference", "best") or "best"
        qpref = [p.strip() for p in qpref_raw.split(",") if p.strip()] or ["best"]

        self._update_job(url, "running", f"Extracting via library [{site_id}]...")

        result = _ex.extract(site_id, url, qpref)
        if not result.ok:
            self.log_event(
                "library_extract_failed",
                f"library {result.extractor or site_id}: {result.error}",
                url=url,
            )
            sys.stderr.write(
                f"  library_extractor: extract failed ({result.error}): "
                f"{(result.error_detail or '')[:200]}\n"
            )
            return False

        # Resolve download directory.
        dl_dir = (self.config.get("download_dir") or "").strip()
        if not dl_dir:
            sys.stderr.write("  library_extractor: no download_dir configured\n")
            return False
        try:
            os.makedirs(dl_dir, exist_ok=True)
        except Exception as e:
            sys.stderr.write(f"  library_extractor: mkdir failed: {e}\n")
            return False

        # Determine output filename. Use library-provided title if any;
        # otherwise the URL's tail.
        # v3.43.64: safe_dest lives in detect.py (not fname.py) — the
        # v3.43.63 inline import was a dormant bug that crashed the
        # moment a library was actually installed.
        from .detect import safe_dest
        ext = ".mp4"
        if result.is_hls:
            ext = ".mp4"  # HLS gets remuxed to mp4 by ffmpeg
        title_root = result.title or url.rsplit("/", 1)[-1].split("?", 1)[0]
        # v3.43.64: thread the user's filename_template through the
        # library-extractor path too. Previously this branch hard-coded
        # `title + .mp4`, ignoring the per-site filename_template
        # entirely. Now we build the same ctx_vars shape the teach path
        # uses (line ~5610), plus the new extractor-specific variables
        # (performer/studio/year/duration/quality/extractor) populated
        # from the ExtractResult.
        now = datetime.now()
        upload_year = ""
        upload_iso = result.upload_date or ""
        if upload_iso:
            # Pull a 4-digit year out of whatever shape upload_date is in.
            import re as _re
            m = _re.search(r"(19|20)\d{2}", upload_iso)
            if m:
                upload_year = m.group(0)
        ctx_vars = {
            "site":        self.config.get("name", "site"),
            "title":       result.title or "",
            "filename":    title_root,
            "stem":        title_root,
            "ext":         ext,
            "resolution":  result.quality or "",
            "date":        now.strftime("%Y-%m-%d"),
            "time":        now.strftime("%H-%M-%S"),
            "datetime":    now.strftime("%Y-%m-%d_%H-%M-%S"),
            # v3.43.64 additions — populated from the extractor result.
            "performer":   result.author or "",
            "artist":      result.author or "",
            "studio":      result.author or self.config.get("name", ""),
            "year":        upload_year,
            "upload_date": upload_iso,
            "duration":    format_duration_for_filename(result.duration_sec),
            "quality":     result.quality or "",
            "extractor":   result.extractor or "",
        }
        tpl = (self.config.get("filename_template", "") or "{filename}{ext}").strip()
        rendered = resolve_filename_template(tpl, ctx_vars)
        if not rendered:
            rendered = title_root + ext
        elif not os.path.splitext(rendered)[1]:
            rendered = rendered + ext
        output_filename = safe_dest(rendered)
        output_path = os.path.join(dl_dir, output_filename)
        # Ensure any subdirectories the template introduced (e.g.
        # "{studio}/{title}{ext}") exist before we start writing.
        try:
            os.makedirs(os.path.dirname(output_path) or dl_dir, exist_ok=True)
        except Exception as e:
            sys.stderr.write(f"  library_extractor: subdir mkdir failed: {e}\n")
            # Not fatal — write into dl_dir directly with a flat name
            output_filename = safe_dest(title_root + ext)
            output_path = os.path.join(dl_dir, output_filename)

        self._update_job(
            url, "running",
            f"Downloading via {result.extractor} "
            f"[{result.quality or qpref[0]}{' HLS' if result.is_hls else ''}]...",
        )

        # Dispatch: HLS or direct.
        if result.is_hls:
            try:
                from . import hls_downloader as _hls
            except ImportError:
                sys.stderr.write("  library_extractor: hls_downloader unavailable\n")
                return False
            if not _hls.is_available():
                sys.stderr.write(
                    "  library_extractor: ffmpeg not on PATH; install ffmpeg "
                    "to enable HLS downloads\n"
                )
                return False

            # Per-progress callback updates job state in DB
            def _progress(p):
                pct = ""
                if result.duration_sec > 0:
                    pct_v = min(100, int(100.0 * p.get("seconds", 0) / result.duration_sec))
                    pct = f" {pct_v}%"
                self._update_job(
                    url, "running",
                    f"HLS{pct} • {fmt_bytes(p.get('bytes', 0))}",
                )

            referer = url  # for Referer header
            user_agent = self.config.get("user_agent", "") or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            dl_result = _hls.download(
                result.file_url,
                output_path,
                user_agent=user_agent,
                referer=referer,
                progress_callback=_progress,
                cancel_check=lambda: self._stop.is_set(),
            )
            if not dl_result.ok:
                self.log_event(
                    "library_hls_failed",
                    f"hls download failed: {dl_result.error}",
                    url=url,
                )
                sys.stderr.write(
                    f"  library_extractor: HLS failed ({dl_result.error}): "
                    f"{(dl_result.error_detail or '')[:200]}\n"
                )
                # Best-effort cleanup of partial file
                try:
                    if os.path.exists(output_path):
                        os.remove(output_path)
                except Exception:
                    pass
                return False
            # v3.43.64: embed MP4 metadata + cover art before reporting
            # done. The tag step is fail-open: a failure here doesn't
            # change the job outcome — the download itself succeeded.
            self._embed_metadata_if_mp4(
                output_path,
                title=result.title,
                performer=result.author,
                site_name=self.config.get("name", ""),
                upload_date=result.upload_date,
                source_url=url,
                thumbnail_url=result.thumbnail_url,
                quality=result.quality,
                duration_sec=result.duration_sec,
                extractor_name=result.extractor,
            )
            file_size_on_disk = self._size_on_disk_after_tagging(
                output_path, dl_result.bytes_written)
            self._update_job(
                url, "done",
                f"Done via {result.extractor} (HLS, {fmt_bytes(dl_result.bytes_written)})",
                filename=output_filename, file_size=file_size_on_disk,
            )
            db_log(self.site_id, self.config.get("name", "?"), url, "done",
                   output_filename, file_size_on_disk,
                   f"library_extractor={result.extractor} hls=1 "
                   f"quality={result.quality}",
                   bytes_fetched=dl_result.bytes_written,
                   # A literal is right here, unlike jsonapi/vixen/aylo: this
                   # arm has its OWN db_log and returns, so the constant cannot
                   # leak onto the direct-URL row below.
                   transfer_mode="segmented", file_path=output_path)
            return True

        # Direct URL path: reuse the existing _http_download path. It
        # already handles resume, progress, retries, and File-on-disk
        # writing. We just need to feed it the right inputs.
        # v3.43.65: tier-probe before download. If the user's site
        # config opts in (rare here since library-extractor sites
        # already pick best quality), swap to a higher tier when the
        # CDN URL contains a tier segment.
        probed_file_url = self._probe_for_higher_tier(
            result.file_url, referer=url)
        try:
            ok = self._do_direct_http_download(
                page_url=url, file_url=probed_file_url,
                output_path=output_path,
                referer=url,
            )
        except Exception as e:
            sys.stderr.write(f"  library_extractor: http path raised {e}\n")
            return False

        if not ok:
            return False

        try:
            size = os.path.getsize(output_path)
        except OSError:
            size = 0
        # v3.43.64: tag the MP4 before reporting done (same fail-open
        # behavior as the HLS branch above).
        self._embed_metadata_if_mp4(
            output_path,
            title=result.title,
            performer=result.author,
            site_name=self.config.get("name", ""),
            upload_date=result.upload_date,
            source_url=url,
            thumbnail_url=result.thumbnail_url,
            quality=result.quality,
            duration_sec=result.duration_sec,
            extractor_name=result.extractor,
        )
        file_size_on_disk = self._size_on_disk_after_tagging(
            output_path, size)
        self._update_job(
            url, "done",
            f"Done via {result.extractor} ({fmt_bytes(size)})",
            filename=output_filename, file_size=file_size_on_disk,
        )
        db_log(self.site_id, self.config.get("name", "?"), url, "done",
               output_filename, file_size_on_disk,
               f"library_extractor={result.extractor} hls=0 "
               f"quality={result.quality}",
               # UNKNOWN, stated rather than guessed: `size` here is
               # os.path.getsize of the output, and this path routes through
               # _do_direct_http_download which returns only a bool. There is
               # no transfer count to report, so NULL -- which a consumer must
               # treat as "not proven", never as a download.
               bytes_fetched=None,
               # The COUNT is unknown here; the TRANSPORT is not. Two separate
               # facts, and the bool return only loses the first one.
               transfer_mode="http", file_path=output_path)
        return True
