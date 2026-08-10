"""Phase 34: structured logging.

Replaces the scattered `sys.stderr.write(...)` calls with a real Python
logger that:
  - Has log levels (DEBUG/INFO/WARNING/ERROR/CRITICAL)
  - Writes to both stderr (preserving the existing visible behavior) AND
    a rotating log file at logs/bulk_downloader.log
  - Includes timestamp, level, module, and a per-site context tag when
    available
  - Is filterable at runtime via /api/global_config without restart

Usage:
    from .log import get_logger
    log = get_logger(__name__)
    log.info("Worker started")
    log.warning("Captcha failed, falling back to manual takeover")
    log.error("DB connection lost: %s", exc, exc_info=True)

Per-site context:
    log = get_logger(__name__).with_site(self.site_id)
    log.info("Job %s moved to needs_review", url)   # logs include the sid

Why not just use logging.getLogger() directly:
  - We want a consistent format across the codebase
  - We want a rotating file handler installed exactly once
  - The .with_site() convenience adds a tag that's tedious to do by hand
"""
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

_INITIALIZED: bool = False
# Marks the handlers and filters THIS module installed, so a re-init can
# replace its own and leave anyone else's alone. An attribute by NAME, not a
# class or a sentinel object, and that is load-bearing: the logger is a stdlib
# global that survives a module wipe, while every class defined in this file
# gets a NEW identity on re-import -- so `isinstance` or `is` could not
# recognise a previous incarnation's handlers and the sweep would remove
# nothing. Same mechanism and same reasoning as ui_events.py's
# _OWN_HANDLER_ATTR (v3.66.907).
_OWN_ATTR = "_bd_log_own"
_LOG_DIR: Path = Path("logs")
_LOG_FILE: Path = _LOG_DIR / "bulk_downloader.log"
_DEFAULT_LEVEL: int = logging.INFO


def _init() -> None:
    """One-shot setup. Idempotent — safe to call from every module."""
    global _INITIALIZED
    if _INITIALIZED: return
    _INITIALIZED = True

    root = logging.getLogger("bulk_downloader")

    # v3.66.1021: REPLACE what a previous incarnation installed; never append.
    # _INITIALIZED is a MODULE global and this logger is a STDLIB global that
    # outlives it, so a module wipe resets the flag and not the logger. Measured
    # before the fix, over 7 wipe cycles in one process: handlers 2,4,6..14
    # (2N), logger filters 1..7, and handler filters 2,6,12,20,30,42,56 --
    # QUADRATIC, because the loop at the end of this function decorated every
    # handler on the logger rather than the ones it had just installed. One
    # .info() printed 28 lines across those cycles, one per surviving
    # StreamHandler, and N RotatingFileHandlers rotated the same file
    # independently.
    #
    # TAGGED ONLY. An untagged sweep of root.handlers would remove a handler an
    # operator or another library attached -- the fix reproducing the shape of
    # the defect, and exactly the denominator mistake ui_events.py:107 records
    # from the other direction.
    #
    # The _INITIALIZED early-return above is deliberately UNCHANGED:
    # tests/test_v3_66_942_integrity_check_path_survives_a_cwd_change.py clears
    # that flag to force a full re-init, because its subject is where logs/ is
    # created relative to cwd. A guard that made _init a no-op when handlers
    # already exist would destroy that test's subject rather than fix this bug.
    for _h in [h for h in root.handlers if getattr(h, _OWN_ATTR, False)]:
        root.removeHandler(_h)
        try: _h.close()
        except Exception: pass
    for _f in [f for f in root.filters if getattr(f, _OWN_ATTR, False)]:
        root.removeFilter(_f)

    root.setLevel(_DEFAULT_LEVEL)
    # Don't propagate to the absolute root logger — that's noisy
    # because Flask/werkzeug install their own handlers there
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s %(name)s%(site_tag)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    # Detect whether downloader_ui has already installed a stderr tee
    # that writes to our target log file. If so, ONLY install the
    # stderr handler — the file is already covered by the tee, and a
    # second file handle would double-write every message.
    # The handlers this call installs, so the filter loop below decorates
    # ITS OWN and not every handler on the logger. That loop was the quadratic
    # term: on the Nth re-init it re-decorated all 2(N-1) survivors as well.
    own_handlers = []

    stderr_is_teed = type(sys.stderr).__name__ == "_StreamTee"

    if not stderr_is_teed:
        # No tee → we own the file. Install a rotating file handler.
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            # v3.47.7: bumped from 5MB×3 to 5GB×6 (≈30GB total cap)
            # per user request. The single-file cap stays at 5GB so the
            # active log can still be opened in a text editor without
            # OOMing; rotation gives us 5 archived files + 1 active =
            # 30GB ceiling. When the 6th rotation triggers, the oldest
            # is dropped — this is the implicit "auto-clear at 30GB"
            # the user asked for. Manual clear is wired via
            # /api/logs/clear.
            fh = logging.handlers.RotatingFileHandler(
                _LOG_FILE, maxBytes=5_000_000_000, backupCount=5,
                encoding="utf-8")
            fh.setFormatter(fmt)
            setattr(fh, _OWN_ATTR, True)
            root.addHandler(fh)
            own_handlers.append(fh)
        except Exception as e:
            # Don't crash on file handler init — fall back to stderr-only
            sys.stderr.write(f"  log: file handler init failed ({e}); stderr-only\n")

    # Stderr handler — always installed. When tee'd, this also reaches
    # the log file via the tee. When not tee'd, it's the console mirror
    # of the file handler above.
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    setattr(sh, _OWN_ATTR, True)
    root.addHandler(sh)
    own_handlers.append(sh)

    # Filter that injects an empty site_tag when none was set, so the
    # format string doesn't KeyError on calls that didn't supply context.
    class _Defaults(logging.Filter):
        def filter(self, record):
            if not hasattr(record, "site_tag"):
                record.site_tag = ""
            return True
    _lf = _Defaults()
    setattr(_lf, _OWN_ATTR, True)
    root.addFilter(_lf)
    # OWN handlers only. The handler-level copy is the load-bearing one: a
    # parent logger's filters do not run for records propagated from a child,
    # and every get_logger(__name__) site is a child logger.
    for h in own_handlers:
        h.addFilter(_Defaults())


