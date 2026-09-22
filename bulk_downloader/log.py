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
import contextlib
import contextvars
import json
import os
import logging
import logging.handlers
import sys
import threading
import time
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


# ── row 769: the site id a login lane is logging under ──────────────────────
#
# The login submit path writes its progress straight to stderr rather than
# through the logger above, and those lines carried no site id at all.  Site
# lanes run concurrently and interleave into one stream, so a `login:` line
# could only be attributed by correlating it against the neighbouring
# `[<sid>][network]` lines -- a correlation that already misattributed one.
#
# A CONTEXT VARIABLE rather than a parameter, because the functions that emit
# most of those lines (`_submit_login`, `_hand_off`, `_report_gate_actions`,
# the selector helpers) are never handed the site config and threading one
# through every signature would be a far larger change than the defect.  A
# contextvar is the right shape twice over: each site lane is its own thread,
# and a thread starts with an EMPTY context, so one lane can never read the id
# another lane set -- which is precisely the misattribution being closed.
_LOGIN_SITE_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "bd_login_site_id", default="")


@contextlib.contextmanager
def login_site(site_id: str):
    """Scope every login line written inside this block to `site_id`.

    Restores the previous value on the way out, including on an exception,
    so a lane that raises cannot leak its id onto the next thing this thread
    logs.
    """
    token = _LOGIN_SITE_ID.set(str(site_id or "").strip())
    try:
        yield
    finally:
        _LOGIN_SITE_ID.reset(token)


def site_tag(site_id: str = "") -> str:
    """`"[<sid>] "` for a known site, `""` for none.

    Written to sit immediately in front of the existing line prefix, so the
    result reads `  [<sid>] login: ...` and keeps the `login:` token every
    existing reader and grep of logs/bulk_downloader.log already matches on.
    The explicit argument wins over the contextvar, for callers such as the
    site runner that hold the id directly; both are absent in the standalone
    and test paths, where an untagged line is the honest answer rather than a
    fabricated id.
    """
    sid = str(site_id or _LOGIN_SITE_ID.get() or "").strip()
    return f"[{sid}] " if sid else ""


_GLOBAL_AUDIT_CHAIN = None
_GLOBAL_AUDIT_SIGNER = None
_GLOBAL_AUDIT_STATE = None      # (key_path, chain_path, head_path) the chain was loaded from
_GLOBAL_AUDIT_LOCK = threading.Lock()
# row1002 H1: the chain, its signer key and the head anchor live next to the
# history database so they survive a restart. audit_chain.jsonl is append-only
# (one block per line); audit_chain.head is the anchor audit_chain_status()
# verifies against; audit_chain.key is the raw Ed25519 seed (0600).
AUDIT_CHAIN_FILES = ("audit_chain.key", "audit_chain.jsonl", "audit_chain.head")


def audit_chain_paths() -> tuple:
    """Where the signed audit chain persists: beside the resolved DB path."""
    from . import db as _db
    base = Path(_db._resolve_db_path()).resolve().parent
    return tuple(base / name for name in AUDIT_CHAIN_FILES)


def _load_or_create_signer(key_path: Path, signer_id: str):
    from .signature_chains import AuditSigner, create_audit_signer
    if key_path.exists():
        return AuditSigner.from_private_key_bytes(signer_id, key_path.read_bytes())
    signer = create_audit_signer(signer_id)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(signer.private_key_bytes())
    return signer


def _load_or_create_chain(chain_path: Path, head_path: Path, signer):
    from .signature_chains import ProvenanceBlock, create_provenance_chain
    if chain_path.exists():
        chain = create_provenance_chain()          # no genesis: blocks come from disk
        chain.register_verifier(_verifier_for(signer))
        with chain_path.open("r", encoding="utf-8") as fh:
            chain.blocks = [ProvenanceBlock.from_dict(json.loads(line))
                            for line in fh if line.strip()]
        return chain
    chain = create_provenance_chain(signer=signer)
    _persist_block(chain_path, head_path, chain.blocks[0], chain.head_hash)
    return chain


