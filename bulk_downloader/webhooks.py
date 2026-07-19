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
  • HMAC-SHA256 signature in `X-BD-Signature` header when subscription
    has a secret.

Failure handling:
  • 5xx + network errors → retry up to 5 times
  • 4xx → drop immediately (caller's config is wrong)
  • 3xx → don't follow redirects (could leak credentials)
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

# Retry schedule (seconds). Attempt index → delay before next retry.
_RETRY_DELAYS = [1, 2, 5, 30, 120]


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
    """Compute HMAC-SHA256 over the request body."""
    return hmac.new(secret.encode("utf-8"), body,
                    hashlib.sha256).hexdigest()


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
            return {"ok": False, "error": "subscription gone"}
        sub = dict(sub_row)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    if not sub.get("enabled"):
        return {"ok": False, "error": "subscription disabled"}
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
        headers["X-BD-Signature"] = "sha256=" + _sign(secret, body_bytes)
    try:
        r = requests.post(url, data=body_bytes, headers=headers,
                         timeout=10, allow_redirects=False)
        if r.status_code < 300:
            return {"ok": True, "status": r.status_code}
        # 4xx is permanent
        if 400 <= r.status_code < 500:
            return {"ok": False, "status": r.status_code,
                    "error": "4xx — drop", "permanent": True}
        return {"ok": False, "status": r.status_code,
                "error": f"HTTP {r.status_code}"}
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
            rows = cx.execute("""SELECT * FROM webhook_queue
                                  WHERE next_retry_ts <= ?
                                  ORDER BY id ASC LIMIT ?""",
                              (now, int(max_items))).fetchall()
        items = [dict(r) for r in rows]
    except Exception:
        return stats
    for item in items:
        stats["processed"] += 1
        result = _deliver_one(item)
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
                    stats["ok"] += 1
                elif result.get("permanent"):
                    cx.execute("DELETE FROM webhook_queue WHERE id = ?",
                               (item["id"],))
                    cx.execute("""UPDATE webhook_subscriptions
                                  SET error_count = error_count + 1,
                                      last_error = ?
                                  WHERE id = ?""",
                               (result.get("error", "")[:300],
                                item["subscription_id"]))
                    stats["dropped"] += 1
                else:
                    next_attempt = item["attempt"] + 1
                    if next_attempt >= len(_RETRY_DELAYS):
                        cx.execute("DELETE FROM webhook_queue WHERE id = ?",
                                   (item["id"],))
                        cx.execute("""UPDATE webhook_subscriptions
                                      SET error_count = error_count + 1,
                                          last_error = ?
                                      WHERE id = ?""",
                                   ("max retries exhausted: " + result.get("error", "")[:200],
                                    item["subscription_id"]))
                        stats["dropped"] += 1
                    else:
                        delay = _RETRY_DELAYS[next_attempt]
                        cx.execute("""UPDATE webhook_queue
                                      SET attempt = ?, next_retry_ts = ?, last_error = ?
                                      WHERE id = ?""",
                                   (next_attempt, time.time() + delay,
                                    result.get("error", "")[:300], item["id"]))
                        stats["retried"] += 1
        except Exception as e:
            sys.stderr.write(f"[webhooks] state update failed: {e}\n")
    return stats


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
                _drain_stop.wait(10)

        _drain_thread = threading.Thread(target=loop, daemon=True,
                                         name="bd-webhook-drain")
        _drain_thread.start()


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
