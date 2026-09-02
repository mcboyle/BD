"""SQLite history + queue persistence (Phase 4).

Two tables:
  - `history`: append-only log of every job outcome (done/failed/needs_review)
  - `queue`:   live state of pending/running/stopped jobs per site, persisted
               so the queue survives app restart, crash, or power loss.

Indexes on both keep filtering sub-millisecond at any size."""
# Load-bearing invariants tagged inline as # INV-<ID>; see DANGER_MAP.md.
import sqlite3
import os as _os
import threading as _threading
import weakref as _weakref
from contextlib import contextmanager
from pathlib import Path as _Path_top
from .constants import DB_PATH
# v3.66.800 (MOD-3 cut 2): the dual-write mirror. Imported eagerly (it is
# stdlib-only and does NOT import psycopg at module scope), so the new edge
# db->pg_backend is declared and frozen rather than hidden in a function.
from . import pg_backend


def _resolve_db_path():
    """v3.66.9: pick the right DB path at call time, not import time.

    Resolution order:
      1. If DB_PATH has been monkeypatched to an absolute path (used by
         the test conftest.py and Docker), use it verbatim.
      2. Else, if BD_INSTALL_DIR is set, join it with the relative
         DB_PATH (fixes the `_isolated_bd()` pattern in
         test_phases_195_199 et al that sets BD_INSTALL_DIR but doesn't
         chdir).
      3. Else, use DB_PATH as-is, which sqlite3.connect() resolves
         against cwd (preserves the chdir-only isolation pattern used
         by ~30 other tests).
    """
    if _os.path.isabs(DB_PATH):
        return DB_PATH
    install_dir = _os.environ.get("BD_INSTALL_DIR")
    if install_dir:
        return str(_Path_top(install_dir).resolve() / DB_PATH)
    return DB_PATH

