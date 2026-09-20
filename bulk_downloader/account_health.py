"""Account health scoring + rotation hints (Phase 100, Block M).

Reads the existing AccountPool state (per-site) and exposes:

  • score_account()  — 0-100 health score derived from fail/lease
                       ratios + recency of last error
  • rotation_hint()  — which account the runner SHOULD pick next
                       (least-failed, least-recently-used, longest-
                       in-cooldown weighted blend)
  • ban_risk()       — boolean prediction: this account is heading
                       toward a ban; cool it down preemptively
  • report_all()     — flat list of {site_id, idx, username, score,
                       state, hint} for dashboards

Doesn't mutate state — read-only over AccountPool. The pool itself
(account_pool.py) stays the single writer; this module just gives the
runner smarter selection guidance + the UI a health view.

Heuristics, not learned models. Operator can override any score via
manual rotation if a ranking looks wrong.
"""
from __future__ import annotations

import contextlib
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

# Row 896: 25-minute periodic session liveness monitoring cadence
LIVENESS_INTERVAL_SECONDS = 1500  # 25 minutes
MIN_LIVENESS_INTERVAL_SECONDS = 1.0  # floor: a sub-second interval would spin the monitor thread
HEAD_TIMEOUT_SECONDS = 15


def _status_of(resp) -> int:
    """HTTP status of any response object the app's transports return:
    requests/httpx (.status_code), urllib HTTPResponse (.status / getcode()),
    or a bare int (tests)."""
    for attr in ("status_code", "status"):
        value = getattr(resp, attr, None)
        if value is not None:
            return int(value)
    getcode = getattr(resp, "getcode", None)
    if callable(getcode):
        return int(getcode())
    return int(resp)


def head_request_for(url: str, timeout: float = HEAD_TIMEOUT_SECONDS) -> Callable:
    """The default HEAD issuer: a non-mutating urllib HEAD through the
    pinned opener the hooks use (LAN sites admitted, link-local/metadata/
    CGNAT refused, one vetted address per hop). Returns a callable for
    register_endpoint()/probe_session(); an HTTP error status is RETURNED
    (urlopen raises HTTPError for 4xx/5xx -- probe_session maps it)."""
    def _head():
        from .hooks import _hook_urlopen
        req = urllib.request.Request(url, method="HEAD")
        with _hook_urlopen(req, timeout=timeout) as resp:
            return _status_of(resp)
    return _head


def reroute_expired_session_queue(
    queue,
    *,
    expired_account_idx: Optional[int] = None,
    target_account_idx: Optional[int] = None,
    site_id: str = "",
) -> int:
    """Proactively reroute queued tasks assigned to an expired session.

    Active downloads (status == 'running') are left completely untouched to
    ensure zero disruption to in-flight work. Pending/queued tasks for the
    expired account are reassigned to target_account_idx (or the next recommended
    healthy account if target_account_idx is None).
    """
    if queue is None:
        return 0

    if target_account_idx is None and site_id:
        target_account_idx = rotation_hint(site_id)

    rerouted_count = 0

    # Support list of task dicts
    if isinstance(queue, list):
        for item in queue:
            if not isinstance(item, dict):
                continue
            # Never interrupt or modify active in-flight downloads
            if item.get("status") == "running":
                continue
            item_acc = item.get("account_idx")
            if expired_account_idx is None or item_acc == expired_account_idx:
                if target_account_idx is not None:
                    item["account_idx"] = target_account_idx
                item["rerouted"] = True
                rerouted_count += 1

    # Support dict of jobs (runner.jobs)
    elif isinstance(queue, dict):
        for url, job in queue.items():
            if not isinstance(job, dict):
                continue
            if job.get("status") == "running":
                continue
            job_acc = job.get("account_idx")
            if expired_account_idx is None or job_acc == expired_account_idx:
                if target_account_idx is not None:
                    job["account_idx"] = target_account_idx
                job["rerouted"] = True
                rerouted_count += 1

    return rerouted_count


