"""bulk_downloader.blackbox_snapshotter -- Row 1076: Post-Crash Flight-Recorder Blackbox Snapshotter.

Provides an in-memory circular ring buffer of recent operational events, thread
frame inspection, process resource telemetry capture, and post-crash blackbox dumps
upon unhandled exceptions and fatal error conditions.
Follows Fleet Rule 21 (zero site logins touched).
"""
from __future__ import annotations

import collections
import datetime
import json
import logging
import os
import platform
import re
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .capture_artifact_redact import redact_value

log = logging.getLogger(__name__)

try:  # POSIX-only; without it the snapshot omits rusage and says why
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore[assignment]

#: Dumps kept on disk; the oldest beyond this are deleted after each write, so a
#: crash storm costs at most this many files, not one file per crash forever.
DEFAULT_MAX_DUMPS = 20
_DUMP_GLOB = "blackbox_crash_*.json"
#: blackbox_crash_<wall-clock ms>_<sequence number>_<pid>_<thread id>.json
_DUMP_NAME = re.compile(r"blackbox_crash_\d+_(\d+)_")

#: Container levels written of one captured value: flight events are a few levels
#: deep (log_event: data -> extra), but one nested thousands deep would drive the
#: redaction and json.dumps past the recursion limit, and every dump would fail
#: while that event is in the ring.  A deeper container is written as _TOO_DEEP.
_MAX_VALUE_DEPTH = 100
_TOO_DEEP = f"<nested deeper than {_MAX_VALUE_DEPTH} levels>"
_CYCLE = "<cycle>"


def _default_dump_dir() -> Path:
    """<the app's log directory>/blackbox (bulk_downloader.log._LOG_DIR)."""
    from . import log as _log
    return Path(_log._LOG_DIR) / "blackbox"


def _crash_cause(exc_info: Any) -> Any:
    """The exception that caused the crash, not Flask's 500 wrapper.

    Flask hands a 500 handler werkzeug's InternalServerError; the real
    exception is its ``original_exception``.  A wrapper without one falls back
    to the exception being handled (sys.exc_info()), if any.
    """
    if isinstance(exc_info, BaseException) and hasattr(exc_info, "original_exception"):
        original = getattr(exc_info, "original_exception", None)
        if isinstance(original, BaseException):
            return original
        current = sys.exc_info()[1]
        if current is not None and current is not exc_info:
            return current
    return exc_info


def _redact_text(value: Any) -> str:
    """The text of ``value`` (bytes decoded, anything else str()), redacted one line at a time.
    An object whose str() fails is written as that failure, not as a failed dump."""
    if isinstance(value, (bytes, bytearray)):
        text = value.decode("utf-8", "backslashreplace")
    else:
        try:
            text = str(value)
        except Exception as exc:  # a broken __str__: it raises, or returns no str
            text = f"<unprintable {type(value).__name__}: str() raised {type(exc).__name__}>"
    return "\n".join(redact_value(line) for line in text.split("\n"))


def _redact_lines(value: Any, _outer: Tuple[int, ...] = ()) -> Any:
    """A JSON-ready copy of a captured value with all of its text redacted, one line at a time.

    redact_value treats the string it is given as one value: handed a whole
    multi-line traceback that holds a '?' and a URL, it reads everything after the
    first '?' as a query string and can cut the rest of the traceback off at a
    chunk that merely looks like a secret key ('session_keeper.py ... resp =<scrubbed>').
    Line by line -- as diagnostics_bundle redacts the log files it ships -- a URL
    is always whole and a cut stays inside its own line.  For the same reason a set
    is walked item by item and bytes are decoded first: written as one repr line, a
    set of URLs or a multi-line body would lose everything after its first secret.
    Any other object is redacted as the text it is written as (str()), so one odd
    flight-event payload cannot abort the dump.  So is a dict key other than a
    number or None: json.dumps refuses a tuple key, and a key can carry a signed
    URL as well as a value can.  A container inside itself (``_outer``: the ids of
    the containers around this one) is written as _CYCLE, and one deeper than
    _MAX_VALUE_DEPTH as _TOO_DEEP.
    """
    if isinstance(value, (dict, list, tuple, set, frozenset)):
        if id(value) in _outer:
            return _CYCLE
        if len(_outer) >= _MAX_VALUE_DEPTH:
            return _TOO_DEEP
        outer = _outer + (id(value),)
        if isinstance(value, dict):
            return {key if key is None or isinstance(key, (int, float)) else _redact_text(key):
                    _redact_lines(item, outer) for key, item in value.items()}
        return [_redact_lines(item, outer) for item in value]
    if value is None or isinstance(value, (int, float)):
        return value
    return _redact_text(value)