class _SiteLogger:
    """A logger wrapper that attaches a site_id tag to each record.

    Built via `get_logger(__name__).with_site(sid)`. The tag shows up
    in the formatted output as `[sid:abc12345]` after the module name.
    """
    def __init__(self, base_logger: logging.Logger, site_id: str) -> None:
        self._base = base_logger
        self._tag = f"[sid:{site_id[:8]}]"
    def _emit(self, level: int, msg: str, *args: Any, **kwargs: Any) -> None:
        extra = kwargs.pop("extra", {}) or {}
        extra["site_tag"] = self._tag
        kwargs["extra"] = extra
        self._base.log(level, msg, *args, **kwargs)
    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:    self._emit(logging.DEBUG, msg, *args, **kwargs)
    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:     self._emit(logging.INFO, msg, *args, **kwargs)
    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:  self._emit(logging.WARNING, msg, *args, **kwargs)
    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:    self._emit(logging.ERROR, msg, *args, **kwargs)
    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs["exc_info"] = True
        self._emit(logging.ERROR, msg, *args, **kwargs)


class _Logger:
    """Wrapper around stdlib logger that adds .with_site()."""
    def __init__(self, name: str) -> None:
        _init()
        self._base = logging.getLogger(name)
    def with_site(self, site_id: str) -> _SiteLogger:
        return _SiteLogger(self._base, site_id)
    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:   self._base.debug(msg, *args, **kwargs)
    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:    self._base.info(msg, *args, **kwargs)
    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None: self._base.warning(msg, *args, **kwargs)
    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:   self._base.error(msg, *args, **kwargs)
    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs["exc_info"] = True
        self._base.error(msg, *args, **kwargs)


def get_logger(name: str) -> _Logger:
    """Return a logger for a module. Use as:
        log = get_logger(__name__)
    Then log.info(...), log.warning(...), etc.
    """
    if not name.startswith("bulk_downloader"):
        name = f"bulk_downloader.{name}"
    return _Logger(name)


def set_level(level_name: str) -> bool:
    """Change the runtime log level. Returns True on success, False if
    the level name was unrecognized."""
    _init()
    level = getattr(logging, str(level_name).upper(), None)
    if level is None: return False
    logging.getLogger("bulk_downloader").setLevel(level)
    return True


def get_level() -> str:
    _init()
    return logging.getLevelName(logging.getLogger("bulk_downloader").level)
