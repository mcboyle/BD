"""Durable operator hold on downloading (backlog row 390).

WHY THIS EXISTS. On 2026-08-29 BD saved 5.1 GB of the WRONG scene and recorded
the row as ``done``. The operator held downloading across every host that can
download -- by calling ``/api/pause_all``, which reaches ``runner.pause()`` and
lives only in process memory. Anything that restarts the service (a crash, a
reboot, systemd ``RestartSec``, or ``scripts/deploy.sh``, which restarts the app
on EVERY deployment) silently re-arms unattended downloading. The measured state
on the held hosts that day was ``paused: false`` / ``state: idle`` with 53 URLs
still queued on one of them: nothing durable recorded that a hold was intended.

So the hold is recorded in the SUPPORTED durable store -- ``app_config.json``,
the same file that already carries every user-tunable setting that must survive
restart -- and re-applied by ``runner.start()`` / ``runner.resume()``, which are
the only paths into a worker pool. No new sidecar file is invented.

FAIL CLOSED. This module is the deliberate INVERSE of ``admission.py``, whose
docstring pins a fail-OPEN contract because admission is a convenience gate. A
hold is a SAFETY gate: an unavailable measurement is UNKNOWN, and UNKNOWN keeps
downloads STOPPED (CLAUDE.md A7). Concretely, ``hold_state()`` returns CLEAR
only on positive evidence that the store was read and carries no hold:

  store file absent          -> CLEAR   (fresh install; no hold ever recorded)
  store unreadable / EACCES  -> UNKNOWN (refuse)
  store is not valid JSON    -> UNKNOWN (refuse)
  store JSON is not an object-> UNKNOWN (refuse)
  no ``download_hold`` key   -> CLEAR   (host was never held)
  record is not an object    -> UNKNOWN (refuse)
  record ``held`` missing or
    not a real bool          -> UNKNOWN (refuse)
  record ``held`` is True    -> HELD    (refuse)
  record ``held`` is False   -> CLEAR   (explicitly lifted)
  any unanticipated error    -> UNKNOWN (refuse)

MISSING STORE IS CLEAR, ON PURPOSE. A brand-new host has no ``app_config.json``
and has never been held; treating its absence as UNKNOWN would refuse every
download on every fresh install and in every test tmpdir. The safety property
that matters is that a RECORDED hold cannot be laundered into "no hold, carry
on" -- which is why ``lift()`` WRITES ``held: false`` rather than deleting the
key, so a hold is never cleared by absence.

DELIBERATELY NOT IN ``GLOBAL_CONFIG_SCHEMA``. ``global_config.get_config()``
runs ``apply_fail_closed`` over declared keys, which would rewrite a malformed
value to its ``safe_default`` -- turning a corrupt hold record into a confident
"not held". The reader below therefore parses the store file itself and never
consults the cached/validated view.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

# Key inside app_config.json.
HOLD_KEY = "download_hold"

# The three states. UNKNOWN is a failing third state, never permission.
HELD = "held"
CLEAR = "clear"
UNKNOWN = "unknown"

# Runner state tokens published when a start/resume is refused. Distinct from
# each other and from every pre-existing token ("idle", "paused",
# "window_paused", "maintenance_paused", "cookies_expired", "low_disk", ...) so
# an operator can tell a hold apart from an empty queue and from a hold whose
# state could not be measured.
STATE_HELD = "download_held"
STATE_UNKNOWN = "download_hold_unknown"

DEFAULT_REASON = "operator"


def _store_path(path: Optional[os.PathLike | str] = None) -> Path:
    """Resolve the durable store. Defaults to global_config's canonical file."""
    if path is not None:
        return Path(path)
    try:
        from . import global_config as _gc
        return Path(getattr(_gc, "_CONFIG_FILE", "app_config.json"))
    except Exception:
        return Path("app_config.json")


def _result(state: str, *, reason: str = "", detail: str = "",
            since: Any = None, note: str = "", by: str = "") -> dict:
    return {
        "state": state,
        "held": state != CLEAR,      # UNKNOWN holds too -- fail closed.
        "reason": reason,
        "detail": detail,
        "since": since,
        "note": note,
        "by": by,
    }