def _dump_order(path: Path) -> Tuple[int, str]:
    """A dump's place in write order: its sequence number, then its name.

    Not the wall-clock ms the name starts with: after the clock steps back (NTP, a
    VM restore) the newest dumps' names sort first.  A name without a sequence
    number (not written by this module) sorts first, as the oldest.
    """
    match = _DUMP_NAME.match(path.name)
    return (int(match.group(1)) if match else 0, path.name)


def _durable_text(payload: Dict[str, Any]) -> str:
    """The dump as it is written to disk: every captured value redacted (_redact_lines).

    Flight events carry site URLs and extras, and the exception message and
    traceback, the context and the thread stacks can quote them, so a signed-URL
    token (X-Amz-Signature=, token=, sig=, userinfo) would otherwise become
    durable under logs/blackbox.  It all goes through the product's artifact
    redaction layer (capture_artifact_redact), which keeps structure (host, path,
    counts).  ``process`` is the snapshotter's own measurement (pids, counts,
    interpreter and platform strings, all JSON types), carries nothing captured,
    and is kept as it is: the redactor would take a platform string such as
    'Linux-6.8.0-139-generic-x86_64-with-glibc2.39' for an opaque token.
    """
    durable = {key: value if key == "process" else _redact_lines(value)
               for key, value in payload.items()}
    return json.dumps(durable, indent=2, ensure_ascii=False)


