"""v3.43.60: Captcha relay (server-side takeover variant).

When a worker hits a captcha challenge it can't auto-solve, this module:

  1. Detects the challenge type via DOM probes (Turnstile / hCaptcha /
     reCAPTCHA / Cloudflare interstitial)
  2. Marks the URL pending in this module's state store
  3. Sends a web push notification to the user's phone with a link to
     the dashboard
  4. The user opens the dashboard URL → "Solve now" button triggers a
     server-side manual takeover (visible Chrome opens at the captcha URL)
  5. User RDPs into the server (or uses iLO console), solves the captcha
     in the visible Chrome window
  6. User clicks "Resume" — cookies persist to the worker's profile dir,
     URL gets re-queued, worker picks it up automatically

Per spec: option A from the planning conversation — server-side takeover,
NOT phone-relay. The phone notification's job is just to tell the user
"RDP in and solve". This is much simpler than browser-mirror over a phone
and reuses the existing manual-login infrastructure.

# Config

Per-site flag: `use_captcha_relay` (boolean, default False). When True,
worker calls into this module's `check_and_handle(page, site_id, url)`
after every page load. When False, captcha behavior is unchanged.

# Detection

We probe for four challenge types. Each pattern is a Playwright locator
expression that returns truthy if the challenge is on the page:

    turnstile:   iframe[src*="challenges.cloudflare.com"], .cf-turnstile
    hcaptcha:    iframe[src*="hcaptcha.com"], .h-captcha
    recaptcha:   iframe[src*="recaptcha"], .g-recaptcha
    cloudflare:  #cf-please-wait, #challenge-stage, [title="Just a moment..."]

The order matters — we check the more specific Cloudflare-Turnstile case
before the generic Cloudflare interstitial.

# State

Pending captcha sessions live in this module's `_pending` dict:

    {
      url: {
        "url": "https://...",
        "site_id": "wowgirls",
        "captcha_type": "turnstile",
        "detected_at": 1715600000,
        "status": "pending" | "solving" | "resolved" | "dismissed",
        "solve_session_id": "<id>" | None,
        "notified": True/False,
      }
    }

# Notification dedupe

We don't push for every URL — that would flood the phone. The dedupe key
is `(site_id, captcha_type)`. After a notification for a given key, we
wait at least PUSH_DEDUPE_WINDOW_S (default 300s) before another one.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from dataclasses import dataclass, asdict
from typing import Callable, Optional

# MOD-1 remote takeover: transport + input-security primitives (stdlib-only leaf)
# and the audit sink for session-summary lifecycle events. Kept coupling-neutral
# at the blueprint layer -- the captcha-api blueprint reaches these THROUGH this
# module, never directly, so app_captcha_relay.py gains no new import edge.
from . import takeover
from . import audit


# ─── Tunables ───────────────────────────────────────────────────────

# Tunables read at CALL TIME (store key > env seed > default) so a Settings
# write takes effect without a restart (v3.66.503 GUI parity). Previously
# module-level constants bound once at import.

# How long to wait between push notifications for the same (site, type).
def _push_dedupe_window_s() -> int:
    from .runtime_flags import num
    return num("captcha_push_dedupe_s", "BD_CAPTCHA_PUSH_DEDUPE_S", 300, int)


# After this many seconds with no user action, a pending captcha session
# auto-expires and the URL is failed in the queue. User can manually
# re-queue if they still want to try.
def _pending_timeout_s() -> int:
    from .runtime_flags import num
    return num("captcha_pending_timeout_s",
               "BD_CAPTCHA_PENDING_TIMEOUT_S", 3600, int)


# MOD-1 A-5b: a `solving` takeover session with no operator input for this long
# is finalized by the sweep; the SSE viewer stream honors the same bound. The
# clock resets on each accepted input (submit_takeover_input).
def _takeover_idle_timeout_s() -> int:
    from .runtime_flags import num
    return num("captcha_takeover_idle_timeout_s",
               "BD_CAPTCHA_TAKEOVER_IDLE_TIMEOUT_S", 300, int)

# Cap on simultaneous pending captcha URLs. Defense against runaway state.
MAX_PENDING = 64


# ─── Detection patterns ─────────────────────────────────────────────

CHALLENGE_TYPES = ("turnstile", "hcaptcha", "recaptcha", "cloudflare")

# Each entry: (challenge_type, [css_selectors that, if any match, mean the
# challenge is present]). Order matters — first match wins, so the more
# specific patterns come first.
_DETECTION_PROBES: list[tuple[str, list[str]]] = [
    # Cloudflare Turnstile — looks like an embedded iframe, fairly unique
    # to challenges.cloudflare.com domain.
    ("turnstile", [
        'iframe[src*="challenges.cloudflare.com"]',
        '.cf-turnstile',
        '[data-sitekey][data-callback]',  # heuristic: Turnstile widget mount
    ]),
    # hCaptcha — used by some adult sites and Cloudflare alternative tier.
    ("hcaptcha", [
        'iframe[src*="hcaptcha.com"]',
        'iframe[src*="newassets.hcaptcha.com"]',
        '.h-captcha',
        'div[data-hcaptcha-widget-id]',
    ]),
    # Google reCAPTCHA (v2 checkbox + v3 invisible)
    ("recaptcha", [
        'iframe[src*="google.com/recaptcha"]',
        'iframe[src*="recaptcha/api2"]',
        '.g-recaptcha',
        'div[data-sitekey][data-callback]',  # may overlap turnstile — checked after
    ]),
    # Cloudflare "Just a moment…" full-page interstitial. Different from
    # Turnstile in that we can't auto-solve it — the page itself runs the
    # JS challenge with no widget the user can interact with.
    ("cloudflare", [
        '#cf-please-wait',
        '#challenge-stage',
        '#challenge-form',
        'meta[http-equiv="refresh"][content*="challenge"]',
    ]),
]

# Page title patterns that strongly indicate a CF interstitial — used when
# DOM selectors haven't shown up yet (the page is still loading the JS chunk).
_INTERSTITIAL_TITLE_RE = re.compile(
    r"(just a moment|attention required|please wait|verifying you are human)",
    re.IGNORECASE,
)


# ─── State ──────────────────────────────────────────────────────────

@dataclass
class PendingCaptcha:
    url: str
    site_id: str
    captcha_type: str
    detected_at: float
    status: str = "pending"          # pending | solving | resolved | dismissed
    solve_session_id: Optional[str] = None
    notified: bool = False
    notified_at: Optional[float] = None
    resolved_at: Optional[float] = None
    worker_idx: Optional[int] = None
    title: str = ""                  # captured page title for context
    # A-5b idle clock: set when the solve starts, refreshed on each accepted
    # operator input. None on entries that never entered `solving`.
    last_input_at: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


_pending: dict[str, PendingCaptcha] = {}
_lock = threading.RLock()

# (site_id, captcha_type) -> last_push_ts. Dedupe push notifications.
_last_push_ts: dict[tuple[str, str], float] = {}

# Hooks injected by runner.py at startup. Keeping them as setters avoids
# a circular import between runner and this module.
_takeover_starter: Optional[Callable[[str, str], dict]] = None
_takeover_ender: Optional[Callable[[str, str, str], None]] = None

# A-5b (A5-R3): live-browser census, injected by app.py. Returns the list of
# (site_id, url) pairs with an actually-open solve browser, across all
# runners. The sweep cross-checks this against the registry -- a live browser
# the registry does not bind is an orphan and is reaped. When the census is
# unregistered (or raises), the browser surface is UNVERIFIABLE and the sweep
# must say so, never fold it into "0 orphans" (unknown-fails-to-reap).
_session_census: Optional[Callable[[], list]] = None


# ─── Public API: setters / queries ──────────────────────────────────

def register_takeover_starter(fn: Callable[[str, str], dict]) -> None:
    """Inject a callable `fn(site_id, url) -> dict` that opens a visible
    browser at `url` for `site_id` and returns a session info dict like
    `{"session_id": ..., "vnc_url": ...}`. SiteRunner provides this."""
    global _takeover_starter
    _takeover_starter = fn


def register_takeover_ender(fn: Callable[[str, str, str], None]) -> None:
    """Inject a callable `fn(site_id, url, resolution)` that closes the
    visible browser for `url` and optionally requeues. `resolution` is
    one of: 'resolved' (requeue), 'dismissed' (leave failed)."""
    global _takeover_ender
    _takeover_ender = fn


def register_session_census(fn: Callable[[], list]) -> None:
    """A-5b: inject a callable returning [(site_id, url), ...] for every
    live solve browser across all runners. app.py provides this. Enables the
    sweep's live-surface cross-check (A5-R3)."""
    global _session_census
    _session_census = fn


