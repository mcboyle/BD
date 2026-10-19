"""bulk_downloader.app_state -- hoisted mutable-state kernel (DECOMP-R2a).

DAG leaf owning the live, mutated-in-place process state moved out of the
app.py hub. Every object here is created ONCE and mutated in place (never
reassigned), so its identity is stable for the life of the process and is
safe to share by reference. app.py imports + re-exports these, so the 149
blueprints' `getattr(import_module("...app"), "s_cfg")` back-edges and
`from bulk_downloader.app import s_cfg` (runner.py, tests) keep resolving to
THESE objects. (R2b will repoint those importers straight at this leaf.)

Note: the pairing lock's construction alias `_t40_threading` (app.py L542)
is normalized to `threading` here -- same Lock type, identical behavior.
"""

import contextlib
import os
import threading
import time
import weakref

runners = {}
s_cfg = {}
s_meta = {}
_pairing_tokens: dict = {}      # pairing_token → {"created", "expires_at"}
from .lock_monitor import register_monitored_lock, get_global_contention_report

_pairing_lock = register_monitored_lock("app_state._pairing_lock", threading.Lock())
_watch_threads = {}   # {site_id: Thread}
_watch_stops = {}     # {site_id: Event}
# Serializes each watch worker's runner/stop/thread lifecycle.  Callers that
# also hold app._BOOT_LOCK must acquire that lock first; targets never acquire
# _BOOT_LOCK, so their identity-checked cleanup cannot invert the order.
_watch_registry_lock = register_monitored_lock("app_state._watch_registry_lock", threading.RLock())
# A config save owns one snapshot-to-replace transaction across every site.
# Per-site lifecycle stripes cannot protect the shared file: two different
# site requests may otherwise race a fixed .tmp path or let an older snapshot
# replace a newer delete.  Fixed process-wide ownership is also memory-bounded.
_sites_config_save_lock = register_monitored_lock("app_state._sites_config_save_lock", threading.RLock())

# sl-lock (sites-lifecycle row 1): one lock per site, never a stripe shared
# with unrelated sites.  Every request that observes or mutates one site's
# identity still serializes on it, and delete still holds it through writer
# retirement and teardown, so a route cannot snapshot a runner/config before
# delete and commit after it.
#
# Memory bound: the registry is weak.  An entry lives only while some caller
# references its lock (holding it, waiting on it, or about to), so unknown
# attacker-supplied IDs cost nothing once their request ends.  Pruning never
# hands out a second lock for a sid: a lock is only collected when nobody can
# be holding or waiting on it.
#
# All-sites exclusion (config import): every per-site acquire first enters
# _site_lifecycle_gate SHARED; the importer enters it EXCLUSIVE.  That excludes
# every per-site transition, including sids that have no lock object yet.
# Lock order: gate -> per-site lock -> sites-config save lock.
class _SiteLifecycleGate:
    """Thread-reentrant shared/exclusive gate with exclusive preference.

    A thread already inside the gate (shared or exclusive) always re-enters
    shared without waiting, so a site route taking a second per-site lock, or
    the importer taking a per-site lock, can never wait on a queued importer.
    """

    def __init__(self):
        self._cond = threading.Condition(threading.Lock())
        self._shared = {}            # thread ident -> shared depth
        self._owner = None           # exclusive owner ident
        self._depth = 0              # exclusive depth
        self._exclusive_waiting = 0

    def acquire_shared(self, blocking=True, timeout=-1):
        me = threading.get_ident()
        with self._cond:
            if self._owner != me and not self._shared.get(me):
                def _free():
                    return self._owner is None and not self._exclusive_waiting
                if not blocking:
                    if not _free():
                        return False
                elif not self._cond.wait_for(
                        _free, None if timeout < 0 else timeout):
                    return False
            self._shared[me] = self._shared.get(me, 0) + 1
            return True

    def release_shared(self):
        me = threading.get_ident()
        with self._cond:
            depth = self._shared.get(me, 0)
            if not depth:
                raise RuntimeError("cannot release un-acquired site gate")
            if depth == 1:
                del self._shared[me]
                self._cond.notify_all()
            else:
                self._shared[me] = depth - 1

    def acquire_exclusive(self):
        me = threading.get_ident()
        with self._cond:
            if self._owner == me:
                self._depth += 1
                return True

            def _free():
                return self._owner is None and not any(
                    t != me for t in self._shared)
            self._exclusive_waiting += 1
            try:
                self._cond.wait_for(_free)
            finally:
                self._exclusive_waiting -= 1
            self._owner = me
            self._depth = 1
            return True

    def release_exclusive(self):
        with self._cond:
            if self._owner != threading.get_ident():
                raise RuntimeError("cannot release un-acquired site gate")
            self._depth -= 1
            if not self._depth:
                self._owner = None
                self._cond.notify_all()


_site_lifecycle_gate = _SiteLifecycleGate()


