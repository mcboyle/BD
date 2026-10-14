"""Outgoing webhooks (Phase 121, Block Q).

Complementary to the plugin system (Phase 116). Plugins run in-process
with full BD state; webhooks POST out to external HTTP endpoints so
operators can integrate without writing Python.

Use cases:
  • Send a Discord embed when a download completes
  • POST to home automation (Home Assistant / Hass.io webhook)
  • Forward events to a queue (RabbitMQ via HTTP, NATS via REST)
  • Trigger a backup-mirror on each successful download

Schema:
  webhook_subscriptions(
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    events TEXT NOT NULL,        -- JSON array, e.g. ["download.done"]
    secret TEXT,                  -- HMAC signing key (optional)
    enabled INTEGER DEFAULT 1,
    created_at REAL,
    last_fired_ts REAL,
    fire_count INTEGER,
    error_count INTEGER,
    last_error TEXT
  )

  webhook_queue(
    id INTEGER PRIMARY KEY,
    subscription_id INTEGER,
    event_name TEXT,
    payload TEXT,                 -- JSON
    attempt INTEGER DEFAULT 0,
    next_retry_ts REAL,
    last_error TEXT
  )

Delivery:
  • Fire is best-effort: queue an HTTP POST, return immediately.
  • Background drain worker pops from webhook_queue, POSTs, retries
    with exponential backoff on failure (1s → 2s → 5s → 30s → drop).
    Each item is claimed (compare-and-set lease) before it is POSTed, so
    the drain thread and POST /api/webhooks/drain never deliver it twice.
    Subscriptions drain in parallel and a failing one stops for the pass,
    so a dead receiver cannot hold back a healthy one.
  • HMAC-SHA256 signature in `X-BD-Signature` header when subscription
    has a secret, over `<X-BD-Timestamp>.<X-BD-Delivery-Id>.<body>`, so a
    captured delivery cannot be replayed under a fresh timestamp.

Failure handling:
  • 5xx, 3xx, 408, 429 + network errors → retry, 5 attempts in all;
    a Retry-After header lengthens the wait (capped at an hour)
  • other 4xx → drop immediately (caller's config is wrong)
  • 3xx → don't follow redirects (could leak credentials)
  • subscription removed or disabled → its queued items are dropped
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import threading
import time
from typing import Optional


_drain_lock = threading.Lock()
_drain_thread: Optional[threading.Thread] = None
_drain_stop = threading.Event()

# Retry schedule (seconds). Index = the attempt that just failed → delay
# before the next one; a failure past the end drops the item.
_RETRY_DELAYS = [1, 2, 5, 30]
# A claimed item's next_retry_ts is pushed this far out while it is in
# flight; longer than the 10 s POST timeout, so only a crashed pass's
# claim ever expires.
_CLAIM_LEASE = 60
_RETRY_AFTER_MAX = 3600
_DRAIN_PARALLEL = 8

# Every name a webhooks.fire() call site emits: runner terminal statuses,
# alerts_engine, maintenance windows. A subscription to any other name
# could never fire, so add_subscription refuses it.
WEBHOOK_EVENTS = ("download.done", "download.failed", "download.needs_review",
                  "alert.fired", "maintenance.start", "maintenance.end")


def _ensure_tables():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS webhook_subscriptions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                events TEXT NOT NULL,
                secret TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                created_at REAL NOT NULL,
                last_fired_ts REAL DEFAULT 0,
                fire_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                last_error TEXT DEFAULT ''
            )""")
            cx.execute("""CREATE TABLE IF NOT EXISTS webhook_queue(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscription_id INTEGER NOT NULL,
                event_name TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL,
                attempt INTEGER DEFAULT 0,
                next_retry_ts REAL DEFAULT 0,
                last_error TEXT DEFAULT ''
            )""")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_wq_retry "
                       "ON webhook_queue(next_retry_ts)")
    except Exception as e:
        sys.stderr.write(f"[webhooks] schema init failed: {e}\n")