def list_pending(include_resolved: bool = False) -> list[dict]:
    now = time.time()
    with _lock:
        items = list(_pending.values())
    if not include_resolved:
        items = [p for p in items if p.status not in ("resolved", "dismissed")]
    out = []
    for p in items:
        d = p.to_dict()
        # A-5c: session age so the operator panel can show how long a challenge
        # has waited; idle_s (time since last operator input) only while solving.
        d["age_s"] = round(now - p.detected_at, 1)
        d["idle_s"] = (round(now - p.last_input_at, 1)
                       if (p.status == "solving" and p.last_input_at is not None)
                       else None)
        out.append(d)
    return out


def get_pending(url: str) -> Optional[dict]:
    with _lock:
        p = _pending.get(url)
        return p.to_dict() if p else None


# ─── Detection (called from worker after each page load) ────────────

def detect_captcha_in_page(page) -> Optional[str]:
    """Probe the page for a known challenge. Returns the challenge type
    (`turnstile` / `hcaptcha` / `recaptcha` / `cloudflare`) or None.

    Bare-except-free; each probe wraps its own try since Playwright's
    locator API can raise various exceptions on detached frames.
    """
    if page is None:
        return None

    # Check title first — covers the "Just a moment" interstitial even
    # before the challenge DOM has settled.
    try:
        title = page.title() or ""
    except Exception:
        title = ""
    if title and _INTERSTITIAL_TITLE_RE.search(title):
        return "cloudflare"

    for ctype, selectors in _DETECTION_PROBES:
        for sel in selectors:
            try:
                # `count()` returns immediately without auto-wait, which is
                # what we want — a single-shot check, not a polling wait.
                loc = page.locator(sel)
                if loc.count() > 0:
                    return ctype
            except Exception:
                # Detached frames, navigation in flight, etc. — keep probing.
                continue
    return None


