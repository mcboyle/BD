"""Module-level constants used across the application."""
import os
import re
from pathlib import Path

# v3.43.80: INSTALL_DIR. BD's launch script CWDs into the install
# directory, so legacy code uses Path() relative to CWD. Some newer
# modules (Phase 116 plugins, 117 diagnostics_bundle, 119 EOL export,
# 135 discovery, 136 fixtures, 137 i18n, 97 scheduling) want an
# explicit absolute path — e.g. to find INSTALL_DIR/plugins/ from a
# bg thread that may have changed CWD. Resolution order:
#   1. BD_INSTALL_DIR env var, if set (tests + Docker use this)
#   2. Current working directory at module import time
# Either way, this is a Path object so callers can do INSTALL_DIR/"foo".
INSTALL_DIR = Path(os.environ.get("BD_INSTALL_DIR", "")).resolve() \
              if os.environ.get("BD_INSTALL_DIR") \
              else Path.cwd().resolve()

# v3.66.9: DB_PATH stays the bare relative string for backwards
# compatibility with chdir-based test isolation (many tests do
# `os.chdir(tmpdir); db_init()` and expect the DB to land in tmpdir).
# The launcher and INSTALL_DIR-aware callers can join with INSTALL_DIR
# explicitly. See db.py::db_conn() for the resolution logic.
DB_PATH = "downloader_history.db"
SCREENSHOTS_DIR = Path("screenshots")
# v3.66.11: was `SCREENSHOTS_DIR.mkdir(exist_ok=True)` at module load.
# That side-effected a `screenshots/` directory into whatever cwd
# happened to be active at `import bulk_downloader` time — including
# `/screenshots` if a tool imported the package from `/`. Callers that
# actually need the dir (runner.py, the screenshot-serving Flask
# route) now call _ensure_screenshots_dir() on first use.
def _ensure_screenshots_dir():
    """Idempotent: create SCREENSHOTS_DIR if absent. Safe to call
    repeatedly; cheap (one stat) on the hot path."""
    SCREENSHOTS_DIR.mkdir(exist_ok=True)

# Words/extensions that hint a clickable element triggers a download. Matched
# anywhere in element text or attribute values.
#
# v3.43.65: extended with URL-shape patterns from the recon survey of 20
# live-player dumps. Catches the specific preview/trailer file naming
# conventions used by major adult networks:
#   - Gamma (AdultTime/DFXtra): `tr_127673_sm.mp4`, `tr_127673_big.mp4`
#   - NewSensations: `mini-bite` / `MiniBite_S.mp4`
#   - Reptyle / Bromo: `bio_small.mp4`
#   - Vixen network: `_353P` (a 353-pixel-tall preview-thumb tier that
#     is literally smaller than 480p — we want it to LOSE every contest)
#   - Naughty America `_desktop10sec` / `_mobile10sec` (10-second teasers)
NON_VIDEO_RE = re.compile(
    r"\b(zip|photos?|images?|pictures?|gallery|galleries|"
    r"trailer|teaser|preview|sample|screenshot|thumbnail|cover|poster)\b"
    # v3.43.65 additions — URL-path-shape exclusions
    r"|tr_\d+_(?:sm|big)\.mp4"          # Gamma trailers
    r"|mini[\s_-]?bite"                  # NewSensations short clips
    r"|bio_small\.mp4"                   # Reptyle/Bromo previews
    r"|_353[pP]"                         # Vixen preview-thumb tier
    r"|_(?:desktop|mobile)\d+sec"        # Naughty America N-second teasers
    r"|previewvideos/"                   # Vixen network preview path
    ,
    re.I)

# File-size strings inside element text. Used as a tiebreaker when multiple
# candidates score the same resolution: bigger file ≈ better encode.
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(tb|gb|mb|kb)\b", re.I)

# Rate-limit indicators. Page text containing any of these triggers a
# 24-hour cooldown so we don't hammer a site that's pushing back.
RL_RE = re.compile(
    r"rate.?limit|too many requests|try again later|download limit"
    r"|quota exceeded|throttled|wait \d+ (?:hours?|minutes?|seconds?)"
    r"|access denied|forbidden|403", re.I)

