"""Bit-rot detection scanner (Phase 105, Block N).

Builds on Phase 104's provenance ledger. Every download recorded with
a SHA-256 hash is a candidate for periodic re-verification. Silent
corruption — bit flips on the storage device, partial writes, fs
metadata desync, ransomware encryption — is invisible until you try
to play a file that's been rotting for months.

Strategy:
  • Sweep a configurable fraction of the library per night (default 5%)
  • For each candidate, recompute SHA-256 and compare to the
    ledger value
  • Mismatch → record a 'bitrot' row in a separate `integrity_issues`
    table, notify operator, optionally auto-replace from a configured
    backup mirror
  • Quotas: cap CPU/IO impact by limiting concurrent rehashes and
    pausing during high-load periods

Schema (lazy creation):
  integrity_issues(
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    provenance_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    expected_sha256 TEXT,
    actual_sha256 TEXT,
    kind TEXT,                  -- 'missing' | 'modified' | 'truncated'
    repaired INTEGER DEFAULT 0,
    notes TEXT
  )

Operator config:
  • bitrot_scan_enabled: bool
  • bitrot_scan_fraction: float (0.0 - 1.0, default 0.05 = 5%/run)
  • bitrot_min_age_days: int (skip files <N days old; recent files
    just downloaded are pointless to re-verify)
  • bitrot_max_files_per_run: int (hard cap on work per invocation)

The scan is read-only on the data plane; the only DB writes are to
`integrity_issues` and the provenance row's verification timestamp.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from collections.abc import Callable


class ScrubberState:
    IDLE = "idle"
    SCRUBBING = "scrubbing"
    PAUSED = "paused"
    STOPPED = "stopped"
    UNAVAILABLE = "unavailable"


class PacingMode:
    NOMINAL = "nominal"
    THROTTLED = "throttled"
    PAUSED_HIGH_LOAD = "paused_high_load"


class AdaptiveIOPacer:
    """I/O rate pacer with dynamic system load adaptation.

    Controls byte read throughput during background verification to avoid
    starving foreground downloads or saturating disk I/O. Dynamically
    adjusts rate or pauses when system load exceeds thresholds.
    """

    def __init__(
        self,
        nominal_bytes_per_sec: float = 10 * 1024 * 1024,
        throttle_bytes_per_sec: float = 1 * 1024 * 1024,
        high_load_threshold: float = 0.80,
        pause_load_threshold: float = 0.95,
        load_getter: Callable[[], float] | None = None,
        burst_seconds: float = 0.1,
    ) -> None:
        self.nominal_bytes_per_sec = float(nominal_bytes_per_sec)
        self.throttle_bytes_per_sec = float(throttle_bytes_per_sec)
        self.high_load_threshold = float(high_load_threshold)
        self.pause_load_threshold = float(pause_load_threshold)
        self.burst_seconds = burst_seconds
        self._load_getter = load_getter

        self._rate = self.nominal_bytes_per_sec
        self._capacity = max(self._rate * self.burst_seconds, 65536.0)
        self._tokens = self._capacity
        self._last_update = time.perf_counter()
        self._last_load_check = 0.0
        self._current_load: float | None = None
        self._mode = PacingMode.NOMINAL
        self._lock = threading.Lock()
        self._total_bytes_paced = 0
        self._total_sleep_time = 0.0

    def get_current_load(self) -> float:
        if self._load_getter is not None:
            return float(self._load_getter())
        try:
            load1, _, _ = os.getloadavg()
            cpus = os.cpu_count() or 1
            return load1 / cpus
        except Exception:
            return 0.0

    def set_nominal_rate(self, bytes_per_sec: float) -> None:
        with self._lock:
            self.nominal_bytes_per_sec = max(1024.0, float(bytes_per_sec))
            if self._mode == PacingMode.NOMINAL:
                self._rate = self.nominal_bytes_per_sec
                self._capacity = max(self._rate * self.burst_seconds, 65536.0)

    def _update_mode_and_rate(self, now: float) -> None:
        if now - self._last_load_check < 1.0 and self._current_load is not None:
            return
        self._last_load_check = now
        load = self.get_current_load()
        self._current_load = load

        if load >= self.pause_load_threshold:
            self._mode = PacingMode.PAUSED_HIGH_LOAD
            self._rate = 0.0
        elif load >= self.high_load_threshold:
            self._mode = PacingMode.THROTTLED
            self._rate = self.throttle_bytes_per_sec
            self._capacity = max(self._rate * self.burst_seconds, 65536.0)
        else:
            self._mode = PacingMode.NOMINAL
            self._rate = self.nominal_bytes_per_sec
            self._capacity = max(self._rate * self.burst_seconds, 65536.0)

    def pace_read(
        self,
        nbytes: int,
        *,
        block: bool = True,
        stop_event: threading.Event | None = None,
    ) -> float:
        if nbytes <= 0:
            return 0.0

        total_slept = 0.0
        while True:
            if stop_event is not None and stop_event.is_set():
                return total_slept

            with self._lock:
                now = time.perf_counter()
                self._update_mode_and_rate(now)

                if self._mode == PacingMode.PAUSED_HIGH_LOAD:
                    sleep_time = 0.2
                    wait_pause = True
                else:
                    wait_pause = False
                    elapsed = now - self._last_update
                    self._last_update = now
                    if self._rate > 0:
                        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)

                    if self._tokens >= nbytes:
                        self._tokens -= nbytes
                        self._total_bytes_paced += nbytes
                        return total_slept

                    needed = nbytes - self._tokens
                    sleep_time = needed / self._rate if self._rate > 0 else 0.1
                    if not block:
                        return sleep_time
                    # Admit the read now and sleep off the debt below. This is
                    # its one charge: a PAUSED_HIGH_LOAD loop admits nothing and
                    # charges nothing, so a held read is charged once, after the wait.
                    self._tokens -= nbytes
                    self._total_bytes_paced += nbytes

            if not block:
                return sleep_time

            if wait_pause:
                time.sleep(sleep_time)
                total_slept += sleep_time
                self._total_sleep_time += sleep_time
            else:
                remaining_sleep = sleep_time
                while remaining_sleep > 0:
                    if stop_event is not None and stop_event.is_set():
                        return total_slept
                    slice_sleep = min(remaining_sleep, 0.25)
                    time.sleep(slice_sleep)
                    total_slept += slice_sleep
                    self._total_sleep_time += slice_sleep
                    remaining_sleep -= slice_sleep
                break

        return total_slept

    def status(self) -> dict:
        with self._lock:
            load = self._current_load if self._current_load is not None else self.get_current_load()
            return {
                "mode": self._mode,
                "nominal_bytes_per_sec": self.nominal_bytes_per_sec,
                "effective_bytes_per_sec": self._rate,
                "current_load": round(load, 3),
                "throttled": self._mode != PacingMode.NOMINAL,
                "paused_high_load": self._mode == PacingMode.PAUSED_HIGH_LOAD,
                "total_bytes_paced": self._total_bytes_paced,
            }


def compute_sha256_paced(
    path: str,
    *,
    pacer: AdaptiveIOPacer | None = None,
    chunk_size: int = 64 * 1024,
    on_progress: Callable[[int, int], None] | None = None,
    stop_event: threading.Event | None = None,
) -> str | None:
    """Stream a file through SHA-256 with adaptive I/O pacing.

    Reads in `chunk_size` chunks, pacing through `pacer` if provided.
    Calls `on_progress(bytes_read, total_size)` after each chunk.
    Respects `stop_event` for clean cancellation.
    Returns hex digest string, or None on error/abort.
    """
    try:
        total_size = os.path.getsize(path)
        bytes_read = 0
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                if stop_event is not None and stop_event.is_set():
                    return None
                buf = f.read(chunk_size)
                if not buf:
                    break
                h.update(buf)
                bytes_read += len(buf)
                if pacer is not None:
                    pacer.pace_read(len(buf), stop_event=stop_event)
                if on_progress is not None:
                    on_progress(bytes_read, total_size)
        return h.hexdigest()
    except Exception as e:
        import sys
        sys.stderr.write(f"[bitrot] compute_sha256_paced failed on {path}: {e}\n")
        return None


class ContinuousBitRotScrubber:
    """Continuous background bit-rot scrubber with adaptive pacing.

    Continuously iterates over stored provenance entries, verifying hashes
    using the AdaptiveIOPacer to ensure low system impact.
    """

    def __init__(self, pacer: AdaptiveIOPacer | None = None) -> None:
        self.pacer = pacer or AdaptiveIOPacer()
        self.state = ScrubberState.IDLE
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._lock = threading.Lock()

        # Telemetry
        self.current_file: str | None = None
        self.current_file_bytes: int | None = None
        self.current_file_total_bytes: int | None = None
        self.bytes_scrubbed_session: int | None = None
        self.files_scrubbed_session: int = 0
        self.bytes_per_sec: float | None = None
        self.eta_seconds: float | None = None
        self.last_scrub_ts: float | None = None
        self._session_start_ts: float | None = None
        self._download_dirs: list[str] = []

    def start(
        self,
        *,
        continuous: bool = True,
        download_dirs: list[str] | None = None,
        min_age_days: int = 7,
        reverify_after_days: int = 90,
        batch_size: int = 25,
        idle_sleep_seconds: float = 30.0,
    ) -> bool:
        with self._lock:
            if self.state == ScrubberState.SCRUBBING:
                return True
            if (self.state == ScrubberState.PAUSED
                    and self._thread is not None and self._thread.is_alive()):
                # pause() parks the live loop on _pause_event: resume it. A
                # second Thread would run two scrub loops over the same
                # candidates through one pacer.
                self._pause_event.set()
                self.state = ScrubberState.SCRUBBING
                return True
            if download_dirs is not None:
                self._download_dirs = list(download_dirs)
            else:
                try:
                    import importlib
                    s_cfg = getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg", {})
                    from . import library_final as _lf
                    roots = _lf.download_roots(s_cfg)
                    if roots or not self._download_dirs:
                        self._download_dirs = roots
                except Exception:
                    if not self._download_dirs:
                        self._download_dirs = []

            if not self._download_dirs:
                self.state = ScrubberState.UNAVAILABLE
                return False

            self._stop_event.clear()
            self._pause_event.set()
            self.state = ScrubberState.SCRUBBING
            self._session_start_ts = time.time()
            self.bytes_scrubbed_session = 0
            self.files_scrubbed_session = 0

            self._thread = threading.Thread(
                target=self._scrub_loop,
                kwargs={
                    "continuous": continuous,
                    "min_age_days": min_age_days,
                    "reverify_after_days": reverify_after_days,
                    "batch_size": batch_size,
                    "idle_sleep_seconds": idle_sleep_seconds,
                },
                daemon=True,
                name="bd-bitrot-scrubber",
            )
            self._thread.start()
            return True

    def pause(self) -> None:
        with self._lock:
            self._pause_event.clear()
            self.state = ScrubberState.PAUSED

    def resume(self) -> None:
        with self._lock:
            if self.state == ScrubberState.PAUSED:
                self._pause_event.set()
                self.state = (
                    ScrubberState.SCRUBBING
                    if (self._thread and self._thread.is_alive())
                    else ScrubberState.IDLE
                )

    def stop(self, timeout: float = 2.0) -> None:
        with self._lock:
            self._stop_event.set()
            self._pause_event.set()
            t = self._thread
            self._thread = None
            self.state = ScrubberState.STOPPED
            self.current_file = None
            self.current_file_bytes = None
            self.current_file_total_bytes = None
            self.bytes_per_sec = None
            self.eta_seconds = None
        if t is not None and t.is_alive():
            t.join(timeout=timeout)

    def scrub_once(
        self,
        *,
        download_dirs: list[str] | None = None,
        min_age_days: int = 7,
        reverify_after_days: int = 90,
        scan_fraction: float = 1.0,
        max_files: int = 10,
    ) -> dict:
        """Synchronously scrub up to max_files using paced verification."""
        roots = download_dirs if download_dirs is not None else self._download_dirs
        return run_scan(
            scan_fraction=scan_fraction,
            min_age_days=min_age_days,
            reverify_after_days=reverify_after_days,
            max_files=max_files,
            download_dirs=roots,
            pacer=self.pacer,
        )

    def _scrub_loop(
        self,
        continuous: bool,
        min_age_days: int,
        reverify_after_days: int,
        batch_size: int,
        idle_sleep_seconds: float,
    ) -> None:
        from . import library_final as _lf
        while not self._stop_event.is_set():
            self._pause_event.wait()
            if self._stop_event.is_set():
                break

            try:
                candidates = _candidates(
                    min_age_days=min_age_days,
                    reverify_after_days=reverify_after_days,
                    limit=batch_size,
                )
            except Exception:
                candidates = []

            if not candidates:
                if not continuous:
                    break
                self._stop_event.wait(timeout=idle_sleep_seconds)
                continue

            roots = self._download_dirs
            if not roots:
                self._stop_event.wait(timeout=idle_sleep_seconds)
                continue

            index = _lf._basename_index(roots) if roots else {}
            measured_in_batch = 0

            for row in candidates:
                if self._stop_event.is_set():
                    break
                self._pause_event.wait()

                filename = row.get("final_filename") or ""
                self.current_file = filename
                self.current_file_bytes = 0
                self.current_file_total_bytes = int(row.get("file_size") or 0)

                def on_progress(bytes_done, total):
                    self.current_file_bytes = bytes_done
                    self.current_file_total_bytes = total
                    if self._session_start_ts and self.bytes_scrubbed_session:
                        dur = max(0.001, time.time() - self._session_start_ts)
                        self.bytes_per_sec = round(self.bytes_scrubbed_session / dur, 1)

                try:
                    res = verify_one(
                        row,
                        download_dir=roots,
                        index=index,
                        pacer=self.pacer,
                        on_progress=on_progress,
                        stop_event=self._stop_event,
                    )
                    kind = res.get("kind", "")
                    if kind in ("intact", "modified", "truncated"):
                        measured_in_batch += 1
                        self.files_scrubbed_session += 1
                        file_sz = int(row.get("file_size") or 0)
                        if self.bytes_scrubbed_session is not None:
                            self.bytes_scrubbed_session += file_sz
                        self.last_scrub_ts = time.time()
                except Exception as e:
                    import sys
                    sys.stderr.write(f"[bitrot] scrubber error on {filename}: {e}\n")

                self.current_file = None
                self.current_file_bytes = None
                self.current_file_total_bytes = None

            if not continuous:
                break

            if measured_in_batch == 0:
                self._stop_event.wait(timeout=idle_sleep_seconds)
            else:
                self._stop_event.wait(timeout=0.05)

        with self._lock:
            if self._thread is threading.current_thread():
                # Deregister before this thread ends, so start() spawns a
                # fresh loop rather than resuming one that has finished.
                self._thread = None
            if self.state != ScrubberState.PAUSED:
                self.state = ScrubberState.IDLE
            self.current_file = None
            self.current_file_bytes = None
            self.current_file_total_bytes = None

    def status(self) -> dict:
        with self._lock:
            st = self.state
            disposition = (
                "active" if st == ScrubberState.SCRUBBING
                else "paused" if st == ScrubberState.PAUSED
                else "unavailable" if st == ScrubberState.UNAVAILABLE
                else "idle"
            )
            is_active = (st == ScrubberState.SCRUBBING)
            return {
                "ok": st != ScrubberState.UNAVAILABLE,
                "state": st,
                "running": is_active,
                "paused": st == ScrubberState.PAUSED,
                "disposition": disposition,
                "pacer": self.pacer.status(),
                "current_file": self.current_file if is_active else None,
                "current_file_bytes": self.current_file_bytes if is_active else None,
                "current_file_total_bytes": self.current_file_total_bytes if is_active else None,
                "bytes_scrubbed_session": self.bytes_scrubbed_session if is_active else None,
                "files_scrubbed_session": self.files_scrubbed_session,
                "bytes_per_sec": self.bytes_per_sec if is_active else None,
                "eta_seconds": self.eta_seconds if is_active else None,
                "last_scrub_ts": self.last_scrub_ts,
            }


_SCRUBBER_INSTANCE: ContinuousBitRotScrubber | None = None
_SCRUBBER_LOCK = threading.Lock()


def get_scrubber() -> ContinuousBitRotScrubber:
    """Return the global ContinuousBitRotScrubber singleton."""
    global _SCRUBBER_INSTANCE
    if _SCRUBBER_INSTANCE is None:
        with _SCRUBBER_LOCK:
            if _SCRUBBER_INSTANCE is None:
                _SCRUBBER_INSTANCE = ContinuousBitRotScrubber()
    return _SCRUBBER_INSTANCE


def reset_scrubber() -> ContinuousBitRotScrubber:
    """Reset the global scrubber singleton (used in test isolation).

    A failed stop() of the old scrubber propagates and leaves it installed:
    swapping in a fresh instance over one whose thread was never joined
    would hide the failure.
    """
    global _SCRUBBER_INSTANCE
    with _SCRUBBER_LOCK:
        if _SCRUBBER_INSTANCE is not None:
            _SCRUBBER_INSTANCE.stop(timeout=1.0)
        _SCRUBBER_INSTANCE = ContinuousBitRotScrubber()
    return _SCRUBBER_INSTANCE


def _ensure_integrity_table():
    """Lazy schema creation for integrity_issues.

    H542: provenance absent is handled silently. When the PRAGMA returns
    empty rows (table does not yet exist), the ALTER is skipped -- no log
    noise, no cross-module init call. The column is added on the next call
    once the main app's provenance._ensure_table() has run.
    """
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS integrity_issues(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                provenance_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                expected_sha256 TEXT DEFAULT '',
                actual_sha256 TEXT DEFAULT '',
                kind TEXT NOT NULL,
                repaired INTEGER DEFAULT 0,
                notes TEXT DEFAULT ''
            )""")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_ii_kind ON integrity_issues(kind)")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_ii_ts ON integrity_issues(ts DESC)")
            # Also ensure provenance has the extra column we need to
            # avoid re-scanning recently-verified rows. Add if missing.
            #
            # Ask FIRST rather than guess from the failure. Until v3.66.931
            # this was a bare `except Exception: pass`, so every failure read
            # as "column already exists" -- a missing provenance table, a
            # locked database and a read-only mount all returned quietly and
            # the init reported a schema it had not created. _candidates then
            # SELECTs last_verified_ts (below), so the real fault surfaced
            # later and somewhere else.
            #
            # The SQLite result code cannot separate these: `duplicate column
            # name` and `no such table` are BOTH SQLITE_ERROR (1), measured.
            # So the structural check carries the common path, and the
            # message-matched tolerance underneath it covers only the genuine
            # race where two processes pass the check and both ALTER.
            have = {r[1] for r in cx.execute("PRAGMA table_info(provenance)")}
            if have and "last_verified_ts" not in have:  # H542: skip if absent

                try:
                    cx.execute("ALTER TABLE provenance "
                               "ADD COLUMN last_verified_ts REAL DEFAULT 0")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e).lower():
                        raise
    except Exception as e:
        import sys
        sys.stderr.write(f"[bitrot] schema init failed: {e}\n")