def detect_captcha_in_html(html: str, title: str = "") -> Optional[str]:
    """Fallback detector for tests + httpx-only paths where there's no
    page object. Less reliable than the Playwright probe but useful for
    static analysis."""
    if not html:
        if title and _INTERSTITIAL_TITLE_RE.search(title):
            return "cloudflare"
        return None
    if title and _INTERSTITIAL_TITLE_RE.search(title):
        return "cloudflare"
    lc = html.lower()
    if "challenges.cloudflare.com" in lc or "cf-turnstile" in lc:
        return "turnstile"
    if "hcaptcha.com" in lc or "h-captcha" in lc:
        return "hcaptcha"
    if "google.com/recaptcha" in lc or "g-recaptcha" in lc or "recaptcha/api2" in lc:
        return "recaptcha"
    if "cf-please-wait" in lc or "challenge-stage" in lc or "challenge-form" in lc:
        return "cloudflare"
    return None


# ─── High-level worker entry point ──────────────────────────────────

def check_and_handle(page, site_id: str, url: str,
                     worker_idx: Optional[int] = None) -> Optional[str]:
    """Called by the worker after each page load when use_captcha_relay
    is on. Detects, marks, notifies. Returns the captcha type if detected
    (the worker should then return the URL to the queue), or None if the
    page is clean.

    Idempotent: if the URL is already pending, doesn't re-mark — just
    refreshes the detected_at timestamp.
    """
    if not site_id or not url:
        return None
    ctype = detect_captcha_in_page(page)
    if not ctype:
        return None
    mark_captcha_needed(site_id, url, ctype, worker_idx=worker_idx,
                        title=_safe_title(page))
    return ctype


def _safe_title(page) -> str:
    try:
        return page.title() or ""
    except Exception:
        return ""