def add_subscription(*, url: str, events: list,
                    secret: str = "") -> Optional[int]:
    """Register a new webhook. `events` is a list of event names like
    ['download.done', 'download.failed']."""
    if not url or not isinstance(events, list) or not events:
        return None
    if any(not isinstance(e, str) or e not in WEBHOOK_EVENTS for e in events):
        return None
    # F-APP05-03: reject SSRF hosts at registration, reusing the operator-chosen
    # webhook policy from v3.66.553 (rejects cloud-metadata/link-local/CGNAT/
    # multicast/unspecified + non-http schemes; ALLOWS RFC1918 LAN + loopback +
    # public -- Plex/Jellyfin/Home Assistant receivers legitimately live on LAN).
    from .hooks import _validate_webhook_url  # lazy: no static import edge
    _ok, _why = _validate_webhook_url(url.strip())
    if not _ok:
        return None
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("""INSERT INTO webhook_subscriptions(
                url, events, secret, created_at
            ) VALUES (?,?,?,?)""",
                (url.strip(), json.dumps(events), secret or "", time.time()))
            return cur.lastrowid
    except Exception:
        return None


def remove_subscription(sub_id: int) -> bool:
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("DELETE FROM webhook_subscriptions WHERE id = ?",
                             (int(sub_id),))
            cx.execute("DELETE FROM webhook_queue WHERE subscription_id = ?",
                       (int(sub_id),))
            return cur.rowcount > 0
    except Exception:
        return False