def db_init():
    with db_conn() as cx:
        cx.execute("""CREATE TABLE IF NOT EXISTS history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id TEXT, site_name TEXT, url TEXT, status TEXT,
            filename TEXT, file_size INTEGER, message TEXT, screenshot TEXT,
            honeypot_score REAL DEFAULT NULL,
            bytes_fetched INTEGER DEFAULT NULL,
            transfer_mode TEXT DEFAULT NULL,
            ts TEXT DEFAULT(strftime('%Y-%m-%dT%H:%M:%S','now')))""")
        # Phase 4: persist the live queue. Any pending/running/stopped/
        # needs_review job is mirrored here so a restart picks up exactly
        # where it left off. force_download persists the Approve flag.
        cx.execute("""CREATE TABLE IF NOT EXISTS queue(
            site_id TEXT NOT NULL,
            url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            message TEXT DEFAULT '',
            retries INTEGER DEFAULT 0,
            retry_after REAL DEFAULT 0,
            screenshot TEXT DEFAULT '',
            force_download INTEGER DEFAULT 0,
            priority TEXT DEFAULT '',
            ord INTEGER DEFAULT 0,
            filename TEXT DEFAULT '',
            listing_title TEXT DEFAULT '',
            file_size INTEGER DEFAULT 0,
            lane TEXT DEFAULT 'default',
            depends_on TEXT DEFAULT '',
            ts_added TEXT DEFAULT(strftime('%Y-%m-%dT%H:%M:%S','now')),
            ts_updated TEXT DEFAULT(strftime('%Y-%m-%dT%H:%M:%S','now')),
            PRIMARY KEY(site_id, url))""")
        # Indexes — keep filter/sort sub-millisecond at any table size.
        cx.execute("CREATE INDEX IF NOT EXISTS idx_hist_site_ts  ON history(site_id, ts DESC)")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_hist_status   ON history(status)")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_hist_url      ON history(url)")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_q_site_status ON queue(site_id, status)")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_q_site_ord    ON queue(site_id, ord)")
        # Phase 2 Cut 2.1: lazy-migrate lane + depends_on onto an existing queue
        # table (CREATE TABLE IF NOT EXISTS never adds columns to a live table).
        _qcols = {r[1] for r in cx.execute("PRAGMA table_info(queue)").fetchall()}
        if "lane" not in _qcols:
            cx.execute("ALTER TABLE queue ADD COLUMN lane TEXT DEFAULT 'default'")
        if "depends_on" not in _qcols:
            cx.execute("ALTER TABLE queue ADD COLUMN depends_on TEXT DEFAULT ''")
        if "listing_title" not in _qcols:
            cx.execute("ALTER TABLE queue ADD COLUMN listing_title TEXT DEFAULT ''")
        # history schema changes belong to bulk_downloader/migrations.py, which
        # is a versioned framework with a schema_migrations ledger and is
        # applied at app.py:1814. #63 added bytes_fetched here as a bespoke
        # loop, outside that ledger; migration v8 owns it now. CREATE TABLE
        # above still carries the column so a FRESH database is born with it,
        # which is exactly how honeypot_score (v7) is handled.
        cx.execute("CREATE INDEX IF NOT EXISTS idx_q_status      ON queue(status)")
        # v3.43.80 Phase 92: FTS5 search across history. SQLite's FTS5
        # virtual table indexes url/filename/message/site_name so the
        # operator can find "that vixen scene from last week with
        # 'beach' in the title" without scrolling the Logs tab. The
        # table is EXTERNAL CONTENT (content='history',
        # content_rowid → history.id) so storage cost is only the
        # inverted index, not duplicated text. It is NOT
        # "content-less" -- that is a different FTS5 mode, and the
        # confusion is load-bearing: external content means SQLite
        # maintains NOTHING for this index, so a deleted history
        # row keeps its terms unless the application issues the
        # FTS5 'delete' command. See db_fts_forget.
        # Tokenizer: unicode61 with diacritic removal and a
        # custom separator list that splits CamelCase / digit-runs /
        # common URL punctuation — important for filename matching.
        #
        # FTS5 not being present is fail-open: we log a warning and
        # skip the index creation. Search falls back to the LIKE-based
        # db_search() in that case.
        try:
            cx.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS history_fts
                USING fts5(
                    site_name, url, filename, message, status UNINDEXED,
                    content='history', content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2 separators ''-_./:?#&='''
                )""")
            # One-time backfill: if FTS5 was just created but `history`
            # already has rows (e.g. upgrading from a prior version),
            # populate the index. INSERT INTO ... SELECT is the
            # documented FTS5 backfill pattern. Cheap on small history,
            # bounded on big history (single full table scan).
            cx.execute("SELECT count(*) FROM history_fts").fetchone()
            fts_count = cx.execute("SELECT count(*) FROM history_fts").fetchone()[0]
            hist_count = cx.execute("SELECT count(*) FROM history").fetchone()[0]
            if fts_count == 0 and hist_count > 0:
                cx.execute("""INSERT INTO history_fts(rowid, site_name, url, filename, message, status)
                              SELECT id, COALESCE(site_name,''), COALESCE(url,''),
                                     COALESCE(filename,''), COALESCE(message,''),
                                     COALESCE(status,'') FROM history""")
        except sqlite3.OperationalError as _e:
            # FTS5 not compiled in. Old SQLite or stripped build.
            # search will fall back to LIKE; everything else works.
            import sys as _sys
            _sys.stderr.write(f"[db] FTS5 unavailable, search will use LIKE fallback: {_e}\n")
        # Phase 16.43: web push subscriptions. Each row is one browser/device
        # that opted into notifications. Endpoint is the subscription's URL
        # (FCM, APNs gateway, etc.); p256dh + auth are the browser's encryption
        # keys; user_agent is recorded for the user-facing list. We dedupe
        # by endpoint (PRIMARY KEY) so a re-subscribe replaces the old row
        # with fresh keys.
        cx.execute("""CREATE TABLE IF NOT EXISTS push_subscriptions(
            endpoint TEXT PRIMARY KEY,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            user_agent TEXT DEFAULT '',
            created_at TEXT DEFAULT(strftime('%Y-%m-%dT%H:%M:%S','now')),
            last_sent_at REAL DEFAULT 0)""")
        # v3.43.15: session keep-alive history. Each row is one observation
        # about a (site, account) session — either a successful login, a
        # heartbeat (verified-still-logged-in), or an observed failure
        # (server rejected our cookies). The runner uses this table to
        # learn each site's session lifetime empirically: median(time from
        # login to first observed failure) becomes the prediction model.
        #
        # event_type: 'login', 'heartbeat_ok', 'heartbeat_fail',
        #             'auto_relogin_ok', 'auto_relogin_fail',
        #             'needs_takeover'
        # account_idx: which account index (NULL for top-level)
        # detail: free-form text for debugging (e.g. the URL we hit, the
        #         marker we matched/missed, HTTP status)
        cx.execute("""CREATE TABLE IF NOT EXISTS session_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            site_id TEXT NOT NULL,
            account_idx INTEGER,
            event_type TEXT NOT NULL,
            detail TEXT DEFAULT '')""")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_sh_site_ts ON session_history(site_id, ts DESC)")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_sh_event   ON session_history(event_type)")
        # Phase 1 Cut 1.1 (v3.66.612): the capture index. Ends the FS-walk
        # class — dom_analyzer.scan_captures() was rebuilt into an in-memory,
        # restart-ephemeral cache on every POST /api/captures/scan. This table
        # makes that inventory durable so the picker/summary/search survive a
        # restart without a re-walk. rel_path (project-root-relative subpath) is
        # the natural PK: it is BOTH the dedup key and the resolve token the
        # existing dom_analyzer resolvers already use. Columns mirror the
        # scan_captures row shape 1:1. first_seen/indexed_at are index bookkeeping
        # (not from the walk). NOTE: this table is a fast LISTING index only — it
        # is never the authority for resolving a token to a file (that stays the
        # on-disk symlink/is_file/_is_under check in dom_analyzer, F2/F-APP03).
        cx.execute("""CREATE TABLE IF NOT EXISTS captures(
            rel_path TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            dir TEXT DEFAULT '',
            host TEXT DEFAULT '',
            captured_at REAL DEFAULT 0,
            size INTEGER DEFAULT 0,
            kind TEXT DEFAULT '',
            redacted INTEGER DEFAULT 0,
            first_seen REAL DEFAULT(strftime('%s','now')),
            indexed_at REAL DEFAULT(strftime('%s','now')))""")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_cap_host     ON captures(host)")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_cap_kind     ON captures(kind)")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_cap_captured ON captures(captured_at DESC)")

# ── Phase 1 Cut 1.1 (v3.66.612): capture-index helpers ──────────────────
# The durable backing for the capture picker/summary/search. db_captures_upsert
# is called by the reconcile scan (app_captures POST /api/captures/scan) with the
# rows dom_analyzer.scan_captures() produces; db_captures_all serves the read
# consumers (Cut 1.2); db_captures_prune_missing lets a reconcile drop rows for
# captures deleted on disk. All value-free metadata only — never an absolute path.

_CAPTURE_COLS = ("rel_path", "name", "dir", "host",
                 "captured_at", "size", "kind", "redacted")


def _ensure_captures_table(cx):
    """Idempotently create the `captures` table + indices on the given connection.
    Called at the top of every capture helper so they are robust whether or not
    db_init() has run in the current cwd/DB (the retention._ensure_tables pattern).
    Fixes the 614 regression: a capture helper reached before db_init raised
    'no such table: captures' and 500'd GET /api/analyzer/captures."""
    cx.execute("""CREATE TABLE IF NOT EXISTS captures(
        rel_path TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        dir TEXT DEFAULT '',
        host TEXT DEFAULT '',
        captured_at REAL DEFAULT 0,
        size INTEGER DEFAULT 0,
        kind TEXT DEFAULT '',
        redacted INTEGER DEFAULT 0,
        first_seen REAL DEFAULT(strftime('%s','now')),
        indexed_at REAL DEFAULT(strftime('%s','now')))""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_cap_host     ON captures(host)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_cap_kind     ON captures(kind)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_cap_captured ON captures(captured_at DESC)")


def db_captures_upsert(rows):
    """Bulk-upsert capture index rows keyed on rel_path (the PK). Each row is a
    mapping with the scan_captures shape (rel_path, name, dir, host, captured_at,
    size, kind, redacted). Re-upserting an existing rel_path UPDATES it in place
    (metadata refreshed, indexed_at bumped) — never a duplicate. Returns the count
    upserted. Missing keys default sensibly so a partial row can't crash the write.
    """
    rows = list(rows or [])
    if not rows:
        return 0
    payload = []
    for r in rows:
        payload.append((
            str(r["rel_path"]),
            str(r.get("name") or ""),
            str(r.get("dir") or ""),
            str(r.get("host") or ""),
            float(r.get("captured_at") or 0.0),
            int(r.get("size") or 0),
            str(r.get("kind") or ""),
            1 if r.get("redacted") else 0,
        ))
    with db_conn() as cx:
        _ensure_captures_table(cx)
        cx.executemany(
            "INSERT INTO captures"
            "(rel_path,name,dir,host,captured_at,size,kind,redacted) "
            "VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(rel_path) DO UPDATE SET "
            "  name=excluded.name, dir=excluded.dir, host=excluded.host, "
            "  captured_at=excluded.captured_at, size=excluded.size, "
            "  kind=excluded.kind, redacted=excluded.redacted, "
            "  indexed_at=strftime('%s','now')",
            payload,
        )
    return len(payload)


def db_captures_all(*, host=None, kind=None, redacted=None,
                    limit=None, offset=0):
    """Return capture index rows as plain dicts, newest first (captured_at DESC),
    with optional host / kind / redacted filters (the picker's facets) and
    optional limit/offset paging. Filtering is done in SQL (indexed) so this stays
    fast at any store size — the whole point of the table.
    """
    where, params = [], []
    if host:
        where.append("host = ?")
        params.append(str(host))
    if kind in ("wacz", "json"):
        where.append("kind = ?")
        params.append(kind)
    if redacted is not None:
        where.append("redacted = ?")
        params.append(1 if redacted else 0)
    sql = "SELECT rel_path,name,dir,host,captured_at,size,kind,redacted FROM captures"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY captured_at DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
    with db_conn() as cx:
        _ensure_captures_table(cx)
        cur = cx.execute(sql, params)
        out = []
        for row in cur.fetchall():
            d = dict(row)
            d["redacted"] = bool(d.get("redacted"))
            out.append(d)
    return out


def db_captures_prune_missing(seen_rel_paths):
    """Delete every capture row whose rel_path is NOT in `seen_rel_paths` — how a
    reconcile scan drops captures deleted on disk. Returns the count removed. An
    empty/None seen set is treated as "seen nothing" and prunes everything (a
    reconcile that found no captures legitimately empties the index).
    """
    seen = set(seen_rel_paths or ())
    with db_conn() as cx:
        _ensure_captures_table(cx)
        existing = {r[0] for r in cx.execute("SELECT rel_path FROM captures").fetchall()}
        stale = existing - seen
        if stale:
            cx.executemany("DELETE FROM captures WHERE rel_path = ?",
                           [(p,) for p in stale])
    return len(stale)


# ── v3.47.8 (#42): periodic DB integrity check ──────────────────────────
# SQLite corruption is rare but silent — a database can pass every
# read/write op for weeks while having corrupt indexes or partially-
# overwritten pages from a power loss. PRAGMA integrity_check forces a
# full structural validation and is the canonical way to detect this.
#
# Cost: O(database_size) — full scan. On a 5 GB history.db this takes
# ~3 seconds. We rate-limit to at most once per 24 hours via a sentinel
# file (`.integrity_check_last`) so app boots stay fast.
#
# Failure mode: returns False + a string list of problems. Caller logs
# and continues — we don't auto-quarantine because false-positives on a
# busy DB (mid-write) would be worse than the corruption itself. The
# operator gets the warning in logs and runs `bdctl db-vacuum` manually.
def db_integrity_check(*, force=False):
    """Run PRAGMA integrity_check if it hasn't run in the last 24 hours.

    Returns (ok: bool, details: list[str]). ok=True if clean OR if the
    check was skipped (recent run). details is empty on clean checks,
    contains SQLite's problem report on failures."""
    import os, time
    from pathlib import Path
    sentinel = Path(_resolve_db_path()).parent / ".integrity_check_last"
    now = time.time()
    if not force and sentinel.exists():
        try:
            last = float(sentinel.read_text().strip())
            if now - last < 86400:  # 24 h
                return True, []
        except (ValueError, OSError):
            pass  # malformed sentinel — fall through and re-check
    try:
        with db_conn() as cx:
            # integrity_check returns one row per problem, or a single
            # "ok" row when clean. quick_check is ~10x faster than the
            # full check and catches the same common corruption modes
            # (page links, btree balance, free list). Use quick_check on
            # boot to keep startup snappy; full check is available via
            # force=True for the cron-driven deep audit.
            rows = cx.execute("PRAGMA quick_check").fetchall()
            problems = [r[0] for r in rows if r[0] != "ok"]
    except Exception as e:
        return False, [f"integrity_check failed to run: {type(e).__name__}: {e}"]
    # Touch sentinel only on clean — if it failed we want the next boot
    # to retry rather than silently swallow.
    if not problems:
        try:
            sentinel.write_text(str(now))
        except OSError:
            pass  # sentinel is best-effort; not fatal
    return (not problems), problems

class _PgResultCursor:
    """v3.66.804: cursor-shaped view over Postgres rows for a cut-over read.

    Wraps the real SQLite cursor so description/rowcount-style attributes still
    resolve; only the ROWS come from Postgres. Consumers use fetchall(),
    fetchone() and iteration, so all three are served from the same list -- a
    partial implementation would fail at an unrelated call site rather than
    here."""

    __slots__ = ("_rows", "_i", "_cur")

    def __init__(self, rows, sqlite_cursor=None):
        self._rows = list(rows)
        self._i = 0
        self._cur = sqlite_cursor

    def fetchall(self):
        out, self._i = self._rows[self._i:], len(self._rows)
        return out

    def fetchone(self):
        if self._i >= len(self._rows):
            return None
        r = self._rows[self._i]
        self._i += 1
        return r

    def fetchmany(self, size=1):
        out = self._rows[self._i:self._i + size]
        self._i += len(out)
        return out

    def __iter__(self):
        return iter(self._rows)

    def __getattr__(self, name):
        return getattr(self._cur, name)


class _DualWriteConn:
    """v3.66.800 (MOD-3 cut 2): SQLite connection wrapper that MIRRORS writes
    to Postgres. Returned by `_open_history_conn()` ONLY when `MOD3_PG_DSN` is
    set; with the feature off the seam hands back the bare sqlite3.Connection,
    so the default path is unchanged rather than merely forwarded.

    Everything delegates to the real connection, which stays authoritative:
    the SQLite call happens FIRST and its result is what the caller gets. The
    mirror runs after, best-effort, and cannot raise (pg_backend swallows) --
    a mirror that can break the primary write is worse than no mirror.

    The DML scope boundary is enforced HERE as well as inside
    `pg_backend.mirror()`: reads are the bulk of DB traffic, and a reader of
    the seam should see what does and does not leave for Postgres without
    having to open another module. `mirror()` keeps its own check so the
    boundary survives a future caller that bypasses this proxy.

    Cursors are wrapped too: consumers use both `cx.execute(...)` and
    `cx.cursor().execute(...)`, and a proxy that covered only the former would
    silently miss writes -- a mirror whose denominator excludes half the call
    sites, reporting a shadow store that looks consistent because nobody wrote
    the missing rows to either side.
    """

    def __init__(self, cx):
        object.__setattr__(self, "_cx", cx)

    def execute(self, sql, params=()):
        cur = self._cx.execute(sql, params)      # SQLite always runs: it stays
                                                 # current so rollback is free
        if pg_backend.is_mirrored(sql):          # scope boundary, AT the seam
            pg_backend.mirror(sql, params)       # best-effort, cannot raise
            return cur
        # v3.66.804 (cut 5): when cutover is ENGAGED (fail-closed -- preflight
        # must pass), Postgres serves the read. None from read_authoritative
        # means 'could not serve', NOT 'empty', so the SQLite cursor is
        # returned instead: an outage degrades to the old store rather than
        # masquerading as data loss.
        rows = pg_backend.read_authoritative(sql, params)
        if rows is not None:
            return _PgResultCursor(rows, cur)
        self._shadow(sql, params)                # v3.66.801, reads only
        return cur

    def _shadow(self, sql, params):
        """v3.66.801 (MOD-3 cut 3): compare this SELECT against the shadow
        store. The caller's cursor is NOT consumed -- the statement is
        re-executed on the same connection for the comparison, so
        caller-isolation is structural rather than argued, at the cost of one
        extra SQLite read while the (opt-in) mode is on."""
        if not pg_backend.shadow_read_enabled():
            return
        try:
            rows = self._cx.execute(sql, params).fetchall()
            pg_backend.shadow_compare(sql, params, rows)
        except Exception:
            pass    # diagnostics must never break the authoritative read

    def executemany(self, sql, seq):
        rows = list(seq)
        cur = self._cx.executemany(sql, rows)
        if pg_backend.is_mirrored(sql):
            for p in rows:
                pg_backend.mirror(sql, p)
        return cur

    def cursor(self, *a, **k):
        return _DualWriteCursor(self._cx.cursor(*a, **k))

    # -- everything else is the real connection ---------------------------
    def __getattr__(self, name):
        return getattr(self._cx, name)

    def __setattr__(self, name, value):
        setattr(self._cx, name, value)

    def __enter__(self):
        self._cx.__enter__()
        return self

    def __exit__(self, *exc):
        return self._cx.__exit__(*exc)


class _DualWriteCursor:
    """Cursor half of the dual-write proxy (see `_DualWriteConn`)."""

    def __init__(self, cur):
        object.__setattr__(self, "_cur", cur)

    def execute(self, sql, params=()):
        r = self._cur.execute(sql, params)
        if pg_backend.is_mirrored(sql):
            pg_backend.mirror(sql, params)
        elif pg_backend.shadow_read_enabled():
            try:
                conn = self._cur.connection
                rows = conn.execute(sql, params).fetchall()
                pg_backend.shadow_compare(sql, params, rows)
            except Exception:
                pass
        return r

    def executemany(self, sql, seq):
        rows = list(seq)
        r = self._cur.executemany(sql, rows)
        if pg_backend.is_mirrored(sql):
            for p in rows:
                pg_backend.mirror(sql, p)
        return r

    def __getattr__(self, name):
        return getattr(self._cur, name)

    def __setattr__(self, name, value):
        setattr(self._cur, name, value)

    def __iter__(self):
        return iter(self._cur)


class _HistoryCursor(sqlite3.Cursor):
    """Cursor whose owning history connection can finalize a logical lease."""

    def close(self):
        owner = getattr(self, "_history_owner", None)
        try:
            return super().close()
        finally:
            if owner is not None:
                owner._lease_cursors.discard(self)


class _HistoryConnection(sqlite3.Connection):
    """SQLite connection that tracks cursors created during a logical lease.

    A physical connection now survives multiple ``db_conn`` blocks. Closing
    its outstanding cursors at each block boundary preserves the old close()
    behaviour: a partially-consumed SELECT cannot retain a read snapshot and
    pin the WAL after its logical owner has left the context manager.

    Pooling also has to preserve what open-per-call gave away for free: the
    borrower's reference DIED at the end of its ``with`` block, so a reference
    that escaped the block could not touch the database. Now the object
    outlives the block, so the lease is tracked explicitly. Outside its lease
    the handle belongs to the pool, and every statement, commit, and rollback
    on it raises ``sqlite3.ProgrammingError`` rather than silently injecting
    work into the NEXT borrower's transaction. ``close()`` is the deliberate
    exception -- a stale borrower closing what it thinks it owns must not
    close the pool's live handle, so it becomes a harmless no-op. The pool
    itself closes through ``_force_close``, which is not guarded.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lease_cursors = _weakref.WeakSet()
        self._slow_query_trace = None
        self._slow_query_config_token = None
        # Open-and-configure in _open_history_conn runs its PRAGMAs before any
        # lease exists, and a caller holding _open_history_conn's return value
        # directly never had a lease to begin with; both must work.
        self._lease_active = True

    def _begin_lease(self):
        self._lease_active = True

    def _end_lease(self):
        self._lease_active = False

    def _require_lease(self):
        if not self._lease_active:
            raise sqlite3.ProgrammingError(
                "Cannot operate on a pooled history connection outside its "
                "db_conn() lease. This reference escaped its context manager; "
                "the handle now belongs to the connection pool.")

    def cursor(self, factory=_HistoryCursor):
        # First statement: the refusal must land before any SQLite work, or
        # the escaped reference has already touched a later transaction.
        self._require_lease()
        cur = super().cursor(factory)
        self._lease_cursors.add(cur)
        if isinstance(cur, _HistoryCursor):
            cur._history_owner = self
        return cur

    def execute(self, sql, parameters=(), /):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, parameters, /):
        return self.cursor().executemany(sql, parameters)

    def executescript(self, sql_script, /):
        return self.cursor().executescript(sql_script)

    def commit(self):
        self._require_lease()
        return super().commit()

    def rollback(self):
        self._require_lease()
        return super().rollback()

    def _close_lease_cursors(self):
        for cur in tuple(self._lease_cursors):
            cur.close()
        self._lease_cursors.clear()

    def _force_close(self):
        """Physically close. The pool's path; never guarded by the lease."""
        try:
            self._close_lease_cursors()
        finally:
            super().close()

    def close(self):
        if not self._lease_active:
            # A stale borrower must not close the pool's idle handle.
            return
        self._force_close()


def _open_history_conn(path=None):
    """v3.66.795 (MOD-3 cut 1): THE single history-DB connection point.

    Every history-DB connection in the app is created here. MOD-3 migrates this
    backend from SQLite to Postgres in staged cuts (dual-write -> shadow-read ->
    cutover); each of those needs exactly one place to intercept, so any module
    that opens `downloader_history.db` itself would silently escape dual-write
    and corrupt the shadow-read comparison. `tests/test_v3_66_795_mod3_seam.py`
    enforces that this stays the only such point (db.py may contain exactly one
    connect; other modules must go through `db_conn()`).

    Behaviour is unchanged from when this lived inline in `db_conn()` -- this is
    a pure extraction. Returns a live connection; the caller owns commit/close
    (see `db_conn()`, which is the context manager everything else uses).

    v3.66.927: `path` is optional and defaults to None, which resolves at call
    time exactly as before -- every existing caller is unaffected. It exists
    for ONE caller: `run_integrity_check`, which schedules work on a
    fire-and-forget thread and must verify the database it was scheduled FOR,
    not whichever one DB_PATH happens to name when the thread finally runs.
    Threaded through here rather than opened directly because this is the
    single connection point the MOD-3 seam depends on -- a second
    sqlite3.connect in this module fails test_v3_66_795_mod3_seam.py, and would
    escape dual-write when the Postgres migration lands.
    """
    cx = sqlite3.connect(
        path or _resolve_db_path(), timeout=10.0, factory=_HistoryConnection)
    cx.row_factory = sqlite3.Row
    # v3.43.13 / v3.47.1: SQLite contention fix.
    #
    #   journal_mode=WAL: write-ahead logging. Multiple readers can
    #     proceed concurrently with a writer. Without WAL, every reader
    #     blocks every writer and vice versa, and concurrent workers
    #     trying to update queue.db hit "database is locked" errors.
    #
    #   synchronous=NORMAL: with WAL, NORMAL is the recommended sync
    #     level (fsync only at checkpoint, not on every commit). Trades
    #     a tiny crash-safety window for big perf wins. FULL is the
    #     default; NORMAL is fine for queue/history data which is
    #     re-derivable from the live runner state if lost.
    #
    #   busy_timeout=10000ms: when a write CAN'T acquire the lock
    #     immediately, wait up to 10s before giving up. Default is 0
    #     (fail instantly) which is what produced "database is locked"
    #     under concurrent worker writes.
    #
    # v3.47.1: Python's sqlite3 module opens an implicit transaction
    # before any DML, and `PRAGMA journal_mode=WAL` cannot run inside
    # a transaction — it silently no-ops and leaves journal_mode at
    # 'delete'. Two fixes needed:
    #   1. Set isolation_level=None to disable Python's auto-BEGIN
    #      around the PRAGMA itself
    #   2. fetchone() the result — without it, some SQLite builds
    #      defer the mode change until the next statement
    # WAL mode persists in the database file once set, so this is
    # cheap on subsequent opens.
    try:
        cx.isolation_level = None  # INV-004; autocommit; the PRAGMA call only
        cur = cx.execute("PRAGMA journal_mode=WAL")  # INV-004
        _ = cur.fetchone()  # consume the result to commit the mode
        cx.execute("PRAGMA synchronous=NORMAL")
        cx.execute("PRAGMA busy_timeout=10000")
        cx.isolation_level = ""  # INV-004; back to default (deferred BEGIN)
    except Exception:
        pass  # Don't let pragma failures break the connection
    # v3.48 (#22): slow-query logging. set_trace_callback fires on every
    # statement; we time the gap between successive callbacks to derive
    # per-statement duration. Threshold + logger configurable via env to
    # support production (high threshold, sample) and dev (low threshold).
    # Resolve enabled (and only then the threshold) once here and bind the
    # result to this physical connection. The trace callback is on the
    # every-statement path; a global-config lookup there stats the config file
    # even when its parsed contents are already cached.
    _bind_slow_query_trace(cx)
    # v3.66.800 (MOD-3 cut 2): dual-write is OFF unless MOD3_PG_DSN is set, in
    # which case the seam -- the single interception point cut 1 exists to
    # provide -- hands back a mirroring proxy instead of the bare connection.
    if pg_backend.dual_write_enabled():
        return _DualWriteConn(cx)
    return cx


_DB_CONN_LOCAL = _threading.local()


def _close_history_conn(cx):
    """Physically close a handle the POOL owns.

    Must not go through ``close()``: that is guarded so a stale borrower
    cannot close the pool's idle handle, and routing eviction through it would
    silently leak every connection the pool retires.
    """
    try:
        force = getattr(cx, "_force_close", None)
        if force is not None:
            force()
        else:
            cx.close()
    except Exception:
        pass


def _begin_history_lease(cx):
    begin = getattr(cx, "_begin_lease", None)
    if begin is not None:
        begin()


def _end_history_lease(cx):
    end = getattr(cx, "_end_lease", None)
    if end is not None:
        end()


def _finish_history_lease(cx):
    """Finalize cursors before an otherwise-clean connection is cached."""
    try:
        close_cursors = getattr(cx, "_close_lease_cursors", None)
        if close_cursors is not None:
            close_cursors()
        return True
    except Exception:
        return False


def _reset_slow_query_trace(cx):
    trace = getattr(cx, "_slow_query_trace", None)
    reset = getattr(trace, "reset", None)
    if reset is not None:
        reset()


_UNBOUND_CONFIG_TOKEN = object()


def _slow_query_config_token():
    """Cheap in-process marker for "the parsed global config was replaced".

    ``global_config`` rebinds its ``_cached`` dict on a ``set_config`` write
    and on any reload its mtime check triggers; an unchanged config returns
    the SAME object from ``get_config`` without rebinding. Comparing object
    identity therefore detects a Settings write with no stat at all, which is
    what keeps a reused connection off the per-lease config-file path. A
    strong reference is held alongside it, so the identity cannot be recycled
    onto a different dict while we are still comparing against it.
    """
    try:
        from bulk_downloader import global_config as _gc
        return _gc._cached
    except Exception:
        return None


def _bind_slow_query_trace(cx):
    """(Re)configure this physical connection's slow-query tracer.

    Costs exactly one config lookup when tracing is off and two when it is on
    -- what the pre-pooling open paid every time -- and is called only on a
    physical open or when the config token says the parsed config changed.
    """
    trace = None
    if _slow_query_log_enabled():
        trace = _make_slow_query_trace(_slow_query_threshold_ms())
    previous = getattr(cx, "_slow_query_trace", None)
    if trace is not None or previous is not None:
        # Install ours, or retire ours. When tracing is off and BD never
        # installed a tracer on this handle, the callback is NOT ours to
        # clear: sqlite3 has one trace slot per connection, and a diagnostic
        # harness that wrapped sqlite3.connect owns whatever sits in it.
        # Clearing unconditionally silently blinded such a recorder.
        try:
            cx.set_trace_callback(trace)
        except Exception:
            pass
    cx._slow_query_trace = trace
    # Captured AFTER the lookups: if the lookup itself reloaded the config,
    # the bound decision is the one that new dict describes.
    cx._slow_query_config_token = _slow_query_config_token()


def _refresh_slow_query_trace(cx):
    """Apply a Settings write to a POOLED connection without polling.

    A physical handle now outlives the lease that configured it, so a
    slow-query toggle written after the open would otherwise never reach it
    short of a reconnect or a stat on every statement. Neither is acceptable,
    so the identity check below decides -- and it costs nothing when nothing
    changed.
    """
    token = _slow_query_config_token()
    current = getattr(cx, "_slow_query_config_token", _UNBOUND_CONFIG_TOKEN)
    if current is not _UNBOUND_CONFIG_TOKEN and current is token:
        return
    _bind_slow_query_trace(cx)


def _history_file_identity(path):
    """Return the named database inode, or None before first creation."""
    try:
        st = _os.stat(path)
        return st.st_dev, st.st_ino
    except OSError:
        return None


_IDENTITY_BIND_ATTEMPTS = 3


def _open_history_conn_bound(target, cache_path):
    """Open the history database and bind the handle to a PROVEN inode.

    ``db_conn`` caches an idle handle under the identity of the file its path
    named, so that a same-path atomic restore retires the stale handle instead
    of serving the replaced database forever. That identity has to be the one
    the handle actually opened. A restore landing between the pre-open stat
    and the open itself would file a handle on the unlinked PRE-restore inode
    under the REPLACEMENT's identity -- and every later lease would then hit
    in the cache precisely because the wrong identity was recorded.

    So stat, open, and stat again. A changed identity means this open lost the
    race: discard the handle and retry. ``None`` before the open is creation,
    not replacement -- SQLite makes the file itself on first use -- so it must
    not be read as a lost race, or every first open would reopen once.

    Returns ``(connection, identity)``; the identity is the proven one, and
    the caller must key the cache on it rather than statting again.
    """
    if cache_path is None:
        return _open_history_conn(target), None
    for attempt in range(_IDENTITY_BIND_ATTEMPTS):
        before = _history_file_identity(cache_path)
        cx = _open_history_conn(target)
        identity = _history_file_identity(cache_path)
        if (before is None or identity == before
                or attempt == _IDENTITY_BIND_ATTEMPTS - 1):
            return cx, identity
        _close_history_conn(cx)


@contextmanager
def db_conn(path=None):
    """Lease a thread-affine history connection and preserve commit boundaries.

    One idle physical connection is retained per thread. Nested leases open a
    second handle, so an inner context cannot observe or commit its outer
    context's transaction. Path, process, dual-write-mode, or database-file
    identity changes retire the idle handle before reuse; ``:memory:`` retains
    its create-per-call semantics and is never cached.

    Three things open-per-call gave away for free are now explicit, because a
    handle outliving its block no longer provides them:

    * the yielded reference stops working at the end of the block
      (``_HistoryConnection._require_lease``), so an escaped borrower cannot
      inject a statement into the NEXT lease's transaction;
    * the cached identity is the one proven across the open
      (``_open_history_conn_bound``), so a same-path restore racing the open
      cannot be filed under the replacement's inode; and
    * a slow-query settings write reaches the live handle on the next lease
      (``_refresh_slow_query_trace``) instead of waiting for a reconnect,
      without statting the config file when nothing changed.
    """
    target = path or _resolve_db_path()
    target_fs = _os.fspath(target)
    # Explicit paths belong to one-shot integrity work whose captured target
    # must be physically opened and independently observed. Pool the ordinary
    # implicit history path only; :memory: likewise keeps create-per-call
    # semantics.
    cacheable = path is None and target_fs not in (":memory:", b":memory:")
    cache_path = _os.path.abspath(target_fs) if cacheable else None
    cache_key = None
    if cacheable:
        cache_key = (
            _os.getpid(),
            cache_path,
            pg_backend.dual_write_enabled(),
            _history_file_identity(cache_path),
        )

    idle = getattr(_DB_CONN_LOCAL, "idle", None)
    cx = None
    if cacheable and idle is not None:
        _DB_CONN_LOCAL.idle = None
        idle_key, idle_cx = idle
        if idle_key == cache_key:
            cx = idle_cx
        else:
            _close_history_conn(idle_cx)
    if cx is None:
        cx, bound_identity = _open_history_conn_bound(target, cache_path)
        if cacheable:
            # Key on the identity proven stable ACROSS the open. Statting
            # again here would reopen the very window the bound open closes.
            cache_key = (
                _os.getpid(),
                cache_path,
                pg_backend.dual_write_enabled(),
                bound_identity,
            )

    _begin_history_lease(cx)
    _refresh_slow_query_trace(cx)
    _reset_slow_query_trace(cx)
    reusable = cacheable
    try:
        try:
            yield cx
        except BaseException:
            try:
                cx.rollback()
            except Exception:
                reusable = False
            raise
        else:
            try:
                cx.commit()
            except BaseException:
                reusable = False
                raise
    finally:
        # Cursors are finalized while the lease is still live, then the
        # cache-or-close decision runs, and only then does the handle stop
        # answering to the borrower. Ending the lease any earlier would make
        # this block's own cleanup refuse itself.
        if not _finish_history_lease(cx):
            reusable = False
        if reusable and getattr(_DB_CONN_LOCAL, "idle", None) is None:
            _DB_CONN_LOCAL.idle = (cache_key, cx)
        else:
            _close_history_conn(cx)
        _end_history_lease(cx)


# ── v3.48 (#22): Slow-query log ─────────────────────────────────────────
# Wraps SQLite's trace_callback to record statement timings. Anything
# slower than the threshold gets logged at WARNING with the SQL text + a
# brief stack-summary so the operator can trace it back to a Python call
# site.
#
# Default threshold: 100ms — fast enough that normal ops don't trip,
# slow enough to catch the "missing index" / "accidental full scan"
# regressions that destroy throughput.

_SLOW_QUERY_DEFAULTS = {
    "enabled": True,
    "threshold_ms": 100,
    "max_sql_log_len": 500,
}


def _slow_query_log_enabled() -> bool:
    """Check store/env override; default on. Set BD_SLOW_QUERY_LOG=0 to silence.

    v3.66.309 (CLI->GUI parity): global_config store key `slow_query_log`
    overrides the env seed when set (read at call time; lazy import; fail-safe).
    """
    import os as _os
    try:
        from bulk_downloader import global_config as _gc
        sv = _gc.get("slow_query_log", None)
        if sv is not None:
            return bool(sv) if isinstance(sv, bool) else \
                str(sv).strip().lower() not in ("0", "false", "off", "no", "")
    except Exception:
        pass
    v = _os.environ.get("BD_SLOW_QUERY_LOG", "")
    if v.lower() in ("0", "false", "off", "no"):
        return False
    return _SLOW_QUERY_DEFAULTS["enabled"]


def _slow_query_threshold_ms() -> int:
    """Override via store key `slow_query_ms` (v3.66.309) or BD_SLOW_QUERY_MS
    env. Store wins when set; bad value -> default 100ms."""
    import os as _os
    try:
        from bulk_downloader import global_config as _gc
        sv = _gc.get("slow_query_ms", None)
        if sv is not None:
            return int(sv)
    except (ValueError, TypeError):
        return _SLOW_QUERY_DEFAULTS["threshold_ms"]
    except Exception:
        pass
    try:
        return int(_os.environ.get("BD_SLOW_QUERY_MS",
                                   _SLOW_QUERY_DEFAULTS["threshold_ms"]))
    except ValueError:
        return _SLOW_QUERY_DEFAULTS["threshold_ms"]


def _make_slow_query_trace(threshold):
    """Build a fresh tracer closure per connection. Each connection has its
    own 'last statement started at' state; we can't share it because
    multiple connections may be open concurrently (pooled in WAL mode).
    """
    import time as _t
    state = {"last_ts": None, "last_sql": None}

    def reset():
        state["last_ts"] = None
        state["last_sql"] = None

    def tracer(sql_str):
        # set_trace_callback fires AFTER each statement completes. The
        # callback gets the SQL of the JUST-STARTED next statement, so
        # we compute the time the PRIOR statement took based on when
        # we recorded the previous start. The very first call has no
        # prior to time.
        now = _t.perf_counter()
        prior_sql = state["last_sql"]
        prior_start = state["last_ts"]
        state["last_sql"] = sql_str
        state["last_ts"] = now
        if prior_sql is None or prior_start is None:
            return
        elapsed_ms = (now - prior_start) * 1000.0
        if elapsed_ms < threshold:
            return
        # Try to log. We avoid importing the logger at module-load time
        # to dodge circular-import risk; bulk_downloader.log loads after db.
        try:
            from . import log as _bdlog
            logger = _bdlog.get_logger("bulk_downloader.db.slow_query")
            max_len = _SLOW_QUERY_DEFAULTS["max_sql_log_len"]
            short_sql = prior_sql if len(prior_sql) <= max_len else (
                prior_sql[:max_len] + f"… (truncated; full length {len(prior_sql)})"
            )
            logger.warning(
                "slow query %.1fms (threshold %dms): %s",
                elapsed_ms, threshold, short_sql.replace("\n", " ")
            )
        except Exception:
            # Slow-query log failure must never affect the actual query
            pass

    tracer.reset = reset
    return tracer


def db_explain(sql: str, *params) -> list:
    """Helper: run EXPLAIN QUERY PLAN against a candidate SQL and return
    the plan as a list of dicts. Useful when a slow-query warning fires
    and the operator wants to see why. Exposed via /api/db/explain in
    dev mode."""
    with db_conn() as cx:
        rows = cx.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
        return [dict(r) for r in rows]


def db_fts_optimize(*, force=False) -> tuple[bool, str]:
    """v3.48 (#75): periodically optimize the FTS5 history index.

    FTS5 inverted indexes degrade as rows are inserted/deleted; query
    performance can drop 5-10x on a heavily-churned table. The
    `optimize` command rebuilds the index segments in a single pass,
    restoring peak performance. Cheap (seconds even on 100K-row indexes)
    but not free — rate-limited to once per 7 days via sentinel file.

    Returns (ran: bool, reason: str). `ran=True` if optimize actually
    executed; `False` if it was skipped (recent run / FTS disabled /
    error). The reason field explains either outcome."""
    import os as _os
    import time as _t
    from pathlib import Path as _Path
    sentinel = _Path(_resolve_db_path()).parent / ".fts_optimize_last"
    now = _t.time()
    if not force and sentinel.exists():
        try:
            last = float(sentinel.read_text().strip())
            if now - last < 604800:  # 7 days
                return False, f"skipped: ran {(now-last)/3600:.1f}h ago"
        except (ValueError, OSError):
            pass
    try:
        with db_conn() as cx:
            # Confirm FTS table exists before running optimize against it.
            row = cx.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='history_fts'").fetchone()
            if not row:
                return False, "skipped: history_fts not present"
            cx.execute("INSERT INTO history_fts(history_fts) "
                       "VALUES('optimize')")
        try:
            sentinel.write_text(str(now))
        except OSError:
            pass
        return True, "fts5 index optimized"
    except sqlite3.OperationalError as e:
        return False, f"failed: {e}"


def _fts_indexed_docs(cx):
    """Rowids the history_fts inverted index actually holds, or None when
    that set cannot be derived.

    `SELECT count(*) FROM history_fts` reads THROUGH to `history` on an
    EXTERNAL-CONTENT table, so it can never show a desync -- the same
    read-through that makes the one-time backfill guard in db_init
    unsatisfiable. fts5vocab reads the index itself, which is the only
    denominator that contains the subject.

    Stated rather than hidden: a document whose indexed columns are all
    empty contributes no term instances and is invisible here, so it
    reads as unindexed.
    """
    try:
        cx.execute("DROP TABLE IF EXISTS temp._bd_fts_docs")
        cx.execute("CREATE VIRTUAL TABLE temp._bd_fts_docs "
                   "USING fts5vocab('main', 'history_fts', 'instance')")
        try:
            return {r[0] for r in cx.execute(
                "SELECT DISTINCT doc FROM temp._bd_fts_docs").fetchall()}
        finally:
            cx.execute("DROP TABLE IF EXISTS temp._bd_fts_docs")
    except sqlite3.DatabaseError:
        return None


_FTS_COLS = ("site_name", "url", "filename", "message", "status")


def db_fts_snapshot(cx, ids):
    """The PRE-UPDATE rows an FTS re-sync will need, on `cx`.

    Call this BEFORE the UPDATE. The FTS5 'delete' command removes a doc by
    replaying the values the index was built from, so it needs the row as it
    was; issued with post-update values it matches nothing, removes nothing,
    and reports success while the stale terms stay. That ordering is the entire
    difficulty of this fix, which is why the snapshot is a separate call the
    reader can see rather than something db_fts_resync does for itself.

    Returns a list of sqlite3.Row. Unknown ids simply do not appear.
    """
    ids = [int(i) for i in ids]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return cx.execute(
        f"SELECT id, {', '.join(_FTS_COLS)} FROM history WHERE id IN ({marks})",
        ids).fetchall()


def db_fts_resync(cx, old_rows) -> dict:
    """Re-point history_fts at the CURRENT values of `old_rows`, on `cx`.

    Call this AFTER the UPDATE, passing what db_fts_snapshot returned. Removes
    the old terms, then re-indexes each row from `history` as it now stands.

    NOT A TRIGGER, for the reason db_fts_forget records: 'delete' for a doc the
    index does not hold raises DatabaseError, and from inside an AFTER UPDATE
    trigger that would roll the whole UPDATE back -- one unindexed row would
    turn a routine rename into a 500 on a desynced database.

    Rows the index never held are re-indexed anyway rather than skipped: the
    row is live and searchable-by-intent, so adding it is a repair, not a
    surprise. db_fts_forget already reports those as `unindexed`, not an error.

    Returns db_fts_forget's dict plus `reindexed`, the number of rows written
    back, counted by re-reading the index rather than by "execute() did not
    raise".
    """
    old_rows = list(old_rows)
    out = db_fts_forget(cx, old_rows)
    out["reindexed"] = 0
    if not out.get("present") or not old_rows:
        return out
    cols = ", ".join(_FTS_COLS)
    coalesced = ", ".join(f"COALESCE({c}, '')" for c in _FTS_COLS)
    for r in old_rows:
        rid = r["id"] if not isinstance(r, dict) else r.get("id")
        try:
            cx.execute(
                f"INSERT INTO history_fts(rowid, {cols}) "
                f"SELECT id, {coalesced} FROM history WHERE id = ?", (int(rid),))
        except Exception:
            out["failed"] = out.get("failed", 0) + 1
            continue
        out["reindexed"] += 1
    return out


def db_fts_forget(cx, rows) -> dict:
    """Drop `rows` from the history_fts inverted index, on `cx`.

    history_fts is an FTS5 EXTERNAL-CONTENT table (content='history',
    content_rowid='id'), so SQLite maintains NOTHING for it: a deleted
    history row keeps its terms forever unless the application issues
    the FTS5 'delete' command with that row's OLD column values. `rows`
    are sqlite3.Row/dict records carrying id, site_name, url, filename,
    message and status.

    NOT A TRIGGER, deliberately. 'delete' for a doc the index does not
    hold raises DatabaseError, and from inside an AFTER DELETE trigger
    that aborts and rolls back the whole DELETE -- one unindexed row
    would turn a prune of a desynced database into a 500.

    Membership is DERIVED from the index, never inferred from an
    exception class. Measured: the malformed-image error fires on a row
    that IS indexed (and that is removed correctly) and stays silent on
    a row whose indexed text was updated in place, so the exception
    carries no information about which row the index held. Counting it
    as "not indexed" announces a desync on a healthy database.

    Returns {present, verified, requested, applied, unindexed,
    remaining, failed}:
      present    there is an index to maintain at all
      verified   the counts were re-read from the index afterwards;
                 False means they are UNKNOWN, not zero
      applied    docs that actually left the index -- verified by
                 re-reading, not by "execute() did not raise"
      unindexed  rows the index did not hold: skipped, not an error
      remaining  rows that were indexed, were asked to leave, and are
                 still there -- the index holds terms nobody remembers
                 because an FTS-indexed column was updated in place
      failed     'delete' statements that raised; an error count, not
                 an outcome. `applied` is the outcome.
    """
    out = {"present": False, "verified": False, "requested": 0,
           "applied": 0, "unindexed": 0, "remaining": 0, "failed": 0}
    rows = list(rows)
    out["requested"] = len(rows)
    try:
        if not cx.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='history_fts'").fetchone():
            return out
    except sqlite3.DatabaseError:
        return out
    out["present"] = True
    if not rows:
        out["verified"] = True
        return out
    before = _fts_indexed_docs(cx)
    if before is None:
        return out
    targets = [r for r in rows if r["id"] in before]
    out["unindexed"] = len(rows) - len(targets)
    for r in targets:
        try:
            cx.execute(
                "INSERT INTO history_fts(history_fts, rowid, site_name, "
                "url, filename, message, status) "
                "VALUES('delete', ?, ?, ?, ?, ?, ?)",
                (r["id"], r["site_name"] or "", r["url"] or "",
                 r["filename"] or "", r["message"] or "", r["status"] or ""))
        except sqlite3.OperationalError:
            # FTS5 gone from under us mid-batch. Nothing further will
            # work on this index; stop rather than log the same failure
            # once per row.
            out["failed"] += 1
            break
        except sqlite3.DatabaseError:
            out["failed"] += 1
    after = _fts_indexed_docs(cx)
    if after is None:
        return out
    out["verified"] = True
    ids = [r["id"] for r in targets]
    out["applied"] = sum(1 for i in ids if i not in after)
    out["remaining"] = sum(1 for i in ids if i in after)
    return out


def db_queue_recovery_summary() -> dict:
    """v3.48 (#127): on boot, report how many queue rows were recovered.

    Returns a dict with counts by status. Lets the operator (and the new
    /api/health endpoint) confirm that no jobs were silently dropped
    across a restart. The runner's __init__ already loads these rows
    into memory; this just inspects what's there."""
    out = {"total": 0, "by_status": {}, "per_site": {}}
    try:
        with db_conn() as cx:
            for row in cx.execute(
                "SELECT site_id, status, COUNT(*) AS n "
                "FROM queue GROUP BY site_id, status"
            ).fetchall():
                d = dict(row)
                out["total"] += d["n"]
                out["by_status"][d["status"]] = (
                    out["by_status"].get(d["status"], 0) + d["n"]
                )
                out["per_site"].setdefault(d["site_id"], {})[
                    d["status"]] = d["n"]
    except Exception as e:
        out["error"] = str(e)
    return out


def db_log(site_id, site_name, url, status, filename="", file_size=0, message="", screenshot="", honeypot_score=None, best_effort=False, bytes_fetched=None, transfer_mode=None, file_path=None, title="", title_source=""):
    """Append one row to the history table. Called on every job-level
    state transition (done/failed/needs_review). Append-only — the row
    is never updated or deleted by application code.

    ``bytes_fetched`` is how many bytes BD actually transferred over the
    network for this job:

        >0    a real transfer
        0     nothing was transferred — a skip_if_exists hit, a Stash dedup
              hit, a 416 resume of an already-complete file, a yt-dlp
              "has already been downloaded", a click with no download dir
        None  this path does not record it — UNKNOWN, and never proof of a
              download

    It exists because no other column can answer the question. ``file_size``
    is not a transfer count: both download helpers return an on-disk stat
    (runner_transport.py:1526, runner_browser.py:25), so a skipped job records
    the size of the file that was already there. ``message`` is prose that
    varies per path, and one of the no-fetch paths positively asserts a
    download that did not happen ("Downloaded via yt-dlp fallback").

    Measured on the deploy host before this existed: eight consecutive seeded
    runs, seven of them skips, and the live check that reads this table
    reported "the end-to-end pipeline has worked" for every one.

    NOTE ON THE DELETER. The line this docstring used to carry — "the only
    deleter is db_prune" — was false. batch_ops.bulk_delete (batch_ops.py:161)
    issues DELETE FROM history WHERE id = ? and is reachable over HTTP at
    POST /api/batch/delete. tools/live_seed.py inherited the false claim from
    here and repeated it in its teardown report.

    v3.43.80 Phase 92: also feeds the FTS5 mirror table when present.
    Wrapped in try/except so an FTS write failure (e.g. table dropped
    out-of-band) never blocks the canonical history insert.

    v3.50 (Phase 3): when status is 'done' and a filename is present,
    also create/update the library row (forward path). This is the
    single chokepoint for "a download finished" — hooking here means
    every completion site in runner.py feeds the library without
    needing N individual call-site edits.

    ``transfer_mode`` is WHICH transport moved the bytes, stated by the branch
    that performed it:

        'segmented'  hls_downloader drove ffmpeg over an .m3u8/.mpd manifest
        'http'       the direct httpx path (_http_download)
        'browser'    Playwright's own download event (_pw_save)
        None         this path does not record it -- UNKNOWN, and never proof
                     that the transfer was NOT segmented

    It exists because no other column can answer "did a segmented download
    complete", and the live check that asks (L12) was inferring it from URL
    spelling and message prose. Both are structurally unable to see the generic
    scrape path's segmented downloads: this function is passed the PAGE url, so
    the manifest never lands in the table, and the done-path message below is
    the empty string. Measured on the deploy host 2026-07-30: a segmented
    download completed (3498 bytes, ffprobe h264) and L12 reported "none
    segmented ... no stream was queued".

    NULL IS A LOWER BOUND, NOT A NEGATIVE. Every pre-v9 row is NULL, and so is
    every path that does not pass the argument -- including
    runner_extractors._try_plugin_extractor, which performs a real segmented
    transfer and then `return True`s without logging a row of its own. A
    consumer may read >0 'segmented' rows as proof the path works; it may not
    read 0 as proof it does not.

    v3.66.36 (P5-2b): optional ``honeypot_score`` (float in [0,1] or
    None) persists the resolve-time honeypot score onto the row so the
    per-site threshold learner can later quantile-fit confirmed traps.
    Default None → column stays NULL, byte-for-byte compatible with prior
    callers."""
    with db_conn() as cx:
        try:
            cur = cx.execute("INSERT INTO history(site_id,site_name,url,status,filename,file_size,message,screenshot,honeypot_score,bytes_fetched,transfer_mode) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (site_id, site_name, url, status, filename, file_size, message, screenshot, honeypot_score, bytes_fetched, transfer_mode))
            history_id = cur.lastrowid
        except Exception as _ins_exc:
            # F3: a 'done' row records a download that already succeeded on
            # disk and passed verification. If the canonical history insert
            # fails (lock contention, disk full, corruption), it must NOT
            # propagate: the caller's done-path would otherwise treat the
            # exception as a download failure, flip the completed job to
            # 'failed', and re-download. Swallow on the done-path (and for
            # any explicit best_effort caller); all other statuses still
            # raise so their callers can react.
            if best_effort or status == "done":
                import sys as _sys
                _sys.stderr.write(
                    f"  ! db_log history insert failed (best-effort, "
                    f"status={status}): {_ins_exc}\n")
                return
            raise
        try:
            cx.execute("INSERT INTO history_fts(rowid, site_name, url, filename, message, status) "
                       "VALUES(?,?,?,?,?,?)",
                       (history_id, site_name or "", url or "",
                        filename or "", message or "", status or ""))
        except sqlite3.OperationalError:
            pass  # FTS5 not present; fall back to LIKE in search
    # v3.50: forward-path library record. Done OUTSIDE the db_conn block
    # above because library_record opens its own connection — nesting
    # would risk a write-lock contention on the same DB. Best-effort:
    # a library bug must never break the canonical history insert.
    # v3.66.837: record ONLY an absolute path. library.file_path is UNIQUE and
    # scan() inserts absolute paths, so a bare basename could never collide
    # with the scanner's row: every download produced a second, permanent
    # ghost row -- born file_exists=0, disagreeing with its twin on file_size.
    # The basename is not reconstructible here either: `filename` has already
    # had template subdirs stripped, and joining a configured download_dir
    # would be wrong for template subdirs, deployment-default and spillover
    # dirs alike. So the caller passes the absolute path it already holds, and
    # a caller that has none (or produced no file at all, like the GCW probe)
    # records nothing rather than a row that is wrong.
    # The test is ABSOLUTENESS, not which parameter carried it. Some callers
    # have always passed a full path as `filename` -- the Stash dedup path
    # (runner_integrations.py) does, and its rows were correct; keying only on
    # the new argument would have silently stopped recording them.
    if status == "done":
        # Inside the try, not outside it: F3 above requires that nothing on the
        # done path propagates, or the caller flips a completed job to failed
        # and re-downloads it. str()/isabs() on caller-supplied data is a thin
        # surface, but it is not the zero surface a bare truthiness test was.
        try:
            _lib_path = file_path or filename
            if _lib_path and _os.path.isabs(str(_lib_path)):
                from . import library as _library
                _library.library_record(
                    str(_lib_path), history_id=history_id, site_id=site_id,
                    file_size=file_size, title=title,
                    title_source=title_source)
        except Exception:
            pass


def _history_title_projection(cx, alias="h"):
    """Return a title-enriched SELECT projection and optional library JOIN.

    Some direct callers create only the base history table with ``db_init``.
    Read paths must keep working in that pre-migration shape, while still
    publishing explicit empty fields instead of omitting them.
    """
    try:
        history_cols = {
            row[1] for row in cx.execute("PRAGMA table_info(history)").fetchall()
        }
        library_cols = {
            row[1] for row in cx.execute("PRAGMA table_info(library)").fetchall()
        }
    except sqlite3.Error:
        history_cols = set()
        library_cols = set()
    if "library_id" in history_cols and "title" in library_cols:
        source = (
            "COALESCE(l.title_source, '')"
            if "title_source" in library_cols else "''"
        )
        projection = (
            f"{alias}.*, COALESCE(l.title, '') AS title, "
            f"{source} AS title_source"
        )
        return projection, f" LEFT JOIN library l ON l.id = {alias}.library_id"
    return f"{alias}.*, '' AS title, '' AS title_source", ""


def db_normalize_history_title(site_id: str, url: str, raw_title: str,
                               title: str, title_source: str) -> int:
    """Retroactively strip a template once another scene proves it repeats.

    The compare against ``raw_title`` is deliberate: library metadata is
    editable, so a title the operator has already changed must not be replaced
    by the template learner. Returns the exact number of library rows enriched.
    """
    if not site_id or not url or not raw_title or not title:
        return 0
    if raw_title == title:
        return 0
    try:
        with db_conn() as cx:
            history_cols = {
                row[1]
                for row in cx.execute("PRAGMA table_info(history)").fetchall()
            }
            library_cols = {
                row[1]
                for row in cx.execute("PRAGMA table_info(library)").fetchall()
            }
            if "library_id" not in history_cols or not {
                "title", "title_source"
            }.issubset(library_cols):
                return 0
            cur = cx.execute(
                "UPDATE library SET title=?, title_source=? "
                "WHERE title=? AND id IN ("
                "SELECT library_id FROM history "
                "WHERE site_id=? AND url=? AND library_id IS NOT NULL)",
                (title, title_source, raw_title, site_id, url),
            )
            return max(0, int(cur.rowcount or 0))
    except Exception:
        return 0


def db_search_fts(query: str, *, site_id=None, status=None, limit: int = 100):
    """v3.43.80 Phase 92: full-text search over history via FTS5.

    `query` accepts FTS5 MATCH syntax — bare terms are AND-ed,
    OR-ing is explicit, NEAR/PHRASE are supported. Returns a list of
    dicts ordered by bm25 rank (most relevant first), with the matched
    text snippets included as `snippet_url` / `snippet_message` /
    `snippet_filename`.

    Falls back to LIKE-based db_search() automatically when FTS5
    isn't available or the query parses as a syntax error — the
    operator never sees a 500 just because they used a quote that
    FTS5 hates.

    Site/status filters are applied AFTER the FTS match so the
    relevance ranking isn't skewed by under-represented sites.
    """
    if not query or not query.strip():
        return []
    # v3.47.7: defend against stored XSS. SQLite's snippet() literally
    # inserts the delimiter strings into the result; if the indexed text
    # itself contained `<script>...` (because a target site put HTML in a
    # URL or filename), the snippet would render as live HTML in the
    # search-results UI. Fix: use control-char sentinels here, then
    # HTML-escape the snippet text after fetch and swap sentinels for
    # real <mark> tags. ASCII 0x02 (STX) and 0x03 (ETX) cannot legitimately
    # appear in URLs, filenames, or event messages.
    _M_OPEN  = "\x02"
    _M_CLOSE = "\x03"
    def _safe_snippet(s):
        if not s: return s or ""
        # Escape HTML metacharacters in the indexed text, THEN swap our
        # control-char sentinels for the real <mark>/</mark> tags. This
        # preserves match highlighting while neutralizing any HTML the
        # target site smuggled into a URL/filename/message.
        s = (s.replace("&", "&amp;")
              .replace("<", "&lt;")
              .replace(">", "&gt;")
              .replace('"', "&quot;")
              .replace("'", "&#39;"))
        return s.replace(_M_OPEN, "<mark>").replace(_M_CLOSE, "</mark>")
    try:
        with db_conn() as cx:
            projection, library_join = _history_title_projection(cx, "h")
            sql = f"""SELECT {projection},
                    snippet(history_fts, 1, ?, ?, '…', 16) AS snippet_url,
                    snippet(history_fts, 2, ?, ?, '…', 16) AS snippet_filename,
                    snippet(history_fts, 3, ?, ?, '…', 16) AS snippet_message
             FROM history_fts
             JOIN history h ON h.id = history_fts.rowid
             {library_join}
             WHERE history_fts MATCH ?"""
            params = [_M_OPEN, _M_CLOSE,
                      _M_OPEN, _M_CLOSE,
                      _M_OPEN, _M_CLOSE,
                      query.strip()]
            if site_id:
                sql += " AND h.site_id = ?"
                params.append(site_id)
            if status:
                sql += " AND h.status = ?"
                params.append(status)
            sql += " ORDER BY bm25(history_fts) LIMIT ?"
            params.append(int(limit))
            rows = [dict(r) for r in cx.execute(sql, params).fetchall()]
        for row in rows:
            for k in ("snippet_url", "snippet_filename", "snippet_message"):
                if k in row:
                    row[k] = _safe_snippet(row[k])
        return rows
    except sqlite3.OperationalError:
        # FTS5 missing OR malformed query — fall back to LIKE search
        # with the bare terms ANDed together. Preserves "find me
        # something with these words" intent without crashing.
        return db_search(site_id=site_id, status=status, query=query.strip(), limit=limit)

def db_search(site_id=None, status=None, query=None, limit=200):
    """Read recent history rows with optional filters. `query` substring-
    matches url/filename/message. Returns a list of dicts ordered newest
    first, capped at `limit`. Used by the Logs tab and the Recent panel."""
    with db_conn() as cx:
        projection, library_join = _history_title_projection(cx, "h")
        sql = f"SELECT {projection} FROM history h{library_join} WHERE 1=1"
        params = []
        if site_id: sql += " AND h.site_id=?"; params.append(site_id)
        if status:  sql += " AND h.status=?";  params.append(status)
        if query:
            sql += " AND (h.url LIKE ? OR h.filename LIKE ? OR h.message LIKE ?)"
            params += [f"%{query}%"] * 3
        sql += " ORDER BY h.id DESC LIMIT ?"; params.append(limit)
        return [dict(r) for r in cx.execute(sql, params).fetchall()]


def db_search_cursor(site_id=None, status=None, query=None,
                     after_id=None, limit=100):
    """v3.48 (#74): cursor-based pagination on the history table.

    Returns (rows, next_cursor). The next_cursor is the id of the LAST
    returned row; callers pass it back as `after_id` on the next request
    to fetch the page immediately older. When there are no more rows,
    next_cursor is None.

    Why this exists: the classic `LIMIT N OFFSET M` pattern degrades to
    O(M) scan cost. Past ~10K rows, requesting "page 100" forces SQLite
    to walk and discard 9,900 rows before emitting 100. Cursor pagination
    uses the indexed `id DESC` order directly, giving O(log N) cost
    regardless of how deep the user has scrolled.

    Index used: `idx_hist_site_ts` already covers `site_id, ts DESC`
    which is fine for our case since `id` is autoincrement-monotonic with
    `ts` (the row with id=N was inserted before the row with id=N+1).
    """
    with db_conn() as cx:
        projection, library_join = _history_title_projection(cx, "h")
        sql = f"SELECT {projection} FROM history h{library_join} WHERE 1=1"
        params = []
        if site_id: sql += " AND h.site_id=?"; params.append(site_id)
        if status:  sql += " AND h.status=?";  params.append(status)
        if query:
            sql += " AND (h.url LIKE ? OR h.filename LIKE ? OR h.message LIKE ?)"
            params += [f"%{query}%"] * 3
        if after_id is not None:
            # Strict less-than because `id` is unique. This is what makes
            # cursor pagination work — no risk of duplicate rows across
            # pages from offset-style INSERT-in-flight races.
            sql += " AND h.id < ?"
            params.append(int(after_id))
        sql += " ORDER BY h.id DESC LIMIT ?"
        params.append(int(limit))
        rows = [dict(r) for r in cx.execute(sql, params).fetchall()]
        next_cursor = rows[-1]["id"] if len(rows) == int(limit) else None
        return rows, next_cursor


# ─── v3.66.221 (F1.5): pre-download exact-URL history match ───────────
def db_find_url_in_history(url, *, exclude_site=None):
    """F1.5: exact-URL pre-download dedup. Returns the most recent
    successfully-downloaded ('done') history row for this exact URL, or
    None. Caller uses it to skip a re-download (status skipped_duplicate)
    and link the prior row. Read-only; fail-soft to None on any error so a
    lookup failure never blocks a legitimate download.

    Distinct from db_find_filename_duplicate (fuzzy basename+size match):
    this is an exact URL string equality on the indexed `url` column and is
    the default-ON dedup path; the fuzzy filename match is the opt-in path.
    """
    if not url:
        return None
    try:
        with db_conn() as cx:
            sql = ("SELECT id, site_id, site_name, url, filename, file_size, ts "
                   "FROM history WHERE url=? AND status='done'")
            params = [url]
            if exclude_site:
                sql += " AND site_id != ?"
                params.append(exclude_site)
            sql += " ORDER BY id DESC LIMIT 1"
            row = cx.execute(sql, params).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def db_find_filename_duplicate(filename, file_size=None, exclude_site=None):
    """Phase 66 (v3.41.0): cross-site filename duplicate detection. Returns
    a row from history where this filename was previously downloaded
    successfully, or None if not seen.

    Matching is on the basename (last path segment), case-insensitive,
    with extension preserved. Optional `file_size` check: when set, only
    matches when sizes are within 5% (catches encoder variants vs exact
    duplicates).

    `exclude_site` skips entries from the current site_id — typical
    caller wants to know if another site already grabbed this."""
    if not filename: return None
    # Strip directory path, keep only basename
    import os as _os
    basename = _os.path.basename(filename)
    if not basename: return None
    # AUDIT FIX (v3.42.0): escape SQL LIKE wildcards. A filename containing
    # `%` or `_` would match unintended rows (e.g., `_movie.mp4` matches
    # `xmovie.mp4`). Not a SQLi (we're parameterized), but a false-positive
    # source. Use the ESCAPE clause to neutralize them.
    escaped = basename.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with db_conn() as cx:
        sql = ("SELECT id, site_id, site_name, url, filename, file_size, ts "
               "FROM history WHERE status='done' AND LOWER(filename) LIKE ? ESCAPE '\\'")
        params = [f"%{escaped}"]
        if exclude_site:
            sql += " AND site_id != ?"; params.append(exclude_site)
        sql += " ORDER BY id DESC LIMIT 1"
        row = cx.execute(sql, params).fetchone()
        if not row:
            return None
        if file_size and row["file_size"] and file_size > 0:
            # Size sanity check — within 5% means probably same file
            pct = abs(file_size - row["file_size"]) / max(file_size, row["file_size"])
            if pct > 0.05:
                return None
        return dict(row)

# Rows 560, 561 and 607. THE PREDICATE THAT DECIDES WHETHER A 'done' ROW PROVES
# BD MOVED BYTES, in one place because two callers must not drift apart.
#
# Row 544 (shipped v3.66.1397) rules that a zero-byte 'done' row is not
# ownership, and it is right: runner_transport.py's "Already have" arm writes
# db_log(..., bytes_fetched=0, "already on disk") itself, runner_integrations.py
# writes the same shape for a Stash dedup hit, and library_record's
# `history_id = COALESCE(?, history_id)` then makes that row the library row's
# CURRENT owner. So neither "zero" nor "current" can separate a real completion
# from a self-manufactured one -- A7's shape of deriving the expected set from
# the artifact under test.
#
# THE COLUMN THAT CAN. ``transfer_mode`` is WHICH transport moved the bytes,
# stated by the branch that performed it ('segmented' | 'http' | 'browser').
# EVERY no-transfer 'done' arm in the tree omits the keyword and records NULL,
# because none of them ran a transport; the HTTP 416 resume-complete arm reaches
# the success db_log through `elif use_http:` and stamps 'http'. That arm is a
# fully downloaded, on-disk file whose final call moved no bytes because the
# server said the range was already satisfied -- a real completion recorded as a
# measured zero. tests/test_row607_a_history_row_proves_a_real_transfer.py parses
# the no-transfer call sites and refuses if one ever starts stamping the column.
#
# NOT ``transfer_mode`` ALONE. runner_extractors.py has a 'done' path stamping
# transfer_mode='http' with bytes_fetched=None, and NULL is UNKNOWN -- never
# proof of a download, per db_log's own contract and CLAUDE.md A2. The proof is
# a MEASURED count: positive, or an explicit zero from a named transport.
_TRANSFER_PROOF_WITH_MODE = (
    "((h.bytes_fetched IS NOT NULL AND h.bytes_fetched > 0) "
    "OR (h.bytes_fetched = 0 AND h.transfer_mode IS NOT NULL))")
_TRANSFER_PROOF_NO_MODE = (
    "(h.bytes_fetched IS NOT NULL AND h.bytes_fetched > 0)")
# A PREDICATE MUST NOT NAME THE COLUMN WHOSE ABSENCE SELECTED IT. ``bytes_fetched``
# arrives with migration 8, and every other predicate here reads it -- so the arm
# for a table that LACKS it cannot be written in terms of it. Returning
# _TRANSFER_PROOF_NO_MODE there produced `no such column: h.bytes_fetched`, which
# db_skip_identity's bare handler turned into "unknown" for EVERY url (the exact
# failure the degradation exists to prevent) and which db_prune did not catch at
# all, so POST /api/history/prune returned 500. The honest predicate is FALSE:
# with no measured byte count in the schema, nothing in the record can prove a
# transfer, and UNKNOWN is never permission (CLAUDE.md A2). db_skip_identity then
# reports "unproven" for a current owner whose file is on disk, so the operator
# LEARNS that the attribution records no transfer instead of silently
# re-downloading.
_TRANSFER_PROOF_UNMEASURABLE = "(0)"


def _transfer_proof_sql(cx):
    """The proof predicate for this database's ACTUAL schema, aliased ``h``.

    ``transfer_mode`` arrives with migration 9. On a database that has not been
    migrated the column does not exist, and naming it would raise -- which
    db_skip_identity's bare handler would turn into "unknown" for EVERY url,
    silently converting a pre-v9 host's whole history into re-downloads. The
    degraded predicate is exactly the shipped row 544 rule, so an unmigrated
    host keeps its current behaviour rather than losing it.

    ``bytes_fetched`` (migration 8) is the older column and is checked FIRST,
    because the row 544 rule is itself written in terms of it and cannot degrade
    to something that names it. See _TRANSFER_PROOF_UNMEASURABLE above.

    A connection that cannot answer PRAGMA at all still degrades to the shipped
    row 544 rule rather than to UNMEASURABLE. That is deliberate and unchanged:
    the schema is not known to be missing anything, and turning a transient
    probe failure into "nothing is ever proven" would revisit row 544 on every
    host rather than on the un-migrated one this arm is about.
    """
    try:
        cols = {row[1] for row in
                cx.execute("PRAGMA table_info(history)").fetchall()}
    except sqlite3.Error:
        return _TRANSFER_PROOF_NO_MODE
    if "bytes_fetched" not in cols:
        return _TRANSFER_PROOF_UNMEASURABLE
    if "transfer_mode" not in cols:
        return _TRANSFER_PROOF_NO_MODE
    return _TRANSFER_PROOF_WITH_MODE


def db_skip_identity(page_url, final_path):
    """Is the file already on disk PROVABLY the same work as ``page_url``?

    Four states, not three (rows 479, 547, 560, 561, 607):

        "same"      this url is the CURRENT owner of a file that is on disk
                    now, and some done row of this url at that library row
                    PROVES a transfer (see ``_transfer_proof_sql``)
        "different" the file on disk is attributed to another url
        "unproven"  a done row points at a file that exists, but nothing in
                    the record proves BD ever fetched it -- ``bytes_fetched``
                    0 with no transport named, or NULL (a pre-v8 row,
                    unmeasurable). The caller must surface this; it is not
                    ownership.
        "unknown"   nothing is known either way


    The "already have" pre-download check used to ask only whether a file sat
    at the rendered path, and then answer a different question: "this page's
    work is already downloaded". Those are the same question only while the
    filename template varies per scene. Under a template like
    ``{site} - {resolution}``, or on a site whose ``?filename=`` basename is
    generic, every scene renders one name -- so scene B's history row was
    written 'done' over scene A's bytes, and db_log's done path then handed
    scene B's title to ``library_record``, whose

        title = CASE WHEN ?<>'' THEN ? ELSE title END

    RETITLED scene A's library row to scene B. Wrong file, right title: the
    shape of the 2026-08-29 incident, recorded twice over.

    Existence is therefore not identity, and this returns three states rather
    than a boolean, per CLAUDE.md A7 -- an unmeasurable identity is UNKNOWN,
    and UNKNOWN is never permission:

        ("same", path)     a prior 'done' row for THIS url recorded ``path``
                           through the library, and ``path`` is still on disk.
                           Skipping is correct, and ``path`` -- NOT the freshly
                           rendered one -- is the file to report and to hand to
                           db_log, because they differ whenever an earlier run
                           landed at a ``safe_dest`` suffix.
        ("different", None) the library attributes ``final_path`` to another
                           page url. Provably not this work; the caller must
                           neither skip nor overwrite it.
        ("unknown", None)  nothing attributes ``final_path`` to anything -- a
                           hand-copied file, a scanner row with no history, a
                           pruned history, or a table this schema lacks. Not
                           provably the same work, so not a skip.

    KEYED ON THE ATTRIBUTED PATH, NOT ON ``final_path``. That is what stops the
    UNKNOWN arm from accreting a copy forever: run 1 leaves an unprovable file
    alone and lands at ``name_1``; run 2 renders ``name`` again, but the url now
    owns ``name_1``, so it answers "same" and skips.

    Attribution is read through BOTH directions of
    ``history.library_id``/``library.history_id``, which db_log and
    library_record backfill on the insert and the update path alike, and the two
    answer different questions. ``library.history_id`` is CURRENT -- it names the
    history row whose url most recently wrote that path -- and decides WHICH file
    this url owns. ``history.library_id`` is HISTORICAL -- every completion at a
    reused UNIQUE library path keeps pointing at the same library row -- and
    supplies the transfer evidence for the path identity already chose.

    Reading only the historical direction let an older url answer "same" over a
    newer url's bytes (row 547). Reading only the current direction would accept
    the skip arm's own row, because that row wins the ``history_id`` COALESCE
    exactly as a real completion does (row 607) -- and would break the healthy
    steady state, whose current row is always the newest skip. Neither half is
    the answer on its own.

    ``history.filename`` cannot answer any of it: that column holds a basename
    with template subdirectories already stripped, never a path.
    """
    final_path = str(final_path or "")
    if not page_url or not final_path:
        return ("unknown", None)
    import os as _os
    try:
        with db_conn() as cx:
            proof = _transfer_proof_sql(cx)
            # PRONG 1 -- IDENTITY: which path does this url own RIGHT NOW?
            # Read through library.history_id, the CURRENT half of the
            # bidirectional link. history.library_id alone is historical: every
            # completion at a reused UNIQUE library path points at the same
            # library row forever, so an older url still resolved that path
            # after another url's bytes replaced the file there, skipped over
            # them and retitled the row back (row 547).
            #
            # A DANGLING CURRENT LINK IS NOT PERMISSION. history rows are
            # deleted by db_prune AND by batch_ops.bulk_delete (reachable at
            # POST /api/batch/delete), so library.history_id can point at a row
            # that is gone. Treating an unclaimed library row as this url's
            # would let an older url skip over a newer one's bytes whenever the
            # newer url's row was deleted -- row 547 arriving by another door,
            # and UNKNOWN converted into permission (CLAUDE.md A2). The JOIN
            # below requires a LIVE owner, so an orphan falls through to
            # "unknown" and the job downloads. db_prune repairs the link it
            # breaks itself; nothing else may assume it was repaired.
            current = cx.execute(
                "SELECT l.id AS lid, l.file_path AS fp, l.file_size AS file_size FROM library l "
                "JOIN history h ON h.id = l.history_id "
                "WHERE h.url = ? AND h.status = 'done' "
                "AND COALESCE(l.file_path,'') <> '' "
                "ORDER BY h.id DESC",
                (page_url,)).fetchall()
            unproven = None
            for row in current:
                # A recorded path whose file has since been deleted proves
                # nothing about what is on disk NOW, so keep looking rather
                # than skipping on the strength of a row alone.
                if not _os.path.isfile(row["fp"]):
                    continue
                # Row 503. A path that merely still exists is not the recorded
                # work if its independently stored size no longer describes the
                # bytes at that path.  Do not let a later skip restat the
                # replacement and overwrite the only contrary measurement.
                try:
                    observed_size = _os.path.getsize(row["fp"])
                except OSError:
                    continue
                recorded_size = row["file_size"]
                if (not isinstance(recorded_size, int)
                        or isinstance(recorded_size, bool)
                        or recorded_size < 0
                        or observed_size != recorded_size):
                    return ("unknown", None)
                # PRONG 2 -- EVIDENCE: does ANY 'done' row of this url at this
                # library row prove a transfer?
                #
                # SCANNED, not read off the current row. The healthy steady
                # state is one real transfer followed by any number of
                # no-transfer skip rows, and each skip becomes the current
                # owner in turn -- so a fix that judged only the current row
                # would turn every legitimate skip into a re-download. It is
                # also why the current row alone cannot be the proof: the skip
                # arm writes that row itself (row 544).
                proven = cx.execute(
                    "SELECT 1 FROM history h WHERE h.url = ? "
                    "AND h.status = 'done' AND h.library_id = ? "
                    "AND " + proof + " LIMIT 1",
                    (page_url, row["lid"])).fetchone()
                if proven is not None:
                    return ("same", row["fp"])
                if unproven is None:
                    unproven = row["fp"]
            if unproven is not None:
                # A file IS there, this url IS its current owner, and nothing
                # in the record says BD ever fetched it. Naming this state
                # separately is the point: "unknown" would send the job down
                # the transfer path silently, and the operator would never learn
                # that an existing attribution was unproven.
                return ("unproven", unproven)
            # THE HISTORICAL DIRECTION, for operator visibility only. An
            # upgraded host carries 'done' rows written before any of this
            # existed: bytes_fetched 0 over someone else's file, or a pre-v8
            # NULL. They never became the current owner, so prong 1 cannot see
            # them, and falling straight through would make row 479's
            # needs_review diagnostic unreachable on exactly the databases it
            # was written for.
            #
            # A STALE ROW THAT DOES PROVE A TRANSFER IS DELIBERATELY EXCLUDED.
            # Once library.history_id names another url, the bytes at that path
            # are no longer the bytes this url fetched (row 547), and the
            # "different" arm below is both the correct verdict and the honest
            # one: the needs_review message this "unproven" produces says the
            # attribution RECORDS NO TRANSFER, which would be false of it. A7 --
            # a diagnostic that collapses distinct failures costs the
            # investigation.
            stale = cx.execute(
                "SELECT l.file_path AS fp, " + proof + " AS proven "
                "FROM history h JOIN library l ON l.id = h.library_id "
                "WHERE h.url = ? AND h.status = 'done' "
                "AND COALESCE(l.file_path,'') <> '' "
                "ORDER BY h.id DESC",
                (page_url,)).fetchall()
            present = [r for r in stale if _os.path.isfile(r["fp"])]
            if present and not any(r["proven"] for r in present):
                return ("unproven", present[0]["fp"])
            attributed = cx.execute(
                "SELECT h.url AS url FROM library l "
                "LEFT JOIN history h ON h.id = l.history_id "
                "WHERE l.file_path = ?",
                (final_path,)).fetchone()
    except Exception:
        # A measurement that could not be taken is UNKNOWN, not OK.
        return ("unknown", None)
    if attributed is not None and attributed["url"] and attributed["url"] != page_url:
        return ("different", None)
    return ("unknown", None)


def db_stats(site_id=None):
    """Aggregate history counts and total downloaded bytes for the
    dashboard. Returns `{"counts": {status: n, ...}, "bytes": total_done_bytes}`.
    Only the 'done' status contributes to `bytes` (other statuses
    don't represent successfully-saved files)."""
    with db_conn() as cx:
        sql = "SELECT status,COUNT(*) c, COALESCE(SUM(file_size),0) bytes FROM history"
        params = []
        if site_id: sql += " WHERE site_id=?"; params.append(site_id)
        sql += " GROUP BY status"
        rows = cx.execute(sql, params).fetchall()
        out = {"counts": {}, "bytes": 0}
        for r in rows:
            out["counts"][r["status"]] = r["c"]
            if r["status"] == "done": out["bytes"] = r["bytes"]
        return out


def db_hourly_success_rate(site_id=None, since_days=30):
    """Phase 74 (v3.41.0): time-of-day analytics. Aggregates from history
    to find hours-of-day where success rate is unusually low.

    Returns a list of 24 dicts, one per hour:
        [{"hour": 0, "total": 12, "done": 10, "failed": 2, "rate": 0.83}, ...]

    Filters to the last `since_days` days. site_id None means all sites.
    The 'rate' field is the success rate (0..1); 'total' is the sample
    size at that hour. Hours with total=0 are still in the list with
    rate=None so the consumer can render a clear "no data" indicator."""
    with db_conn() as cx:
        # AUDIT FIX (v3.42.0): SQLite's strftime defaults to UTC. The user
        # cares about local time (their "bad hours" should match their
        # wall clock). The 'localtime' modifier converts before formatting.
        sql = ("SELECT CAST(strftime('%H', ts, 'localtime') AS INTEGER) AS hour, "
               "status, COUNT(*) AS c "
               "FROM history WHERE ts >= datetime('now', ?)")
        params = [f"-{int(since_days)} days"]
        if site_id:
            sql += " AND site_id=?"
            params.append(site_id)
        sql += " GROUP BY hour, status"
        rows = cx.execute(sql, params).fetchall()
        # Build a per-hour bucket
        buckets = {h: {"total": 0, "done": 0, "failed": 0, "needs_review": 0}
                   for h in range(24)}
        for r in rows:
            h = int(r["hour"])
            status = r["status"]
            c = int(r["c"])
            if h in buckets:
                buckets[h]["total"] += c
                if status in ("done", "failed", "needs_review"):
                    buckets[h][status] += c
        out = []
        for h in range(24):
            b = buckets[h]
            t = b["total"]
            rate = (b["done"] / t) if t > 0 else None
            out.append({"hour": h, "total": t,
                        "done": b["done"], "failed": b["failed"],
                        "rate": rate})
        return out


def db_prune(days):
    """Delete history rows older than `days` days. Returns the count
    removed. Useful for keeping the DB file small over months of
    operation — completion history isn't infinitely valuable.

    v3.66.820: also drops the removed rows from the history_fts
    EXTERNAL-CONTENT index (db_fts_forget). Before this, every
    pruned row left its terms in the inverted index permanently.
    Return type is unchanged: POST /api/history/prune puts this
    int straight in the JSON body.

    ROW 563: THE NEWEST TRANSFER-PROVING 'done' ROW PER URL IS RETAINED.
    ``db_skip_identity``'s healthy steady state is ONE real transfer followed by
    any number of no-transfer skip rows, so the only row that proves ownership
    is the OLDEST one -- exactly the row an age-based prune reaches first. Losing
    it turned a healthy repeated skip into a needless re-download and a
    full-size duplicate at ``name_1.mp4``. The carve-out cannot be made in
    ``db_skip_identity`` instead: the rows that survive the prune are the
    self-manufactured skip rows, and accepting those is precisely the defect
    row 544 closed. Retention is per-URL and keyed on evidence, so a history of
    failures, skips and no-transfer rows still empties completely."""
    with db_conn() as cx:
        # v3.66.820: the cutoff is computed ONCE and bound into both
        # statements. Two separate `datetime('now', ...)` evaluations
        # can straddle a second boundary and select different row sets,
        # which would strip a LIVE row from the index -- unsearchable,
        # the opposite and worse failure.
        cutoff = cx.execute("SELECT datetime('now', ?)",
                            (f"-{int(days)} days",)).fetchone()[0]
        # The retained set is materialised into a TEMP TABLE rather than
        # concatenated into the two statements below, for two reasons.
        #
        # ONE SOURCE OF TRUTH. The doomed SELECT and the DELETE must select the
        # same rows or a LIVE row loses its FTS terms -- unsearchable, and the
        # worse failure the cutoff is computed once for. A temp table gives both
        # statements one identical, literal WHERE clause.
        #
        # AND THE DELETE STAYS A STRING LITERAL. tests/test_fts_external_content
        # _delete.py enumerates every function that passes a `DELETE FROM
        # history` CONSTANT to .execute() and then checks each maintains the FTS
        # index. Building this statement by concatenation made db_prune
        # invisible to that gate -- an untracked deleter, which is precisely the
        # bug the gate exists to catch.
        #
        # The proof predicate is only interpolated into a SELECT, and only from
        # module constants chosen by `_transfer_proof_sql`; no caller value
        # reaches SQL text.
        cx.execute("DROP TABLE IF EXISTS _bd_prune_keep")
        cx.execute("CREATE TEMP TABLE _bd_prune_keep(id INTEGER PRIMARY KEY)")
        try:
            keep_ids = [r[0] for r in cx.execute(
                "SELECT MAX(h.id) FROM history h WHERE h.status = 'done' "
                "AND " + _transfer_proof_sql(cx) + " GROUP BY h.url"
            ).fetchall() if r[0] is not None]
            for _kid in keep_ids:
                cx.execute("INSERT INTO _bd_prune_keep(id) VALUES(?)", (_kid,))
            doomed = cx.execute(
                "SELECT id, site_name, url, filename, message, status "
                "FROM history WHERE ts < ? "
                "AND id NOT IN (SELECT id FROM _bd_prune_keep)",
                (cutoff,)).fetchall()
            # THE LINKS THIS DELETE IS ABOUT TO BREAK, named BEFORE it breaks
            # them, because afterwards the deleted row's url is gone and there
            # is nothing left to identify the owner by.
            #
            # THE SAME LITERAL WHERE CLAUSE as the DELETE below and the SELECT
            # above, for the same reason the temp table exists: three statements
            # that must agree about one row set. Keyed by LIBRARY row, so the
            # snapshot is bounded by the number of files whose current owner is
            # doomed -- typically zero -- and not by the number of history rows
            # a long prune removes.
            #
            # Its own handler, and one that CANNOT SKIP THE DELETE: a caller
            # that created only the history table (db_init without the library
            # schema) has no library to snapshot, and the prune must still
            # prune. An empty snapshot then means "no links to repair", which is
            # true of exactly that shape.
            try:
                broken = [(r["lid"], r["url"]) for r in cx.execute(
                    "SELECT l.id AS lid, h.url AS url "
                    "FROM library l JOIN history h ON h.id = l.history_id "
                    "WHERE h.ts < ? "
                    "AND h.id NOT IN (SELECT id FROM _bd_prune_keep)",
                    (cutoff,)).fetchall()]
            except sqlite3.OperationalError:
                broken = []
            db_fts_forget(cx, doomed)
            removed = cx.execute(
                "DELETE FROM history WHERE ts < ? "
                "AND id NOT IN (SELECT id FROM _bd_prune_keep)",
                (cutoff,)).rowcount
            # REPAIR THE LINKS THIS DELETE JUST BROKE -- THOSE, AND ONLY THOSE,
            # AND ONLY BACK TO THE URL THAT HELD THEM.
            #
            # library.history_id names the history row whose url most recently
            # wrote that path, and db_skip_identity refuses to read a library row
            # whose owner is gone -- correctly, because an unclaimed row would
            # let an older url skip over a newer one's bytes. Retaining the
            # evidence row is therefore not enough on its own: the retained row
            # must also be the one the library names.
            #
            # THE FIRST VERSION OF THIS REPAIR WAS UNSCOPED, and the scope is the
            # whole safety property. Gated only on "the current owner is gone and
            # SOMETHING carries this library id", it
            #
            #   * repaired links this prune never broke. batch_ops.bulk_delete
            #     (POST /api/batch/delete) also deletes history rows, and
            #     db_skip_identity's answer for that dangle is a deliberate
            #     "unknown" -- so whether an out-of-band delete was honoured came
            #     to depend on whether a scheduled prune had run since; and
            #   * repointed ACROSS URLS. MAX(h.id) took the newest surviving row
            #     at that path whoever wrote it, so a prune that deleted ZERO
            #     rows could hand url B's file to url A. A then skipped over B's
            #     bytes, reported "Already have", wrote a done row, and
            #     library_record's `title = CASE WHEN ?<>'' THEN ? ELSE title END`
            #     retitled B's library row to A's title. Wrong file, right title
            #     -- CLAUDE.md A7's 2026-08-29 shape, through the prune door.
            #
            # So: one UPDATE per library row in `broken`, addressed by that row's
            # id, choosing the newest surviving 'done' row OF THAT ROW'S OWN
            # URL. That is the rule library_record actually applies, which only
            # ever writes from db_log's `status == "done"` arm on behalf of one
            # url. Requiring 'done' also keeps the installed owner readable:
            # db_skip_identity's prong-1 JOIN requires it.
            #
            # A LIBRARY ROW WHOSE URL HAS NO SURVIVING COMPLETION KEEPS ITS
            # DANGLING ID. The EXISTS guard is what prevents the correlated
            # subquery writing NULL there. Dangling is a legitimate terminal
            # state, not a hole to fill: db_skip_identity answers "unknown" for
            # it, the job re-downloads to a safe_dest name, and the next write
            # re-links it. Repointing to SOMETHING is what the defect above cost.
            try:
                for _lid, _url in broken:
                    if not _url:
                        continue
                    cx.execute(
                        "UPDATE library SET history_id = ("
                        "  SELECT MAX(h.id) FROM history h "
                        "  WHERE h.library_id = library.id "
                        "    AND h.url = ? AND h.status = 'done') "
                        "WHERE id = ? "
                        "AND EXISTS (SELECT 1 FROM history s "
                        "            WHERE s.library_id = library.id "
                        "              AND s.url = ? AND s.status = 'done')",
                        (_url, _lid, _url))
            except sqlite3.OperationalError:
                # A caller that created only the history table (db_init without
                # the library schema) has nothing to repair -- and `broken` is
                # already empty there. Any other failure still propagates.
                pass
            return removed
        finally:
            # The connection is pooled, so a surviving temp table would silently
            # retain rows on the NEXT prune -- a stale artifact deciding a
            # destructive operation.
            try:
                cx.execute("DROP TABLE IF EXISTS _bd_prune_keep")
            except Exception:
                pass

def db_vacuum():
    """Run SQLite VACUUM to reclaim space from deleted rows. Returns
    True on success, False on any error. Does NOT use db_conn() because
    VACUUM can't run inside a transaction — it needs a fresh connection
    with autocommit semantics.

    v3.66.795 (MOD-3 cut 1): it still needs its own CONNECTION, but no longer
    its own CONNECT -- it opens through `_open_history_conn()` like everything
    else, so the seam stays the single place that knows how to reach the
    history DB. The isolation level is untouched (`_open_history_conn` leaves
    it at the sqlite3 default, under which VACUUM is not wrapped in an implicit
    transaction), so the autocommit semantics this function relies on are
    unchanged; it simply also gets the standard timeout/pragmas now."""
    cx = _open_history_conn()
    try: cx.execute("VACUUM"); return True
    except Exception: return False
    finally: cx.close()

# ─── QUEUE PERSISTENCE (Phase 4.2) ─────────────────────────────────────────

def queue_load(site_id):
    """Return all queue entries for a site, ordered by `ord` then ts_added.
    Used at SiteRunner construction to restore state from a previous run.

    v3.66.511: tolerate a not-yet-created `queue` table. ``_create_site``
    constructs a SiteRunner (which calls this) and a stale-bytecode / first-call
    ordering can reach here before ``db_init`` has run, making the SELECT raise
    ``OperationalError("no such table: queue")`` and 500-ing the add-site POST.
    On that specific error we lazily run ``db_init`` (idempotent CREATE TABLE IF
    NOT EXISTS) and retry once, returning ``[]`` for the brand-new table. Any
    other OperationalError still propagates.
    """
    _SELECT = "SELECT * FROM queue WHERE site_id=? ORDER BY ord, ts_added"
    with db_conn() as cx:
        try:
            rows = cx.execute(_SELECT, (site_id,)).fetchall()
        except sqlite3.OperationalError as e:
            if "no such table" not in str(e).lower():
                raise
            rows = None
    if rows is None:
        # Connection above is closed; db_init opens its own. Create the schema,
        # then retry the read once against the now-existing table.
        db_init()
        with db_conn() as cx:
            rows = cx.execute(_SELECT, (site_id,)).fetchall()
    return [dict(r) for r in rows]


def queue_search(site_id=None, query=None, status=None, priority=None,
                 *, after_ord=None, limit=200):
    """v3.49 (#71): Server-side queue filtering with cursor pagination.

    Different from `db.db_search()` which hits the history table; this
    queries the LIVE queue. Used by the queue tab's search input when
    the queue exceeds the client-side filter's comfort threshold
    (≥ ~2000 rows — anything more and a MutationObserver-based filter
    starts to lag the main thread on input).

    Returns (rows, next_cursor) following the same cursor pattern as
    db_search_cursor. `after_ord` is the `ord` column of the LAST row
    returned in the previous batch.

    `query` substring-matches against url, message, filename, screenshot."""
    sql = "SELECT * FROM queue WHERE 1=1"
    params = []
    if site_id:
        sql += " AND site_id=?"; params.append(site_id)
    if status:
        sql += " AND status=?"; params.append(status)
    if priority:
        sql += " AND priority=?"; params.append(priority)
    if query:
        sql += (" AND (url LIKE ? OR message LIKE ? "
                "OR filename LIKE ? OR screenshot LIKE ?)")
        params += [f"%{query}%"] * 4
    if after_ord is not None:
        sql += " AND ord > ?"
        params.append(int(after_ord))
    sql += " ORDER BY ord, ts_added LIMIT ?"
    params.append(int(limit))
    with db_conn() as cx:
        rows = [dict(r) for r in cx.execute(sql, params).fetchall()]
    next_cursor = rows[-1]["ord"] if len(rows) == int(limit) else None
    return rows, next_cursor


def queue_count_by_status(site_id=None) -> dict:
    """v3.49: aggregate queue counts by status for a site (or globally).
    Used by the queue-summary panel and the SSE dashboard pushes."""
    sql = ("SELECT status, COUNT(*) AS n FROM queue "
           "WHERE site_id=? GROUP BY status" if site_id else
           "SELECT status, COUNT(*) AS n FROM queue GROUP BY status")
    params = (site_id,) if site_id else ()
    out = {}
    with db_conn() as cx:
        for r in cx.execute(sql, params).fetchall():
            out[r["status"]] = r["n"]
    return out


def queue_group_by(site_id, group_by: str = "host", *, limit=2000) -> dict:
    """v3.49 (#57): bucket queue rows into groups for collapsible-section
    display.

    `group_by` values:
      - 'host'     — domain (e.g. 'cdn1.example.com') from URL
      - 'path'     — first path segment after host (e.g. '/videos/')
      - 'status'   — pending/running/done/etc.
      - 'priority' — high/normal/low/(blank)

    Returns {group_key: [row, row, ...]}. Within each bucket, rows keep
    their `ord` ordering. Limit caps total rows considered, not per-
    group; with 50K queues, this is a courtesy to the UI more than the
    DB (sqlite can scan 50K rows in well under 50ms).

    Server-side grouping (rather than client-side) is the cheaper
    pattern when groups are mostly uniform — the UI gets to render one
    section at a time, and the slow bits (regex on every URL to extract
    host/path) happen once on the server."""
    if group_by not in ("host", "path", "status", "priority"):
        group_by = "host"
    rows, _ = queue_search(site_id=site_id, limit=limit)
    groups = {}
    from urllib.parse import urlparse as _urlparse
    for r in rows:
        if group_by == "status":
            key = r.get("status") or "unknown"
        elif group_by == "priority":
            key = r.get("priority") or "normal"
        else:
            # host or path — parse the URL
            try:
                parsed = _urlparse(r.get("url") or "")
            except ValueError:
                parsed = None
            if group_by == "host":
                key = (parsed.netloc if parsed else "") or "(no host)"
            else:  # path
                parts = (parsed.path if parsed else "").split("/")
                # First non-empty path segment, fall back to "/"
                key = next((p for p in parts if p), "/")
        groups.setdefault(key, []).append(r)
    return groups

_QUEUE_COLUMNS = frozenset({
    "status", "message", "retries", "retry_after", "screenshot",
    "force_download", "priority", "ord", "filename", "file_size",
    "listing_title", "lane", "depends_on",
})

def queue_upsert(site_id, url, **fields):
    """Insert or update a single queue row. Stamps ts_updated automatically.
    Common case is updating an existing row's status/message during a run.

    Phase 41.7: defends against any caller smuggling a malicious column
    name into the f-string. All callers in this codebase pass developer-
    controlled kwargs, but a whitelist is cheap and removes a class of
    bug. Unknown keys are silently dropped + logged."""
    # Drop unknown column names defensively
    bad = [k for k in fields if k not in _QUEUE_COLUMNS]
    if bad:
        try:
            import logging
            logging.getLogger("bulk_downloader.db").warning(
                "queue_upsert: dropping unknown column(s) %s for site_id=%s",
                bad, site_id)
        except Exception: pass
        fields = {k: v for k, v in fields.items() if k in _QUEUE_COLUMNS}
    fields["ts_updated"] = None  # marker, overwritten below
    with db_conn() as cx:
        # Try update first (the hot path: existing job changed state)
        if len(fields) > 1:
            update_fields = {k: v for k, v in fields.items() if k != "ts_updated"}
            cols = ", ".join(f"{k}=?" for k in update_fields)
            cols += ", ts_updated=strftime('%Y-%m-%dT%H:%M:%S','now')"
            params = list(update_fields.values()) + [site_id, url]
            cur = cx.execute(f"UPDATE queue SET {cols} WHERE site_id=? AND url=?", params)
            if cur.rowcount > 0: return
        # Fall through to insert
        defaults = {"status":"pending","message":"","retries":0,"retry_after":0,
                    "screenshot":"","force_download":0,"priority":"","ord":0,
                    "filename":"","listing_title":"","file_size":0,
                    "lane":"default","depends_on":""}
        defaults.update({k: v for k, v in fields.items() if k != "ts_updated"})
        cx.execute("""INSERT OR REPLACE INTO queue
            (site_id,url,status,message,retries,retry_after,screenshot,
             force_download,priority,ord,filename,listing_title,file_size,lane,depends_on)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (site_id, url, defaults["status"], defaults["message"],
             defaults["retries"], defaults["retry_after"], defaults["screenshot"],
             defaults["force_download"], defaults["priority"], defaults["ord"],
             defaults["filename"], defaults["listing_title"], defaults["file_size"],
             defaults["lane"], defaults["depends_on"]))

def queue_bulk_upsert(site_id, urls, ord_start=0, listing_titles=None):
    """Bulk-insert URLs in one transaction. Massively faster than per-URL
    upserts for large lists (one transaction vs N)."""
    title_map = listing_titles if isinstance(listing_titles, dict) else {}
    with db_conn() as cx:
        cx.executemany(
            "INSERT OR IGNORE INTO queue(site_id,url,status,ord,listing_title) "
            "VALUES(?,?,'pending',?,?)",
            [(site_id, u, ord_start + i, title_map.get(u, ""))
             for i, u in enumerate(urls)])

def queue_delete(site_id, url):
    """Remove one URL from the queue table. Used when a user deletes
    a single URL from the queue UI."""
    with db_conn() as cx:
        cx.execute("DELETE FROM queue WHERE site_id=? AND url=?", (site_id, url))

def queue_delete_status(site_id, status):
    """For "Clear Done" / "Clear Failed" bulk actions."""
    with db_conn() as cx:
        return cx.execute("DELETE FROM queue WHERE site_id=? AND status=?",
                          (site_id, status)).rowcount

def queue_delete_site(site_id):
    """Called when a site is removed."""
    with db_conn() as cx:
        cx.execute("DELETE FROM queue WHERE site_id=?", (site_id,))


# ── v3.49 (#55): bulk queue operations ──────────────────────────────────
# UI workflows where the operator wants to act on N URLs at once:
#   - "Select all failed → bulk retry"
#   - "Filter by tag → bulk delete"
#   - "Top-5 of the queue → bulk priority-high"
# Doing N round-trips for these would be wasteful. Each helper here
# wraps the operation in a single transaction so it's atomic and fast
# even on lists of thousands.

def queue_bulk_delete(site_id, urls):
    """Delete N rows in one transaction. Returns rowcount.

    `urls` is an iterable of URL strings; the SQL uses parameter binding
    so injection isn't a concern, but the list is chunked at 500/batch
    to stay under SQLite's default 999-parameter limit."""
    urls = list(urls)
    if not urls:
        return 0
    deleted = 0
    with db_conn() as cx:
        for chunk_start in range(0, len(urls), 500):
            chunk = urls[chunk_start:chunk_start + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = cx.execute(
                f"DELETE FROM queue WHERE site_id=? "
                f"AND url IN ({placeholders})",
                (site_id, *chunk))
            deleted += cur.rowcount or 0
    return deleted


def queue_bulk_mark(site_id, urls, status, *, message=""):
    """Set status (and optionally message) on N URLs in one transaction.
    Returns rowcount. Allowed statuses match the per-URL `jobs/mark`
    endpoint: pending, failed, needs_review, done."""
    urls = list(urls)
    if not urls:
        return 0
    allowed = {"pending", "failed", "needs_review", "done"}
    if status not in allowed:
        raise ValueError(f"status must be one of {sorted(allowed)}")
    updated = 0
    with db_conn() as cx:
        for chunk_start in range(0, len(urls), 500):
            chunk = urls[chunk_start:chunk_start + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = cx.execute(
                f"UPDATE queue SET status=?, message=?, "
                f"ts_updated=strftime('%Y-%m-%dT%H:%M:%S','now') "
                f"WHERE site_id=? AND url IN ({placeholders})",
                (status, message, site_id, *chunk))
            updated += cur.rowcount or 0
    return updated


def queue_reorder(site_id, url_to_ord):
    """v3.49 (#56): bulk-update `ord` column for drag-to-reorder.

    `url_to_ord` is a dict {url: new_ord_int}. Wrapped in a single
    transaction so the queue is always in a consistent partial-order
    state, never half-reordered. Returns rowcount."""
    if not url_to_ord:
        return 0
    updated = 0
    with db_conn() as cx:
        # executemany is the right primitive — single transaction,
        # bound parameters, no string interpolation, no parameter limit
        cur = cx.executemany(
            "UPDATE queue SET ord=?, "
            "ts_updated=strftime('%Y-%m-%dT%H:%M:%S','now') "
            "WHERE site_id=? AND url=?",
            [(int(ord_val), site_id, url)
             for url, ord_val in url_to_ord.items()])
        updated = cur.rowcount or 0
    return updated


def queue_set_priority(site_id, urls, priority):
    """v3.49 (#71): tag a set of URLs with a priority label.

    Priority is a freeform short string ('high', 'normal', 'low', or
    empty); the runner consults it when picking the next URL to dequeue.
    Empty string clears the flag."""
    urls = list(urls)
    if not urls:
        return 0
    if priority and len(priority) > 20:
        priority = priority[:20]
    updated = 0
    with db_conn() as cx:
        for chunk_start in range(0, len(urls), 500):
            chunk = urls[chunk_start:chunk_start + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = cx.execute(
                f"UPDATE queue SET priority=?, "
                f"ts_updated=strftime('%Y-%m-%dT%H:%M:%S','now') "
                f"WHERE site_id=? AND url IN ({placeholders})",
                (priority, site_id, *chunk))
            updated += cur.rowcount or 0
    return updated


def queue_bulk_update(site_id, urls, **fields):
    """v3.62.x: set the SAME column values on N URLs in ONE (chunked)
    transaction. The bulk counterpart to queue_upsert for the runner's
    bulk_* operations (pause/resume/retry/approve, retry-all, the
    folder-scan done-marking). Those used to loop queue_upsert — one
    transaction per URL — which, on a large selection, is a write
    storm that serializes against the download workers and stalls the
    whole app. One transaction here instead of N.

    Updates existing rows only. Unknown column names are dropped (same
    whitelist as queue_upsert); ts_updated is always stamped. Chunked
    at 500 URLs/statement to stay under SQLite's parameter limit.
    Returns the rowcount."""
    urls = list(urls)
    if not urls or not fields:
        return 0
    bad = [k for k in fields if k not in _QUEUE_COLUMNS]
    if bad:
        try:
            import logging
            logging.getLogger("bulk_downloader.db").warning(
                "queue_bulk_update: dropping unknown column(s) %s for "
                "site_id=%s", bad, site_id)
        except Exception: pass
        fields = {k: v for k, v in fields.items() if k in _QUEUE_COLUMNS}
    if not fields:
        return 0
    # Column names come only from the _QUEUE_COLUMNS whitelist; values
    # are bound — same injection-safety model as queue_upsert.
    set_clause = ", ".join(f"{k}=?" for k in fields)
    set_clause += ", ts_updated=strftime('%Y-%m-%dT%H:%M:%S','now')"
    set_vals = list(fields.values())
    updated = 0
    with db_conn() as cx:
        for chunk_start in range(0, len(urls), 500):
            chunk = urls[chunk_start:chunk_start + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = cx.execute(
                f"UPDATE queue SET {set_clause} "
                f"WHERE site_id=? AND url IN ({placeholders})",
                (*set_vals, site_id, *chunk))
            updated += cur.rowcount or 0
    return updated


# ── Phase 2 Cut 2.1: dead-letter helpers ─────────────────────────────────
# A retry-exhausted job moves to the TERMINAL 'dead_letter' status (distinct from
# plain 'failed', which the queue housekeeping can re-pick) with a reason. Listed
# via queue_search(status='dead_letter'); requeued explicitly by the operator.
# Same lifecycle plugins.py already uses for webhook-sink delivery.

def db_queue_dead_letter(site_id, url, reason=""):
    """Move a job to the terminal 'dead_letter' status with a reason. Returns
    True if a row was updated."""
    with db_conn() as cx:
        cur = cx.execute(
            "UPDATE queue SET status='dead_letter', message=?, "
            "ts_updated=strftime('%Y-%m-%dT%H:%M:%S','now') "
            "WHERE site_id=? AND url=?",
            (str(reason or "dead-lettered"), site_id, url))
    return cur.rowcount > 0


def db_queue_requeue_dead_letter(site_id, url):
    """Requeue a dead-lettered job: back to 'pending', retry counters cleared.
    Returns True if a row was updated. Only acts on rows currently dead-lettered
    so a live/done job can't be silently reset."""
    with db_conn() as cx:
        cur = cx.execute(
            "UPDATE queue SET status='pending', retries=0, retry_after=0, "
            "message='requeued from dead-letter', "
            "ts_updated=strftime('%Y-%m-%dT%H:%M:%S','now') "
            "WHERE site_id=? AND url=? AND status='dead_letter'",
            (site_id, url))
    return cur.rowcount > 0


def queue_count(site_id, status=None):
    """Return the number of queue rows for a site. With `status` set,
    counts only rows in that status. Used for the dashboard's per-site
    pending count without loading every row into memory."""
    with db_conn() as cx:
        if status:
            r = cx.execute("SELECT COUNT(*) c FROM queue WHERE site_id=? AND status=?",
                           (site_id, status)).fetchone()
        else:
            r = cx.execute("SELECT COUNT(*) c FROM queue WHERE site_id=?",
                           (site_id,)).fetchone()
        return r["c"]

def queue_paginate(site_id, status_filter=None, search=None, offset=0, limit=200):
    """Server-side pagination for the queue UI (Phase 4.5/4.6)."""
    with db_conn() as cx:
        sql = "SELECT * FROM queue WHERE site_id=?"
        params = [site_id]
        if status_filter and status_filter != "all":
            sql += " AND status=?"; params.append(status_filter)
        if search:
            sql += " AND url LIKE ?"; params.append(f"%{search}%")
        sql += " ORDER BY ord, ts_added LIMIT ? OFFSET ?"
        params += [limit, offset]
        return [dict(r) for r in cx.execute(sql, params).fetchall()]

def queue_changed_since(site_id, ts_since, limit=500):
    """Return queue rows updated since the given ISO timestamp. Used by
    delta-polling endpoint (Phase 4.6) so the frontend only re-renders rows
    that actually changed."""
    with db_conn() as cx:
        rows = cx.execute(
            "SELECT * FROM queue WHERE site_id=? AND ts_updated > ? ORDER BY ts_updated LIMIT ?",
            (site_id, ts_since, limit)).fetchall()
        return [dict(r) for r in rows]


# ─── v3.43.15: session_history helpers ────────────────────────────

def session_event_record(site_id, account_idx, event_type, detail=""):
    """Append one row to session_history. event_type is one of:
        'login'              - fresh login succeeded (zero point for
                                lifetime measurement)
        'heartbeat_ok'       - we verified the session is still valid
        'heartbeat_fail'     - session rejected by server
        'auto_relogin_ok'    - the keep-alive triggered a relogin
                                that succeeded
        'auto_relogin_fail'  - relogin attempt failed (bad creds,
                                rate-limited, network down)
        'needs_takeover'     - relogin needs a human (captcha, 2FA)
    """
    import time
    with db_conn() as cx:
        cx.execute(
            "INSERT INTO session_history(ts, site_id, account_idx, event_type, detail) "
            "VALUES(?,?,?,?,?)",
            (time.time(), site_id, account_idx, event_type, detail or ""))


def session_event_recent(site_id=None, account_idx=None, limit=100):
    """Return recent session_history rows. Used by the UI event log."""
    with db_conn() as cx:
        sql = "SELECT * FROM session_history WHERE 1=1"
        params = []
        if site_id:
            sql += " AND site_id=?"; params.append(site_id)
        if account_idx is not None:
            sql += " AND account_idx=?"; params.append(account_idx)
        sql += " ORDER BY id DESC LIMIT ?"; params.append(limit)
        return [dict(r) for r in cx.execute(sql, params).fetchall()]


def session_lifetime_observations(site_id, account_idx=None, lookback_days=30):
    """For a given (site, account), find all session lifetimes we've
    observed and return the list in seconds. A 'lifetime' is the time
    from a 'login' or 'auto_relogin_ok' event to the next
    'heartbeat_fail' or 'auto_relogin_fail' event.

    Walk the history in time order. Each successful login starts a
    measurement window; the next failure closes it. Heartbeat_ok in
    between proves the session was still alive at that point but
    doesn't close the window.

    Used to predict expiry: median of observed lifetimes is a robust
    estimate that ignores extreme outliers (network blips that
    looked like session failures, very-short test runs).
    """
    import time
    cutoff = time.time() - lookback_days * 86400
    with db_conn() as cx:
        sql = ("SELECT id, ts, event_type FROM session_history "
               "WHERE site_id=? AND ts >= ?")
        params = [site_id, cutoff]
        if account_idx is not None:
            sql += " AND account_idx=?"; params.append(account_idx)
        # Adjacent events can share a timestamp on coarse-resolution clocks.
        # Preserve their causal insertion order so a next-cycle login cannot
        # sort ahead of the failure that closed the previous cycle.
        sql += " ORDER BY ts ASC, id ASC"
        rows = cx.execute(sql, params).fetchall()
    lifetimes = []
    start = None  # ts of the last 'login' or 'auto_relogin_ok' we saw
    for r in rows:
        et = r["event_type"]
        ts = r["ts"]
        if et in ("login", "auto_relogin_ok"):
            start = ts
        elif et in ("heartbeat_fail", "auto_relogin_fail") and start is not None:
            lifetimes.append(ts - start)
            start = None
    return lifetimes


# ─── v3.66.219 (F2.1): session failure clustering ─────────────────────
# Failure event types in session_history (see session_event_record):
_SESSION_FAILURE_EVENTS = ("heartbeat_fail", "auto_relogin_fail", "needs_takeover")
_SESSION_SUCCESS_EVENTS = ("login", "heartbeat_ok", "auto_relogin_ok")


def db_session_failure_clusters(lookback_days=7):
    """F2.1: cluster session_history failure events by (site, event_type)
    over a recent window. Read-only aggregation — no mutation.

    Returns a dict:
        {
          "lookback_days": N,
          "since_ts": <unix>,
          "clusters": [ {site_id, event_type, count, last_ts}, ... ]   # desc by count
          "per_site": { site_id: {failures, successes, by_type{...},
                                  last_failure_ts} },
          "total_failures": int,
        }

    Uses the idx_sh_site_ts / idx_sh_event indexes. event_type taxonomy is
    fixed by session_event_record; we count only the three failure kinds and
    track successes for a per-site failure-rate denominator.
    """
    import time as _t
    cutoff = _t.time() - max(1, int(lookback_days)) * 86400
    try:
        with db_conn() as cx:
            rows = cx.execute(
                "SELECT site_id, event_type, ts FROM session_history "
                "WHERE ts >= ? ORDER BY ts ASC",
                (cutoff,)).fetchall()
    except Exception:
        # Table absent / not yet initialized -> read-only surface stays empty
        # rather than 500-ing the cockpit panel.
        rows = []
    clusters = {}          # (site_id, event_type) -> {count, last_ts}
    per_site = {}          # site_id -> {failures, successes, by_type{}, last_failure_ts}
    total_failures = 0
    for r in rows:
        sid = r["site_id"]
        et = r["event_type"]
        ts = r["ts"]
        ps = per_site.setdefault(
            sid, {"failures": 0, "successes": 0, "by_type": {},
                  "last_failure_ts": None})
        if et in _SESSION_FAILURE_EVENTS:
            key = (sid, et)
            c = clusters.setdefault(key, {"count": 0, "last_ts": None})
            c["count"] += 1
            if c["last_ts"] is None or ts > c["last_ts"]:
                c["last_ts"] = ts
            ps["failures"] += 1
            ps["by_type"][et] = ps["by_type"].get(et, 0) + 1
            if ps["last_failure_ts"] is None or ts > ps["last_failure_ts"]:
                ps["last_failure_ts"] = ts
            total_failures += 1
        elif et in _SESSION_SUCCESS_EVENTS:
            ps["successes"] += 1
    cluster_list = [
        {"site_id": k[0], "event_type": k[1],
         "count": v["count"], "last_ts": v["last_ts"]}
        for k, v in clusters.items()
    ]
    # Most urgent first: highest count, then most recent.
    cluster_list.sort(key=lambda c: (c["count"], c["last_ts"] or 0), reverse=True)
    return {
        "lookback_days": int(lookback_days),
        "since_ts": cutoff,
        "clusters": cluster_list,
        "per_site": per_site,
        "total_failures": total_failures,
    }


# ── v3.47.8 (#42): scheduled deep integrity check ──────────────────────
# SQLite corruption is rare but silent until it bites — the first symptom
# is usually a fetchone() that returns wrong data, or a write that vanishes
# at the next read. By the time the user notices, the damaged file has been
# the working state for days or weeks.
#
# Defense: a full PRAGMA integrity_check, once per 24 hours, on a
# background thread so it doesn't block startup. `integrity_check` walks
# the entire btree + indexes + foreign keys (vs. quick_check which only
# does the btree pages) and returns "ok" or a list of specific corruptions.
#
# Note: selftest already runs quick_check synchronously at boot via
# auto_recover_sqlite() — that catches "obviously broken." This one
# catches "subtly broken" on a daily cadence.
import os as _os
import time as _time
from pathlib import Path as _Path

_INTEGRITY_STATE_FILE = ".integrity_last_run"
_INTEGRITY_INTERVAL_S = 24 * 60 * 60  # 24 hours

def _integrity_state_path(path=None):
    """Where we record the last successful check timestamp. Lives next to
    the DB so it travels with backups + survives BD_HOME changes."""
    _resolved = path or _resolve_db_path()
    db_dir = _Path(_resolved).parent if _resolved else _Path.cwd()
    return db_dir / _INTEGRITY_STATE_FILE

def _last_integrity_check_ts():
    """Returns the unix timestamp of the most recent successful check, or
    0 if no record exists / file is unreadable."""
    p = _integrity_state_path()
    if not p.exists():
        return 0
    try:
        return float(p.read_text().strip() or 0)
    except (OSError, ValueError):
        return 0

def _record_integrity_check_ts(ts, path=None):
    """Atomic write of the timestamp marker. Best-effort — a failed write
    just means we'll re-run the check sooner than 24h next time, which
    is harmless."""
    p = _integrity_state_path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(f"{ts}\n")
        _os.replace(str(tmp), str(p))
    except OSError:
        pass

def run_integrity_check(force=False, sync=False):
    """Run PRAGMA integrity_check on a background thread, debounced to
    once per 24 hours unless force=True.

    Args:
        force: ignore the 24h debounce and run immediately.
        sync:  run inline (caller waits) instead of on a background thread.
               Used by tests and by the post-recovery verification path.

    Returns:
        - sync=False:  the threading.Thread (or None if debounced out)
        - sync=True:   dict with {ok, result, elapsed_s}
    """
    now = _time.time()
    if not force and (now - _last_integrity_check_ts()) < _INTEGRITY_INTERVAL_S:
        return None  # within debounce window, skipped

    # v3.66.927: RESOLVE THE PATH HERE, in the CALLING thread, and hand it to
    # _do_check. It used to call db_conn() with no argument, which re-resolves
    # DB_PATH at CALL time -- and this runs on a fire-and-forget daemon thread
    # that is never joined, so "call time" is whenever the scheduler gets
    # around to it. If DB_PATH moved in between, the check verified a database
    # nobody asked about and sqlite3.connect() CREATED it on contact.
    #
    # Traced, not reasoned: wrapping sqlite3.connect with a stack recorder
    # pinned the repo-root `downloader_history.db` that survived v3.66.926 to
    # exactly this frame. The file had no tables at all, which is the tell --
    # a connection opened without db_init.
    #
    # Capturing at schedule time is the only reading of "check the database"
    # that stays true across a thread boundary: a check scheduled for database
    # A verifies A even if the process later points DB_PATH elsewhere.
    # @942: ABSOLUTE, and that is the whole point. @927 moved the resolution
    # here so it would survive the thread boundary -- correct -- but
    # _resolve_db_path() returns a bare RELATIVE name whenever BD_INSTALL_DIR
    # is unset and DB_PATH has not been monkeypatched (its own docstring:
    # "use DB_PATH as-is, which sqlite3.connect() resolves against cwd").
    #
    # A relative string captured across a boundary captures NOTHING: it is
    # re-resolved against whatever cwd exists when the thread wakes. So the
    # guarantee stated above could not be delivered by the value beneath it,
    # and nothing looked wrong -- the comment read as though it had been.
    #
    # MEASURED at v3.66.941: a plugin wrapping sqlite3.connect over a
    # 156-suite band caught thread `bd-db-integrity` opening
    # `<repo>/downloader_history.db` after a test's fixture restored cwd to
    # the checkout. sqlite3.connect CREATES on contact, so it did not merely
    # read the wrong database -- it made one, together with
    # .integrity_last_run and a logs/ directory beside it.
    #
    # abspath, not resolve(): an already-absolute DB_PATH must pass through
    # verbatim (the conftest and Docker both set one), and abspath is the
    # idiom app.py:137 already uses for this exact reason.
    _scheduled_path = _os.path.abspath(_resolve_db_path())

    def _do_check():
        from . import log as _log
        llog = _log.get_logger("bulk_downloader.db.integrity")
        t0 = _time.time()
        try:
            with db_conn(_scheduled_path) as cx:
                # integrity_check returns one row per problem; "ok" if clean.
                # Wrap in list() so the connection isn't held open longer
                # than necessary.
                rows = list(cx.execute("PRAGMA integrity_check"))
            elapsed = _time.time() - t0
            messages = [r[0] for r in rows]
            if messages == ["ok"]:
                llog.info(
                    "integrity_check: OK (%.2fs, %d rows scanned)",
                    elapsed, _row_count_estimate(_scheduled_path))
                _record_integrity_check_ts(_time.time(), _scheduled_path)
                return {"ok": True, "result": ["ok"], "elapsed_s": elapsed}
            else:
                # Log every problem at ERROR so the operator sees it in
                # the standard log tail. Don't auto-recover here — that's
                # destructive; let the operator decide.
                llog.error(
                    "integrity_check: FAILED with %d issue(s) after %.2fs",
                    len(messages), elapsed)
                for m in messages[:50]:  # cap to avoid log flood
                    llog.error("  integrity issue: %s", m)
                if len(messages) > 50:
                    llog.error("  ...and %d more", len(messages) - 50)
                # Don't record success timestamp — we want to re-run next
                # startup until the operator addresses it.
                return {"ok": False, "result": messages, "elapsed_s": elapsed}
        except sqlite3.Error as e:
            llog.error("integrity_check raised %s: %s",
                       type(e).__name__, e)
            return {"ok": False, "result": [f"{type(e).__name__}: {e}"],
                    "elapsed_s": _time.time() - t0}

    if sync:
        return _do_check()
    t = _threading.Thread(target=_do_check, daemon=True,
                          name="bd-db-integrity")
    t.start()
    return t

def _row_count_estimate(path=None):
    """Cheap estimate of total history+queue rows for the log message —
    informational only, doesn't fail the check if it errors."""
    try:
        with db_conn(path) as cx:
            h = cx.execute("SELECT COUNT(*) FROM history").fetchone()[0]
            q = cx.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        return h + q
    except sqlite3.Error:
        return 0


# ── EXT-3: per-host throughput store (adaptive multi-conn count) ──────
def _ensure_host_throughput_table(cx):
    """Idempotently create the per-host throughput table. One row per host,
    holding the LAST multi-conn run's outcome so the next run can adapt N."""
    cx.execute("""CREATE TABLE IF NOT EXISTS host_throughput(
        host TEXT PRIMARY KEY,
        chunk_count INTEGER DEFAULT 0,
        avg_speed_bps REAL DEFAULT 0,
        chunks_failed INTEGER DEFAULT 0,
        updated_at REAL DEFAULT(strftime('%s','now')))""")


def host_throughput_record(host, *, chunk_count, avg_speed_bps, chunks_failed):
    """Upsert the last multi-conn outcome for a host. Best-effort; never raises."""
    if not host:
        return
    try:
        with db_conn() as cx:
            _ensure_host_throughput_table(cx)
            cx.execute(
                "INSERT INTO host_throughput(host,chunk_count,avg_speed_bps,"
                "chunks_failed,updated_at) VALUES(?,?,?,?,strftime('%s','now')) "
                "ON CONFLICT(host) DO UPDATE SET chunk_count=excluded.chunk_count,"
                "avg_speed_bps=excluded.avg_speed_bps,"
                "chunks_failed=excluded.chunks_failed,"
                "updated_at=excluded.updated_at",
                (str(host), int(chunk_count), float(avg_speed_bps),
                 int(chunks_failed)))
    except Exception:
        pass


def host_throughput_get(host):
    """Return {chunk_count, avg_speed_bps, chunks_failed, updated_at} for a host,
    or None if unseen. Fail-open: any error returns None."""
    if not host:
        return None
    try:
        with db_conn() as cx:
            _ensure_host_throughput_table(cx)
            row = cx.execute(
                "SELECT chunk_count, avg_speed_bps, chunks_failed, updated_at "
                "FROM host_throughput WHERE host=?", (str(host),)).fetchone()
            if not row:
                return None
            return {"chunk_count": int(row[0]), "avg_speed_bps": float(row[1]),
                    "chunks_failed": int(row[2]), "updated_at": float(row[3])}
    except Exception:
        return None

# probe marker
