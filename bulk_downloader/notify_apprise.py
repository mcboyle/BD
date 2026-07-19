"""v3.43.70: apprise notification dispatcher.

# What this module is

A fail-open wrapper around the `apprise` library that lets the user
send notifications to ANY of ~80 services (Telegram, Discord,
Pushover, ntfy, Gotify, Slack, Pushbullet, Pushsafer, Webex, Mattermost,
Matrix, signal, SMS gateways, email, ...) from one config field. The
user writes a list of apprise URLs (one per service per line) and BD
fires events to all of them.

The apprise URL syntax is:

    <service>://<creds>[/path]?<options>

Examples:

    tgram://<bot_token>/<chat_id>
    discord://<webhook_id>/<webhook_token>
    pover://<user_key>@<app_token>
    ntfy://ntfy.sh/my-topic
    gotify://gotify.example.com/<app_token>
    slack://<token_a>/<token_b>/<token_c>/<channel>
    pbul://<accesstoken>
    matrix://<user>:<pass>@<host>/<room_id>
    mailto://<user>:<pass>@<smtp_host>?to=alice@example.com

See https://github.com/caronc/apprise/wiki for the full catalog.

# Architecture

  - `is_available()` — True if `apprise` is importable. Fail-open
    elsewhere when False.
  - `validate_urls(urls)` — parse a list of URLs through apprise and
    return per-URL (ok, schema, error) tuples. Used by the UI "Test
    URLs" button and by `send()` to skip dead entries cleanly.
  - `send(urls, title, body, *, tag=None, body_format=None)` — fire
    a notification to all configured URLs. Returns a dict with
    `sent_count`, `failed_count`, `errors`. Synchronous, with a hard
    timeout per service so a hung webhook doesn't block the worker.
  - `NotificationDispatcher` — stateful wrapper used by runner. Keeps
    config + a rolling batch buffer so 50 download-done events in 30s
    coalesce into one notification.

# Throttling / batching

Two modes per event:

  - "per-event"   — fire a notification on every event of that type
  - "batched"     — buffer events; flush when (a) N events accumulated,
                    or (b) T seconds elapsed since first event in batch

Defaults (Matt's "show me more, not less" profile):

  - "done"        — batched, flush at 10 events OR 120 seconds
  - "failed"      — batched, flush at 5 events OR 60 seconds
  - "captcha"     — per-event (always important)
  - "auth_required" — per-event
  - "disk_full"   — per-event
  - "queue_empty" — per-event (rare)
  - "server_start" — per-event
  - "server_shutdown" — per-event

Users can override any of these in the per-site or global config.

# Fail-open

Every notification dispatch is wrapped in try/except. A failed
notification does not block the worker, doesn't propagate the error,
and doesn't crash the dispatcher. The runner calls
`dispatcher.notify(event_type, ...)` and just keeps going regardless
of return value.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ─── Apprise availability ───────────────────────────────────────────


def is_available() -> bool:
    """True if apprise is importable. Cached after first call."""
    try:
        import apprise  # noqa: F401
        return True
    except ImportError:
        return False


# ─── URL validation ─────────────────────────────────────────────────


@dataclass
class UrlValidation:
    url: str
    ok: bool
    schema: str = ""        # The service identifier ("tgram", "discord", etc.)
    error: str = ""         # Human-readable error if not ok


def _safe_url_display(url: str) -> str:
    """Mask credentials in an apprise URL for log display.

    `tgram://1234:secrettoken/chatid` -> `tgram://****/chatid`
    `discord://webhook_id/webhook_token` -> `discord://****/****`
    Keeps the schema visible so the user can see what service it is.

    POS-4 (v3.66.659): masks BOTH userinfo credentials (before the first '/') and
    PATH-embedded secrets. Many services carry the secret in the path, not userinfo
    (Discord/Slack/webhook tokens), which the userinfo-only mask left visible. A path
    segment >= 16 chars is treated as a token and masked; shorter segments (numeric
    chat IDs, channel names) stay readable so an operator can still tell destinations
    apart without leaking the secret into logs.
    """
    if not url or "://" not in url:
        return url
    scheme, _, rest = url.partition("://")
    if "/" not in rest:
        return f"{scheme}://****"
    _creds, _, path = rest.partition("/")
    masked = "/".join("****" if len(seg) >= 16 else seg
                      for seg in path.split("/"))
    return f"{scheme}://****/{masked[:60]}"


def validate_urls(urls: list) -> list:
    """Run each URL through apprise's URL parser. Returns a list of
    UrlValidation results in the same order.

    Comment lines (`#...`) and blank lines are skipped — they're not
    real URLs and shouldn't show up in the results.

    On apprise-not-installed: returns all entries marked ok=False with
    error='apprise_not_installed'.
    """
    # Filter comment/blank lines uniformly across both code paths so
    # the result count matches what the user would expect from looking
    # at the textarea (real URLs only).
    real_urls: list = []
    for u in (urls or []):
        u = (u or "").strip()
        if not u or u.startswith("#"):
            continue
        real_urls.append(u)
    if not is_available():
        return [
            UrlValidation(url=u, ok=False, error="apprise_not_installed")
            for u in real_urls
        ]
    try:
        import apprise
    except Exception as e:
        return [
            UrlValidation(url=u, ok=False, error=f"apprise_import:{e}")
            for u in real_urls
        ]
    out: list = []
    # apprise removed the public ``AppriseURLBase`` alias in >=1.7; ``URLBase`` is
    # the stable parser. Resolve whichever exists; if neither, fall back to
    # deriving the schema from the URL string and let ``Apprise.add()`` be the
    # validity arbiter. (VR-P05: the hard reference to AppriseURLBase raised an
    # AttributeError on apprise>=1.7, rejecting every URL.)
    _parser = (getattr(apprise, "URLBase", None)
               or getattr(apprise, "AppriseURLBase", None))
    for u in real_urls:
        try:
            parsed = None
            if _parser is not None:
                try:
                    parsed = _parser.parse_url(u)
                except Exception:
                    parsed = None
            schema = (parsed or {}).get("schema", "") or u.split("://", 1)[0]
            # Apprise.add() is the real validity check (it instantiates the
            # service plugin); the parse above is only a schema hint, so we do
            # NOT fail-closed when parse_url returns None.
            apobj = apprise.Apprise()
            ok = apobj.add(u)
            if not ok:
                out.append(UrlValidation(
                    url=u, ok=False, schema=schema,
                    error="add_failed_unknown_schema",
                ))
                continue
            out.append(UrlValidation(url=u, ok=True, schema=schema))
        except Exception as e:
            out.append(UrlValidation(
                url=u, ok=False,
                error=f"{type(e).__name__}:{str(e)[:120]}",
            ))
    return out


def list_known_schemes() -> list:
    """Return the list of schema names apprise recognizes, for the UI
    help text. Returns empty list if apprise unavailable."""
    if not is_available():
        return []
    try:
        import apprise
        # apprise exposes a list of registered service classes; their
        # `protocol` attribute is the schema string.
        schemas: set = set()
        for cls in apprise.plugins.SCHEMA_MAP.values():
            proto = getattr(cls, "protocol", None) or getattr(cls, "secure_protocol", None)
            if isinstance(proto, str):
                schemas.add(proto)
            elif isinstance(proto, (list, tuple)):
                for p in proto:
                    if isinstance(p, str):
                        schemas.add(p)
        return sorted(schemas)
    except Exception as e:
        log.debug("apprise: list_known_schemes failed: %s", e)
        return []


# ─── Synchronous send ───────────────────────────────────────────────


@dataclass
class SendResult:
    sent_count: int = 0
    failed_count: int = 0
    errors: list = field(default_factory=list)  # list of (url, error_str)
    skipped_count: int = 0  # URLs that didn't parse


def send(
    urls: list,
    title: str,
    body: str,
    *,
    tag: Optional[str] = None,
    body_format: str = "text",  # "text" / "html" / "markdown"
    timeout_s: float = 10.0,
) -> SendResult:
    """Fire a notification to every URL in `urls`. Returns a SendResult.

    `urls` is a list of apprise URL strings. Comments (lines starting
    with `#`) and empty lines are skipped silently.

    `tag` if provided is attached to the notification; some services
    surface this as a label or filter (e.g. Telegram message prefix).

    `body_format`: "text" (default), "html", or "markdown". Most
    services accept all three; some (Telegram, Discord) render
    markdown.

    `timeout_s` is the per-notification timeout. Apprise itself
    blocks until each service responds — we wrap the whole call in a
    thread with a join timeout to enforce.

    Never raises. On any error returns SendResult with errors
    populated.
    """
    result = SendResult()
    clean_urls: list = []
    for u in (urls or []):
        u = (u or "").strip()
        if not u or u.startswith("#"):
            continue
        clean_urls.append(u)
    if not clean_urls:
        return result
    if not is_available():
        result.failed_count = len(clean_urls)
        result.errors.append(("__module__", "apprise_not_installed"))
        return result
    try:
        import apprise
    except Exception as e:
        result.failed_count = len(clean_urls)
        result.errors.append(("__module__", f"import_failed:{e}"))
        return result

    fmt = {
        "text": apprise.NotifyFormat.TEXT,
        "html": apprise.NotifyFormat.HTML,
        "markdown": apprise.NotifyFormat.MARKDOWN,
    }.get(body_format.lower(), apprise.NotifyFormat.TEXT)

    apobj = apprise.Apprise()
    for u in clean_urls:
        try:
            # VR-P01: add the URL UNDER the same tag we notify with. apprise's
            # notify(tag=...) only fires services added with a matching tag, so a
            # tagless add() meant every tagged runtime event matched 0 services
            # and was silently dropped ("sent 0, failed 1"). tag=None adds
            # untagged and notify(tag=None) still fires it, so this is correct for
            # both the default and the per-event paths.
            ok = apobj.add(u, tag=tag)
            if not ok:
                result.skipped_count += 1
                result.errors.append((_safe_url_display(u), "add_failed"))
        except Exception as e:
            result.skipped_count += 1
            result.errors.append((
                _safe_url_display(u),
                f"add_raised:{type(e).__name__}",
            ))

    # apprise.notify is blocking — wrap in a thread for timeout enforcement
    success = [False]
    err = [None]

    def _do_notify():
        try:
            ok = apobj.notify(
                body=body, title=title or "BulkDownloader",
                tag=tag, body_format=fmt,
            )
            success[0] = bool(ok)
        except Exception as ex:
            err[0] = ex

    t = threading.Thread(target=_do_notify, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        result.failed_count = len(clean_urls) - result.skipped_count
        result.errors.append(("__send__", f"timeout_after_{timeout_s}s"))
        return result
    if err[0] is not None:
        result.failed_count = len(clean_urls) - result.skipped_count
        result.errors.append((
            "__send__", f"raised:{type(err[0]).__name__}:{err[0]}",
        ))
        return result
    if success[0]:
        result.sent_count = len(clean_urls) - result.skipped_count
    else:
        # apprise returned False — usually means all individual services
        # failed. We don't get per-URL detail from a single .notify(),
        # so this is best-effort.
        result.failed_count = len(clean_urls) - result.skipped_count
        result.errors.append(("__send__", "apprise_returned_false"))
    return result


# ─── Event types ────────────────────────────────────────────────────


# Canonical event names. Used as keys in batch/throttle config.
EVENT_DOWNLOAD_DONE = "download_done"
EVENT_DOWNLOAD_FAILED = "download_failed"
EVENT_CAPTCHA = "captcha"
EVENT_AUTH_REQUIRED = "auth_required"
EVENT_DISK_FULL = "disk_full"
EVENT_QUEUE_EMPTY = "queue_empty"
EVENT_QUEUE_PAUSED = "queue_paused"
EVENT_QUEUE_RESUMED = "queue_resumed"
EVENT_SERVER_START = "server_start"
EVENT_SERVER_SHUTDOWN = "server_shutdown"

ALL_EVENT_TYPES = [
    EVENT_DOWNLOAD_DONE,
    EVENT_DOWNLOAD_FAILED,
    EVENT_CAPTCHA,
    EVENT_AUTH_REQUIRED,
    EVENT_DISK_FULL,
    EVENT_QUEUE_EMPTY,
    EVENT_QUEUE_PAUSED,
    EVENT_QUEUE_RESUMED,
    EVENT_SERVER_START,
    EVENT_SERVER_SHUTDOWN,
]


# Per-event default batching policy. (mode, batch_size, max_wait_s)
# mode = "per_event" or "batched"
DEFAULT_POLICY: dict = {
    EVENT_DOWNLOAD_DONE:    ("batched",   10, 120.0),
    EVENT_DOWNLOAD_FAILED:  ("batched",    5,  60.0),
    EVENT_CAPTCHA:          ("per_event", 0,   0.0),
    EVENT_AUTH_REQUIRED:    ("per_event", 0,   0.0),
    EVENT_DISK_FULL:        ("per_event", 0,   0.0),
    EVENT_QUEUE_EMPTY:      ("per_event", 0,   0.0),
    EVENT_QUEUE_PAUSED:     ("per_event", 0,   0.0),
    EVENT_QUEUE_RESUMED:    ("per_event", 0,   0.0),
    EVENT_SERVER_START:     ("per_event", 0,   0.0),
    EVENT_SERVER_SHUTDOWN:  ("per_event", 0,   0.0),
}


# ─── Batching dispatcher ────────────────────────────────────────────


@dataclass
class _PendingBatch:
    event_type: str
    started_at: float
    items: list = field(default_factory=list)  # list of dict (title, body, ctx)


class NotificationDispatcher:
    """Singleton-ish dispatcher used by the runner.

    Construction is cheap. Call `notify(event_type, title, body, **ctx)`
    on every event. The dispatcher handles batching, throttling, and
    fail-open sending. Behavior:

      - per-event events go out immediately
      - batched events accumulate; the first event in a batch starts
        a flush timer; flush fires when EITHER the batch_size cap is
        hit OR the max_wait_s timer expires
      - on shutdown, call `flush_all()` to send any pending batches

    Thread-safe. The flush timer is a single daemon thread.
    """

    def __init__(self):
        self._urls: list = []
        self._enabled: bool = False
        self._policy: dict = dict(DEFAULT_POLICY)
        # event_type -> _PendingBatch
        self._pending: dict = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._timer_thread: Optional[threading.Thread] = None

    def configure(
        self,
        *,
        urls: Optional[list] = None,
        enabled: Optional[bool] = None,
        policy_overrides: Optional[dict] = None,
    ) -> None:
        """Update config. Safe to call at runtime — pending batches keep
        their original policy until they flush; new events get the new
        policy."""
        with self._lock:
            if urls is not None:
                self._urls = list(urls)
            if enabled is not None:
                self._enabled = bool(enabled)
            if policy_overrides:
                for k, v in policy_overrides.items():
                    if k in self._policy and isinstance(v, tuple) and len(v) == 3:
                        self._policy[k] = v
            self._maybe_start_timer()

    def _maybe_start_timer(self) -> None:
        """Start the flush timer thread if not running and enabled."""
        if (self._timer_thread is None or
                not self._timer_thread.is_alive()):
            if self._enabled and self._urls:
                self._stop_event.clear()
                t = threading.Thread(
                    target=self._timer_loop, daemon=True,
                    name="apprise-flush-timer",
                )
                t.start()
                self._timer_thread = t

    def _timer_loop(self) -> None:
        """Check every 5 seconds for batches whose max_wait_s has
        elapsed and flush them."""
        while not self._stop_event.is_set():
            try:
                self._check_and_flush_expired()
            except Exception as e:
                log.debug("apprise: timer loop raised: %s", e)
            self._stop_event.wait(timeout=5.0)

    def _check_and_flush_expired(self) -> None:
        """For each pending batch, flush if its max_wait_s has passed."""
        now = time.monotonic()
        to_flush: list = []
        with self._lock:
            for et, batch in list(self._pending.items()):
                _mode, _size, max_wait = self._policy.get(
                    et, ("per_event", 0, 0.0))
                if max_wait <= 0:
                    continue
                if (now - batch.started_at) >= max_wait:
                    to_flush.append(et)
        for et in to_flush:
            self._flush_batch(et)

    def notify(
        self,
        event_type: str,
        title: str,
        body: str,
        **ctx,
    ) -> None:
        """Submit an event for dispatch. Fail-open — never raises.

        For `per_event` policy: fires immediately in this thread.
        For `batched` policy: accumulates in the batch buffer and
        either fires inline if size cap hit, or relies on the timer
        for time-based flushing.
        """
        try:
            self._notify_inner(event_type, title, body, ctx)
        except Exception as e:
            log.debug("apprise: notify(%s) raised: %s", event_type, e)

    def _notify_inner(self, event_type, title, body, ctx):
        with self._lock:
            if not self._enabled or not self._urls:
                return
            mode, size, _max_wait = self._policy.get(
                event_type, ("per_event", 0, 0.0))
        if mode == "per_event":
            self._fire(title, body, tag=event_type)
            return
        # Batched
        flush_now = False
        with self._lock:
            batch = self._pending.get(event_type)
            if batch is None:
                batch = _PendingBatch(
                    event_type=event_type, started_at=time.monotonic(),
                )
                self._pending[event_type] = batch
                self._maybe_start_timer()
            batch.items.append({"title": title, "body": body, "ctx": ctx})
            if size > 0 and len(batch.items) >= size:
                flush_now = True
        if flush_now:
            self._flush_batch(event_type)

    def _flush_batch(self, event_type: str) -> None:
        """Render accumulated batch into one combined notification."""
        with self._lock:
            batch = self._pending.pop(event_type, None)
        if batch is None or not batch.items:
            return
        # Coalesced title: "BD: 10 download_done"
        n = len(batch.items)
        et_short = event_type.replace("_", " ")
        title = f"BulkDownloader: {n} {et_short}"
        # Body: one line per item, truncated
        lines: list = []
        for it in batch.items[:20]:  # cap at 20 to keep msg short
            t = (it.get("title") or "")[:80]
            b = (it.get("body") or "")[:120]
            if t and b:
                lines.append(f"- {t}: {b}")
            else:
                lines.append(f"- {t or b}")
        if n > 20:
            lines.append(f"  ... and {n - 20} more")
        body = "\n".join(lines)
        self._fire(title, body, tag=event_type)

    def _fire(self, title: str, body: str, *, tag: str = "") -> None:
        """Actually dispatch via apprise.send. Captured in its own
        method so it can be unit-tested via mock."""
        try:
            urls = list(self._urls)
        except Exception:
            return
        if not urls:
            return
        try:
            result = send(urls, title=title, body=body, tag=tag)
            if result.failed_count > 0:
                log.info(
                    "apprise: %s — sent %d, failed %d, errors %s",
                    tag, result.sent_count, result.failed_count,
                    result.errors[:3],
                )
        except Exception as e:
            log.debug("apprise: _fire raised: %s", e)

    def flush_all(self) -> None:
        """Flush every pending batch immediately. Call on shutdown."""
        with self._lock:
            keys = list(self._pending.keys())
        for et in keys:
            self._flush_batch(et)

    def shutdown(self) -> None:
        """Stop the timer thread + flush pending."""
        self._stop_event.set()
        try:
            self.flush_all()
        except Exception:
            pass
        if self._timer_thread is not None:
            try:
                self._timer_thread.join(timeout=2.0)
            except Exception:
                pass


# ─── Module-level singleton (for the Flask app) ─────────────────────


_dispatcher_singleton: Optional[NotificationDispatcher] = None
_dispatcher_lock = threading.Lock()


def get_dispatcher() -> NotificationDispatcher:
    """Get the module-level dispatcher singleton. Thread-safe."""
    global _dispatcher_singleton
    if _dispatcher_singleton is None:
        with _dispatcher_lock:
            if _dispatcher_singleton is None:
                _dispatcher_singleton = NotificationDispatcher()
    return _dispatcher_singleton


def parse_urls_text(text: str) -> list:
    """Parse a textarea-style multi-line string into a list of URLs.

    Skips comments (`#`) and blank lines. Strips per-line whitespace.
    """
    if not text:
        return []
    out: list = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def policy_from_config(cfg: dict) -> dict:
    """Build a policy override dict from a flat config dict.

    Config keys: `notify_<event>_mode`, `notify_<event>_batch`,
    `notify_<event>_wait_s`. Missing keys keep the DEFAULT_POLICY entry.
    """
    overrides: dict = {}
    for et in ALL_EVENT_TYPES:
        mode_key = f"notify_{et}_mode"
        size_key = f"notify_{et}_batch"
        wait_key = f"notify_{et}_wait_s"
        if any(k in cfg for k in (mode_key, size_key, wait_key)):
            default = DEFAULT_POLICY.get(et, ("per_event", 0, 0.0))
            mode = str(cfg.get(mode_key, default[0])).strip().lower()
            if mode not in ("per_event", "batched"):
                mode = default[0]
            try:
                size = int(cfg.get(size_key, default[1]) or 0)
            except (ValueError, TypeError):
                size = default[1]
            try:
                wait = float(cfg.get(wait_key, default[2]) or 0.0)
            except (ValueError, TypeError):
                wait = default[2]
            overrides[et] = (mode, max(0, size), max(0.0, wait))
    return overrides


__all__ = [
    "is_available",
    "validate_urls",
    "UrlValidation",
    "send",
    "SendResult",
    "list_known_schemes",
    # Event constants
    "EVENT_DOWNLOAD_DONE",
    "EVENT_DOWNLOAD_FAILED",
    "EVENT_CAPTCHA",
    "EVENT_AUTH_REQUIRED",
    "EVENT_DISK_FULL",
    "EVENT_QUEUE_EMPTY",
    "EVENT_QUEUE_PAUSED",
    "EVENT_QUEUE_RESUMED",
    "EVENT_SERVER_START",
    "EVENT_SERVER_SHUTDOWN",
    "ALL_EVENT_TYPES",
    "DEFAULT_POLICY",
    # Dispatcher
    "NotificationDispatcher",
    "get_dispatcher",
    "parse_urls_text",
    "policy_from_config",
]