def mark_captcha_needed(site_id: str, url: str, captcha_type: str,
                        worker_idx: Optional[int] = None,
                        title: str = "") -> None:
    """Record a pending captcha, send a push (with dedupe)."""
    if captcha_type not in CHALLENGE_TYPES:
        sys.stderr.write(f"[captcha-relay] unknown type {captcha_type!r}\n")
        return

    needs_push = False
    with _lock:
        if len(_pending) >= MAX_PENDING and url not in _pending:
            # Drop the oldest resolved/dismissed entry to make room.
            _evict_oldest_resolved_locked()
            if len(_pending) >= MAX_PENDING:
                sys.stderr.write(f"[captcha-relay] MAX_PENDING reached; dropping {url}\n")
                return

        existing = _pending.get(url)
        if existing and existing.status in ("solving",):
            # User already working on it — don't re-notify.
            existing.detected_at = time.time()
            return

        if existing and existing.status == "pending":
            existing.detected_at = time.time()
            # Audit 2026-05 (Phase 5): still attempt push if we never
            # successfully notified for this URL. Previous behavior was
            # to return here unconditionally, which meant a failed first
            # push (network blip, missing VAPID keys, etc.) had no retry
            # path — the user stayed oblivious until the 1-hour sweep
            # dismissed it. _maybe_push has its own dedupe so this is
            # safe to call repeatedly.
            needs_push = not existing.notified
        else:
            _pending[url] = PendingCaptcha(
                url=url, site_id=site_id, captcha_type=captcha_type,
                detected_at=time.time(),
                worker_idx=worker_idx, title=title,
            )
            needs_push = True

    if needs_push:
        _maybe_push(site_id, captcha_type, url)


def _maybe_push(site_id: str, captcha_type: str, url: str) -> None:
    """Send a push notification, deduped per (site_id, captcha_type).

    Audit 2026-05 / Phase 5: the dedupe timestamp is updated only AFTER
    a successful push. Previously it was set unconditionally before the
    send_push call, so a transient push failure (network blip / VAPID
    misconfig) would suppress legitimate notifications for the next
    PUSH_DEDUPE_WINDOW_S seconds.
    """
    key = (site_id, captcha_type)
    now = time.time()
    last = _last_push_ts.get(key, 0)
    if now - last < _push_dedupe_window_s():
        return

    pushed_ok = False
    try:
        import importlib
        push = importlib.import_module("bulk_downloader.push")
        # The notification opens the dashboard at the captcha relay panel.
        dashboard_path = f"/#captcha-relay&url={_url_quote(url)}"
        push.send_push(
            title=f"Captcha required: {site_id}",
            body=f"{captcha_type.title()} challenge — RDP in to solve",
            url=dashboard_path,
            tag=f"captcha-{site_id}",
        )
        pushed_ok = True
    except ImportError:
        pass
    except Exception as e:
        sys.stderr.write(f"[captcha-relay] push failed: {e}\n")

    if pushed_ok:
        _last_push_ts[key] = now
        with _lock:
            p = _pending.get(url)
            if p is not None:
                p.notified = True
                p.notified_at = now


def _url_quote(s: str) -> str:
    try:
        from urllib.parse import quote
        return quote(s, safe="")
    except Exception:
        return s


def _evict_oldest_resolved_locked() -> None:
    """Caller holds _lock. Drop the oldest resolved/dismissed entry."""
    candidates = [(p.resolved_at or p.detected_at, k) for k, p in _pending.items()
                  if p.status in ("resolved", "dismissed")]
    if not candidates:
        return
    candidates.sort()
    _pending.pop(candidates[0][1], None)


# ─── Solve session (user clicks "Solve now") ────────────────────────