def probe_session(
    head_request: Callable,
    queue=None,
    *,
    site_id: str = "",
    account_idx: Optional[int] = None,
    target_account_idx: Optional[int] = None,
    queue_lock=None,
) -> dict:
    """Probe session liveness via non-mutating HTTP HEAD check.

    Issues a non-mutating HEAD check (Rule 21: 0 site logins touched).
    On 401/403 (unauthorized/expired session), proactively reroutes pending tasks
    in `queue` without touching active running downloads, and returns a structured
    alert. On network exceptions, returns unreachable status.
    """
    now = time.time()
    try:
        try:
            status = _status_of(head_request())
        except urllib.error.HTTPError as exc:
            # urllib raises for 4xx/5xx: the status IS the answer (a 401 here
            # is the expired session this probe exists to catch)
            status = int(exc.code)
    except Exception:
        return {
            "healthy": False,
            "status": None,
            "alert": "unreachable",
            "site_id": site_id,
            "account_idx": account_idx,
            "timestamp": now,
            "rerouted": 0,
        }

    if status in (401, 403):
        rerouted = 0
        # rerouting needs a named account: with account_idx=None it would
        # reassign every pending task of every account
        if queue is not None and account_idx is not None:
            with (queue_lock if queue_lock is not None else contextlib.nullcontext()):
                rerouted = reroute_expired_session_queue(
                    queue,
                    expired_account_idx=account_idx,
                    target_account_idx=target_account_idx,
                    site_id=site_id,
                )
        return {
            "healthy": False,
            "status": status,
            "alert": "unauthorized",
            "site_id": site_id,
            "account_idx": account_idx,
            "timestamp": now,
            "rerouted": rerouted,
        }

    if 200 <= status < 400:
        return {
            "healthy": True,
            "status": status,
            "alert": None,
            "site_id": site_id,
            "account_idx": account_idx,
            "timestamp": now,
            "rerouted": 0,
        }

    return {
        "healthy": False,
        "status": status,
        "alert": f"http_{status}",
        "site_id": site_id,
        "account_idx": account_idx,
        "timestamp": now,
        "rerouted": 0,
    }


class SessionLivenessMonitor:
    """Periodic background monitor issuing HTTP HEAD checks every 25 minutes.

    Keeps authenticated sessions warm and detects expired credentials before
    worker tasks fail mid-flight. Rule 21 compliant: zero site logins touched.
    """

    def __init__(
        self,
        interval_seconds: float = LIVENESS_INTERVAL_SECONDS,
        *,
        queue=None,
        on_alert: Optional[Callable[[dict], None]] = None,
        queue_lock=None,
    ):
        # a float with a floor: int() truncated 0.5 -> 0 and wait(0) spun the thread
        self.interval_seconds = max(float(interval_seconds), MIN_LIVENESS_INTERVAL_SECONDS)
        self.queue = queue
        self.queue_lock = queue_lock
        self.on_alert = on_alert
        self._endpoints: list[dict] = []
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.last_run: float = 0.0
        self.alerts_history: list[dict] = []

    def register_endpoint(
        self,
        site_id: str,
        account_idx: int,
        head_fn=None,
        *,
        url: Optional[str] = None,
        target_account_idx: Optional[int] = None,
    ) -> None:
        """Register an authenticated endpoint probe for a (site_id, account_idx):
        either a caller-supplied ``head_fn`` or a ``url`` the monitor HEADs
        itself through ``head_request_for``."""
        if head_fn is None:
            if not url:
                raise ValueError("register_endpoint needs head_fn or url")
            head_fn = head_request_for(url)
        with self._lock:
            self._endpoints.append({
                "site_id": site_id,
                "account_idx": account_idx,
                "head_fn": head_fn,
                "target_account_idx": target_account_idx,
            })

    def run_once(self) -> list[dict]:
        """Execute one sweep of HTTP HEAD liveness probes across registered endpoints."""
        results = []
        with self._lock:
            endpoints = list(self._endpoints)

        for ep in endpoints:
            res = probe_session(
                ep["head_fn"],
                self.queue,
                site_id=ep["site_id"],
                account_idx=ep["account_idx"],
                target_account_idx=ep["target_account_idx"],
                queue_lock=self.queue_lock,
            )
            results.append(res)
            if not res["healthy"] and res.get("alert"):
                self.alerts_history.append(res)
                if self.on_alert is not None:
                    try:
                        self.on_alert(res)
                    except Exception:
                        pass
        self.last_run = time.time()
        return results

    def is_alive(self) -> bool:
        """True if the background liveness monitoring thread is running."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the background 25-minute periodic monitoring thread."""
        if self.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="bd-session-liveness",
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background monitor thread cleanly."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception:
                pass
            self._stop_event.wait(self.interval_seconds)


