"""v3.43.71: Telegram bot remote-control.

# What this module is

A Telegram bot that lets the user control BD from their phone via
chat commands. Pairs with v3.43.70's apprise notifications — apprise
sends notifications OUT to Telegram (and 80 other services); this
module receives commands IN from Telegram and dispatches them to
runner actions.

# Architecture

Long-polling daemon, not a webhook. Telegram bots can be operated in
two modes: webhook (Telegram POSTs to your public HTTPS endpoint) or
long-poll (your server polls `getUpdates` with a 30-second wait).
Matt's BD runs internally on Windows Server 2025 with no public
HTTPS — long-poll is the only realistic option, and it has zero
config burden (no cert, no firewall rule, no DDNS).

The poll loop runs in a daemon thread; it issues a GET to
`https://api.telegram.org/bot<TOKEN>/getUpdates?offset=N&timeout=30`
which blocks server-side until either an update arrives or 30s pass.
On update, command handlers run on a per-command worker thread so
slow commands don't block polling for new updates.

# Auth model

Two layers:

  1. Bot token gates network access (only the owner of the token
     can talk to the bot's Telegram entity)
  2. Allowlist of chat IDs gates command authorization (only listed
     chat IDs can issue commands; everyone else gets a polite
     "I don't recognize this chat" response and the attempt is
     logged)

Without the allowlist, anyone who guesses the bot's username could
issue `/mirror malicious://url` and inject jobs. The allowlist is
mandatory in practice; the bot refuses to start the poll loop unless
at least one chat ID is configured.

# Commands

  /start        — intro message + auth check
  /help         — list available commands
  /status       — site overview (count + state per site)
  /queue [sid]  — queue summary or per-site queue (limited to N entries)
  /mirror <url> — add URL to queue (auto-route by host to the matching site)
  /cancel <url> — cancel a pending URL
  /retry <sid|all> — reset failed→pending in a site (or globally)
  /pause <sid|all>  — pause processing
  /resume <sid|all> — resume processing
  /recent       — last 20 events from the in-memory ring buffer

# Output formatting

Telegram supports plain text + HTML + MarkdownV2. HTML is simplest
and has less aggressive escape requirements. All messages render as
HTML. The 4096-char cap is enforced by `_truncate_for_telegram()`
which adds "...and N more" when exceeded.

# Throttling

Per chat ID, commands are rate-limited to 1 per second. Repeated
fast commands return "rate limited, try again in N seconds". The
poll loop itself doesn't rate-limit; this is per-issuer.

# Hot reload

When the user changes the bot token or allowlist via the UI, we
call `configure()` on the running bot. Old poll loop exits, new one
starts with the fresh credentials. Existing in-flight commands keep
running on their worker threads.

# Fail-open

Every Telegram API call is wrapped in try/except. If the bot is
disabled or unreachable, BD keeps running normally. Local commands
(downloads, queue management) NEVER depend on the bot being up.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Callable

log = logging.getLogger(__name__)


# Telegram bot API base
_TG_API_BASE = "https://api.telegram.org"

# Per-chat command throttle (seconds between commands from one chat)
_THROTTLE_SECONDS = 1.0

# Maximum size of one Telegram message (Telegram cap is 4096)
_MAX_MSG_LEN = 4000  # leave headroom for `...and N more` footer

# Long-poll timeout (Telegram caps at 50; we use 30 for safety margin)
_LONG_POLL_TIMEOUT_S = 30

# HTTP timeout for direct API calls (non-poll). Slightly longer than
# the long-poll timeout so a long-poll call doesn't error.
_DIRECT_API_TIMEOUT_S = 35

# Recent-events ring buffer size (used by /recent)
_RECENT_EVENTS_CAP = 20


# ─── Result types ───────────────────────────────────────────────────


@dataclass
class TgUpdate:
    """One parsed Telegram update."""
    update_id: int
    chat_id: Optional[int]
    chat_type: str = ""       # "private" / "group" / "supergroup"
    user_id: Optional[int] = None
    username: str = ""
    text: str = ""
    timestamp: float = 0.0


@dataclass
class BotStatus:
    """Snapshot of bot health for /api/tg/status and bdctl tg-status."""
    enabled: bool = False
    running: bool = False
    last_poll_ts: float = 0.0     # monotonic
    last_poll_wall: float = 0.0   # unix time
    last_error: str = ""
    updates_received: int = 0
    commands_executed: int = 0
    allowlist_size: int = 0
    bot_username: str = ""        # filled after first getMe() call


# ─── HTTP helper (no library dependency) ────────────────────────────


def _is_httpx_available() -> bool:
    try:
        import httpx  # noqa: F401
        return True
    except ImportError:
        return False


def _http_get_json(url: str, timeout_s: float = _DIRECT_API_TIMEOUT_S) -> Optional[dict]:
    """GET a Telegram API URL, parse the response as JSON. Returns the
    `result` key on success, None on any failure. Never raises."""
    if not _is_httpx_available():
        log.info("tg_bot: httpx not installed; bot disabled")
        return None
    try:
        import httpx
        r = httpx.get(url, timeout=timeout_s)
        if r.status_code != 200:
            log.debug("tg_bot: GET %s -> %d", url[:60], r.status_code)
            return None
        data = r.json()
        if not isinstance(data, dict) or not data.get("ok"):
            return None
        return data.get("result")
    except Exception as e:
        log.debug("tg_bot: GET raised %s", e)
        return None


def _http_post_json(url: str, payload: dict,
                    timeout_s: float = 10.0) -> Optional[dict]:
    """POST JSON to a Telegram API URL. Returns parsed `result` on
    success, None on failure. Never raises."""
    if not _is_httpx_available():
        return None
    try:
        import httpx
        r = httpx.post(url, json=payload, timeout=timeout_s)
        if r.status_code != 200:
            log.debug("tg_bot: POST %s -> %d", url[:60], r.status_code)
            return None
        data = r.json()
        if not isinstance(data, dict) or not data.get("ok"):
            return None
        return data.get("result")
    except Exception as e:
        log.debug("tg_bot: POST raised %s", e)
        return None


# ─── Helpers ────────────────────────────────────────────────────────


def _html_escape(text: str) -> str:
    """Escape HTML special chars for Telegram's HTML parse mode.
    Telegram permits <b>, <i>, <code>, <pre>, <a> — we only emit
    these and escape everything else."""
    if not isinstance(text, str):
        return ""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def _truncate_for_telegram(text: str) -> str:
    """Truncate a message body to Telegram's 4096-char limit, adding a
    footer noting the truncation."""
    if not text or len(text) <= _MAX_MSG_LEN:
        return text or ""
    return text[:_MAX_MSG_LEN] + "\n…<i>(truncated)</i>"


def parse_command(text: str) -> tuple:
    """Parse a Telegram message body into (command, args).

    Telegram commands start with `/`. They can be followed by `@botname`
    when the bot is in a group (e.g. `/status@MyBulkBot`). We strip the
    bot suffix.

    Returns `("", "")` if the text doesn't start with `/`.
    """
    if not text or not isinstance(text, str):
        return ("", "")
    text = text.strip()
    if not text.startswith("/"):
        return ("", "")
    head, _, args = text[1:].partition(" ")
    head = head.strip()
    # Strip bot suffix (@BotName) if present
    if "@" in head:
        head = head.split("@", 1)[0]
    return (head.lower(), args.strip())


# ─── Settings persistence ───────────────────────────────────────────


def parse_allowlist(text: str) -> list:
    """Parse a multi-line allowlist string into a list of int chat IDs.

    Skips blanks and comments. Bad lines are silently dropped (logged
    at debug level). Returns int chat IDs only.
    """
    out: list = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Accept both bare ints and lines with names: "12345  Matt"
        first = line.split()[0]
        try:
            cid = int(first)
            out.append(cid)
        except ValueError:
            log.debug("tg_bot: skipping non-numeric allowlist entry: %r", line)
    return out


# ─── Command handler registry ───────────────────────────────────────
#
# Command handlers are callables taking (update, args, callbacks) and
# returning an HTML reply string. Handlers run on a worker thread per
# command so the poll loop stays responsive.

# Each callable returns a STRING (the HTML reply to send back).
CommandHandler = Callable[["TgUpdate", str, dict], str]


def _make_handlers() -> dict:
    """Build the handler dict. Returns a dict mapping command name
    (without leading `/`) to a handler function."""
    return {
        "start":   _cmd_start,
        "help":    _cmd_help,
        "status":  _cmd_status,
        "queue":   _cmd_queue,
        "mirror":  _cmd_mirror,
        "cancel":  _cmd_cancel,
        "retry":   _cmd_retry,
        "pause":   _cmd_pause,
        "resume":  _cmd_resume,
        "recent":  _cmd_recent,
    }


def _cmd_start(update: TgUpdate, args: str, cb: dict) -> str:
    """Welcome message + auth confirmation."""
    return (
        "<b>BulkDownloader bot</b>\n\n"
        f"Chat ID: <code>{update.chat_id}</code>\n"
        "You are <b>authorized</b>.\n\n"
        "Type /help for commands."
    )


def _cmd_help(update: TgUpdate, args: str, cb: dict) -> str:
    return (
        "<b>Available commands</b>\n\n"
        "<code>/status</code> — site overview\n"
        "<code>/queue [sid]</code> — queue summary or per-site queue\n"
        "<code>/mirror &lt;url&gt;</code> — add URL (auto-routed by host)\n"
        "<code>/cancel &lt;url&gt;</code> — cancel a pending URL\n"
        "<code>/retry &lt;sid|all&gt;</code> — reset failed → pending\n"
        "<code>/pause &lt;sid|all&gt;</code> — pause processing\n"
        "<code>/resume &lt;sid|all&gt;</code> — resume processing\n"
        "<code>/recent</code> — last 20 events"
    )


def _cmd_status(update: TgUpdate, args: str, cb: dict) -> str:
    """Site overview — one line per site."""
    get_status = cb.get("get_status")
    if not callable(get_status):
        return "<i>status callback unavailable</i>"
    try:
        sites = get_status() or {}
    except Exception as e:
        return f"<i>status failed: {_html_escape(str(e))}</i>"
    if not sites:
        return "<i>No sites configured.</i>"
    lines = ["<b>Sites</b>"]
    for sid, info in sorted(sites.items()):
        if not isinstance(info, dict):
            continue
        name = info.get("name") or sid
        state = info.get("state") or "?"
        counts = info.get("counts") or {}
        pending = counts.get("pending", 0)
        running = counts.get("running", 0)
        done = counts.get("done", 0)
        failed = counts.get("failed", 0)
        lines.append(
            f"<code>{_html_escape(sid)}</code> "
            f"<b>{_html_escape(name)}</b> · {_html_escape(state)} · "
            f"p={pending} r={running} d={done} f={failed}"
        )
    return "\n".join(lines)


def _cmd_queue(update: TgUpdate, args: str, cb: dict) -> str:
    """Queue summary for one site or all sites."""
    get_queue = cb.get("get_queue")
    get_status = cb.get("get_status")
    if not callable(get_queue) or not callable(get_status):
        return "<i>queue callback unavailable</i>"
    sid = args.strip() if args else ""
    if not sid:
        # Show queue summary across all sites
        try:
            sites = get_status() or {}
        except Exception as e:
            return f"<i>status failed: {_html_escape(str(e))}</i>"
        lines = ["<b>Queue summary</b>"]
        for s_id, info in sorted(sites.items()):
            counts = (info or {}).get("counts", {})
            p = counts.get("pending", 0)
            r = counts.get("running", 0)
            if p == 0 and r == 0:
                continue
            lines.append(
                f"<code>{_html_escape(s_id)}</code>: "
                f"{p} pending, {r} running"
            )
        if len(lines) == 1:
            lines.append("<i>nothing queued.</i>")
        return "\n".join(lines)
    # Per-site queue
    try:
        jobs = get_queue(sid) or []
    except Exception as e:
        return f"<i>queue {_html_escape(sid)} failed: {_html_escape(str(e))}</i>"
    if not jobs:
        return f"<code>{_html_escape(sid)}</code>: <i>queue empty.</i>"
    lines = [f"<b>Queue for {_html_escape(sid)}</b> ({len(jobs)} jobs)"]
    for j in jobs[:20]:
        if not isinstance(j, dict):
            continue
        url = j.get("url", "")[:80]
        st = j.get("status", "?")
        lines.append(f"• <code>{_html_escape(st)}</code> {_html_escape(url)}")
    if len(jobs) > 20:
        lines.append(f"…and {len(jobs) - 20} more")
    return "\n".join(lines)


def _cmd_mirror(update: TgUpdate, args: str, cb: dict) -> str:
    """Add a URL to the queue, auto-routed by host."""
    add_url = cb.get("add_url")
    if not callable(add_url):
        return "<i>mirror callback unavailable</i>"
    url = args.strip()
    if not url:
        return "Usage: <code>/mirror &lt;url&gt;</code>"
    if not (url.startswith("http://") or url.startswith("https://")):
        return f"<i>not a valid URL:</i> <code>{_html_escape(url)}</code>"
    try:
        result = add_url(url) or {}
    except Exception as e:
        return f"<i>mirror failed: {_html_escape(str(e))}</i>"
    if not isinstance(result, dict):
        return "<i>mirror returned malformed result</i>"
    if result.get("ok"):
        sid = result.get("site_id", "?")
        return (
            f"✓ added to <code>{_html_escape(sid)}</code>\n"
            f"<code>{_html_escape(url[:80])}</code>"
        )
    err = result.get("error") or "unknown reason"
    return f"<i>mirror rejected: {_html_escape(str(err))}</i>"


def _cmd_cancel(update: TgUpdate, args: str, cb: dict) -> str:
    """Cancel a pending URL."""
    cancel_url = cb.get("cancel_url")
    if not callable(cancel_url):
        return "<i>cancel callback unavailable</i>"
    url = args.strip()
    if not url:
        return "Usage: <code>/cancel &lt;url&gt;</code>"
    try:
        result = cancel_url(url) or {}
    except Exception as e:
        return f"<i>cancel failed: {_html_escape(str(e))}</i>"
    if result.get("ok"):
        return f"✓ cancelled <code>{_html_escape(url[:80])}</code>"
    return f"<i>cancel rejected: {_html_escape(str(result.get('error', '')))}</i>"


def _cmd_retry(update: TgUpdate, args: str, cb: dict) -> str:
    """Reset failed jobs → pending for a site (or 'all')."""
    retry_site = cb.get("retry_site")
    if not callable(retry_site):
        return "<i>retry callback unavailable</i>"
    sid = args.strip()
    if not sid:
        return "Usage: <code>/retry &lt;site_id|all&gt;</code>"
    try:
        result = retry_site(sid) or {}
    except Exception as e:
        return f"<i>retry failed: {_html_escape(str(e))}</i>"
    if result.get("ok"):
        n = result.get("count", 0)
        return f"✓ retried <b>{n}</b> jobs in <code>{_html_escape(sid)}</code>"
    return f"<i>retry rejected: {_html_escape(str(result.get('error', '')))}</i>"


def _cmd_pause(update: TgUpdate, args: str, cb: dict) -> str:
    return _action_dispatch(args, cb, "pause_site",
                            "✓ paused", "pause")


def _cmd_resume(update: TgUpdate, args: str, cb: dict) -> str:
    return _action_dispatch(args, cb, "resume_site",
                            "✓ resumed", "resume")


def _action_dispatch(args: str, cb: dict, cb_name: str,
                     ok_prefix: str, action_label: str) -> str:
    fn = cb.get(cb_name)
    if not callable(fn):
        return f"<i>{action_label} callback unavailable</i>"
    sid = args.strip()
    if not sid:
        return f"Usage: <code>/{action_label} &lt;site_id|all&gt;</code>"
    try:
        result = fn(sid) or {}
    except Exception as e:
        return f"<i>{action_label} failed: {_html_escape(str(e))}</i>"
    if result.get("ok"):
        sids = result.get("site_ids") or [sid]
        return f"{ok_prefix} <code>{_html_escape(', '.join(sids))}</code>"
    return f"<i>{action_label} rejected: {_html_escape(str(result.get('error', '')))}</i>"


def _cmd_recent(update: TgUpdate, args: str, cb: dict) -> str:
    """Last N events from the in-memory ring buffer."""
    get_recent = cb.get("get_recent_events")
    if not callable(get_recent):
        return "<i>recent callback unavailable</i>"
    try:
        events = get_recent() or []
    except Exception as e:
        return f"<i>recent failed: {_html_escape(str(e))}</i>"
    if not events:
        return "<i>no recent events.</i>"
    lines = [f"<b>Recent events</b> ({len(events)})"]
    for ev in events[-20:]:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("kind", "?")
        msg = ev.get("message", "")[:80]
        sid = ev.get("site_id", "")
        lines.append(
            f"• <code>{_html_escape(kind)}</code>"
            + (f" [{_html_escape(sid)}]" if sid else "")
            + f" {_html_escape(msg)}"
        )
    return "\n".join(lines)


# ─── TelegramBot ────────────────────────────────────────────────────


class TelegramBot:
    """Long-polling Telegram bot daemon. Thread-safe.

    Lifecycle:
      bot = TelegramBot()
      bot.set_callbacks(get_status=..., add_url=..., ...)
      bot.configure(token=..., allowlist=[...], enabled=True)
      # Daemon thread auto-starts when enabled+token+allowlist
      ...
      bot.shutdown()  # on app exit

    Configure can be called at runtime to reload token/allowlist.
    """

    def __init__(self):
        self._token: str = ""
        self._allowlist: set = set()
        self._enabled: bool = False
        self._callbacks: dict = {}
        self._handlers: dict = _make_handlers()
        # State
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._last_update_id: int = 0
        self._status = BotStatus()
        # Per-chat throttle: chat_id -> last_command_ts (monotonic)
        self._throttle: dict = {}
        # Recent events for /recent (also injected from log_event hook)
        self._recent_events: deque = deque(maxlen=_RECENT_EVENTS_CAP)
        # Wire the recent-events accessor into the callbacks by default
        # (caller can override if they want a different source).
        self._callbacks["get_recent_events"] = lambda: list(self._recent_events)

    def set_callbacks(self, **kwargs) -> None:
        """Register action callbacks. Unrecognized names are silently
        ignored. Pass any subset of: get_status, get_queue, add_url,
        cancel_url, retry_site, pause_site, resume_site,
        get_recent_events."""
        valid = {
            "get_status", "get_queue", "add_url", "cancel_url",
            "retry_site", "pause_site", "resume_site",
            "get_recent_events",
        }
        with self._lock:
            for k, v in kwargs.items():
                if k in valid and callable(v):
                    self._callbacks[k] = v

    def configure(
        self, *,
        token: Optional[str] = None,
        allowlist: Optional[list] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        """Update config. Restarts the poll loop if token/allowlist
        changes (so it picks up the new credentials)."""
        with self._lock:
            restart = False
            if token is not None and token != self._token:
                self._token = (token or "").strip()
                restart = True
            if allowlist is not None:
                new_al = set(int(x) for x in allowlist if x is not None)
                if new_al != self._allowlist:
                    self._allowlist = new_al
                    restart = True
                self._status.allowlist_size = len(self._allowlist)
            if enabled is not None:
                if bool(enabled) != self._enabled:
                    self._enabled = bool(enabled)
                    restart = True
                self._status.enabled = self._enabled
            if restart:
                self._stop_polling()
                self._maybe_start_polling()

    def record_event(self, kind: str, message: str, site_id: str = "") -> None:
        """Append to the recent-events ring. Called by runner hooks
        (alongside the apprise dispatcher) so /recent has data."""
        try:
            with self._lock:
                self._recent_events.append({
                    "kind": kind,
                    "message": str(message)[:240],
                    "site_id": site_id,
                    "ts": time.time(),
                })
        except Exception:
            pass

    def get_status(self) -> BotStatus:
        with self._lock:
            return BotStatus(
                enabled=self._status.enabled,
                running=bool(self._poll_thread and self._poll_thread.is_alive()),
                last_poll_ts=self._status.last_poll_ts,
                last_poll_wall=self._status.last_poll_wall,
                last_error=self._status.last_error,
                updates_received=self._status.updates_received,
                commands_executed=self._status.commands_executed,
                allowlist_size=self._status.allowlist_size,
                bot_username=self._status.bot_username,
            )

    def shutdown(self) -> None:
        """Stop the poll thread."""
        self._stop_polling()

    def _maybe_start_polling(self) -> None:
        """Start the poll thread if conditions are met (enabled +
        token + non-empty allowlist + httpx available)."""
        if not self._enabled:
            return
        if not self._token:
            return
        if not self._allowlist:
            log.info("tg_bot: not starting — empty allowlist")
            return
        if not _is_httpx_available():
            log.info("tg_bot: not starting — httpx not installed")
            return
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return
        self._stop_event.clear()
        t = threading.Thread(target=self._poll_loop, daemon=True,
                             name="tg-bot-poll")
        t.start()
        self._poll_thread = t

    def _stop_polling(self) -> None:
        """Signal the poll thread to exit. Doesn't wait for the
        long-poll request to return (it'll time out within 30s on its
        own)."""
        self._stop_event.set()

    def _poll_loop(self) -> None:
        """Main long-poll loop. Runs until _stop_event is set."""
        # Verify the token works first with getMe()
        try:
            me = _http_get_json(
                f"{_TG_API_BASE}/bot{self._token}/getMe",
                timeout_s=10.0,
            )
            if isinstance(me, dict):
                self._status.bot_username = me.get("username", "")
                log.info("tg_bot: connected as @%s",
                         self._status.bot_username)
        except Exception as e:
            self._status.last_error = f"getMe: {e}"
            log.info("tg_bot: getMe failed: %s", e)

        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as e:
                self._status.last_error = f"poll: {e}"
                log.debug("tg_bot: poll iter raised %s", e)
                # Back off briefly so a persistent error doesn't spin
                time.sleep(2.0)

    def _poll_once(self) -> None:
        """One getUpdates iteration."""
        with self._lock:
            token = self._token
            offset = self._last_update_id + 1
            allowlist = set(self._allowlist)
        if not token:
            return
        url = (
            f"{_TG_API_BASE}/bot{token}/getUpdates"
            f"?offset={offset}&timeout={_LONG_POLL_TIMEOUT_S}"
        )
        updates = _http_get_json(url, timeout_s=_DIRECT_API_TIMEOUT_S)
        with self._lock:
            self._status.last_poll_ts = time.monotonic()
            self._status.last_poll_wall = time.time()
        if not updates:
            return
        if not isinstance(updates, list):
            return
        for raw in updates:
            try:
                parsed = self._parse_update(raw)
                if parsed is None:
                    continue
                with self._lock:
                    self._last_update_id = max(
                        self._last_update_id, parsed.update_id)
                    self._status.updates_received += 1
                # Auth check
                if parsed.chat_id is None or parsed.chat_id not in allowlist:
                    self._handle_unauthorized(parsed)
                    continue
                # Dispatch command on a worker thread so the poll loop
                # stays responsive
                t = threading.Thread(
                    target=self._handle_command,
                    args=(parsed,), daemon=True,
                    name=f"tg-cmd-{parsed.update_id}",
                )
                t.start()
            except Exception as e:
                log.debug("tg_bot: parse/dispatch raised %s", e)

    def _parse_update(self, raw: dict) -> Optional[TgUpdate]:
        if not isinstance(raw, dict):
            return None
        msg = raw.get("message") or raw.get("edited_message") or {}
        if not isinstance(msg, dict):
            return None
        chat = msg.get("chat") or {}
        user = msg.get("from") or {}
        text = msg.get("text", "")
        if not isinstance(text, str):
            return None
        return TgUpdate(
            update_id=int(raw.get("update_id", 0) or 0),
            chat_id=int(chat.get("id")) if chat.get("id") is not None else None,
            chat_type=str(chat.get("type", "") or ""),
            user_id=int(user.get("id")) if user.get("id") is not None else None,
            username=str(user.get("username", "") or ""),
            text=text,
            timestamp=float(msg.get("date", 0) or 0),
        )

    def _handle_unauthorized(self, update: TgUpdate) -> None:
        """An unrecognized chat ID tried to talk to the bot. Reply
        politely; log the attempt."""
        log.info(
            "tg_bot: rejecting unauthorized chat %s (user=%s @%s text=%r)",
            update.chat_id, update.user_id, update.username,
            update.text[:80],
        )
        # Reply with a generic message — don't confirm the bot's purpose
        # to unknown chats. Include the chat ID so the user can add
        # themselves to the allowlist if it's their own chat.
        reply = (
            "I don't recognize this chat.\n"
            f"Chat ID: <code>{update.chat_id}</code>\n"
            "Ask the bot owner to add this ID to the allowlist."
        )
        try:
            self._send_message(update.chat_id, reply)
        except Exception:
            pass

    def _handle_command(self, update: TgUpdate) -> None:
        """Dispatch a single command on the worker thread."""
        # Throttle check
        now = time.monotonic()
        with self._lock:
            last_ts = self._throttle.get(update.chat_id, 0.0)
            wait = _THROTTLE_SECONDS - (now - last_ts)
            if wait > 0:
                # Rate-limited; respond once
                self._throttle[update.chat_id] = now
                reply = (
                    f"<i>rate limited; wait {int(wait * 1000)}ms</i>"
                )
                try:
                    self._send_message(update.chat_id, reply)
                except Exception:
                    pass
                return
            self._throttle[update.chat_id] = now
        cmd, args = parse_command(update.text)
        if not cmd:
            return  # Not a command — silently ignore non-command messages
        handler = self._handlers.get(cmd)
        if handler is None:
            self._send_message(
                update.chat_id,
                f"<i>unknown command:</i> <code>/{_html_escape(cmd)}</code>\n"
                "Type /help.",
            )
            return
        try:
            reply = handler(update, args, self._callbacks)
        except Exception as e:
            reply = f"<i>command failed: {_html_escape(str(e))}</i>"
            log.debug("tg_bot: handler /%s raised %s", cmd, e)
        with self._lock:
            self._status.commands_executed += 1
        if reply:
            try:
                self._send_message(update.chat_id, reply)
            except Exception as e:
                log.debug("tg_bot: send reply failed: %s", e)

    def _send_message(self, chat_id: int, text: str) -> None:
        """Send an HTML-mode message to a chat."""
        with self._lock:
            token = self._token
        if not token or chat_id is None:
            return
        url = f"{_TG_API_BASE}/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": _truncate_for_telegram(text),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        _http_post_json(url, payload, timeout_s=10.0)


# ─── Module singleton ───────────────────────────────────────────────


_singleton: Optional[TelegramBot] = None
_singleton_lock = threading.Lock()


def get_bot() -> TelegramBot:
    """Get the module-level singleton bot instance."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = TelegramBot()
    return _singleton


__all__ = [
    "TelegramBot",
    "BotStatus",
    "TgUpdate",
    "get_bot",
    "parse_command",
    "parse_allowlist",
    "_html_escape",
    "_truncate_for_telegram",
]