# Retry backoff schedule in seconds. After each failed attempt, the next
# retry waits the corresponding entry. We cap at the last value (1h).
RETRY_DELAYS = [600, 3600]  # 10m, then 1h

# Auth/blocking redirect detection. URL fragments that, when present after
# a navigation, indicate either a captcha/block (long cooldown) or an
# auth-redirect (re-login needed). Split because the response is different.
BLOCK_HINTS = ["/captcha", "/blocked", "/verify"]
AUTH_HINTS = ["/login", "/signin", "/sign-in", "/auth", "/log-in",
              "/account/login", "/members/login", "/session/new",
              "/users/sign_in", "/accounts/login"]
LOGIN_HINTS = BLOCK_HINTS + AUTH_HINTS

# In-place login walls don't change the URL — detect them by body text.
# Conservative: needs a clear login-form signal, not just the word
# "login" (which appears in nav bars on logged-in pages too). Used by
# _check_redirect to catch a session that died mid-process without a
# redirect. Detect-side only — we report/recover, never evade.
AUTH_BODY_RE = re.compile(
    r"(?:please\s+)?(?:log|sign)\s*in\s+to\s+(?:continue|view|download|watch)"
    r"|your\s+session\s+has\s+expired"
    r"|sign\s*in\s+to\s+your\s+account"
    r"|<input[^>]+type=[\"']password[\"']", re.I)


class _HTTPDownloadFailed(Exception):
    """Raised by SiteRunner._http_download when httpx can't complete the
    transfer. Caller catches this and falls back to Playwright save_as.
    Distinct from regular Exception so we don't swallow programming bugs."""
    pass


class _StagingUnavailable(Exception):
    """part-staging-collision: raised when a transfer cannot RESERVE the
    ``.part`` it would stage into -- because a different live download owns
    it, or because ownership cannot be measured at all.

    The transfer refuses; it never writes into a staging path it does not own.
    That is the whole defect: two workers derived one ``.part`` from one final
    name, the second read the first's partial bytes as a resume position, and
    appended a different scene onto it.

    Kept deliberately NOT a subclass of ``_HTTPDownloadFailed`` for the same
    reason ``_DownloadTruncated`` is not: the Playwright fallback would happily
    write the browser's bytes to the very destination this refusal is
    protecting. The caller routes the URL to ``needs_review`` instead."""
    pass


class _DownloadTruncated(Exception):
    """BP-INT (v3.66.284): raised when a transfer's stream ends before the
    advertised Content-Length is satisfied. The .part is NOT promoted to the
    final name (a short file must never look ``done``); the caller routes the
    URL to ``needs_review`` so the operator can force a re-download. Kept
    deliberately NOT a subclass of _HTTPDownloadFailed so the mirror-retry /
    Playwright fallback handlers don't swallow it."""
    pass


# ─── Phase 7.1: browser fingerprint pools ─────────────────────────────────
# Realistic User-Agent strings + matching Sec-CH-UA hints. Picked to look
# like common consumer setups (Windows Chrome > Mac Chrome > Windows Firefox)
# rather than rare combos that flag fingerprinting systems. All are
# desktop — sites that gate by mobile/desktop should still see desktop.
FINGERPRINT_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]
# Common 16:9 viewports — uncommon resolutions look fingerprintable on their own
FINGERPRINT_VIEWPORTS = [
    (1920, 1080), (1920, 1080), (1920, 1080),  # 1080p × 3 weight
    (2560, 1440), (2560, 1440),                 # 1440p × 2
    (1366, 768), (1536, 864), (1440, 900),      # laptop sizes
    (3840, 2160),                               # 4K
]
FINGERPRINT_TIMEZONES = [
    "America/Los_Angeles", "America/Denver", "America/Chicago",
    "America/New_York", "America/Phoenix", "Europe/London",
    "Europe/Berlin", "Europe/Paris", "Australia/Sydney", "Asia/Tokyo",
]
FINGERPRINT_LOCALES = ["en-US", "en-US", "en-US", "en-GB", "en-CA", "en-AU"]