def attach_liveness_monitor(runner, endpoints, *, interval_seconds: float = LIVENESS_INTERVAL_SECONDS,
                            on_alert: Optional[Callable[[dict], None]] = None) -> SessionLivenessMonitor:
    """The runner's start point: build a monitor over ``runner.jobs`` (guarded
    by ``runner._lock``), register ``endpoints`` as (account_idx, url[, target_account_idx])
    tuples for ``runner.site_id``, start the daemon thread and return it. The
    caller keeps the handle and calls ``stop()`` when the runner stops."""
    monitor = SessionLivenessMonitor(
        interval_seconds,
        queue=getattr(runner, "jobs", None),
        queue_lock=getattr(runner, "_lock", None),
        on_alert=on_alert,
    )
    site_id = getattr(runner, "site_id", "")
    for ep in endpoints:
        account_idx, url = ep[0], ep[1]
        target = ep[2] if len(ep) > 2 else None
        monitor.register_endpoint(site_id, account_idx, url=url, target_account_idx=target)
    monitor.start()
    return monitor


_global_liveness_monitor: Optional[SessionLivenessMonitor] = None
_global_liveness_lock = threading.Lock()


def get_liveness_monitor(
    interval_seconds: float = LIVENESS_INTERVAL_SECONDS,
    queue=None,
    on_alert: Optional[Callable[[dict], None]] = None,
) -> SessionLivenessMonitor:
    """Return the global SessionLivenessMonitor singleton."""
    global _global_liveness_monitor
    with _global_liveness_lock:
        if _global_liveness_monitor is None:
            _global_liveness_monitor = SessionLivenessMonitor(
                interval_seconds=interval_seconds,
                queue=queue,
                on_alert=on_alert,
            )
        return _global_liveness_monitor


class AccountHealthUnavailable(RuntimeError):
    """The account-pool census could not be measured."""


def _safe_get_pool(site_id: str):
    """Returns AccountPool or None. Avoids hard-importing in module
    scope so account_pool import order doesn't matter."""
    try:
        from . import account_pool as _ap
        return _ap.get_pool(site_id)
    except Exception:
        return None


def score_account(account_state) -> int:
    """0-100 health score for one _AccountState. Higher = healthier.

    Inputs (all read off the existing _AccountState):
      • fail_count     — total observed failures since pool init
      • lease_count    — total leases (denominator)
      • last_error     — most recent error string (presence flags risk)
      • cooldown_until — non-zero = currently sin-binned
      • state          — "available" | "in_use" | "cooldown" | etc.

    Score components (additive, clamped to [0, 100]):
      • base: 100 if state != "cooldown" else 30
      • penalty: -50 * (fail_count / lease_count) if lease_count > 0
      • recency penalty: -20 if last_error contains "429" or "ban"
      • bonus: +10 if no failures observed yet

    Caller passes the state object directly; we don't deep-import."""
    if account_state is None:
        return 0
    state = getattr(account_state, "state", "")
    fail_count = getattr(account_state, "fail_count", 0)
    lease_count = max(1, getattr(account_state, "lease_count", 0))
    last_error = (getattr(account_state, "last_error", "") or "").lower()
    cooldown_until = getattr(account_state, "cooldown_until", 0.0)

    score = 100 if state != "cooldown" else 30
    if cooldown_until > time.time():
        score = min(score, 40)
    # Failure-ratio penalty: at 100% fail rate, deducts 50 points
    score -= int(50 * min(1.0, fail_count / lease_count))
    # Specific risk patterns
    if any(token in last_error for token in ("429", "rate limit", "ban", "blocked",
                                              "suspended", "captcha")):
        score -= 20
    # First-use grace bonus
    if fail_count == 0:
        score += 10
    return max(0, min(100, score))


