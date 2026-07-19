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
    "s_cfg",
    "s_meta",
    "_watch_threads",
    "_watch_stops",
    "_pairing_tokens",
    "_pairing_lock",
    "_dedup_scan_state",
    "_dedup_scan_lock",
]