def list_subscriptions() -> list:
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT * FROM webhook_subscriptions
                                  ORDER BY id DESC""").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["events"] = json.loads(d.get("events") or "[]")
            except Exception:
                d["events"] = []
            # Redact secret in list output — full value never leaks
            if d.get("secret"):
                d["secret"] = f"<{len(d['secret'])} chars>"
            out.append(d)
        return out
    except Exception:
        return []


def fire(event_name: str, payload: dict):
    """Queue webhook deliveries for any subscription matching
    `event_name`. Returns immediately — actual HTTP happens in the
    drain worker."""
    if not event_name:
        return
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            subs = cx.execute("""SELECT id, events FROM webhook_subscriptions
                                  WHERE enabled = 1""").fetchall()
        for s in subs:
            try:
                events_list = json.loads(s[1] if not hasattr(s, "keys") else s["events"])
            except Exception:
                continue
            if event_name not in events_list:
                continue
            sub_id = int(s[0] if not hasattr(s, "keys") else s["id"])
            with _db.db_conn() as cx:
                cx.execute("""INSERT INTO webhook_queue(
                    subscription_id, event_name, payload, created_at
                ) VALUES (?,?,?,?)""",
                    (sub_id, event_name,
                     json.dumps(payload or {}, default=str),
                     time.time()))
    except Exception as e:
        sys.stderr.write(f"[webhooks] fire queueing failed: {e}\n")


def _sign(secret: str, body: bytes) -> str:
    """Compute HMAC-SHA256 over `body` (the bytes _signed_bytes builds)."""
    return hmac.new(secret.encode("utf-8"), body,
                    hashlib.sha256).hexdigest()


def _signed_bytes(timestamp: str, delivery_id: str, body: bytes) -> bytes:
    """What X-BD-Signature covers: `<timestamp>.<delivery id>.<body>`. The
    receiver rebuilds it from the two headers and the raw body, then checks
    the timestamp is recent and the delivery id unseen."""
    return f"{timestamp}.{delivery_id}.".encode("utf-8") + body


def _deliver_one(item: dict) -> dict:
    """POST one queued item. Returns {ok, status, error}."""
    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "requests not installed"}
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            sub_row = cx.execute("""SELECT * FROM webhook_subscriptions
                                     WHERE id = ?""",
                                 (item["subscription_id"],)).fetchone()
        if not sub_row:
            return {"ok": False, "error": "subscription gone", "permanent": True}
        sub = dict(sub_row)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    if not sub.get("enabled"):
        return {"ok": False, "error": "subscription disabled", "permanent": True}
    url = sub["url"]
    # F-CBD12-01: re-validate the stored URL at delivery time -- defends against
    # DNS rebinding between registration and delivery, and against a hand-edited
    # or pre-guard subscription row. Same operator-chosen v3.66.553 policy.
    from .hooks import _validate_webhook_url  # lazy: no static import edge
    _ok, _why = _validate_webhook_url(url)
    if not _ok:
        return {"ok": False, "error": f"blocked url: {_why}", "permanent": True}
    secret = sub.get("secret", "")
    body_text = item["payload"] or "{}"
    body_bytes = body_text.encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "BulkDownloader-Webhook/1.0",
        "X-BD-Event": item["event_name"],
        "X-BD-Delivery-Id": str(item["id"]),
        "X-BD-Timestamp": str(int(time.time())),
    }
    if secret:
        headers["X-BD-Signature"] = "sha256=" + _sign(
            secret, _signed_bytes(headers["X-BD-Timestamp"],
                                  headers["X-BD-Delivery-Id"], body_bytes))
    try:
        r = requests.post(url, data=body_bytes, headers=headers,
                         timeout=10, allow_redirects=False)
        if r.status_code < 300:
            return {"ok": True, "status": r.status_code}
        from .hooks import _http_status_retryable, _retry_after_seconds  # lazy: no static import edge
        # 4xx is permanent, except 408/429: the receiver asks us to come back
        if not _http_status_retryable(r.status_code):
            return {"ok": False, "status": r.status_code,
                    "error": "4xx — drop", "permanent": True}
        out = {"ok": False, "status": r.status_code,
               "error": f"HTTP {r.status_code}"}
        retry_after = _retry_after_seconds(
            (getattr(r, "headers", None) or {}).get("Retry-After"))
        if retry_after is not None:
            out["retry_after"] = min(retry_after, _RETRY_AFTER_MAX)
        return out
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _drain_once(*, max_items: int = 50) -> dict:
    """Process pending webhook items. Designed for the drain thread
    OR for manual /api/webhooks/drain invocation."""
    _ensure_tables()
    now = time.time()
    stats = {"processed": 0, "ok": 0, "retried": 0, "dropped": 0}
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            # Round-robin over subscriptions (every lane's head before any
            # lane's second item), so one subscription's backlog cannot
            # fill the whole LIMIT and starve the others.
            rows = cx.execute("""SELECT * FROM (
                                    SELECT *, ROW_NUMBER() OVER (
                                        PARTITION BY subscription_id ORDER BY id
                                    ) AS lane_pos
                                    FROM webhook_queue WHERE next_retry_ts <= ?)
                                  ORDER BY lane_pos, id LIMIT ?""",
                              (now, int(max_items))).fetchall()
        items = [dict(r) for r in rows]
    except Exception:
        return stats
    # One lane per subscription: items keep their order inside a lane, and
    # lanes run side by side, so a receiver that hangs costs only its own.
    lanes: dict = {}
    for item in items:
        lanes.setdefault(item["subscription_id"], []).append(item)
    stats_lock = threading.Lock()

    def run_lane(lane):
        for item in lane:
            if not _claim(item):
                continue  # another pass (drain thread vs POST /drain) owns it
            result = _deliver_one(item)
            _record(item, result, stats, stats_lock)
            if not result.get("ok") and not result.get("permanent"):
                break  # receiver failing: its later items wait for the next pass

    if len(lanes) <= 1:
        for lane in lanes.values():
            run_lane(lane)
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(_DRAIN_PARALLEL, len(lanes)),
                                thread_name_prefix="bd-webhook-lane") as ex:
            for fut in [ex.submit(run_lane, lane) for lane in lanes.values()]:
                try:
                    fut.result()
                except Exception as e:
                    sys.stderr.write(f"[webhooks] drain lane failed: {e}\n")
    return stats


def _claim(item: dict) -> bool:
    """Compare-and-set lease on one queue row: only the pass whose UPDATE
    still sees the row's (attempt, next_retry_ts) delivers it."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("""UPDATE webhook_queue SET next_retry_ts = ?
                                WHERE id = ? AND attempt = ? AND next_retry_ts = ?""",
                             (time.time() + _CLAIM_LEASE, item["id"],
                              item["attempt"], item["next_retry_ts"]))
            return cur.rowcount == 1
    except Exception as e:
        sys.stderr.write(f"[webhooks] claim failed: {e}\n")
        return False


def _record(item: dict, result: dict, stats: dict, stats_lock) -> None:
    """Write one delivery's outcome back to the queue and its subscription."""
    outcome = "ok" if result.get("ok") else "dropped"
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            if result.get("ok"):
                cx.execute("DELETE FROM webhook_queue WHERE id = ?",
                           (item["id"],))
                cx.execute("""UPDATE webhook_subscriptions
                              SET last_fired_ts = ?,
                                  fire_count = fire_count + 1
                              WHERE id = ?""",
                           (time.time(), item["subscription_id"]))
            elif result.get("permanent"):
                cx.execute("DELETE FROM webhook_queue WHERE id = ?",
                           (item["id"],))
                cx.execute("""UPDATE webhook_subscriptions
                              SET error_count = error_count + 1,
                                  last_error = ?
                              WHERE id = ?""",
                           (result.get("error", "")[:300],
                            item["subscription_id"]))
            elif item["attempt"] >= len(_RETRY_DELAYS):
                cx.execute("DELETE FROM webhook_queue WHERE id = ?",
                           (item["id"],))
                cx.execute("""UPDATE webhook_subscriptions
                              SET error_count = error_count + 1,
                                  last_error = ?
                              WHERE id = ?""",
                           ("max retries exhausted: " + result.get("error", "")[:200],
                            item["subscription_id"]))
            else:
                delay = max(_RETRY_DELAYS[item["attempt"]],
                            result.get("retry_after") or 0)
                cx.execute("""UPDATE webhook_queue
                              SET attempt = ?, next_retry_ts = ?, last_error = ?
                              WHERE id = ?""",
                           (item["attempt"] + 1, time.time() + delay,
                            result.get("error", "")[:300], item["id"]))
                outcome = "retried"
    except Exception as e:
        sys.stderr.write(f"[webhooks] state update failed: {e}\n")
        outcome = None
    with stats_lock:
        stats["processed"] += 1
        if outcome:
            stats[outcome] += 1