class InventoryUnavailable(RuntimeError):
    """The integrity inventory could not be measured."""


def _table_is_absent(error: sqlite3.OperationalError, table: str) -> bool:
    """Return whether SQLite reported one exact, expected schema absence."""
    return str(error).casefold() == f"no such table: {table}".casefold()


def _candidates(
    *,
    min_age_days: int,
    limit: int,
    reverify_after_days: int = 90,
) -> list[dict]:
    """Pick rows due for verification: have a sha256, are old enough,
    and either never verified or verified > reverify_after_days ago."""
    _ensure_integrity_table()
    try:
        from . import db as _db
        cutoff_old = time.time() - (min_age_days * 86400)
        cutoff_reverify = time.time() - (reverify_after_days * 86400)
        with _db.db_conn() as cx:
            rows = cx.execute("""
                SELECT id, source_url, final_filename, file_size, sha256,
                       last_verified_ts, ts
                FROM provenance
                WHERE sha256 != ''
                  AND ts <= ?
                  AND (last_verified_ts = 0 OR last_verified_ts < ?)
                ORDER BY RANDOM()
                LIMIT ?
            """, (cutoff_old, cutoff_reverify, limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        import sys
        sys.stderr.write(f"[bitrot] candidate query failed: {e}\n")
        raise InventoryUnavailable(f"candidate inventory unreadable: {e}") from e


def _mark_verified(prov_id: int):
    """Stamp last_verified_ts on the provenance row."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("UPDATE provenance SET last_verified_ts = ? WHERE id = ?",
                       (time.time(), int(prov_id)))
    except Exception:
        pass


def _record_issue(*, provenance_id: int, path: str, expected: str,
                  actual: str, kind: str, notes: str = ""):
    """Append one integrity_issues row, unless that finding is already open.

    v3.66.925 -- the dedup half. A non-intact verdict returns before
    _mark_verified, so the row keeps its last_verified_ts of 0 and _candidates
    re-selects it on the very next scan. Without this guard ONE genuinely
    missing file adds one row per night, forever: `stats()` counts unrepaired
    rows as `open_issues`, so alerts_engine.py:75's `bitrot_growing` rule fires
    on a library whose only fault is a single file that has been gone for a
    week and is being re-noticed nightly.

    Keyed on (provenance_id, kind) rather than including the path, so the same
    finding stays one row even if download_dir moves. `repaired=0` is part of
    the key on purpose: once the operator resolves an issue, a later
    recurrence opens a FRESH row, which is the signal they want.
    """
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            already_open = cx.execute(
                """SELECT 1 FROM integrity_issues
                   WHERE provenance_id = ? AND kind = ? AND repaired = 0
                   LIMIT 1""", (int(provenance_id), kind)).fetchone()
            if already_open:
                return
            cx.execute("""INSERT INTO integrity_issues(
                ts, provenance_id, path, expected_sha256, actual_sha256,
                kind, notes
            ) VALUES (?,?,?,?,?,?,?)""",
                (time.time(), int(provenance_id), path,
                 expected or "", actual or "", kind, notes[:500]))
    except Exception as e:
        import sys
        sys.stderr.write(f"[bitrot] record_issue failed: {e}\n")


def verify_one(row: dict, *, download_dir: str | list[str] | tuple[str, ...] = "",
               index: dict | None = None,
               pacer: AdaptiveIOPacer | None = None,
               on_progress: Callable[[int, int], None] | None = None,
               stop_event: threading.Event | None = None) -> dict:
    """Verify one provenance row's file against its recorded hash.

    Returns {ok, kind, message}:
      ok=True,  kind='intact':    hash matches
      ok=False, kind='missing':   file genuinely gone from disk
      ok=False, kind='modified':  hash mismatch
      ok=False, kind='truncated': size mismatch (caught before hash)
      ok=False, kind='error':     couldn't read the file
      ok=False, kind='ambiguous': several files share the recorded basename
      ok=False, kind='unknown':   no download_dir to resolve against
      ok=False, kind='aborted':   stop_event was set before the hash finished

    Records an integrity_issues row for missing/modified/truncated/error.
    Records NOTHING for ambiguous/unknown/aborted. Stamps last_verified_ts on intact.

    v3.66.925 -- `final_filename` IS A BARE BASENAME. runner.py:2040 records
    `extra["filename"]`, and runner_transport.py:1297 shows that key is the
    basename ("path" is the separate key holding the full path). This function
    fed it to `Path(path).exists()`, which resolves against the PROCESS CWD, so
    every relative row missed and took the branch that WRITES a "your file is
    gone" row for a file sitting on disk.

    That made this the only producer in item 12 that persisted its wrong
    answer, and it did not converge: the missing branch returns before
    _mark_verified, so last_verified_ts stayed 0, _candidates re-selected the
    same rows the next night, and the false rows were written again. Measured
    before the fix -- three present files, three consecutive scans, 3 -> 6 -> 9
    rows. bg_scheduler.py:254 runs it nightly and alerts_engine.py:75 alarms on
    exactly that growth, so the system reported its own bookkeeping as rot.

    Resolution goes through library_final._resolve_recorded rather than a sixth
    hand-rolled `download_dir / fn` join -- a flat join cannot find a file the
    filename template put in a subdirectory, since the recorded basename has
    already lost it. Its "ambiguous" and "unknown" states are deliberately NOT
    folded into "missing": a row this scanner cannot place is not evidence of
    rot, and guessing first-match-wins would hash the wrong twin and report a
    modification that is an artefact of the guess.
    """
    from .library_final import _basename_index, _resolve_recorded

    recorded = row.get("final_filename") or ""
    if index is None:
        index = _basename_index(download_dir)
    p, state = _resolve_recorded(recorded, download_dir, index)
    if state == "ambiguous":
        return {"ok": False, "kind": "ambiguous",
                "message": f"several files match {recorded!r}; refusing to guess"}
    if state == "unknown":
        return {"ok": False, "kind": "unknown",
                "message": f"nothing to resolve {recorded!r} against"}
    if state == "absent" or p is None:
        shown = str(p) if p is not None else recorded
        _record_issue(provenance_id=row["id"], path=shown,
                      expected=row.get("sha256", ""), actual="",
                      kind="missing")
        return {"ok": False, "kind": "missing",
                "message": f"file gone: {shown}"}
    path = str(p)
    expected_size = int(row.get("file_size", 0) or 0)
    try:
        actual_size = p.stat().st_size
    except OSError as e:
        _record_issue(provenance_id=row["id"], path=path,
                      expected=row.get("sha256", ""), actual="",
                      kind="error", notes=f"stat: {e}")
        return {"ok": False, "kind": "error", "message": str(e)}
    if expected_size > 0 and actual_size != expected_size:
        _record_issue(provenance_id=row["id"], path=path,
                      expected=row.get("sha256", ""), actual="",
                      kind="truncated",
                      notes=f"expected {expected_size} got {actual_size}")
        return {"ok": False, "kind": "truncated",
                "message": f"size {actual_size} ≠ recorded {expected_size}"}
    try:
        if pacer is None and on_progress is None and stop_event is None:
            from .provenance import compute_sha256
            actual_hash = compute_sha256(str(p))
        else:
            actual_hash = compute_sha256_paced(
                str(p),
                pacer=pacer,
                on_progress=on_progress,
                stop_event=stop_event,
            )
    except Exception as e:
        _record_issue(provenance_id=row["id"], path=path,
                      expected=row.get("sha256", ""), actual="",
                      kind="error", notes=f"hash: {e}")
        return {"ok": False, "kind": "error", "message": str(e)}
    if actual_hash is None:
        if stop_event is not None and stop_event.is_set():
            # A stop mid-file says nothing about the file. Record no issue and
            # leave last_verified_ts alone, so the next pass measures it again.
            return {"ok": False, "kind": "aborted",
                    "message": "verification aborted: stop requested"}
        _record_issue(provenance_id=row["id"], path=path,
                      expected=row.get("sha256", ""), actual="",
                      kind="error", notes="hash computation failed")
        return {"ok": False, "kind": "error", "message": "hash computation failed"}
    expected = row.get("sha256", "") or ""
    if actual_hash != expected:
        _record_issue(provenance_id=row["id"], path=path,
                      expected=expected, actual=actual_hash or "",
                      kind="modified")
        return {"ok": False, "kind": "modified",
                "message": f"hash {actual_hash[:8]} ≠ recorded {expected[:8]}"}
    _mark_verified(row["id"])
    return {"ok": True, "kind": "intact", "message": "hash matches"}


def run_scan(*,
            scan_fraction: float = 0.05,
            min_age_days: int = 7,
            max_files: int = 100,
            reverify_after_days: int = 90,
            download_dir: str = "",
            download_dirs=(),
            pacer: AdaptiveIOPacer | None = None) -> dict:
    """Verify a random subset of provenance rows. Returns summary.

    Designed to run from a nightly scheduler. The fraction × library
    size produces the candidate count, capped at max_files to keep
    each run bounded.

    `download_dir` is what a recorded BASENAME is resolved against; see
    verify_one. It is optional and defaults to "" so every existing caller
    keeps working -- but omitted, no relative row can be placed and the summary
    reports them under `unknown` rather than writing them off as missing.

    THAT NO-OP IS DELIBERATE AND IT IS VISIBLE. bg_scheduler.py:254 calls this
    with no download_dir, so until that call site is given one the nightly scan
    decides nothing on a relative library. A scan that reports `unknown: N` is
    saying it could not see its subject; the behaviour this replaced reported
    `missing: N` and persisted it, which is the same blindness wearing a
    verdict. Sourcing the configured roots belongs with the path-allowlist
    validation the library routes already do (app_library.py:262) and is a
    separate cut, not a line here.

    The index is built ONCE per scan rather than per row: a library is a few
    thousand files and a per-row rglob would make the nightly job quadratic.
    """
    def unavailable(error: Exception, *, total_library=None) -> dict:
        import sys
        sys.stderr.write(f"[bitrot] inventory unavailable: {error}\n")
        return {
            "ok": False,
            "available": False,
            "inventory_status": "unknown",
            "error": str(error)[:200],
            "checked": None,
            "intact": None,
            "missing": None,
            "modified": None,
            "truncated": None,
            "errors": None,
            "ambiguous": None,
            "unknown": None,
            "total_library": total_library,
        }

    _ensure_integrity_table()
    # Determine total candidate pool size to compute the fraction
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("""SELECT COUNT(*) AS n FROM provenance
                                 WHERE sha256 != ''""").fetchone()
        total = int(row[0] if not hasattr(row, "keys") else row["n"])
    except Exception as e:
        return unavailable(
            InventoryUnavailable(f"provenance inventory unreadable: {e}"))
    if total == 0:
        return {"ok": True, "available": True,
                "inventory_status": "measured",
                "checked": 0, "intact": 0, "missing": 0, "modified": 0,
                "truncated": 0, "errors": 0, "ambiguous": 0, "unknown": 0,
                "total_library": 0}
    target = max(1, int(total * scan_fraction))
    target = min(target, max_files)
    try:
        candidates = _candidates(min_age_days=min_age_days, limit=target,
                                reverify_after_days=reverify_after_days)
    except InventoryUnavailable as e:
        return unavailable(e, total_library=total)
    # Additive keys, verified rather than assumed: no test references this
    # module, `/api/bitrot/scan` hands the dict straight to jsonify
    # (app_bitrot.py:28), and frontend/src/lib/api-types.ts declares no bitrot
    # scan type -- so `ambiguous` and `unknown` cost zero TS edits. They are
    # _resolve_recorded's own state strings verbatim, matching the v3.66.916
    # precedent, so a reader maps counter to state with no translation step.
    summary = {"ok": True, "available": True,
               "inventory_status": "measured",
               "checked": 0, "intact": 0, "missing": 0, "modified": 0,
               "truncated": 0, "errors": 0, "ambiguous": 0, "unknown": 0,
               "total_library": total}
    from . import library_final as _lf
    # `download_dirs` is the multi-site form and `download_dir` the original
    # single one; a caller may pass either. Both are threaded to verify_one so
    # the flat join and the index agree on the same root set -- handing the
    # index every root while verify_one saw only one would resolve a row the
    # per-row call could not place.
    roots = list(download_dirs) or ([download_dir] if download_dir else [])
    index = _lf._basename_index(roots)
    for row in candidates:
        try:
            r = verify_one(row, download_dir=roots, index=index, pacer=pacer)
            summary["checked"] += 1
            kind = r.get("kind", "error")
            if kind in summary:
                summary[kind] += 1
            else:
                summary["errors"] += 1
        except Exception as e:
            summary["errors"] += 1
            import sys
            sys.stderr.write(f"[bitrot] verify {row.get('id')} raised: {e}\n")
    return summary


def list_issues(*, kind: str | None = None, repaired: bool | None = None,
                limit: int = 100) -> list:
    """Return recent integrity_issues rows. Filter by kind ('missing',
    'modified', 'truncated', 'error') and repaired status.

    Raises InventoryUnavailable when the query cannot be measured; ``[]`` is
    reserved for a successful query with no matching issues.
    """
    _ensure_integrity_table()
    sql = "SELECT * FROM integrity_issues WHERE 1=1"
    params: list = []
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if repaired is not None:
        sql += " AND repaired = ?"
        params.append(1 if repaired else 0)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(int(limit))
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            return [dict(r) for r in cx.execute(sql, params).fetchall()]
    except Exception as e:
        # [] is reserved for a successful query that measured no issues.
        # Callers must surface an unreadable inventory as unavailable instead
        # of serialising it as a clean inventory.
        raise InventoryUnavailable(
            f"integrity issue inventory unavailable: {e}"
        ) from e


def stats() -> dict:
    """Aggregate counters for the bit-rot dashboard."""
    out = {"ok": True, "available": True, "inventory_status": "measured",
           "error": "", "open_issues": 0, "by_kind": {}, "repaired": 0,
           "last_scan_ts": 0}
    try:
        out["scrubber"] = get_scrubber().status()
    except Exception:
        out["scrubber"] = {
            "ok": False,
            "state": ScrubberState.UNAVAILABLE,
            "disposition": "unavailable",
            "bytes_per_sec": None,
            "current_file_bytes": None,
        }
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            for row in cx.execute("""SELECT kind, COUNT(*) AS n
                                     FROM integrity_issues
                                     WHERE repaired = 0
                                     GROUP BY kind"""):
                out["by_kind"][row[0]] = int(row[1])
                out["open_issues"] += int(row[1])
            row = cx.execute("""SELECT COUNT(*) AS n FROM integrity_issues
                                 WHERE repaired = 1""").fetchone()
            out["repaired"] = int(row[0]) if row else 0
            try:
                row = cx.execute("""SELECT MAX(last_verified_ts) AS t
                                     FROM provenance""").fetchone()
            except sqlite3.OperationalError as e:
                if not _table_is_absent(e, "provenance"):
                    raise
                row = None
            out["last_scan_ts"] = float(row[0] or 0) if row else 0.0
    except sqlite3.OperationalError as e:
        # A fresh install has no inventory table until the first scan.  That
        # absence is a measured empty inventory, not a failed measurement.
        # Keep every other SQLite operational failure on the fail-closed path
        # below: locked, read-only, and otherwise unreadable stores are not
        # evidence of zero open issues.
        if _table_is_absent(e, "integrity_issues"):
            return out
        return {
            "ok": False,
            "available": False,
            "inventory_status": "unknown",
            "error": f"integrity issue inventory unreadable: {e}"[:200],
            "open_issues": None,
            "by_kind": None,
            "repaired": None,
            "last_scan_ts": None,
            "scrubber": {
                "ok": False,
                "state": ScrubberState.UNAVAILABLE,
                "disposition": "unavailable",
                "bytes_per_sec": None,
                "current_file_bytes": None,
            },
        }
    except Exception as e:
        return {
            "ok": False,
            "available": False,
            "inventory_status": "unknown",
            "error": f"integrity issue inventory unreadable: {e}"[:200],
            "open_issues": None,
            "by_kind": None,
            "repaired": None,
            "last_scan_ts": None,
            "scrubber": {
                "ok": False,
                "state": ScrubberState.UNAVAILABLE,
                "disposition": "unavailable",
                "bytes_per_sec": None,
                "current_file_bytes": None,
            },
        }
    return out


def mark_repaired(issue_id: int, *, notes: str = "") -> bool:
    """Flag an integrity_issue as resolved. Caller might re-download
    the file, restore from backup, or accept the drift."""
    _ensure_integrity_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("""UPDATE integrity_issues SET repaired = 1,
                              notes = COALESCE(notes,'') || ?
                              WHERE id = ?""",
                              (f" | repaired {time.strftime('%Y-%m-%d')}: {notes}"[:500],
                               int(issue_id)))
            return cur.rowcount > 0
    except Exception:
        return False