class FlightRecorderRingBuffer:
    """Thread-safe bounded circular ring buffer retaining recent operational events."""

    def __init__(self, capacity: int = 500) -> None:
        self.capacity = max(1, capacity)
        self._buffer: collections.deque = collections.deque(maxlen=self.capacity)
        self._lock = threading.RLock()

    def record(self, topic: str, data: Any, level: str = "INFO") -> None:
        """Record an event into the circular ring buffer."""
        event = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "topic": str(topic),
            "level": str(level),
            "data": data,
        }
        with self._lock:
            self._buffer.append(event)

    def get_recent_events(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Retrieve recent events from the ring buffer."""
        with self._lock:
            items = list(self._buffer)
        if limit is not None and limit > 0:
            return items[-limit:]
        return items

    def clear(self) -> None:
        """Clear all buffered events."""
        with self._lock:
            self._buffer.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)


class BlackboxSnapshotter:
    """Post-crash flight recorder capturing stack traces, thread frames, and system telemetry."""

    def __init__(
        self,
        dump_dir: Optional[Path] = None,
        ring_buffer_capacity: int = 500,
        max_dumps: int = DEFAULT_MAX_DUMPS,
    ) -> None:
        if max_dumps < 1:
            raise ValueError("max_dumps must be >= 1")
        self.max_dumps = max_dumps
        self.dump_dir = Path(dump_dir) if dump_dir else _default_dump_dir()
        self.ring_buffer = FlightRecorderRingBuffer(capacity=ring_buffer_capacity)
        self._lock = threading.RLock()
        # One dump written (and pruned) at a time, apart from _lock, which
        # log_event's record_event takes: that never waits on a dump's disk I/O.
        self._dump_lock = threading.RLock()
        self._snapshots_taken = 0
        self._dump_seq = 0
        self._events_recorded = 0
        self._original_excepthook: Optional[Callable] = None
        self._original_threading_excepthook: Optional[Callable] = None
        self._excepthook_installed = False
        self.last_dump_path: Optional[Path] = None
        # A crash path must not raise, so a step that fails there is recorded
        # here (and shown by get_metrics()) instead of being swallowed.
        self._failures = 0
        self.last_failure: Optional[str] = None

    def _record_failure(self, step: str, exc: BaseException) -> None:
        """Count a failed best-effort step and keep why, for get_metrics()."""
        reason = f"{step}: {''.join(traceback.format_exception_only(type(exc), exc)).strip()}"
        with self._lock:
            self._failures += 1
            self.last_failure = reason
        log.warning("blackbox %s", reason)

    def record_event(self, topic: str, data: Any, level: str = "INFO") -> None:
        """Record an operational flight event."""
        self.ring_buffer.record(topic, data, level=level)
        with self._lock:
            self._events_recorded += 1

    def snapshot(
        self,
        exc_info: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
        dump_to_disk: bool = True,
    ) -> Dict[str, Any]:
        """Capture complete post-crash diagnostic state."""
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        iso_ts = now_dt.isoformat()

        # 1. Parse exception information
        exc_data: Optional[Dict[str, Any]] = None
        exc_info = _crash_cause(exc_info)
        if exc_info is not None:
            if isinstance(exc_info, BaseException):
                exc_type = type(exc_info).__name__
                exc_msg = str(exc_info)
                tb_lines = traceback.format_exception(type(exc_info), exc_info, exc_info.__traceback__)
            elif isinstance(exc_info, tuple) and len(exc_info) == 3:
                exc_type = exc_info[0].__name__ if exc_info[0] else "None"
                exc_msg = str(exc_info[1])
                tb_lines = traceback.format_exception(*exc_info)
            else:
                exc_type = "UnknownException"
                exc_msg = str(exc_info)
                tb_lines = [str(exc_info)]

            exc_data = {
                "type": exc_type,
                "message": exc_msg,
                "traceback": "".join(tb_lines),
            }

        # 2. Inspect active thread frames
        thread_frames: Dict[str, Any] = {}
        named_threads = {t.ident: t.name for t in threading.enumerate() if t.ident is not None}
        for thread_id, frame in sys._current_frames().items():
            t_name = named_threads.get(thread_id, f"Thread-{thread_id}")
            stack = traceback.format_stack(frame)
            thread_frames[str(thread_id)] = {
                "name": t_name,
                "stack": "".join(stack),
            }

        # 3. Process & system resources
        proc_stats: Dict[str, Any] = {
            "pid": os.getpid(),
            "ppid": os.getppid() if hasattr(os, "getppid") else 0,
            "active_threads": threading.active_count(),
            "python_version": sys.version,
            "platform": platform.platform(),
        }

        # rusage is POSIX-only; where it is missing or fails the dump says why.
        if resource is None:
            proc_stats["rusage_unavailable"] = "no 'resource' module on this platform"
        else:
            try:
                rusage = resource.getrusage(resource.RUSAGE_SELF)
            except OSError as e:
                proc_stats["rusage_unavailable"] = f"getrusage failed: {e}"
            else:
                proc_stats["max_rss_kb"] = rusage.ru_maxrss
                proc_stats["utime"] = rusage.ru_utime
                proc_stats["stime"] = rusage.ru_stime

        # 4. Assembled flight snapshot
        payload = {
            "timestamp": iso_ts,
            "exception": exc_data,
            "context": context or {},
            "process": proc_stats,
            "threads": thread_frames,
            "flight_events": self.ring_buffer.get_recent_events(),
        }

        # 5. Atomic write: serialize first (redacted, see _durable_text; an odd
        # flight-event payload is written as its text, so it cannot abort the dump
        # half-way), then temp file + os.replace. A snapshot is counted only once it
        # exists in full.
        if dump_to_disk:
            with self._dump_lock:  # its sequence number and prune pass see every earlier dump
                tmp_file: Optional[Path] = None  # set once the temp file exists
                try:
                    text = _durable_text(payload)
                    self.dump_dir.mkdir(parents=True, exist_ok=True)
                    earlier = self._list_dumps()
                    # seq orders the dumps (_dump_order): one above every dump already
                    # there, this process's or a restarted one's, whatever the wall
                    # clock says; it also keeps two crashes in one millisecond apart.
                    self._dump_seq = max([self._dump_seq] + [_dump_order(p)[0] for p in earlier or []]) + 1
                    target_file = self.dump_dir / (
                        f"blackbox_crash_{int(time.time() * 1000)}_{self._dump_seq:06d}"
                        f"_{os.getpid()}_{threading.get_ident()}.json")
                    tmp_path = target_file.with_suffix(".json.tmp")
                    with open(tmp_path, "w", encoding="utf-8") as f:
                        tmp_file = tmp_path
                        f.write(text)
                    os.replace(tmp_file, target_file)
                except Exception as e:
                    self._record_failure("dump write", e)
                    # A write that died half-way (disk full, I/O error) must not leave
                    # its partial temp file behind: pruning only sees finished dumps,
                    # so every failed write would otherwise stay on disk forever.
                    if tmp_file is not None:
                        try:
                            os.unlink(tmp_file)
                        except OSError as cleanup_error:
                            # still fail-open (a crash path), but on the record
                            self._record_failure("temp-file cleanup", cleanup_error)
                    return payload
                self.last_dump_path = target_file
                if earlier is not None:
                    self._prune_old_dumps(earlier)

        with self._lock:
            self._snapshots_taken += 1
        return payload

    def _list_dumps(self) -> Optional[List[Path]]:
        """The dumps in dump_dir; None when it cannot be listed (recorded: the dump is
        still written, only its prune pass is skipped)."""
        try:
            # iterdir, not glob: pathlib's glob gives [] for a directory it cannot
            # read, and pruning would stop without a word.
            return [p for p in self.dump_dir.iterdir() if p.match(_DUMP_GLOB)]
        except OSError as e:
            self._record_failure("prune listing", e)
            return None

    def _prune_old_dumps(self, earlier: List[Path]) -> None:
        """Keep the newest ``max_dumps`` dumps: delete the oldest (_dump_order: write
        order, not the wall clock) of the ``earlier`` ones, listed before the dump just
        written, which is never among them -- a crash is not pruned by its own snapshot."""
        excess = len(earlier) + 1 - self.max_dumps
        for stale in sorted(earlier, key=_dump_order)[:max(0, excess)]:
            try:
                stale.unlink(missing_ok=True)  # already gone (another process pruned it): fine
            except OSError as e:
                self._record_failure(f"prune {stale.name}", e)

    def safe_snapshot(
        self,
        exc_info: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
        dump_to_disk: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """snapshot() for the crash paths (the excepthooks, the 500 handler), which must
        never raise into the program that is crashing: a snapshot that fails gives None
        and is recorded (get_metrics() "failures" / "last_failure")."""
        try:
            return self.snapshot(exc_info=exc_info, context=context, dump_to_disk=dump_to_disk)
        except Exception as e:
            self._record_failure("snapshot", e)
            return None

    def install_excepthook(self) -> None:
        """Snapshot unhandled crashes in the main thread (sys.excepthook) and in
        worker threads (threading.excepthook, which sys.excepthook never sees),
        then chain to the hooks that were there before."""
        with self._lock:
            if not self._excepthook_installed:
                self._original_excepthook = sys.excepthook
                self._original_threading_excepthook = threading.excepthook

                def _hook(exc_type, exc_value, exc_tb):
                    self.safe_snapshot(exc_info=(exc_type, exc_value, exc_tb), dump_to_disk=True)
                    if self._original_excepthook:
                        self._original_excepthook(exc_type, exc_value, exc_tb)

                def _thread_hook(args):
                    # A thread that calls sys.exit() just ends -- threading's own hook
                    # ignores SystemExit silently -- so that is no crash and no dump.
                    if not issubclass(args.exc_type, SystemExit):
                        name = args.thread.name if args.thread is not None else ""
                        self.safe_snapshot(exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
                                           context={"thread": name}, dump_to_disk=True)
                    if self._original_threading_excepthook:
                        self._original_threading_excepthook(args)

                sys.excepthook = _hook
                threading.excepthook = _thread_hook
                self._excepthook_installed = True

    def uninstall_excepthook(self) -> None:
        """Uninstall both hooks and restore the originals."""
        with self._lock:
            if self._excepthook_installed:
                if self._original_excepthook is not None:
                    sys.excepthook = self._original_excepthook
                if self._original_threading_excepthook is not None:
                    threading.excepthook = self._original_threading_excepthook
                self._excepthook_installed = False

    def get_metrics(self) -> Dict[str, Any]:
        """Return operational telemetry metrics."""
        with self._lock:
            return {
                "snapshots_taken": self._snapshots_taken,
                "events_recorded": self._events_recorded,
                "ring_buffer_size": len(self.ring_buffer),
                "excepthook_installed": self._excepthook_installed,
                "failures": self._failures,
                "last_failure": self.last_failure,
                "timestamp": time.time(),
            }


_GLOBAL_SNAPSHOTTER: Optional[BlackboxSnapshotter] = None
_INIT_LOCK = threading.Lock()


def get_blackbox_snapshotter() -> BlackboxSnapshotter:
    """Return global BlackboxSnapshotter singleton."""
    global _GLOBAL_SNAPSHOTTER
    if _GLOBAL_SNAPSHOTTER is None:
        with _INIT_LOCK:
            if _GLOBAL_SNAPSHOTTER is None:
                _GLOBAL_SNAPSHOTTER = BlackboxSnapshotter()
    return _GLOBAL_SNAPSHOTTER


def record_flight_event(topic: str, data: Any, level: str = "INFO") -> None:
    """Record an operational flight event in the global blackbox ring buffer."""
    get_blackbox_snapshotter().record_event(topic, data, level=level)


def capture_crash_snapshot(
    exc_info: Optional[Any] = None,
    context: Optional[Dict[str, Any]] = None,
    dump_to_disk: bool = True,
) -> Optional[Dict[str, Any]]:
    """Capture post-crash snapshot in the global blackbox. Never raises (it runs in
    the 500 handler): a failed capture gives None and is recorded in get_metrics()."""
    return get_blackbox_snapshotter().safe_snapshot(
        exc_info=exc_info, context=context, dump_to_disk=dump_to_disk
    )
