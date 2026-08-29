"""Per-site daily byte budget (Phase 196).

Opt-in cap on how many bytes per day a site can download. When the
budget is exhausted, the worker pauses the site (`window_paused`-like
state) until the next local-midnight rollover.

Use cases:
  • Metered home connection — cap each site to 10GB/day
  • Politeness — don't hammer a single source
  • Quota-aware paywalled sites where the operator pays per GB

Storage: a single SQL table tracking `(site_id, ymd, bytes)`. Download
transports accumulate newly written bytes in memory and pass bounded deltas
to record_site_bytes(); response-buffer boundaries are deliberately not
database-write boundaries. Read on every worker pickup to check "would taking
this URL push us over?"

Config: per-site `daily_byte_budget` (integer, bytes). 0/unset = no cap.

Reset: ymd rolls over at the operator's local midnight (uses
local time.strftime to determine the current ymd). No timezone
plumbing needed since BD already runs in operator's local TZ.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from . import db as _db


_TABLE_READY = False
_ACCOUNTING_STATE_LOCK = threading.Lock()
_SITE_ACCOUNTING_LOCKS: dict[str, threading.RLock] = {}
_SITE_ACCOUNTING_EPOCHS: dict[tuple[str, str], int] = {}
_RETRY_CONDITION = threading.Condition()
_RETRY_DELTAS: dict[tuple[str, str, int], int] = {}
_RETRY_WORKER: Optional[threading.Thread] = None
_RETRY_MAX_BACKOFF = 60.0


def _ensure_table():
    # NOTE: always run the idempotent CREATE IF NOT EXISTS rather than
    # caching a process-global "ready" flag. The old cache went stale across
    # a db re-init (new DB, flag still True -> table never created -> silent
    # data loss). The create is cheap and a no-op when the table exists.
    try:
        with _db.db_conn() as cx:
            cx.execute("""
                CREATE TABLE IF NOT EXISTS daily_site_bytes (
                    site_id TEXT NOT NULL,
                    ymd TEXT NOT NULL,
                    bytes INTEGER NOT NULL DEFAULT 0,
                    last_update_ts REAL,
                    PRIMARY KEY (site_id, ymd)
                )
            """)
            cx.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_site_bytes_ymd
                ON daily_site_bytes(ymd)
            """)
    except Exception:
        pass


def _today_ymd() -> str:
    """Current date in operator's local TZ as YYYY-MM-DD."""
    return time.strftime("%Y-%m-%d", time.localtime())


def record_site_bytes(
    site_id: str,
    n_bytes: int,
    *,
    ymd: Optional[str] = None,
) -> bool:
    """Durably increment one day's counter for this site.

    This is a database boundary, not a per-response-buffer primitive. Streaming
    callers batch byte deltas before invoking it; each invocation still ensures
    the lazily-created schema and opens a database connection.

    ``ymd`` lets a delayed batch retain the date on which its bytes were
    written. Callers that omit it retain the historical "today" behavior.

    Fail-silent: a transient DB lock shouldn't abort a download. The Boolean
    result lets a batching caller retain and retry an uncommitted delta."""
    if not site_id or n_bytes <= 0:
        return True
    _ensure_table()
    try:
        accounting_ymd = ymd or _today_ymd()
        with _db.db_conn() as cx:
            now = time.time()
            cx.execute("""
                INSERT INTO daily_site_bytes (site_id, ymd, bytes, last_update_ts)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(site_id, ymd) DO UPDATE SET
                    bytes = daily_site_bytes.bytes + excluded.bytes,
                    last_update_ts = excluded.last_update_ts
            """, (site_id, accounting_ymd, n_bytes, now))
        return True
    except Exception:
        return False  # fail-silent


def _site_accounting_lock(site_id: str) -> threading.RLock:
    with _ACCOUNTING_STATE_LOCK:
        lock = _SITE_ACCOUNTING_LOCKS.get(site_id)
        if lock is None:
            lock = threading.RLock()
            _SITE_ACCOUNTING_LOCKS[site_id] = lock
        return lock