def _read_record(path: Optional[os.PathLike | str]) -> dict:
    p = _store_path(path)
    try:
        exists = p.exists()
    except OSError as e:                      # e.g. EACCES on a parent dir
        return _result(UNKNOWN, reason="store_stat_failed",
                       detail=type(e).__name__)
    if not exists:
        # Fresh install: the store has never been written, so no hold has ever
        # been recorded. See MISSING STORE IS CLEAR above.
        return _result(CLEAR, reason="store_absent")
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as e:
        return _result(UNKNOWN, reason="store_unreadable",
                       detail=type(e).__name__)
    except UnicodeDecodeError as e:
        return _result(UNKNOWN, reason="store_undecodable",
                       detail=type(e).__name__)
    try:
        data = json.loads(raw)
    except Exception as e:
        return _result(UNKNOWN, reason="store_corrupt", detail=type(e).__name__)
    if not isinstance(data, dict):
        return _result(UNKNOWN, reason="store_not_object",
                       detail=type(data).__name__)
    if HOLD_KEY not in data:
        return _result(CLEAR, reason="no_record")
    rec = data[HOLD_KEY]
    if not isinstance(rec, dict):
        # A bare `true`/`"yes"`/`1` is NOT accepted as a lift or a hold: a
        # malformed record is unmeasurable, and unmeasurable refuses.
        return _result(UNKNOWN, reason="record_malformed",
                       detail=type(rec).__name__)
    held = rec.get("held")
    note = rec.get("note") if isinstance(rec.get("note"), str) else ""
    by = rec.get("by") if isinstance(rec.get("by"), str) else ""
    since = rec.get("since")
    if not isinstance(since, (int, float)) or isinstance(since, bool):
        since = None
    # `held` must be a real bool. Truthy strings ("false"!) and numbers are
    # ambiguous, and an ambiguous safety flag is UNKNOWN.
    if held is True:
        reason = rec.get("reason")
        return _result(HELD,
                       reason=(reason if isinstance(reason, str) and reason
                               else DEFAULT_REASON),
                       detail="record_held", since=since, note=note, by=by)
    if held is False:
        return _result(CLEAR, reason="record_lifted", since=since,
                       note=note, by=by)
    return _result(UNKNOWN, reason="record_held_malformed",
                   detail=type(held).__name__, since=since, note=note, by=by)


def hold_state(path: Optional[os.PathLike | str] = None) -> dict:
    """HELD / CLEAR / UNKNOWN for the durable download hold.

    Never raises: an unanticipated failure is UNKNOWN, which refuses. This
    module's own error path must not reproduce the fail-open shape it exists
    to prevent (CLAUDE.md A7, "every fix tends to reproduce the defect").
    """
    try:
        return _read_record(path)
    except Exception as e:                    # pragma: no cover - belt & braces
        return _result(UNKNOWN, reason="read_failed", detail=type(e).__name__)


def downloads_allowed(path: Optional[os.PathLike | str] = None) -> tuple[bool, dict]:
    """``(allowed, state)``. Allowed only when the store positively says CLEAR."""
    st = hold_state(path)
    return (st["state"] == CLEAR), st


def runner_state_token(state: dict) -> str:
    """The ``_state`` token a refused runner should publish for ``state``."""
    return STATE_UNKNOWN if state.get("state") == UNKNOWN else STATE_HELD


def _write_record(record: dict) -> bool:
    from . import global_config as _gc
    return bool(_gc.set_config({HOLD_KEY: record}))


def hold(reason: str = DEFAULT_REASON, note: str = "", *,
         by: str = "", now: Optional[float] = None) -> bool:
    """Record a durable hold. Returns True when the store was written."""
    record = {
        "held": True,
        "reason": str(reason or DEFAULT_REASON),
        "note": str(note or ""),
        "by": str(by or ""),
        "since": float(now if now is not None else time.time()),
    }
    return _write_record(record)


def lift(note: str = "", *, by: str = "", now: Optional[float] = None) -> bool:
    """Explicitly lift the hold, DURABLY.

    Writes ``held: false`` instead of deleting the key so that a lift is a
    positive, restart-surviving record and a hold is never cleared by the
    record simply going missing.
    """
    record = {
        "held": False,
        "reason": "lifted",
        "note": str(note or ""),
        "by": str(by or ""),
        "since": float(now if now is not None else time.time()),
    }
    return _write_record(record)


def health_block(state: Optional[dict] = None,
                 path: Optional[os.PathLike | str] = None) -> dict:
    """The ``download_hold`` block for /api/health.

    A held host SAYS SO while staying ``ok`` -- the hold is a deliberate
    operator action, and flipping /api/health to 503 would report every
    correctly-held host as unhealthy to the very deploy that ships this.
    UNKNOWN is a real degradation and is reported as such by the caller.
    """
    st = state if state is not None else hold_state(path)
    return {
        "state": st.get("state"),
        "downloads_allowed": st.get("state") == CLEAR,
        "reason": st.get("reason"),
        "detail": st.get("detail"),
        "since": st.get("since"),
        "note": st.get("note"),
        "by": st.get("by"),
    }