def _verifier_for(signer):
    from .signature_chains import AuditVerifier
    return AuditVerifier(signer.signer_id, public_key_bytes=signer.public_key_bytes)


def _persist_block(chain_path: Path, head_path: Path, block, head_hash: str) -> None:
    chain_path.parent.mkdir(parents=True, exist_ok=True)
    with chain_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(block.to_dict(), sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    tmp = head_path.with_suffix(".head.tmp")
    tmp.write_text(head_hash, encoding="utf-8")
    os.replace(tmp, head_path)


def get_audit_provenance_chain(signer_id: str = "default-auditor"):
    """The process-wide signed audit chain, loaded from (or created at)
    audit_chain_paths(). audit.audit_log() mirrors every audit row into it;
    /api/audit/recent reports its head. A DB path change (tests) reloads."""
    global _GLOBAL_AUDIT_CHAIN, _GLOBAL_AUDIT_SIGNER, _GLOBAL_AUDIT_STATE
    paths = audit_chain_paths()
    with _GLOBAL_AUDIT_LOCK:
        if _GLOBAL_AUDIT_CHAIN is None or _GLOBAL_AUDIT_STATE != paths:
            key_path, chain_path, head_path = paths
            _GLOBAL_AUDIT_SIGNER = _load_or_create_signer(key_path, signer_id)
            _GLOBAL_AUDIT_CHAIN = _load_or_create_chain(chain_path, head_path, _GLOBAL_AUDIT_SIGNER)
            _GLOBAL_AUDIT_STATE = paths
        return _GLOBAL_AUDIT_CHAIN


def audit_chain_anchor() -> str:
    """The persisted head anchor ('' when none), read fresh from disk."""
    head_path = audit_chain_paths()[2]
    try:
        return head_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# row1002 r3 (B7-B): the file anchor lives beside the chain it anchors, so a
# wipe of the whole directory would read as a fresh first run. The head is
# therefore ALSO mirrored into the history DB on every append; the two
# anchors cross-check and a wipe of either side is a mismatch, not a reset.
_ANCHOR_DDL = ("CREATE TABLE IF NOT EXISTS audit_chain_anchor("
               "id INTEGER PRIMARY KEY CHECK (id = 1), head_hash TEXT NOT NULL, "
               "block_count INTEGER NOT NULL, updated_at REAL NOT NULL)")


def _mirror_anchor_to_db(head_hash: str, block_count: int) -> None:
    from . import db as _db
    with _db.db_conn() as cx:
        cx.execute(_ANCHOR_DDL)
        cx.execute("INSERT INTO audit_chain_anchor(id, head_hash, block_count, updated_at) "
                   "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET head_hash=excluded.head_hash, "
                   "block_count=excluded.block_count, updated_at=excluded.updated_at",
                   (head_hash, int(block_count), time.time()))


def audit_chain_db_anchor():
    """(head_hash, block_count) mirrored in the history DB, or None when the
    DB has never seen a chain (first run)."""
    from . import db as _db
    with _db.db_conn() as cx:
        cx.execute(_ANCHOR_DDL)
        row = cx.execute("SELECT head_hash, block_count FROM audit_chain_anchor WHERE id = 1").fetchone()
    return (str(row[0]), int(row[1])) if row else None


def record_audit_provenance_event(event_type: str, payload: dict, signer=None):
    """Append a signed block to the global chain and persist it. Signed by the
    chain's own signer unless one is given; a per-event throwaway signer would
    leave a block no verifier can vouch for (row1002 F1)."""
    chain = get_audit_provenance_chain()
    with _GLOBAL_AUDIT_LOCK:
        block = chain.append_event(event_type, payload, signer or _GLOBAL_AUDIT_SIGNER)
        _key, chain_path, head_path = _GLOBAL_AUDIT_STATE
        _persist_block(chain_path, head_path, block, chain.head_hash)
        _mirror_anchor_to_db(chain.head_hash, len(chain.blocks))
        return block