def ban_risk(account_state) -> bool:
    """Predict imminent ban. Currently: True if last_error matches
    a known-bad pattern AND fail_count is rising. Operator should
    cool down preemptively."""
    if account_state is None:
        return False
    last_error = (getattr(account_state, "last_error", "") or "").lower()
    fail_count = getattr(account_state, "fail_count", 0)
    if fail_count < 2:
        return False
    risky = ("429", "captcha rate", "too many", "suspended", "blocked",
             "abuse", "stop spamming")
    return any(token in last_error for token in risky)


def rotation_hint(site_id: str) -> Optional[int]:
    """Suggest which account index the runner should claim next.
    Returns idx, or None when no clear preference / no pool.

    Strategy:
      1. Filter to currently-available accounts (state == "available"
         AND cooldown_until <= now)
      2. Rank by: score DESC, then last_used ASC (LRU breaks ties)
      3. Return the top idx
    """
    pool = _safe_get_pool(site_id)
    if pool is None:
        return None
    try:
        now = time.time()
        candidates = []
        for st in getattr(pool, "_accounts", []):
            if getattr(st, "state", "") != "available":
                continue
            if getattr(st, "cooldown_until", 0) > now:
                continue
            candidates.append((score_account(st),
                              -getattr(st, "last_used", 0),  # negative so older=greater
                              getattr(st, "idx", 0)))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][2]
    except Exception:
        return None


def report_all() -> list:
    """Return [{site_id, idx, username, score, state, ban_risk, ...}]
    across all configured pools. Used by the operator dashboard.

    Raises AccountHealthUnavailable when the pool census cannot be measured;
    an empty list is reserved for a successful census with no accounts.
    """
    out = []
    try:
        from . import account_pool as _ap
        for entry in _ap.get_all_pools_status():
            # get_all_pools_status returns dicts; we re-score + add risk.
            # Defensive against shape changes — only consult expected keys.
            sid = entry.get("site_id", "")
            for acc in entry.get("accounts", []):
                # Reconstruct a duck-typed object so score_account works
                # against the dict shape too.
                class _Duck:
                    pass
                duck = _Duck()
                for k, v in acc.items():
                    setattr(duck, k, v)
                out.append({
                    "site_id": sid,
                    "idx": acc.get("idx", -1),
                    "username": acc.get("username", ""),
                    "state": acc.get("state", ""),
                    "fail_count": acc.get("fail_count", 0),
                    "lease_count": acc.get("lease_count", 0),
                    "last_error": (acc.get("last_error") or "")[:120],
                    "score": score_account(duck),
                    "ban_risk": ban_risk(duck),
                })
    except Exception as e:
        # An empty list is a valid, measured census (there may be no configured
        # pools), so it cannot also be the exception fallback.  Let callers map
        # the unavailable state into their own status vocabulary.
        raise AccountHealthUnavailable(
            f"account pool census unavailable: {e}"
        ) from e
    return out


def summary() -> dict:
    """Aggregate report. Counts accounts in each bucket so the operator
    sees portfolio-level health at a glance."""
    rows = report_all()
    n = len(rows)
    if n == 0:
        return {"total": 0, "healthy": 0, "warning": 0, "critical": 0,
                "at_risk": 0, "avg_score": 0}
    healthy = sum(1 for r in rows if r["score"] >= 70)
    warning = sum(1 for r in rows if 40 <= r["score"] < 70)
    critical = sum(1 for r in rows if r["score"] < 40)
    at_risk = sum(1 for r in rows if r["ban_risk"])
    avg = sum(r["score"] for r in rows) / n
    return {
        "total": n,
        "healthy": healthy,
        "warning": warning,
        "critical": critical,
        "at_risk": at_risk,
        "avg_score": round(avg, 1),
    }
