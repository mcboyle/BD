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