class _SiteLifecycleLock:
    """A site's reentrant lifecycle lock (RLock interface, gate-aware)."""

    __slots__ = ("site_id", "_lock", "_holds", "__weakref__")

    def __init__(self, site_id):
        self.site_id = site_id
        self._lock = threading.RLock()
        self._holds = 0  # mutated only by the owning thread

    def acquire(self, blocking=True, timeout=-1):
        deadline = None if timeout < 0 else time.monotonic() + timeout
        if not _site_lifecycle_gate.acquire_shared(blocking, timeout):
            return False
        try:
            remaining = -1 if deadline is None else max(
                0.0, deadline - time.monotonic())
            got = self._lock.acquire(blocking, remaining)
        except BaseException:
            _site_lifecycle_gate.release_shared()
            raise
        if not got:
            _site_lifecycle_gate.release_shared()
            return False
        self._holds += 1
        if self._holds == 1:
            # A held lock stays registered even if the acquirer dropped its
            # reference, so a later lookup for this sid cannot mint a twin.
            _site_lifecycle_locks_held[self.site_id] = self
        return True

    def release(self):
        if not self._lock._is_owned():
            raise RuntimeError("cannot release un-acquired site lock")
        self._holds -= 1
        if not self._holds:
            _site_lifecycle_locks_held.pop(self.site_id, None)
        self._lock.release()
        _site_lifecycle_gate.release_shared()

    def _is_owned(self):
        return self._lock._is_owned()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_exc):
        self.release()


_site_lifecycle_locks = weakref.WeakValueDictionary()
_site_lifecycle_locks_guard = threading.Lock()
_site_lifecycle_locks_held = {}  # sid -> lock, while acquired


def site_lifecycle_lock(site_id: str):
    key = str(site_id)
    with _site_lifecycle_locks_guard:
        lock = _site_lifecycle_locks.get(key)
        if lock is None:
            lock = _SiteLifecycleLock(key)
            _site_lifecycle_locks[key] = lock
        return lock


@contextlib.contextmanager
def all_site_lifecycle_locks():
    """Exclude every per-site lifecycle transaction, known sid or not."""
    _site_lifecycle_gate.acquire_exclusive()
    try:
        yield
    finally:
        _site_lifecycle_gate.release_exclusive()


def runners_snapshot():
    """Return ``list(runners.items())`` captured under ``_watch_registry_lock``.

    ``runners`` is inserted on site-create request threads and popped on delete;
    a reader that walks it bare gets ``RuntimeError: dictionary changed size
    during iteration`` the moment either lands.  In an endpoint that is an
    operator-visible 500 raised at the ``for`` statement itself -- AFTER the loop
    body has already acted on a prefix of the fleet -- so the operator is left
    with a half-applied change and no report of which sites it reached.

    COPY, DO NOT HOLD.  Callers must iterate the returned list rather than doing
    their work inside the lock: ``runner.stop()`` / ``start()`` join worker
    threads, and a watch worker's own finalizer acquires this same lock on its
    way out, so holding it across a loop converts an iteration bug into a stall
    or a deadlock.  The copy is O(n) over pointers and gives the caller one
    stable, complete generation to walk while create/delete proceed normally.

    Not relying on ``list(d.items())`` being implicitly atomic is deliberate:
    that property is a GIL implementation detail, and the point of this helper is
    that create, delete, and enumeration agree on ONE lock rather than on an
    interpreter's threading model.
    """
    with _watch_registry_lock:
        return list(runners.items())


def runners_generation(mapping):
    """``runners_snapshot()`` when ``mapping`` IS the live registry, else a
    plain copy.

    Several helpers accept a runners mapping that is EITHER the live registry
    or a caller-scoped ``{sid: runner}`` copy.  The scoped copy is private and
    needs no lock; the live one does.  Deciding by identity keeps that
    distinction in one place instead of at every call site, where getting it
    wrong is invisible until a create lands mid-walk.
    """
    if mapping is runners:
        return runners_snapshot()
    return list((mapping or {}).items())


def _lock_live_state_before_fork():
    """Hold ``_watch_registry_lock`` across every ``os.fork()`` in this process.

    ``fork()`` carries ONLY the calling thread.  A lock another thread held at
    the instant of the fork is inherited LOCKED by a child that contains no
    thread able to release it, so the child's first ``runners_snapshot()``
    blocks forever.  That became reachable the moment this module put the
    registry lock on operator-facing READ paths: /metrics, /api/dashboard/v2
    and /api/widgets/data take it now and took no lock before.

    Taking the lock in the FORKING thread is the remedy the
    ``os.register_at_fork`` documentation names.  It makes the fork wait for
    any current holder, so no child can inherit the lock from a thread that
    will not exist -- and because the forking thread owns it, the paired
    release below leaves both processes with a lock in the state they started
    from.  The lock is an RLock, so a thread that already holds it when it
    forks simply re-enters and the release unwinds one level.

    NOT REBOUND, DELIBERATELY.  This module's contract is that its objects are
    created once and mutated in place, because app.py re-exports them by value
    (``from .app_state import _watch_registry_lock``).  Replacing the lock in
    the child would leave app.py's alias naming a DIFFERENT lock from
    ``runners_snapshot()``, so create/delete and enumeration would stop
    agreeing on one lock -- the exact defect the snapshot exists to remove.
    """
    _watch_registry_lock.acquire()