def _accounting_period(site_id: str) -> tuple[str, int]:
    """Capture date and reset generation at the byte-accounting boundary."""
    accounting_ymd = _today_ymd()
    with _ACCOUNTING_STATE_LOCK:
        epoch = _SITE_ACCOUNTING_EPOCHS.get((site_id, accounting_ymd), 0)
    return accounting_ymd, epoch


def _record_dated_delta(
    site_id: str, accounting_ymd: str, epoch: int, n_bytes: int
) -> bool:
    """Write only if an operator reset has not invalidated this delta."""
    lock = _site_accounting_lock(site_id)
    with lock:
        with _ACCOUNTING_STATE_LOCK:
            current_epoch = _SITE_ACCOUNTING_EPOCHS.get(
                (site_id, accounting_ymd), 0
            )
        if epoch != current_epoch:
            return True
        try:
            result = record_site_bytes(
                site_id, n_bytes, ymd=accounting_ymd
            )
            return result is not False
        except Exception:
            return False


def _ensure_retry_worker_locked() -> None:
    """Start the sole retry worker; retain queued deltas if start fails."""
    global _RETRY_WORKER
    if _RETRY_WORKER is not None or not _RETRY_DELTAS:
        return
    worker = None
    try:
        worker = threading.Thread(
            target=_retry_worker_main,
            daemon=True,
            name="daily-byte-retry",
        )
        _RETRY_WORKER = worker
        worker.start()
    except Exception:
        if _RETRY_WORKER is worker:
            _RETRY_WORKER = None


def _enqueue_retry_delta(
    site_id: str, accounting_ymd: str, epoch: int, n_bytes: int
) -> bool:
    """Transfer a failed delta to one merged, process-wide retry owner."""
    if not site_id or n_bytes <= 0:
        return True
    key = (site_id, accounting_ymd, epoch)
    try:
        with _RETRY_CONDITION:
            _RETRY_DELTAS[key] = _RETRY_DELTAS.get(key, 0) + n_bytes
            _ensure_retry_worker_locked()
        return True
    except Exception:
        return False


def _kick_retry_worker() -> None:
    """Best-effort recovery when a prior worker could not be started."""
    try:
        with _RETRY_CONDITION:
            _ensure_retry_worker_locked()
    except Exception:
        pass