def start_drain_worker():
    """Spin up a background thread that drains the webhook queue every
    10 seconds. Idempotent."""
    global _drain_thread
    with _drain_lock:
        if _drain_thread is not None and _drain_thread.is_alive():
            return
        _drain_stop.clear()

        def loop():
            while not _drain_stop.is_set():
                try:
                    _drain_once()
                except Exception as e:
                    sys.stderr.write(f"[webhooks] drain loop: {e}\n")
                _drain_stop.wait(_next_wait())

        _drain_thread = threading.Thread(target=loop, daemon=True,
                                         name="bd-webhook-drain")
        _drain_thread.start()


def _next_wait(cap: float = 10.0) -> float:
    """Seconds until the earliest queued item is due, within [0.5, cap], so
    the 1 s and 2 s retries are not floored to the 10 s tick."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            r = cx.execute("SELECT MIN(next_retry_ts) FROM webhook_queue").fetchone()
        if r and r[0] is not None:
            return max(0.5, min(cap, float(r[0]) - time.time()))
    except Exception:
        pass
    return cap


def stop_drain_worker():
    _drain_stop.set()


def stats() -> dict:
    """Surface for /api/webhooks/stats."""
    _ensure_tables()
    out = {"subscriptions": 0, "enabled": 0, "queue_size": 0,
           "drain_running": _drain_thread is not None and _drain_thread.is_alive()}
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            r = cx.execute("""SELECT COUNT(*), SUM(enabled)
                              FROM webhook_subscriptions""").fetchone()
            out["subscriptions"] = int(r[0] or 0)
            out["enabled"] = int(r[1] or 0)
            r = cx.execute("SELECT COUNT(*) FROM webhook_queue").fetchone()
            out["queue_size"] = int(r[0] or 0)
    except Exception:
        pass
    return out