def _unlock_live_state_after_fork():
    """Release the fork-time hold, in the parent and in the child alike."""
    _watch_registry_lock.release()


if hasattr(os, "register_at_fork"):     # POSIX only; a no-fork platform
    os.register_at_fork(              # cannot exhibit the hazard at all
        before=_lock_live_state_before_fork,
        after_in_parent=_unlock_live_state_after_fork,
        after_in_child=_unlock_live_state_after_fork,
    )


# The concrete type of ``threading.Lock()``.  ``threading.Lock`` is a factory
# function, not a class, so ``isinstance`` needs the object it produces.
_PLAIN_LOCK_TYPE = type(threading.Lock())


def _reinit_runner_locks_in_child():
    """Give the forked child a FREE ``threading.Lock`` for every per-runner lock
    another thread held at the instant of the fork.

    Row 639.  ``_watch_registry_lock`` is only half the fork hazard: eleven
    operator routes (/api/capacity, /api/dashboard, /api/dashboard/v2,
    /api/health, /api/health/v2, /api/queue/preflight, /api/queue/v2,
    /api/sites/v2, /api/status, /api/widgets/data, /metrics) copy each runner's
    jobs under that runner's OWN ``_lock``.  ``fork()`` carries only the calling
    thread, so a runner lock held by the auto-retry scanner
    (``runner_scheduler.SchedulerMixin._auto_retry_loop``, awake once per 60s) is
    inherited LOCKED by a child containing no thread that can ever release it,
    and the child's first such route blocks forever.  Identical mechanism to the
    registry lock, whose child hung with a stack at app_state.py:67.

    REINITIALISED IN THE CHILD RATHER THAN HELD ACROSS THE FORK, which is the
    opposite of the choice above and deliberately so.  Taking these locks in the
    forking thread -- the remedy the registry lock uses -- cannot work here:
    ``Runner._lock`` is a NON-REENTRANT ``threading.Lock`` (runner.py:812), so a
    thread that forks while already holding one would self-deadlock in its own
    ``before`` hook, permanently and with no diagnostic; and ``before`` hooks run
    in REVERSE registration order, so a second registration would take runner
    locks BEFORE the registry lock and invert the order every route uses
    (snapshot under the registry lock, then the runner's lock).  Rebinding in the
    child has neither failure mode: the parent is untouched, no fork waits on
    anything new, and it is the remedy CPython itself applies to its own logging
    locks (``logging._after_at_fork_child_reinit_locks``).

    Rebinding is safe HERE and was refused for ``_watch_registry_lock`` above for
    a reason that does not apply: that lock is a module global re-exported BY
    VALUE (``from .app_state import _watch_registry_lock``), so replacing it
    would leave two aliases naming different locks.  A runner's lock is an
    instance attribute looked up afresh at every ``with runner._lock:``, and no
    module caches one.

    ONLY LOCKS THAT ARE ACTUALLY HELD are replaced, so an uncontended fork
    changes nothing.  Only non-reentrant locks are replaced: an ``RLock`` records
    an owning thread and a recursion count, so blindly swapping one the forking
    thread itself owns would corrupt its unwind.  Runner RLocks held across a
    fork are therefore a smaller population this hook does not claim to cover.
    Returns the number of locks rebound so a caller can assert a nonzero effect.
    """
    rebound = 0
    for _sid, runner in runners_snapshot():
        try:
            attrs = list(vars(runner).items())
        except TypeError:            # __slots__ or a C object: nothing to walk
            continue
        for name, value in attrs:
            if not name.endswith("_lock"):
                continue
            if type(value) is not _PLAIN_LOCK_TYPE:
                continue
            if not value.locked():
                continue
            try:
                setattr(runner, name, threading.Lock())
            except Exception:        # read-only attribute: cannot be helped
                continue
            rebound += 1
    return rebound

if hasattr(os, "register_at_fork"):     # same POSIX guard as above; a second
    os.register_at_fork(              # registration keeps the registry hold's
        after_in_child=_reinit_runner_locks_in_child,   # own contract intact
    )
_dedup_scan_state = {
    "running": False,
    "started_at": 0.0,
    "done": 0,
    "total": 0,
    "current_path": "",
    "summary": None,
    "thread": None,
    "cancel_event": None,
}
_dedup_scan_lock = register_monitored_lock("app_state._dedup_scan_lock", threading.Lock())


def get_lock_contention_report():
    """Returns mutex contention and lock queue telemetry report (Row 1059)."""
    return get_global_contention_report()


__all__ = [
    "runners",
    "runners_snapshot",
    "runners_generation",
    "s_cfg",
    "s_meta",
    "_watch_threads",
    "_watch_stops",
    "_watch_registry_lock",
    "_sites_config_save_lock",
    "site_lifecycle_lock",
    "all_site_lifecycle_locks",
    "_pairing_tokens",
    "_pairing_lock",
    "_dedup_scan_state",
    "_dedup_scan_lock",
    "get_lock_contention_report",
]