def start_solve(url: str) -> dict:
    """Kick off a server-side manual takeover for `url`. Returns the
    session info dict. The dashboard polls /api/captcha/pending for status.

    Raises RuntimeError if no takeover starter is registered (runner.py
    not initialized) or if the URL isn't pending.
    """
    with _lock:
        p = _pending.get(url)
        if p is None:
            raise RuntimeError(f"URL not pending: {url}")
        if p.status not in ("pending",):
            raise RuntimeError(f"URL not in 'pending' state (currently {p.status!r})")
        site_id = p.site_id

    if _takeover_starter is None:
        raise RuntimeError(
            "no takeover_starter registered — runner.py must call "
            "captcha_relay.register_takeover_starter() at startup"
        )

    try:
        info = _takeover_starter(site_id, url)
    except Exception as e:
        raise RuntimeError(f"takeover starter raised: {e}") from e

    session_id = (info or {}).get("session_id") or f"solve-{int(time.time())}"
    with _lock:
        p = _pending.get(url)
        if p is None:
            return info or {}
        # Audit 2026-05 (Phase 5): also guard against a concurrent
        # mark_dismissed / mark_resolved that flipped the status during
        # the (unlocked) takeover_starter call. Without this, we'd
        # overwrite a "dismissed" entry's status back to "solving" and
        # the takeover ender that already fired would be inconsistent
        # with our state.
        if p.status not in ("pending",):
            return info or {}
        p.status = "solving"
        p.solve_session_id = session_id
        p.last_input_at = time.time()  # A-5b: idle clock starts at solve start
    # MOD-1: open the per-session takeover channel so the cockpit screencast
    # route can subscribe, and audit the takeover start (session-summary
    # granularity -- NOT per input frame/event).
    takeover.open_channel(session_id)
    audit.audit_log("takeover", "start", session_id,
                    after={"url": url, "site_id": site_id})
    return {**(info or {}), "session_id": session_id, "url": url}


def mark_resolved(url: str) -> bool:
    """Called when the user clicks "Done" in the dashboard — or by the
    worker on a successful retry. Idempotent."""
    site_id = None
    sid = None
    with _lock:
        p = _pending.get(url)
        if p is None:
            return False
        site_id = p.site_id
        sid = p.solve_session_id
        p.status = "resolved"
        p.resolved_at = time.time()
    _call_ender(site_id, url, "resolved")
    if sid:
        takeover.close_channel(sid)
    audit.audit_log("takeover", "resolved", sid or url, after={"url": url})
    return True


def mark_dismissed(url: str) -> bool:
    """User gives up on this URL — it stays failed in the queue."""
    site_id = None
    sid = None
    with _lock:
        p = _pending.get(url)
        if p is None:
            return False
        site_id = p.site_id
        sid = p.solve_session_id
        p.status = "dismissed"
        p.resolved_at = time.time()
    _call_ender(site_id, url, "dismissed")
    if sid:
        takeover.close_channel(sid)
    audit.audit_log("takeover", "dismissed", sid or url, after={"url": url})
    return True


def _call_ender(site_id: str, url: str, resolution: str) -> None:
    """Invoke the registered takeover ender callback (close browser,
    optionally requeue). Soft-fail — ender errors must not break the
    state transition."""
    if _takeover_ender is None:
        return
    try:
        _takeover_ender(site_id, url, resolution)
    except Exception as e:
        sys.stderr.write(f"[captcha-relay] ender callback raised: {e}\n")


def is_pending(url: str) -> bool:
    with _lock:
        p = _pending.get(url)
        return p is not None and p.status in ("pending", "solving")


# ─── Maintenance ────────────────────────────────────────────────────

def sweep_expired(now: Optional[float] = None) -> int:
    """Legacy surface: count of pending/solving sessions moved to dismissed
    (expired or idle). Delegates to sweep_report (A-5b), which also reaps
    orphan channels/browsers as a side effect, but this int keeps its
    original meaning -- the number of TRACKED sessions swept -- so existing
    callers and tests hold. Use sweep_report for the full orphan accounting."""
    return sweep_report(now=now)["expired_or_idle"]


