"""v3.43.5: UI event logging.

Captures fine-grained user-interaction events from the browser:
clicks, hovers, fetches, errors, keyboard events, etc. Three tiers:

    basic   - clicks, modal opens, tab switches, fetches, JS errors
    verbose - basic + hover enter/exit, focus/blur, scroll, form changes
    extreme - verbose + mousemove, keystrokes, DOM mutations, console output

Events arrive in batches at /api/ui_events. We write them to
``ui_events.log`` next to the working dir with a daily rotation
handler keeping 7 days of history. This file is SEPARATE from the
main bulk_downloader.log so the operational log stays grep-able when
EXTREME mode is generating thousands of lines/minute.

Password fields are redacted on the client side BEFORE shipping. The
server still applies a defensive redaction on any payload claiming to
be from a `password` input, in case the client gets bypassed.

Why a separate module: keeps the file handler installation logic +
the redaction rules in one place; keeps app.py's request handler
short and obviously correct.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import re
import time
from pathlib import Path

# Tier constants — match the radio button values in the Settings UI
TIER_BASIC = "basic"
TIER_VERBOSE = "verbose"
TIER_EXTREME = "extreme"
VALID_TIERS = (TIER_BASIC, TIER_VERBOSE, TIER_EXTREME)

# Per-tier whitelist of event categories. Events whose category isn't
# in the user's selected tier (or a lower one) are dropped server-side.
# Defense-in-depth: the client already filters but a malicious or buggy
# extension could try to ship extreme-mode events to a basic-mode user.
_TIER_CATEGORIES = {
    TIER_BASIC: {
        "click", "tab_switch", "modal_open", "modal_close", "fetch",
        "js_error", "promise_rejection", "visibility", "shortcut",
        "ws_open", "ws_close", "page_load", "page_unload",
    },
    TIER_VERBOSE: {
        "hover_enter", "hover_exit", "focus", "blur", "scroll",
        "field_change", "ws_message", "render",
    },
    TIER_EXTREME: {
        "mousemove", "keystroke", "dom_mutation", "storage_read",
        "storage_write", "element_bounds", "console",
    },
}

# Tier ordering for "include lower-tier events at higher tiers"
_TIER_RANK = {TIER_BASIC: 0, TIER_VERBOSE: 1, TIER_EXTREME: 2}


def _allowed_categories(tier: str) -> set[str]:
    """Return the set of event categories permitted at this tier.
    Higher tiers include all lower-tier categories."""
    if tier not in VALID_TIERS: return set()
    rank = _TIER_RANK[tier]
    out: set[str] = set()
    for t, cats in _TIER_CATEGORIES.items():
        if _TIER_RANK[t] <= rank:
            out |= cats
    return out


# ── File handler ────────────────────────────────────────────────────
# We use TimedRotatingFileHandler with daily rotation + 7-day retention.
# The handler is installed lazily on first event so we don't create the
# log file on startup if UI logging is disabled.

_LOG_PATH = Path("ui_events.log")
_logger: logging.Logger | None = None

# Marks the handler _get_logger() attached, so its idempotency guard can ask
# "is MINE already here?" rather than "is ANY handler here?". See the guard.
_OWN_HANDLER_ATTR = "_bd_ui_events_own"


def _get_logger() -> logging.Logger:
    """Return the singleton ui_events logger, initializing if needed.
    Idempotent — safe to call from every request."""
    global _logger
    if _logger is not None: return _logger
    lg = logging.getLogger("bulk_downloader.ui_events")
    lg.setLevel(logging.INFO)
    lg.propagate = False  # don't double-log into main bulk_downloader.log
    # Idempotency: if somehow this gets called twice concurrently, the
    # second caller would add a second handler. Guard against it.
    #
    # v3.66.907 — key on OUR OWN handler, not on `if lg.handlers:`. The subject
    # of this guard is the handler this function attaches; `lg.handlers` is a
    # denominator containing every handler ANYONE attached, so a foreign one
    # arriving first made this return before opening the log. Nothing then
    # writes ui_events.log, and propagate is False, so the events do not reach
    # root either -- they are dropped silently, which is the feature failing
    # quietly rather than loudly. pytest 9's logging plugin attaches such a
    # handler, which is how it surfaced; the bug is not specific to pytest.
    if any(getattr(h, _OWN_HANDLER_ATTR, False) for h in lg.handlers):
        _logger = lg
        return lg
    try:
        h = logging.handlers.TimedRotatingFileHandler(
            _LOG_PATH, when="midnight", backupCount=7,
            encoding="utf-8", utc=False)
        # One JSON object per line — easy to grep, easy to parse.
        h.setFormatter(logging.Formatter("%(message)s"))
        setattr(h, _OWN_HANDLER_ATTR, True)
        lg.addHandler(h)
    except Exception as e:
        # Fall back to a null handler so callers don't crash on
        # filesystem errors. Surface the failure on stderr once.
        import sys
        sys.stderr.write(f"  ui_events: log file init failed ({e}); "
                         f"events will be silently dropped\n")
        nh = logging.NullHandler()
        # Tagged for the same reason the file handler is: the fallback is still
        # OUR handler, and an untagged one would leave the guard blind to it.
        setattr(nh, _OWN_HANDLER_ATTR, True)
        lg.addHandler(nh)
    _logger = lg
    return lg


# ── Redaction ───────────────────────────────────────────────────────

def _redact(event: dict) -> dict:
    """Sanitize an event before logging. Defensive — the client SHOULD
    already redact password-field values, but we re-apply the rule on
    the server because we can't trust client-side hygiene alone.

    Rules:
      - field_change / keystroke events on inputs whose 'input_type' is
        'password' have their 'value' replaced with '[redacted N chars]'
      - 'storage_read'/'storage_write' values containing the string
        'password' or 'token' or 'secret' in the key are redacted
      - Any field longer than 10_000 chars is truncated (prevents
        accidentally shipping a multi-MB clipboard paste to the log)
    """
    data = event.get("data") or {}
    if not isinstance(data, dict): return event
    cat = event.get("category", "")

    # Password-input redaction
    if cat in ("field_change", "keystroke", "focus", "blur"):
        if (data.get("input_type") or "").lower() == "password":
            val = data.get("value")
            if isinstance(val, str):
                data["value"] = f"[redacted {len(val)} chars]"

    # Storage key/value redaction — only check key name (case-insensitive).
    # Values for these keys are dropped entirely, not redacted-length,
    # because the length itself could leak info (token length is a hint).
    # AUDIT v3.43.48: expanded needle list to cover api_key / bearer /
    # private_key variants the original list missed.
    if cat in ("storage_read", "storage_write"):
        key = (data.get("key") or "").lower()
        if any(needle in key for needle in (
                "password", "token", "secret", "auth", "session", "csrf",
                "api_key", "apikey", "api-key", "bearer",
                "private_key", "privatekey", "secret_key",
                "credential", "passphrase", "passwd")):
            if "value" in data:
                data["value"] = "[redacted]"

    # Length cap on any string fields. Stops a single event from being
    # a megabyte of clipboard content.
    for k, v in list(data.items()):
        if isinstance(v, str) and len(v) > 10_000:
            data[k] = v[:10_000] + f"...[truncated {len(v) - 10_000} chars]"

    # v3.43.20: strip lone UTF-16 surrogates from any string field. The
    # client now uses _safeTextSlice everywhere DOM text gets truncated
    # (which prevents these at the source), but a misbehaving extension
    # or pre-3.43.20 client could still send them. When `json.dumps(...,
    # ensure_ascii=False)` is fed a lone surrogate, the resulting str is
    # technically valid Python but invalid UTF-8 — and stdlib `logging`
    # crashes when writing to a UTF-8 stderr (Windows Python 3.12 default).
    # The traceback floods bulk_downloader.log AND the actual event is
    # lost. Strip silently here.
    for k, v in list(data.items()):
        if isinstance(v, str) and _LONE_SURROGATE_RE.search(v):
            data[k] = _LONE_SURROGATE_RE.sub("", v)

    event["data"] = data
    return event


# Matches every UTF-16 surrogate code unit (high or low). In valid text
# they only appear as paired (high, low) sequences encoding U+10000+;
# any standalone occurrence indicates a slice happened inside a pair.
_LONE_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


# ── Ingest ──────────────────────────────────────────────────────────

def ingest(events: list[dict], current_tier: str) -> tuple[int, int]:
    """Write a batch of UI events to the log. Returns (accepted, dropped)
    so the caller can report metrics if desired.

    Each event must be a dict with at least:
      ts        - client-side timestamp (ms since epoch)
      category  - one of the values in _TIER_CATEGORIES
      event     - short string describing the specific event
      data      - optional dict of additional fields

    Events outside the active tier's whitelist are dropped silently.
    This is the server's defense against a misbehaving client; the
    client already filters by tier before shipping.
    """
    if not events: return 0, 0
    if current_tier not in VALID_TIERS:
        return 0, len(events)
    allowed = _allowed_categories(current_tier)
    lg = _get_logger()
    server_ts = int(time.time() * 1000)
    accepted = 0
    dropped = 0
    for ev in events:
        if not isinstance(ev, dict):
            dropped += 1; continue
        cat = ev.get("category", "")
        if cat not in allowed:
            dropped += 1; continue
        ev = _redact(ev)
        # Server-side timestamp lets us reconcile against client clock
        # skew. Client ts is preserved; server ts is added.
        ev["server_ts"] = server_ts
        try:
            lg.info(json.dumps(ev, default=str, ensure_ascii=False))
            accepted += 1
        except Exception:
            dropped += 1
    return accepted, dropped


def get_log_path() -> Path:
    """Path to the current ui_events.log file. Used by the /download
    endpoint so the Settings UI can offer a "Download log" button."""
    return _LOG_PATH


def list_log_files() -> list[Path]:
    """List the current log file plus all daily rotations. Used by the
    Settings UI to optionally zip up the full retention window."""
    out: list[Path] = []
    if _LOG_PATH.exists():
        out.append(_LOG_PATH)
    # Rotated files are named ui_events.log.YYYY-MM-DD by the handler
    parent = _LOG_PATH.parent if _LOG_PATH.parent.as_posix() else Path(".")
    for p in sorted(parent.glob(f"{_LOG_PATH.name}.*")):
        out.append(p)
    return out