def make_fingerprint():
    """Generate a fresh randomized fingerprint dict. The user can call
    this from the UI to randomize a site's fingerprint, or it's used
    automatically when a site is created without one."""
    import random
    vw, vh = random.choice(FINGERPRINT_VIEWPORTS)
    return {
        "user_agent": random.choice(FINGERPRINT_UAS),
        "viewport_w": vw,
        "viewport_h": vh,
        "timezone":   random.choice(FINGERPRINT_TIMEZONES),
        "locale":     random.choice(FINGERPRINT_LOCALES),
    }

# ─── Phase 7.2: captcha detection ─────────────────────────────────────────
# Selectors that, when visible on a page, indicate a captcha challenge
# the user needs to solve. Detection should be conservative — false
# positives interrupt automation. These selectors target the rendered
# challenge UI, not just script tags (which may be present on every page).
CAPTCHA_SELECTORS = [
    "iframe[src*='hcaptcha.com/captcha']",
    "iframe[src*='recaptcha/api2/anchor']",
    "iframe[src*='challenges.cloudflare.com']",
    ".h-captcha iframe",
    ".g-recaptcha iframe",
    "#cf-challenge-stage:visible",
    "div[id^='cf-chl-widget']",
    "div.g-recaptcha-bubble-arrow",  # widget arrow only renders when active
]