def sweep_report(now: Optional[float] = None) -> dict:
    """A-5b / A5-R3: the no-orphan sweep. ONE atomic registry (_pending
    under _lock) is the source of truth; the sweep DERIVES its active set
    from it and then CROSS-CHECKS the live surfaces against that set:

      * expired: pending/solving older than captcha_pending_timeout_s
      * idle:    solving with no accepted input for captcha_takeover_idle_
                 timeout_s (clock baselined at start_solve, reset on input)
      * orphan channels: an open takeover channel whose sid the registry does
        not bind to an ACTIVE solving entry (includes channels leaked past a
        terminal resolved/dismissed transition) -> closed
      * orphan browsers: a live solve browser (census) whose url the registry
        does not carry as active -> ender('dismissed')

    unknown-fails-to-reap: a surface the sweep CANNOT verify (census
    unregistered or raising) is returned in `unverified` and audited --
    a reaper whose denominator excludes an orphan reports "0" truthfully
    and uselessly, so unknown is a loud third state, never a silent zero.

    Enders/closes run OUTSIDE the lock (they may touch subprocesses).
    """
    if now is None:
        now = time.time()
    to_end: list[tuple[str, str, Optional[str]]] = []  # (site_id,url,sid) expired/idle
    close_sids: list[str] = []              # channels of reaped sessions
    active_urls: set[str] = set()
    active_sids: set[str] = set()
    idle_after = _takeover_idle_timeout_s()
    expire_after = _pending_timeout_s()
    with _lock:
        for p in _pending.values():
            if p.status not in ("pending", "solving"):
                continue  # terminal entries: their leaked channels are caught
                          # by the orphan-channel cross-check below
            expired = now - p.detected_at > expire_after
            idle = (p.status == "solving"
                    and now - (p.last_input_at or p.detected_at) > idle_after)
            if expired or idle:
                p.status = "dismissed"
                p.resolved_at = now
                to_end.append((p.site_id, p.url, p.solve_session_id))
                if p.solve_session_id:
                    close_sids.append(p.solve_session_id)
            else:
                active_urls.add(p.url)
                if p.solve_session_id:
                    active_sids.add(p.solve_session_id)

    # Cross-check 1: takeover channels. Denominator = ALL open channels, not
    # just the ones the registry remembers -- that inversion is the fix.
    unverified: list[str] = []
    try:
        open_sids = takeover.list_channel_sids()
    except Exception:
        open_sids = None
    if open_sids is None:
        unverified.append("channels")
        orphan_channels: list[str] = []
    else:
        orphan_channels = [s for s in open_sids if s not in active_sids]

    # Cross-check 2: live solve browsers via the census hook.
    orphan_browsers: list[tuple[str, str]] = []
    live = None
    if _session_census is not None:
        try:
            live = list(_session_census() or [])
        except Exception as e:
            sys.stderr.write(f"[captcha-relay] session census raised: {e}\n")
            live = None
    if live is None:
        unverified.append("browsers")
    else:
        orphan_browsers = [(sid_, u) for (sid_, u) in live
                           if u not in active_urls]

    # Act outside the lock.
    for site_id, url, sid in to_end:
        _call_ender(site_id, url, "dismissed")
        # A-5c: a sweep reap is a TIMEOUT lifecycle event, distinct from an
        # operator dismiss -- audited per session (summary granularity).
        try:
            audit.audit_log("takeover", "timeout", sid or url,
                            after={"url": url, "site_id": site_id})
        except Exception:
            pass
    for s in close_sids:
        takeover.close_channel(s)
    for s in orphan_channels:
        takeover.close_channel(s)
    for site_id, url in orphan_browsers:
        _call_ender(site_id, url, "dismissed")

    reaped = len(to_end) + len(orphan_channels) + len(orphan_browsers)
    report = {
        "reaped": reaped,
        "expired_or_idle": len(to_end),
        "orphan_channels": len(orphan_channels),
        "orphan_browsers": len(orphan_browsers),
        "unverified": unverified,
    }
    if reaped or unverified:
        try:
            audit.audit_log("takeover", "sweep", "-", after=report)
        except Exception:
            pass
    return report


# ── A-5b: the reaper actually runs ──────────────────────────────────
#
# sweep_expired previously had ZERO production callers -- a reaper nobody
# runs protects nothing. start_sweeper launches a daemon loop; app.py calls
# it in the captcha wiring block. BD_DISABLE_KEEPALIVE-guarded so tests and
# tools that import the app never spawn it. Idempotent.

_sweeper_started = threading.Event()


def start_sweeper(interval_s: float = 30.0) -> bool:
    """Start the periodic no-orphan sweep. Returns True if this call started
    it, False when disabled (BD_DISABLE_KEEPALIVE) or already running."""
    if os.environ.get("BD_DISABLE_KEEPALIVE", "").strip() == "1":
        return False
    if _sweeper_started.is_set():
        return False
    _sweeper_started.set()

    def _loop():
        while True:
            time.sleep(max(1.0, float(interval_s)))
            try:
                sweep_report()
            except Exception as e:  # never let the reaper die silently
                sys.stderr.write(f"[captcha-relay] sweep raised: {e}\n")

    threading.Thread(target=_loop, daemon=True, name="captcha-sweeper").start()
    return True


