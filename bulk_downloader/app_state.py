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

import os
import threading

runners = {}
s_cfg = {}
s_meta = {}
_pairing_tokens: dict = {}      # pairing_token → {"created", "expires_at"}
_pairing_lock = threading.Lock()
_watch_threads = {}   # {site_id: Thread}
_watch_stops = {}     # {site_id: Event}
# Serializes each watch worker's runner/stop/thread lifecycle.  Callers that
# also hold app._BOOT_LOCK must acquire that lock first; targets never acquire
# _BOOT_LOCK, so their identity-checked cleanup cannot invert the order.
_watch_registry_lock = threading.RLock()
# A config save owns one snapshot-to-replace transaction across every site.
# Per-site lifecycle stripes cannot protect the shared file: two different
# site requests may otherwise race a fixed .tmp path or let an older snapshot
# replace a newer delete.  Fixed process-wide ownership is also memory-bounded.
_sites_config_save_lock = threading.RLock()
# Fixed stripes serialize every request that observes or mutates one site's
# identity. Delete holds the same lock through writer retirement and teardown,
# so a route cannot snapshot a runner/config before delete and commit after it.
# Stripes (rather than an attacker-keyed dict) keep unknown IDs memory-bounded.
_site_lifecycle_locks = tuple(threading.RLock() for _ in range(64))


def site_lifecycle_lock(site_id: str):
    return _site_lifecycle_locks[
        hash(str(site_id)) % len(_site_lifecycle_locks)]


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
_dedup_scan_lock = threading.Lock()
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
    "_pairing_tokens",
    "_pairing_lock",
    "_dedup_scan_state",
    "_dedup_scan_lock",
]