# ─── Phase 9.2: Stealth init script ───────────────────────────────────────
# Patches that hide the most obvious Playwright/headless tells. Injected via
# page.add_init_script() so it runs BEFORE every page's own scripts, including
# Cloudflare's challenge page. This is what `puppeteer-extra-plugin-stealth`
# does — well-documented set of fingerprint correction patches.
#
# What gets patched:
#   navigator.webdriver       → undefined (vs. true on plain Playwright)
#   navigator.plugins         → length 3, realistic PluginArray
#   navigator.languages       → ["en-US","en"] (was [] under headless)
#   navigator.permissions     → Notification returns "default", not "denied"
#   chrome.runtime            → present (was missing — dead giveaway)
#   chrome.loadTimes/csi      → stubbed methods that return plausible data
#   WebGL vendor/renderer     → Intel Iris, not "Brian Paul Mesa OffScreen"
#   Canvas fingerprint        → tiny per-pixel noise so canvas hash varies
#   AudioContext fingerprint  → tiny per-sample noise
#   Function.prototype.toString → returns "function name() { [native code] }"
#                                 for our patched functions (so toString() can't
#                                 detect we replaced built-ins)
#
# References: bot.sannysoft.com, abrahamjuliot.github.io/creepjs, and the
# stealth-evasions list at puppeteer-extra/blob/master/packages/
#   puppeteer-extra-plugin-stealth/evasions
STEALTH_JS = r"""
(() => {
  if (window.__pw_stealth_applied) return;
  window.__pw_stealth_applied = true;

  // Helper: install a property that survives toString() inspection
  const _patch = (obj, prop, value) => {
    try {
      Object.defineProperty(obj, prop, { get: () => value, configurable: true });
    } catch (e) {}
  };

  // 1. webdriver — the #1 most-checked tell
  _patch(navigator, 'webdriver', undefined);

  // 2. plugins — empty PluginArray is the second-most-checked tell.
  // Real Chrome has Chromium PDF plugin + a few others.
  try {
    const pluginData = [
      { name: 'PDF Viewer',           filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer',    filename: 'internal-pdf-viewer', description: '' },
      { name: 'Chromium PDF Viewer',  filename: 'internal-pdf-viewer', description: '' },
      { name: 'Microsoft Edge PDF',   filename: 'internal-pdf-viewer', description: '' },
      { name: 'WebKit built-in PDF',  filename: 'internal-pdf-viewer', description: '' },
    ];
    const plugins = pluginData.map(p => Object.assign(Object.create(Plugin.prototype), p));
    plugins.namedItem = name => plugins.find(p => p.name === name) || null;
    plugins.refresh = () => {};
    plugins.item = i => plugins[i] || null;
    Object.defineProperty(plugins, 'length', { get: () => pluginData.length });
    _patch(navigator, 'plugins', plugins);
  } catch (e) {}

  // 3. languages — headless reports [], real Chrome reports user's locale
  if (!navigator.languages || !navigator.languages.length) {
    _patch(navigator, 'languages', ['en-US', 'en']);
  }

  // 4. permissions.query — headless returns "denied" for Notification
  // (a tell-tale sign); real browsers return "default" for non-secure pages
  // and the actual setting elsewhere.
  try {
    const origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (params) => (
      params && params.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission || 'default' })
        : origQuery(params)
    );
  } catch (e) {}

  // 5. chrome.runtime — Chrome installs this for extension messaging.
  // Headless Chromium has `window.chrome` but no runtime. CF checks for it.
  if (!window.chrome) window.chrome = {};
  if (!window.chrome.runtime) {
    window.chrome.runtime = {
      OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
      OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
      PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
      PlatformOs:   { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
      RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' },
    };
  }
  // chrome.loadTimes() and chrome.csi() — old APIs, still queried by some checks
  if (!window.chrome.loadTimes) {
    window.chrome.loadTimes = () => ({
      requestTime: Date.now()/1000 - 1, startLoadTime: Date.now()/1000 - 1,
      commitLoadTime: Date.now()/1000 - 0.5, finishDocumentLoadTime: Date.now()/1000 - 0.3,
      finishLoadTime: Date.now()/1000 - 0.1, firstPaintTime: Date.now()/1000 - 0.2,
      firstPaintAfterLoadTime: 0, navigationType: 'Other', wasFetchedViaSpdy: true,
      wasNpnNegotiated: true, npnNegotiatedProtocol: 'h2', wasAlternateProtocolAvailable: false,
      connectionInfo: 'h2',
    });
  }
  if (!window.chrome.csi) {
    window.chrome.csi = () => ({ onloadT: Date.now(), pageT: 100, startE: Date.now()-200, tran: 15 });
  }

  // 6. WebGL vendor/renderer — headless reports "Brian Paul" / "Mesa OffScreen"
  // which is a fingerprintable tell. Spoof to Intel/Iris which is generic enough
  // to be plausible across many users.
  try {
    const origGetParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (parameter) {
      // UNMASKED_VENDOR_WEBGL=37445, UNMASKED_RENDERER_WEBGL=37446
      if (parameter === 37445) return 'Intel Inc.';
      if (parameter === 37446) return 'Intel Iris OpenGL Engine';
      return origGetParameter.call(this, parameter);
    };
    if (window.WebGL2RenderingContext) {
      const orig2 = WebGL2RenderingContext.prototype.getParameter;
      WebGL2RenderingContext.prototype.getParameter = function (parameter) {
        if (parameter === 37445) return 'Intel Inc.';
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
        return orig2.call(this, parameter);
      };
    }
  } catch (e) {}

  // 7. Canvas fingerprint noise — overwrite toDataURL/getImageData with
  // a tiny per-pixel offset so canvas-hash fingerprinting gives a different
  // result every time (real browsers' GPUs have similar variation between
  // sessions, plus modern browsers add noise too).
  try {
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function (...args) {
      try {
        const ctx = this.getContext('2d');
        if (ctx && this.width > 0 && this.height > 0) {
          // Add a barely-visible per-canvas offset so hashes vary
          ctx.fillStyle = 'rgba(0,0,0,0.001)';
          ctx.fillRect(0, 0, 1, 1);
        }
      } catch (e) {}
      return origToDataURL.apply(this, args);
    };
  } catch (e) {}

  // 8. AudioContext noise — same idea, for AudioBuffer fingerprinting
  try {
    const origGetChannelData = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = function (channel) {
      const data = origGetChannelData.call(this, channel);
      // Add 1e-7 noise to each sample (inaudible, fingerprint-disrupting)
      for (let i = 0; i < Math.min(data.length, 100); i++) {
        data[i] += (Math.random() - 0.5) * 1e-7;
      }
      return data;
    };
  } catch (e) {}
})();
"""