# ─── Test/introspection helpers ─────────────────────────────────────

def _reset_for_tests() -> None:
    global _takeover_starter, _takeover_ender, _session_census
    with _lock:
        _pending.clear()
    _last_push_ts.clear()
    _takeover_starter = None
    _takeover_ender = None
    _session_census = None
    try:
        takeover.reset_takeover_total()  # A-5c: zero the cumulative counter
    except Exception:
        pass


# ─── MOD-1 remote takeover: binding-aware wrappers ──────────────────
#
# The solving-state binding lives HERE (this module owns _pending); takeover.py
# is binding-agnostic transport. The blueprint calls only these.

def _status_for_sid(sid: str) -> Optional[str]:
    """Return the status of the pending item whose solve_session_id == sid, or
    None if no such session exists."""
    if not sid:
        return None
    with _lock:
        for p in _pending.values():
            if p.solve_session_id == sid:
                return p.status
    return None


def takeover_screencast(sid: str):
    """Return an SSE frame generator for a sid that is actively `solving`, or
    None if the sid is unknown or not in `solving` (the route then 404s). Binds
    the viewer to a live solve session -- an arbitrary sid cannot open a stream."""
    if _status_for_sid(sid) != "solving":
        return None
    takeover.open_channel(sid)

    def _stream():
        # A-5b: the stream honors the CONFIG idle bound (not the transport
        # default), and viewer disconnect (GeneratorExit) or idle exit tears
        # the channel down -- freeing the A-5a concurrency slot. A reconnect
        # re-opens the channel (this wrapper runs again).
        try:
            for chunk in takeover.sse_frames(
                    sid, idle_max_s=float(_takeover_idle_timeout_s())):
                yield chunk
        finally:
            takeover.close_channel(sid)

    return _stream()


def submit_takeover_input(sid: str, event: dict) -> str:
    """Bind + validate + rate-limit + enqueue one CDP input event. Returns:
    'ok', 'unknown' (no such sid -> 404), 'gone' (resolved/dismissed -> 410),
    or 'rejected' (validation or rate failure -> 400/429). Injected text is
    redacted in transit; audit is at session-summary granularity, not per event."""
    status = _status_for_sid(sid)
    if status is None:
        return "unknown"
    if status != "solving":
        return "gone"
    result = takeover.enqueue_input(sid, event)  # 'ok'|'invalid'|'rate'|'closed'
    if result == "ok":
        # A-5b: an accepted input resets the session's idle clock.
        with _lock:
            for p in _pending.values():
                if p.solve_session_id == sid:
                    p.last_input_at = time.time()
                    break
    return result


def push_takeover_frame(sid: str, frame_b64: str) -> bool:
    """Driver-side (A-4): push a base64 JPEG screencast frame into the sid's
    channel. Returns False if no channel is open for the sid."""
    return takeover.push_frame(sid, frame_b64)


def drain_takeover_inputs(sid: str, max_n: int = 16) -> list:
    """Driver-side (A-4): pop up to max_n queued operator input events for the
    solve browser's owning thread to dispatch to the CDP session. The A-2 route
    already validated + rate-limited them; this just hands them to the driver."""
    return takeover.drain_inputs(sid, max_n)


__all__ = [
    "CHALLENGE_TYPES",
    "takeover_screencast", "submit_takeover_input", "push_takeover_frame",
    "drain_takeover_inputs",
    "_push_dedupe_window_s", "_pending_timeout_s", "_takeover_idle_timeout_s",
    "detect_captcha_in_page", "detect_captcha_in_html",
    "check_and_handle",
    "mark_captcha_needed",
    "start_solve", "mark_resolved", "mark_dismissed",
    "is_pending",
    "list_pending", "get_pending",
    "register_takeover_starter", "register_takeover_ender",
    "register_session_census",
    "sweep_expired", "sweep_report", "start_sweeper",
]