def _retry_wait_locked(timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while _RETRY_DELTAS:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        _RETRY_CONDITION.wait(timeout=remaining)


def _retry_worker_main() -> None:
    """Drain merged failed deltas with one globally bounded backoff loop."""
    global _RETRY_WORKER
    backoff = 1.0
    while True:
        with _RETRY_CONDITION:
            if not _RETRY_DELTAS:
                _RETRY_WORKER = None
                return
            _retry_wait_locked(backoff)
            if not _RETRY_DELTAS:
                _RETRY_WORKER = None
                return
            batch = list(_RETRY_DELTAS.items())

        any_failed = False
        for key, n_bytes in batch:
            site_id, accounting_ymd, epoch = key
            if _record_dated_delta(
                    site_id, accounting_ymd, epoch, n_bytes):
                with _RETRY_CONDITION:
                    remaining = _RETRY_DELTAS.get(key, 0) - n_bytes
                    if remaining > 0:
                        _RETRY_DELTAS[key] = remaining
                    else:
                        _RETRY_DELTAS.pop(key, None)
            else:
                any_failed = True

        backoff = (
            min(_RETRY_MAX_BACKOFF, backoff * 2.0)
            if any_failed else 1.0
        )


class DailyByteAccumulator:
    """Thread-safe, per-transfer batching for daily byte accounting.

    ``add`` is safe on response-buffer hot paths. It performs a durable flush
    no more than once per ``flush_interval``; transports call ``flush`` at
    lifecycle boundaries (completion, pause, stop, and failure) so a short or
    interrupted transfer cannot strand an in-memory remainder.
    """

    def __init__(self, site_id: str, *, flush_interval: float = 1.0):
        self.site_id = site_id
        self.flush_interval = max(1.0, float(flush_interval))
        self._pending_by_period: dict[tuple[str, int], int] = {}
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()

    def add(self, n_bytes: int) -> None:
        try:
            delta = int(n_bytes)
        except (TypeError, ValueError):
            return
        if delta <= 0:
            return

        accounting_period = _accounting_period(self.site_id)
        with self._lock:
            self._pending_by_period[accounting_period] = (
                self._pending_by_period.get(accounting_period, 0) + delta
            )
            now = time.monotonic()
            if now - self._last_flush >= self.flush_interval:
                self._flush_locked(now)

    def flush(self) -> bool:
        """Write pending deltas or transfer failures to the retry broker."""
        _kick_retry_worker()
        with self._lock:
            return self._flush_locked(time.monotonic())

    def _flush_locked(self, now: float) -> bool:
        """Flush while holding ``_lock`` so concurrent adds cannot race."""
        if not self._pending_by_period:
            return True

        all_recorded = True
        for period, n_bytes in list(self._pending_by_period.items()):
            accounting_ymd, epoch = period
            if self._record(accounting_ymd, epoch, n_bytes):
                del self._pending_by_period[period]
            elif _enqueue_retry_delta(
                    self.site_id, accounting_ymd, epoch, n_bytes):
                # Ownership moves atomically to the merged retry broker. The
                # transfer may now unregister without losing this delta.
                del self._pending_by_period[period]
                all_recorded = False
            else:
                all_recorded = False
        # A locked database must not turn the hot path back into one attempted
        # write per response buffer. Failed deltas share one bounded broker.
        self._last_flush = now
        return all_recorded

    def _record(self, accounting_ymd: str, epoch: int, n_bytes: int) -> bool:
        if n_bytes <= 0:
            return True
        return _record_dated_delta(
            self.site_id, accounting_ymd, epoch, n_bytes
        )


def bytes_today(site_id: str) -> Optional[int]:
    """How many bytes this site downloaded today, or ``None`` if unreadable."""
    if not site_id:
        return 0
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            row = cx.execute("""
                SELECT bytes FROM daily_site_bytes
                WHERE site_id = ? AND ymd = ?
            """, (site_id, _today_ymd())).fetchone()
        return int(row["bytes"]) if row else 0
    except Exception:
        return None


def _budget_report(*, budget: int, used: Optional[int], source: str) -> dict:
    """Build a budget verdict without equating unreadable with zero usage."""
    if used is None:
        return {
            "over": None,
            "available": False,
            "unknown": True,
            "error": f"{source} daily-byte counter unavailable",
            "used_bytes": None,
            "budget_bytes": budget,
            "remaining_bytes": None,
            "pct_used": None,
        }
    if budget <= 0:
        return {
            "over": False,
            "available": True,
            "unknown": False,
            "error": "",
            "used_bytes": used,
            "budget_bytes": 0,
            "remaining_bytes": 0,
            "pct_used": 0.0,
        }
    return {
        "over": used >= budget,
        "available": True,
        "unknown": False,
        "error": "",
        "used_bytes": used,
        "budget_bytes": budget,
        "remaining_bytes": budget - used,
        "pct_used": min(100.0, round(used * 100.0 / budget, 1)),
    }


def is_over_budget(site_id: str, *, site_cfg: dict) -> dict:
    """Check whether this site is over its daily budget. Returns:

        {
          over: bool,
          used_bytes: int,
          budget_bytes: int,
          remaining_bytes: int,  # may be negative
          pct_used: float,
        }

    If the site has no `daily_byte_budget` configured (0 or absent),
    returns over=False. An unreadable counter returns over=None and
    unknown=True; admission callers hold only when a cap is configured."""
    budget = int((site_cfg or {}).get("daily_byte_budget") or 0)
    used = bytes_today(site_id)
    return _budget_report(budget=budget, used=used, source="site")


# ── Cut 8: global (cross-site) daily byte budget ──────────────────────
# Per-site `daily_byte_budget` already exists; this is the global cap key
# set from POST /api/global_config (mirrors how global_max_concurrent calls
# runner.set_global_concurrent_cap). 0 = uncapped.
_GLOBAL_BUDGET = 0


def set_global_budget(n: int):
    """Set the global daily byte budget (bytes). 0/negative = uncapped."""
    global _GLOBAL_BUDGET
    try:
        _GLOBAL_BUDGET = max(0, int(n))
    except Exception:
        _GLOBAL_BUDGET = 0


def get_global_budget() -> int:
    return _GLOBAL_BUDGET


def bytes_today_all() -> Optional[int]:
    """Total bytes today, or ``None`` when the counter cannot be read."""
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            row = cx.execute(
                "SELECT COALESCE(SUM(bytes),0) AS t FROM daily_site_bytes "
                "WHERE ymd = ?", (_today_ymd(),)).fetchone()
        return int(row["t"]) if row else 0
    except Exception:
        return None


def is_over_global_budget(*, global_budget: Optional[int] = None) -> dict:
    """Whether the combined cross-site usage has hit the global cap. Same
    shape as is_over_budget. `global_budget` defaults to the module state
    set via set_global_budget. An unreadable counter is UNKNOWN rather than
    the same over=False result as measured usage below the cap."""
    budget = int(_GLOBAL_BUDGET if global_budget is None else global_budget)
    used = bytes_today_all()
    return _budget_report(budget=budget, used=used, source="global")


def status_all(s_cfg: dict) -> list:
    """Snapshot of every site's today usage. Includes sites without
    a budget configured (budget_bytes=0) so the UI can show "no cap"
    next to them. Sorted by pct_used desc."""
    out = []
    for sid, cfg in (s_cfg or {}).items():
        out.append({
            "site_id": sid,
            "site_name": (cfg or {}).get("name") or sid,
            **is_over_budget(sid, site_cfg=cfg),
        })
    out.sort(key=lambda r: -(r.get("pct_used") or 0))
    return out


def reset_today(site_id: str) -> bool:
    """Operator override — zero out today's counter so the site can
    keep downloading. Useful for testing or in emergencies. Returns
    True if a row existed and was reset."""
    lock = _site_accounting_lock(site_id)
    with lock:
        _ensure_table()
        try:
            accounting_ymd = _today_ymd()
            with _db.db_conn() as cx:
                cur = cx.execute("""
                    UPDATE daily_site_bytes
                    SET bytes = 0, last_update_ts = ?
                    WHERE site_id = ? AND ymd = ?
                """, (time.time(), site_id, accounting_ymd))
            # Linearization point: all adds after this generation bump count
            # from zero; buffered/retry deltas from the old generation become
            # stale and are discarded instead of undoing the operator reset.
            epoch_key = (site_id, accounting_ymd)
            with _ACCOUNTING_STATE_LOCK:
                new_epoch = _SITE_ACCOUNTING_EPOCHS.get(epoch_key, 0) + 1
                _SITE_ACCOUNTING_EPOCHS[epoch_key] = new_epoch
            row_existed = cur.rowcount > 0
        except Exception:
            return False

    with _RETRY_CONDITION:
        for key in list(_RETRY_DELTAS):
            queued_site, queued_ymd, epoch = key
            if (queued_site == site_id
                    and queued_ymd == accounting_ymd
                    and epoch < new_epoch):
                _RETRY_DELTAS.pop(key, None)
    return row_existed


def history(site_id: str, *, days: int = 30) -> list:
    """Daily usage history for charting. Most recent first."""
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            rs = cx.execute("""
                SELECT ymd, bytes FROM daily_site_bytes
                WHERE site_id = ?
                ORDER BY ymd DESC
                LIMIT ?
            """, (site_id, int(days))).fetchall()
        return [{"ymd": r["ymd"], "bytes": r["bytes"]} for r in rs]
    except Exception:
        return []
