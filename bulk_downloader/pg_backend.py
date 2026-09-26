"""MOD-3 cut 2 of 5 -- Postgres dual-write mirror for the history DB.

SQLite remains AUTHORITATIVE. This module mirrors history-DB *writes* to
Postgres so a later cut can shadow-read and compare, then cut over. Nothing
here is on the read path (cut 3). backfill() (v3.66.1679) copies the
baseline once, on operator request, before shadow-read.

Three properties, in the order they matter:

  1. DEFAULT OFF. No ``MOD3_PG_DSN`` -> ``dual_write_enabled()`` is False, the
     seam hands back a plain sqlite3.Connection, and psycopg is never imported.
     A staged migration is invisible until it is switched on.

  2. FAIL-OPEN, ALWAYS. Every Postgres interaction is best-effort. A dead
     server, a missing driver, a dialect the translator does not recognise, a
     constraint violation on the mirror -- none of them may propagate to the
     caller, because the caller is committing the AUTHORITATIVE write. A mirror
     that can take down the primary is worse than no mirror. Failures are
     counted and logged, never raised.

  3. DML ONLY. INSERT / UPDATE / DELETE are mirrored. SELECT / PRAGMA / CREATE /
     ALTER / VACUUM / EXPLAIN are not. The Postgres schema is bootstrapped from
     the explicit PG-dialect DDL below rather than by translating SQLite's
     ``AUTOINCREMENT`` / ``strftime(...)`` CREATE statements -- translating DDL
     is where a migration acquires silent divergence between the two stores.

Env var name: ``MOD3_PG_DSN``, deliberately NOT the BD_-prefixed form.
(That token is not spelled out anywhere in this file ON PURPOSE -- see below.)
tools/config_surface_inventory.py bare-token-scans for ``BD_[A-Z0-9_]+``, so a
BD_-prefixed name registers as an operator-tunable setting and owes FE settings
wiring plus a config_gui_manifest row (the ENV-TRANCHE footgun). This is an
internal staged-migration switch, not an operator surface -- same reasoning as
``NETNS_NS``.

v3.66.802 -- and the scan is a BARE TOKEN scan, so it does not care whether the
token is code, a string, a comment or a DOCSTRING. Cut 2 shipped with the
BD_-prefixed name written out in the prose explaining why it was being avoided,
and that mention ALONE registered as an open operator-tunable env var and
failed seven config-parity gates on stash. Naming the footgun triggered the
footgun. Do not spell that token out in this file.
"""
from __future__ import annotations

import logging
import os
import re
import threading

log = logging.getLogger(__name__)

# Statement verbs that are mirrored. Anything else is SQLite-only. Kept as a
# frozenset (not a regex of "not SELECT") so an unrecognised verb is NOT
# mirrored by default -- the safe direction for a store we are not yet reading.
_MIRRORED_VERBS = frozenset({"INSERT", "UPDATE", "DELETE"})

# Counters are diagnostic only; nothing gates on them in cut 2. They exist so a
# silently-degraded mirror is observable instead of merely absent (cut 3 needs
# to know whether the shadow store was ever actually written).
_stats = {"mirrored": 0, "skipped": 0, "failed": 0, "degraded_reason": None}
_lock = threading.Lock()

# --- explicit PG-dialect schema ------------------------------------------
# Mirrors the SQLite history DB shape from db.db_init(). Written by hand, in PG
# dialect, on purpose (see property 3): these are the FRESH-INSTALL shapes, and
# they carry db.py's primary keys because backfill() conflicts on them.
#
# v3.66.1679 (row 127 PG-PARITY): the hand DDL had drifted in 5 of 6 tables
# (queue lacked ts_added/ts_updated/lane/depends_on/listing_title/file_size;
# push_subscriptions, session_history, captures and host_throughput had
# invented column sets). Hand DDL alone cannot keep up: SQLite's live shape is
# CREATE + migrations.py + lazy ALTERs in other modules. So ensure_schema() no
# longer trusts this list to be complete -- it reads the live SQLite columns
# (PRAGMA table_info) and ADDs whatever Postgres lacks (_sqlite_col_to_pg is the
# one translation, tested per column type). It never drops or retypes.
_PG_TS_DEFAULT = "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS')"
_PG_EPOCH_DEFAULT = "extract(epoch from now())"
_PG_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS history(
        id BIGSERIAL PRIMARY KEY,
        site_id TEXT, site_name TEXT, url TEXT, status TEXT,
        filename TEXT, file_size BIGINT, message TEXT, screenshot TEXT,
        honeypot_score DOUBLE PRECISION DEFAULT NULL,
        bytes_fetched BIGINT DEFAULT NULL,
        transfer_mode TEXT DEFAULT NULL,
        egress_ip TEXT NOT NULL DEFAULT 'UNKNOWN',
        ts TEXT DEFAULT {_PG_TS_DEFAULT})""",
    f"""CREATE TABLE IF NOT EXISTS queue(
        site_id TEXT NOT NULL,
        url TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        message TEXT DEFAULT '',
        retries BIGINT DEFAULT 0,
        retry_after DOUBLE PRECISION DEFAULT 0,
        screenshot TEXT DEFAULT '',
        force_download BIGINT DEFAULT 0,
        priority TEXT DEFAULT '',
        ord BIGINT DEFAULT 0,
        filename TEXT DEFAULT '',
        listing_title TEXT DEFAULT '',
        file_size BIGINT DEFAULT 0,
        lane TEXT DEFAULT 'default',
        depends_on TEXT DEFAULT '',
        ts_added TEXT DEFAULT {_PG_TS_DEFAULT},
        ts_updated TEXT DEFAULT {_PG_TS_DEFAULT},
        PRIMARY KEY(site_id, url))""",
    f"""CREATE TABLE IF NOT EXISTS push_subscriptions(
        endpoint TEXT PRIMARY KEY,
        p256dh TEXT NOT NULL,
        auth TEXT NOT NULL,
        user_agent TEXT DEFAULT '',
        created_at TEXT DEFAULT {_PG_TS_DEFAULT},
        last_sent_at DOUBLE PRECISION DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS session_history(
        id BIGSERIAL PRIMARY KEY,
        ts DOUBLE PRECISION NOT NULL,
        site_id TEXT NOT NULL,
        account_idx BIGINT,
        event_type TEXT NOT NULL,
        detail TEXT DEFAULT '')""",
    f"""CREATE TABLE IF NOT EXISTS captures(
        rel_path TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        dir TEXT DEFAULT '',
        host TEXT DEFAULT '',
        captured_at DOUBLE PRECISION DEFAULT 0,
        size BIGINT DEFAULT 0,
        kind TEXT DEFAULT '',
        redacted BIGINT DEFAULT 0,
        first_seen DOUBLE PRECISION DEFAULT {_PG_EPOCH_DEFAULT},
        indexed_at DOUBLE PRECISION DEFAULT {_PG_EPOCH_DEFAULT})""",
    f"""CREATE TABLE IF NOT EXISTS host_throughput(
        host TEXT PRIMARY KEY,
        chunk_count BIGINT DEFAULT 0,
        avg_speed_bps DOUBLE PRECISION DEFAULT 0,
        chunks_failed BIGINT DEFAULT 0,
        updated_at DOUBLE PRECISION DEFAULT {_PG_EPOCH_DEFAULT})""",
)

# Mirrored tables derived from _PG_SCHEMA. DML targeting any table outside this
# set is SQLite-only (skipped by the mirror, no PG round-trip attempted).
_MIRRORED_TABLES = frozenset(
    m.group(1).lower()
    for ddl in _PG_SCHEMA
    if (m := re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_]+)", ddl, re.IGNORECASE))
)


def pg_dsn():
    """The configured DSN, or None when dual-write is off."""
    return (os.environ.get("MOD3_PG_DSN") or "").strip() or None


def dual_write_enabled():
    """True only when a DSN is configured. Presence IS the switch: there is no
    separate enable flag to drift out of sync with it."""
    return pg_dsn() is not None


def stats():
    with _lock:
        return dict(_stats)


def _degrade(reason):
    """Record why the mirror is not working. Called instead of raising -- the
    point is that a degraded mirror is VISIBLE, not that it is fatal."""
    with _lock:
        if _stats["degraded_reason"] != reason:
            _stats["degraded_reason"] = reason
            log.warning("MOD3 dual-write degraded: %s", reason)


def _connect():
    """A psycopg connection, or None. Never raises: an absent driver and an
    unreachable server are both ordinary 'mirror is unavailable' states."""
    dsn = pg_dsn()
    if not dsn:
        return None
    try:
        import psycopg
    except Exception as e:      # ImportError, and anything a broken install raises
        _degrade(f"psycopg unavailable ({type(e).__name__})")
        return None
    try:
        return psycopg.connect(dsn, connect_timeout=5)
    except Exception as e:
        _degrade(f"connect failed ({type(e).__name__})")
        return None


# Tables whose SQLite key is INTEGER PRIMARY KEY AUTOINCREMENT (-> BIGSERIAL
# here). Their ids are assigned by SQLite; a mirrored INSERT that let Postgres
# pick its own id would FORK the id space, and every later id-keyed UPDATE /
# DELETE (batch_ops, library, storage_rebalance) would hit a different row --
# same row count, different content. See mirror(rowid=...).
_ROWID_TABLES = frozenset(
    m.group(1).lower()
    for ddl in _PG_SCHEMA
    if re.search(r"\bid\s+BIGSERIAL\s+PRIMARY\s+KEY", ddl, re.IGNORECASE)
    and (m := re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_]+)", ddl, re.IGNORECASE))
)

_SQLITE_TS_DEFAULT = re.compile(
    r"^\(?\s*strftime\s*\(\s*'%Y-%m-%dT%H:%M:%S'\s*,\s*'now'\s*\)\s*\)?$", re.IGNORECASE)
_SQLITE_EPOCH_DEFAULT = re.compile(
    r"^\(?\s*strftime\s*\(\s*'%s'\s*,\s*'now'\s*\)\s*\)?$", re.IGNORECASE)
_SQL_LITERAL = re.compile(r"^(NULL|-?\d+(\.\d+)?|'(?:[^']|'')*')$", re.IGNORECASE)


def _pg_type(decl):
    """SQLite declared type -> PG type, by SQLite's own affinity rules
    (sqlite.org/datatype3 3.1): INT -> integer; CHAR/CLOB/TEXT -> text;
    REAL/FLOA/DOUB -> real; BLOB -> blob; anything else TEXT (the value a
    SQLite column with no usable affinity round-trips as)."""
    d = (decl or "").upper()
    if "INT" in d:
        return "BIGINT"
    if "CHAR" in d or "CLOB" in d or "TEXT" in d:
        return "TEXT"
    if "BLOB" in d:
        return "BYTEA"
    if "REAL" in d or "FLOA" in d or "DOUB" in d:
        return "DOUBLE PRECISION"
    return "TEXT"


def _pg_default(dflt):
    """PRAGMA table_info dflt_value -> PG default expression, or None when
    there is none OR it is an expression this translator does not positively
    understand (an unknown default is dropped, never guessed at)."""
    if dflt is None:
        return None
    d = str(dflt).strip()
    if _SQLITE_TS_DEFAULT.match(d):
        return _PG_TS_DEFAULT
    if _SQLITE_EPOCH_DEFAULT.match(d):
        return _PG_EPOCH_DEFAULT
    if _SQL_LITERAL.match(d):
        return d
    return None


def _sqlite_col_to_pg(name, decl, notnull, dflt):
    """One PRAGMA table_info column -> the `name TYPE [NOT NULL] [DEFAULT x]`
    fragment used by ALTER TABLE ... ADD COLUMN. NOT NULL is only carried
    with a default: adding a defaultless NOT NULL column to a populated PG
    table fails, and failing the whole sync over one constraint is worse."""
    frag = f'"{name}" {_pg_type(decl)}'
    default = _pg_default(dflt)
    if notnull and default is not None and default.upper() != "NULL":
        frag += " NOT NULL"
    if default is not None:
        frag += f" DEFAULT {default}"
    return frag


def _sqlite_columns(tables=None):
    """({table: [(name, decl, notnull, dflt, pk), ...]}, error) read from the
    LIVE SQLite store through the seam. A table SQLite has not created yet
    (host_throughput is lazy) maps to []. Uses the proxy's underlying
    connection so this schema read is never itself shadow-compared."""
    try:
        from . import db as _db  # deferred: db imports pg_backend (see rehearsal)
        out = {}
        with _db.db_conn() as cx:
            raw = getattr(cx, "_cx", cx)
            for t in sorted(tables or _MIRRORED_TABLES):
                out[t] = [(r[1], r[2], int(r[3] or 0), r[4], int(r[5] or 0))
                          for r in raw.execute(f"PRAGMA table_info({t})").fetchall()]
        return out, None
    except Exception as e:
        return {}, f"sqlite schema read failed ({type(e).__name__})"


def _pg_columns(cx, table):
    """{column: data_type} for `table` in the connection's current schema."""
    rows = cx.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s",
        (table,)).fetchall()
    return {r[0]: r[1] for r in rows}


_PG_TYPE_NAMES = {"BIGINT": "bigint", "TEXT": "text", "BYTEA": "bytea",
                  "DOUBLE PRECISION": "double precision"}


def schema_parity():
    """{table: {missing, extra, type_mismatch, sqlite_absent}} comparing the
    live SQLite columns against Postgres, or {"error": ...}. Never raises.
    `missing` (in SQLite, not PG) is what breaks mirror writes and shadow
    reads; `extra` is harmless PG debris; `type_mismatch` is reported, never
    auto-fixed (retyping a populated column is an operator decision)."""
    cols, err = _sqlite_columns()
    if err:
        return {"error": err}
    cx = _connect()
    if cx is None:
        return {"error": "postgres unavailable"}
    out = {}
    try:
        for t in sorted(_MIRRORED_TABLES):
            pg = _pg_columns(cx, t)
            sq = {c[0]: _PG_TYPE_NAMES[_pg_type(c[1])] for c in cols.get(t, [])}
            out[t] = {
                "sqlite_absent": not sq,
                "missing": sorted(set(sq) - set(pg)),
                "extra": sorted(set(pg) - set(sq)) if sq else [],
                "type_mismatch": sorted(
                    f"{c}: sqlite->{sq[c]} pg={pg[c]}"
                    for c in set(sq) & set(pg) if sq[c] != pg[c]),
            }
        return out
    except Exception as e:
        return {"error": f"parity check failed ({type(e).__name__})"}
    finally:
        try:
            cx.close()
        except Exception:
            pass


def _has_unique_key(cx, table, cols):
    """Whether `table` already has a unique index over exactly `cols`."""
    rows = cx.execute(
        "SELECT array_agg(a.attname::text ORDER BY a.attname) "
        "FROM pg_index i JOIN pg_attribute a "
        "  ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = to_regclass(%s) AND i.indisunique "
        "GROUP BY i.indexrelid", (table,)).fetchall()
    want = sorted(cols)
    return any(list(r[0]) == want for r in rows)


def ensure_schema():
    """Best-effort bootstrap of the PG-side schema. Returns True when the
    schema is known-present, False when the mirror is unavailable -- never
    raises, and never reports True on an unverified store.

    v3.66.1679: after the CREATEs, every column the LIVE SQLite table has and
    Postgres lacks is ADDED (ADD COLUMN IF NOT EXISTS; nothing is ever dropped
    or retyped), and a unique index on SQLite's primary key is ensured so a
    table created by an older, keyless DDL can still be backfilled with
    ON CONFLICT. A column that cannot be read or added makes this False."""
    cx = _connect()
    if cx is None:
        return False
    try:
        for ddl in _PG_SCHEMA:
            cx.execute(ddl)
        cx.commit()
        cols, err = _sqlite_columns()
        if err:
            _degrade(f"schema column sync failed: {err}")
            return False
        for t, tcols in cols.items():
            have = _pg_columns(cx, t)
            for name, decl, notnull, dflt, _pk in tcols:
                if name not in have:
                    cx.execute(f'ALTER TABLE {t} ADD COLUMN IF NOT EXISTS '
                               f'{_sqlite_col_to_pg(name, decl, notnull, dflt)}')
            pk = [c[0] for c in sorted(tcols, key=lambda c: c[4]) if c[4]]
            if pk and not _has_unique_key(cx, t, pk):
                cx.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS mod3_pk_{t} "
                           f"ON {t}(" + ", ".join(f'"{c}"' for c in pk) + ")")
            cx.commit()
        return True
    except Exception as e:
        _degrade(f"schema bootstrap failed ({type(e).__name__})")
        return False
    finally:
        try:
            cx.close()
        except Exception:
            pass


def _verb(sql):
    m = re.match(r"\s*(--[^\n]*\n|/\*.*?\*/|\s)*\s*([A-Za-z]+)", sql or "",
                 re.S)
    return (m.group(2) or "").upper() if m else ""


def _target_table(sql):
    """Target table name for an INSERT / UPDATE / DELETE statement, lowercased,
    or "" if unparseable."""
    if not sql:
        return ""
    m = re.search(
        r"\b(?:INSERT\s+(?:OR\s+[A-Za-z]+\s+)?(?:INTO\s+)?|UPDATE\s+(?:ONLY\s+)?|DELETE\s+(?:FROM\s+)?)"
        r'["`\']?(?:[A-Za-z0-9_]+\.)?([A-Za-z0-9_]+)["`\']?',
        sql,
        re.IGNORECASE,
    )
    return (m.group(1) or "").lower() if m else ""


_NAME = r'["`\[]?(?:[A-Za-z0-9_]+\.)?([A-Za-z0-9_]+)["`\]]?'
_ALIAS_STOP = frozenset({
    "WHERE", "GROUP", "ORDER", "LIMIT", "HAVING", "UNION", "INTERSECT",
    "EXCEPT", "ON", "USING", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER",
    "CROSS", "FULL", "NATURAL", "OFFSET", "WINDOW"})


def _read_tables(sql):
    """Lowercased set of the tables a SELECT reads (every FROM list entry and
    JOIN target), frozenset() when it names none, or None when the statement
    has a shape this parser does not positively understand (subquery, table
    function). None is out of scope, never 'no tables'."""
    s = re.sub(r"'(?:[^']|'')*'", "''", sql or "")
    if re.search(r"\(\s*SELECT\b", s, re.IGNORECASE):
        return None
    tables = set()
    for kw in re.finditer(r"\b(FROM|JOIN)\s+", s, re.IGNORECASE):
        pos = kw.end()
        while True:
            m = re.compile(_NAME).match(s, pos)
            if not m or re.match(r"\s*\(", s[m.end():]):
                return None
            tables.add(m.group(1).lower())
            pos = m.end()
            if kw.group(1).upper() != "FROM":
                break
            a = re.compile(r"\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
                           re.IGNORECASE).match(s, pos)
            if a and a.group(1).upper() not in _ALIAS_STOP:
                pos = a.end()
            c = re.compile(r"\s*,\s*").match(s, pos)
            if not c:
                break
            pos = c.end()
    return frozenset(tables)


def is_mirrored(sql):
    """Whether this statement is in scope for the mirror. Public so the gate
    can assert the scope boundary rather than infer it."""
    return _verb(sql) in _MIRRORED_VERBS and _target_table(sql) in _MIRRORED_TABLES


def translate(sql):
    """SQLite -> Postgres for the DML this app issues: qmark placeholders
    become %s. Returns None when the statement contains a construct the
    translator does not positively understand -- an untranslatable statement is
    SKIPPED, never guessed at, because a wrong mirror write is worse than a
    missing one (cut 3 compares the two stores)."""
    if not sql:
        return None
    # sqlite-only constructs we will not attempt to rewrite
    if re.search(r"\bINSERT\s+OR\s+(REPLACE|IGNORE)\b", sql, re.I):
        return None
    if re.search(r"\bstrftime\s*\(|\bAUTOINCREMENT\b|\bPRAGMA\b", sql, re.I):
        return None
    # qmark -> %s, but not inside string literals
    out, in_str, quote = [], False, ""
    for ch in sql:
        if in_str:
            out.append(ch)
            if ch == quote:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str, quote = True, ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


_INSERT_COLS = re.compile(
    r"^(\s*INSERT\s+INTO\s+[\"`']?[A-Za-z0-9_]+[\"`']?\s*\()([^)]*)(\)\s*VALUES\s*\()",
    re.IGNORECASE)


def _with_rowid(pg_sql, params, rowid):
    """(sql, params) with SQLite's assigned id prepended to a single-row
    `INSERT INTO t(cols) VALUES(...)`, or None when the statement is not that
    shape (INSERT ... SELECT, named params, no column list). An id-less
    mirror INSERT into a _ROWID_TABLES table is refused, not sent: letting
    Postgres pick the id forks the id space (see _ROWID_TABLES)."""
    m = _INSERT_COLS.match(pg_sql)
    if not m or rowid is None or isinstance(params, dict):
        return None
    cols = [c.strip().strip('"`').lower() for c in m.group(2).split(",")]
    if "id" in cols:
        return pg_sql, params          # caller already supplied the id
    return (m.group(1) + "id, " + m.group(2) + m.group(3) + "%s, "
            + pg_sql[m.end():]), (int(rowid),) + tuple(params or ())


def inserted_rowid(cur):
    """The id SQLite assigned to the row this statement inserted, or None.
    sqlite3 leaves `lastrowid` at the PREVIOUS insert's value when a statement
    inserts nothing, so it is only trusted when exactly one row changed; the
    mirror() refuses an id-less INSERT into an id-keyed table rather than let
    Postgres fork the id space (_ROWID_TABLES)."""
    try:
        return cur.lastrowid if cur.rowcount == 1 else None
    except Exception:
        return None


def _upsert_on_id(cx, table, pg_sql):
    """`pg_sql` (an id-aligned INSERT) as an upsert on id. SQLite is
    authoritative while dual-write is on, so a Postgres row already holding
    this id is a fork -- a PG-native write, or a row the pre-alignment mirror
    wrote under a PG-picked id -- and SQLite's row replaces it whole. EXCLUDED
    carries the defaults for columns the INSERT omits, so the result equals a
    fresh insert (RULING-ROW127-PARITY-R3-803, option B)."""
    if re.search(r"\bON\s+CONFLICT\b", pg_sql, re.IGNORECASE):
        return pg_sql
    sets = ", ".join(f'"{c}" = EXCLUDED."{c}"'
                     for c in sorted(_pg_columns(cx, table)) if c != "id")
    return (pg_sql.rstrip().rstrip(";")
            + (f" ON CONFLICT (id) DO UPDATE SET {sets}" if sets
               else " ON CONFLICT (id) DO NOTHING"))


def _advance_serial(cx, table, rowid):
    """Keep `table`'s BIGSERIAL sequence at or past the explicit id the mirror
    just wrote. An explicit-id INSERT does not touch the sequence, so without
    this the next Postgres-native INSERT draws an id SQLite already owns and
    dies on the primary key. GREATEST, never a plain setval(rowid): a mirror
    of an older row must not move the sequence backwards."""
    cx.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
               f"GREATEST(%s, COALESCE(pg_sequence_last_value("
               f"pg_get_serial_sequence('{table}', 'id')::regclass), 1)))",
               (int(rowid),))


def mirror(sql, params=(), rowid=None):
    """Best-effort mirror of one DML statement. Returns True if it reached
    Postgres. NEVER raises -- see property 2.

    `rowid` is the id SQLite assigned to this INSERT (cursor.lastrowid, passed
    by the db.py seam). For _ROWID_TABLES it is written explicitly so both
    stores agree on which row every later `WHERE id = ?` means."""
    if not dual_write_enabled():
        return False
    if not is_mirrored(sql):
        with _lock:
            _stats["skipped"] += 1
        return False
    pg_sql = translate(sql)
    seq_table = None
    if pg_sql is not None and _verb(sql) == "INSERT" \
            and _target_table(sql) in _ROWID_TABLES:
        aligned = _with_rowid(pg_sql, params, rowid)
        pg_sql, params = aligned if aligned else (None, params)
        seq_table = _target_table(sql)
    if pg_sql is None:
        with _lock:
            _stats["skipped"] += 1
        return False
    cx = _connect()
    if cx is None:
        with _lock:
            _stats["failed"] += 1
        return False
    try:
        if seq_table is not None:
            pg_sql = _upsert_on_id(cx, seq_table, pg_sql)
        cx.execute(pg_sql, tuple(params or ()))
        if seq_table is not None:
            _advance_serial(cx, seq_table, rowid)
        cx.commit()
        with _lock:
            _stats["mirrored"] += 1
        return True
    except Exception as e:
        with _lock:
            _stats["failed"] += 1
        _degrade(f"mirror write failed ({type(e).__name__})")
        return False
    finally:
        try:
            cx.close()
        except Exception:
            pass


# ── MOD-3 cut 3 (v3.66.801): shadow-read comparison ──────────────────────
#
# Reads BOTH stores for the same statement and compares. SQLite stays
# authoritative and the caller's result object is never touched -- the
# comparison re-executes the statement on the same SQLite connection rather
# than consuming the caller's cursor, so caller-isolation is structural, not
# argued.
#
# THE DESIGN CONSTRAINT: an unmeasurable comparison reports UNKNOWN, never
# MATCH. `compared` is exposed beside `diverged` because "0 diverged" is
# meaningless without its denominator -- a comparator that skips everything it
# cannot translate and then reports clean is the failure shape this project
# exists to catch. Skips are counted separately and never as agreement.
_shadow = {"compared": 0, "matched": 0, "diverged": 0, "skipped": 0,
           "errors": 0, "last_divergence": None}
# Distinct shadow-read error messages already logged. An in-scope read the PG
# side cannot answer (UndefinedColumn, UndefinedFunction, ...) is a schema or
# translation gap, not a degraded store: it is counted in `errors`, logged
# once per message, and never touches degraded_reason.
_shadow_errors_seen: set = set()
_SHADOW_ERRORS_SEEN_MAX = 256    # bounds the log-once memory, not the count
_SHADOW_MAX_ROWS = 5000     # a comparison bigger than this is skipped, not faked


def shadow_read_enabled():
    """True only when dual-write is on AND MOD3_SHADOW_READ is truthy.

    The dual-write requirement is not belt-and-braces: shadow-reading a store
    nothing has written to would diverge on every row and teach nothing, so the
    flag alone must not arm it."""
    if not dual_write_enabled():
        return False
    return (os.environ.get("MOD3_SHADOW_READ") or "").strip().lower() \
        in ("1", "true", "yes", "on")


def shadow_stats():
    with _lock:
        return dict(_shadow)


def _scalar(v):
    """Coerce one value to a cross-engine comparable form. Postgres returns
    Decimal/date types where SQLite returns float/str; comparing raw would
    manufacture divergences that are really type artefacts."""
    import decimal
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (int, float, str, bytes)):
        return v
    return str(v)


def _norm_rows(rows):
    out = []
    for r in rows or []:
        try:
            vals = tuple(_scalar(x) for x in tuple(r))
        except TypeError:
            vals = (_scalar(r),)
        out.append(vals)
    # order-insensitive: without ORDER BY the engines may legitimately differ.
    # sorted() on mixed types can raise, so fall back to a stable string key.
    try:
        return sorted(out)
    except TypeError:
        return sorted(out, key=lambda t: tuple(str(x) for x in t))


def _rows_equal(a, b):
    """Order-insensitive row-set equality. Public-ish (underscored but pinned
    by the gate) because 'ordering is not a divergence' is a semantic decision
    that deserves a test naming it, not an implementation detail."""
    return _norm_rows(a) == _norm_rows(b)


def _shadow_fetch(sql, params=()):
    """(rows, errored) from Postgres for a translated SELECT. rows is None when
    the shadow cannot answer -- UNKNOWN, callers must not read it as empty;
    errored says whether PG answered with an error (counted in `errors`) as
    opposed to being unreachable."""
    cx = _connect()
    if cx is None:
        return None, False
    try:
        cur = cx.execute(sql, tuple(params or ()))
        return cur.fetchall(), False
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"
        with _lock:
            _shadow["errors"] += 1
            first = (msg not in _shadow_errors_seen
                     and len(_shadow_errors_seen) < _SHADOW_ERRORS_SEEN_MAX)
            if first:
                _shadow_errors_seen.add(msg)
        if first:
            log.warning("MOD3 shadow read error: %s -- %s", msg,
                        (sql or "")[:120])
        return None, True
    finally:
        try:
            cx.close()
        except Exception:
            pass


def shadow_compare(sql, params, sqlite_rows):
    """Compare one SELECT's SQLite result against Postgres. Returns True on
    agreement, False on divergence, None when NOT COMPARABLE (untranslatable
    statement, unreachable shadow, oversized result). Never raises; never
    reports agreement it did not observe."""
    if not shadow_read_enabled():
        return None
    if _verb(sql) != "SELECT":
        return None
    tables = _read_tables(sql)
    pg_sql = translate(sql)
    if (not tables or not tables <= _MIRRORED_TABLES or pg_sql is None
            or len(sqlite_rows or []) > _SHADOW_MAX_ROWS):
        with _lock:
            _shadow["skipped"] += 1
        return None
    pg_rows, errored = _shadow_fetch(pg_sql, params)
    if pg_rows is None:
        if not errored:
            with _lock:
                _shadow["skipped"] += 1
        return None
    same = _rows_equal(sqlite_rows, pg_rows)
    with _lock:
        _shadow["compared"] += 1
        if same:
            _shadow["matched"] += 1
        else:
            _shadow["diverged"] += 1
            _shadow["last_divergence"] = {
                "sql": (sql or "")[:200],
                "sqlite_rows": len(sqlite_rows or []),
                "pg_rows": len(pg_rows),
            }
    if not same:
        log.warning("MOD3 shadow-read DIVERGENCE: %s (sqlite=%d pg=%d)",
                    (sql or "")[:120], len(sqlite_rows or []), len(pg_rows))
    return same


# ── MOD-3 cut 4 (v3.66.803): migration REHEARSAL ─────────────────────────
#
# Cuts 2/3 move new writes and compare reads; neither moves the data that was
# already in SQLite, and neither answers the question cutover depends on: would
# a FULL migration succeed and would the result be EQUAL? This rehearses it in
# a scratch Postgres schema, verifies by CONTENT, reports, and tears down.
#
# Contract inherited verbatim from backup_verify.rehearse() (X-AUTO-1 @706):
# never raise, and NOT-OK is the honest answer for the empty case. Zero rows
# migrated with zero mismatches is arithmetically perfect and epistemically
# worthless -- the empty denominator wearing a green badge. It reports not-ok.
#
# Verification compares CONTENT, never counts. Equal counts can mask a swap;
# a count-only verifier is clean and blind, and the gate falsifies this one with
# a planted same-count, different-content corruption.
_REHEARSAL_TABLE = "history"


def _sqlite_rows_for_rehearsal():
    """(rows, error). Reads the SQLite source of truth THROUGH THE SEAM.

    An earlier draft opened its own ``sqlite3.connect`` here, reasoning that the
    rehearsal should observe the source rather than its own mirror. The @795
    seam gate failed it, correctly: the invariant is ONE connection point, and a
    second one is exactly what makes a later cut's interception incomplete. The
    reasoning was also unnecessary -- the proxy does not alter read results
    (pinned by the cut-3 caller-isolation test), so ``db_conn()`` returns the
    same source rows without breaking the invariant."""
    try:
        # Function-scoped import ON PURPOSE: db imports pg_backend at module
        # level, so a module-level import here would be a real cycle. Deferred
        # to call time it is not -- both modules import standalone (verified),
        # and the graph edge is declared + frozen rather than hidden.
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute(
                f"SELECT site_id, url, status FROM {_REHEARSAL_TABLE} "
                f"ORDER BY rowid")
            return [tuple(r) for r in cur.fetchall()], None
    except Exception as e:
        return [], f"source read failed ({type(e).__name__})"


def rehearse_migration(_corrupt_for_test=False):
    """Rehearse a full SQLite -> Postgres migration in a scratch schema.

    Returns {ok, rows_source, rows_migrated, mismatches, scratch_schema,
    error, seconds}. NEVER raises: a rehearsal that crashes the scheduler takes
    out the very thing meant to reassure you.

    `_corrupt_for_test` deliberately alters CONTENT without altering COUNT. It
    exists so the gate can falsify the verifier -- a verifier that cannot fail
    a planted swap proves nothing when it passes.
    """
    import time as _time
    import uuid as _uuid
    t0 = _time.time()
    out = {"ok": False, "rows_source": 0, "rows_migrated": 0,
           "mismatches": 0, "scratch_schema": "", "error": "", "seconds": 0.0}

    def _done(err=""):
        out["error"] = err
        out["seconds"] = round(_time.time() - t0, 3)
        return out

    if not dual_write_enabled():
        return _done("no MOD3_PG_DSN configured -- nothing to rehearse against")

    rows, rerr = _sqlite_rows_for_rehearsal()
    out["rows_source"] = len(rows)
    if rerr:
        return _done(rerr)
    if not rows:
        # X-AUTO-1 posture: the empty case is the loudest failure, not a pass.
        return _done("source is EMPTY -- a rehearsal over zero rows proves "
                     "nothing and must not read as ok")

    cx = _connect()
    if cx is None:
        return _done("postgres unavailable -- rehearsal could not run")

    schema = "mod3_rehearsal_" + _uuid.uuid4().hex[:12]
    out["scratch_schema"] = schema
    try:
        # Scratch schema: isolated from the live mirror, so a rehearsal can
        # never corrupt the signal cut 3 compares and cut 5 trusts.
        cx.execute(f'CREATE SCHEMA "{schema}"')
        cx.execute(f'CREATE TABLE "{schema}".{_REHEARSAL_TABLE}('
                   f"site_id TEXT, url TEXT, status TEXT)")
        with cx.cursor() as cur:
            cur.executemany(
                f'INSERT INTO "{schema}".{_REHEARSAL_TABLE}'
                f"(site_id, url, status) VALUES (%s,%s,%s)", rows)
        if _corrupt_for_test:
            # same row COUNT, different CONTENT -- the swap a counting
            # verifier cannot see.
            cx.execute(f'UPDATE "{schema}".{_REHEARSAL_TABLE} '
                       f"SET status = 'CORRUPTED' WHERE ctid IN "
                       f'(SELECT ctid FROM "{schema}".{_REHEARSAL_TABLE} '
                       f"LIMIT 1)")
        cx.commit()
        got = cx.execute(
            f'SELECT site_id, url, status FROM "{schema}".{_REHEARSAL_TABLE}'
        ).fetchall()
        out["rows_migrated"] = len(got)
        # CONTENT comparison, order-insensitive (reusing the cut-3 normaliser
        # so the two stages cannot drift apart in what "equal" means).
        src_n, dst_n = _norm_rows(rows), _norm_rows(got)
        if src_n == dst_n:
            out["mismatches"] = 0
            out["ok"] = True
        else:
            src_c, dst_c = {}, {}
            for r in src_n:
                src_c[r] = src_c.get(r, 0) + 1
            for r in dst_n:
                dst_c[r] = dst_c.get(r, 0) + 1
            diff = 0
            for k in set(src_c) | set(dst_c):
                diff += abs(src_c.get(k, 0) - dst_c.get(k, 0))
            out["mismatches"] = diff
            out["ok"] = False
            return _done(f"content mismatch: {diff} differing row(s) "
                         f"(counts source={len(rows)} target={len(got)})")
        return _done("")
    except Exception as e:
        out["ok"] = False
        return _done(f"rehearsal failed ({type(e).__name__})")
    finally:
        # Tear down unconditionally: a scratch schema left behind turns every
        # later run into a comparison against stale debris.
        try:
            cx.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cx.commit()
        except Exception:
            pass
        try:
            cx.close()
        except Exception:
            pass


# ── MOD-3 cut 5 (v3.66.804): cutover + rollback ──────────────────────────
#
# The centre of this cut is the PREFLIGHT REFUSAL, not the flip.
#
# The failure it exists to make impossible: cutting over because shadow-read
# reported "0 divergences" while having performed ZERO comparisons. That number
# is truthful, clean, and catastrophic -- the empty denominator wearing a green
# badge, authorising a move of the authoritative store on no evidence. Cut 3
# built `compared` so this preflight could demand it: agreement requires
# compared > 0 AND diverged == 0. Either alone is not evidence.
#
# Reversibility over confidence: writes continue to SQLite while cut over, so
# rollback is a flag flip with nothing to reconcile. A cutover you cannot walk
# back is not a migration step, it is a leap.
_MIN_SHADOW_COMPARISONS = 1


def cutover_requested():
    return (os.environ.get("MOD3_CUTOVER") or "").strip().lower() \
        in ("1", "true", "yes", "on")


def preflight_cutover():
    """Is it SAFE to make Postgres authoritative for reads?

    Returns {ok, reasons[], checks{}}. Never raises. Every refusal is NAMED,
    and the numbers judged on are returned so the verdict is auditable rather
    than trusted."""
    reasons = []
    st = shadow_stats()
    checks = {
        "dual_write": dual_write_enabled(),
        "shadow_read": shadow_read_enabled(),
        "shadow_compared": st.get("compared", 0),
        "shadow_diverged": st.get("diverged", 0),
        "shadow_skipped": st.get("skipped", 0),
        "degraded_reason": st.get("degraded_reason"),
    }
    if not dual_write_enabled():
        reasons.append("dual-write is not enabled -- Postgres has not been "
                       "receiving writes")
    if not shadow_read_enabled():
        reasons.append("shadow-read is not enabled -- no comparison evidence "
                       "exists")
    # THE refusal. compared == 0 means the comparison never ran; a zero
    # divergence count over a zero denominator is not agreement.
    if checks["shadow_compared"] < _MIN_SHADOW_COMPARISONS:
        reasons.append(
            "shadow-read has compared %d statement(s): zero comparisons is "
            "NOT evidence of agreement, it is an empty denominator"
            % checks["shadow_compared"])
    if checks["shadow_diverged"]:
        reasons.append("shadow-read recorded %d divergence(s)"
                       % checks["shadow_diverged"])
    if not _connect_ok():
        reasons.append("postgres is not reachable")
    return {"ok": not reasons, "reasons": reasons, "checks": checks}


def _connect_ok():
    cx = _connect()
    if cx is None:
        return False
    try:
        cx.close()
    except Exception:
        pass
    return True


def cutover_engaged():
    """FAIL-CLOSED: true only when cutover was REQUESTED and the preflight
    positively passes. An unverifiable precondition never reads as
    permission."""
    if not cutover_requested():
        return False
    try:
        return bool(preflight_cutover()["ok"]) and _sync_serials()
    except Exception:
        return False        # cannot verify -> not engaged


_serials_synced = False


def _sync_serials():
    """Once per process at cutover engage: every _ROWID_TABLES sequence to
    >= max(id), so a PG-native write after cutover never draws an id the
    mirror already wrote. False (-> not engaged) if it cannot be done."""
    global _serials_synced
    if _serials_synced:
        return True
    cx = _connect()
    if cx is None:
        return False
    try:
        for t in sorted(_ROWID_TABLES):
            seq = f"pg_get_serial_sequence('{t}', 'id')"
            cx.execute(f"SELECT setval({seq}, GREATEST("
                       f"(SELECT COALESCE(max(id), 1) FROM {t}), "
                       f"COALESCE(pg_sequence_last_value({seq}::regclass), 1)))")
        cx.commit()
        _serials_synced = True
        return True
    except Exception:
        return False
    finally:
        try:
            cx.close()
        except Exception:
            pass


def read_authoritative(sql, params=()):
    """Rows from the CUTOVER-authoritative store (Postgres), or None when the
    read cannot be served there -- None means 'fall back to SQLite', never
    'empty result'. Conflating those would silently turn an outage into
    apparent data loss."""
    if not cutover_engaged():
        return None
    if _verb(sql) != "SELECT":
        return None
    pg_sql = translate(sql)
    if pg_sql is None:
        return None
    cx = _connect()
    if cx is None:
        return None
    try:
        from psycopg.rows import dict_row
        cur = cx.cursor(row_factory=dict_row)
        cur.execute(pg_sql, tuple(params or ()))
        # Consumers index rows BY COLUMN NAME (sqlite3.Row semantics). Returning
        # bare tuples here would not fail loudly -- it would raise TypeError deep
        # inside unrelated call sites, which is a worse failure than not cutting
        # over at all.
        return [_PgRow(d) for d in cur.fetchall()]
    except Exception as e:
        _degrade(f"authoritative read failed ({type(e).__name__})")
        return None
    finally:
        try:
            cx.close()
        except Exception:
            pass


class _PgRow:
    """sqlite3.Row-compatible view over a psycopg dict row: supports
    row["col"], row[0], tuple(row), len() and iteration, so a cutover does not
    require touching 371 call sites."""

    __slots__ = ("_d", "_v")

    def __init__(self, d):
        self._d = d
        self._v = list(d.values())

    def __getitem__(self, k):
        return self._d[k] if isinstance(k, str) else self._v[k]

    def keys(self):
        return list(self._d.keys())

    def __iter__(self):
        return iter(self._v)

    def __len__(self):
        return len(self._v)

    def __eq__(self, other):
        if isinstance(other, _PgRow):
            return self._v == other._v
        try:
            return tuple(self._v) == tuple(other)
        except TypeError:
            return NotImplemented

    def __repr__(self):
        return f"_PgRow({self._d!r})"


# ── row 127 PG-PARITY (v3.66.1679): baseline BACKFILL ────────────────────
#
# Dual-write only moves rows written AFTER it was switched on, so without a
# baseline copy every aggregate shadow read diverges by design and
# preflight_cutover() can never see shadow_diverged == 0 (plan Stage 3.2).
# This copies SQLite -> Postgres for the mirrored tables, once, before
# shadow-read.
#
# ON CONFLICT (SQLite's primary key) DO UPDATE ... WHERE IS DISTINCT FROM: an
# upsert, because SQLite is authoritative and a PG row on the same key with
# other content is a fork -- chiefly history/session_history rows the
# pre-alignment mirror wrote under PG-picked ids (RULING-ROW127-PARITY-R3-803).
# The WHERE keeps it idempotent: a second run copies exactly 0. Keys PG holds
# that SQLite lacks are left alone. Safety under dual-write comes from the
# per-batch SQLite write lock in _backfill_table (see the lock comment there).
_BACKFILL_BATCH = 500


def _backfill_table(cx, raw, table, sqlite_cols):
    cols = [c[0] for c in sqlite_cols]
    pk = [c[0] for c in sorted(sqlite_cols, key=lambda c: c[4]) if c[4]]
    if not pk:
        return {"error": f"{table}: no primary key in SQLite -- refusing a "
                         f"copy that could not be idempotent"}
    missing = set(cols) - set(_pg_columns(cx, table))
    if missing:
        return {"error": f"{table}: postgres lacks column(s) {sorted(missing)}"}
    qcols = ", ".join(f'"{c}"' for c in cols)
    rest = [c for c in cols if c not in pk]
    on_pk = f"ON CONFLICT ({', '.join(chr(34) + c + chr(34) for c in pk)}) "
    ins = (f"INSERT INTO {table} AS t ({qcols}) VALUES "
           f"({', '.join(['%s'] * len(cols))}) " + on_pk
           + ("DO UPDATE SET " + ", ".join(f'"{c}" = EXCLUDED."{c}"'
                                            for c in rest)
              + f" WHERE ({', '.join(f't.{chr(34)}{c}{chr(34)}' for c in rest)})"
              f" IS DISTINCT FROM ({', '.join(f'EXCLUDED.{chr(34)}{c}{chr(34)}' for c in rest)})"
              if rest else "DO NOTHING"))
    qpk = ", ".join(f'"{c}"' for c in pk)
    first = f"SELECT {qcols} FROM {table} ORDER BY {qpk} LIMIT ?"
    after = (f"SELECT {qcols} FROM {table} WHERE ({qpk}) > "
             f"({', '.join(['?'] * len(pk))}) ORDER BY {qpk} LIMIT ?")
    pk_at = [cols.index(c) for c in pk]
    source = copied = 0
    last = None
    while True:
        # THE RACE THIS CLOSES (lens REFUTE on tree 001bcbef): a mirrored
        # UPDATE landing between this batch's SQLite read and its PG insert
        # hit 0 PG rows, then the insert wrote the older snapshot and ON
        # CONFLICT DO NOTHING kept it stale forever. Holding SQLite's write
        # lock (BEGIN IMMEDIATE) from the read until the PG commit makes a
        # concurrent writer either finish first (we read its committed value)
        # or wait (its mirror lands on the row we just inserted). The db.py
        # seam writes SQLite BEFORE mirroring, so the lock orders both stores.
        # A fresh keyset SELECT per batch keeps each lock short (busy_timeout
        # is 10 s on seam connections).
        if raw.in_transaction:
            raise RuntimeError("seam connection already inside a transaction")
        raw.execute("BEGIN IMMEDIATE")
        try:
            batch = [tuple(r) for r in (
                raw.execute(first, (_BACKFILL_BATCH,)) if last is None else
                raw.execute(after, (*last, _BACKFILL_BATCH)))]
            if batch:
                with cx.cursor() as cur:
                    # psycopg >= 3.1 sums rowcount across executemany; a row
                    # already equal contributes 0 (the IS DISTINCT FROM
                    # guard), so this counts inserts + repaired rows.
                    cur.executemany(ins, batch)
                    copied += max(cur.rowcount, 0)
                cx.commit()
        finally:
            raw.rollback()      # read-only: releases the write lock
        if not batch:
            break
        source += len(batch)
        last = tuple(batch[-1][i] for i in pk_at)
    if table in _ROWID_TABLES:
        # PG's own sequence must not hand out an id SQLite already owns.
        cx.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                   f"GREATEST((SELECT max(id) FROM {table}), 1))")
        cx.commit()
    return {"source": source, "copied": copied, "skipped": source - copied}


def backfill(tables=None):
    """Copy the SQLite baseline into Postgres for the mirrored tables.

    Returns {table: {source, copied, skipped, seconds}} (or {table: {error,
    seconds}}). NEVER raises; a table outside _MIRRORED_TABLES is refused by
    name rather than silently ignored."""
    import time as _time
    want = [t.lower() for t in (tables or sorted(_MIRRORED_TABLES))]
    out = {}
    for t in want:
        if t not in _MIRRORED_TABLES:
            out[t] = {"error": f"{t}: not a mirrored table -- refused",
                      "seconds": 0.0}
    todo = [t for t in want if t in _MIRRORED_TABLES]
    if not todo:
        return out
    if not dual_write_enabled():
        return {**out, **{t: {"error": "no MOD3_PG_DSN configured",
                              "seconds": 0.0} for t in todo}}
    if not ensure_schema():
        return {**out, **{t: {"error": "schema bootstrap failed: "
                              + str(stats().get("degraded_reason")),
                              "seconds": 0.0} for t in todo}}
    cols, err = _sqlite_columns(todo)
    if err:
        return {**out, **{t: {"error": err, "seconds": 0.0} for t in todo}}
    cx = _connect()
    if cx is None:
        return {**out, **{t: {"error": "postgres unavailable",
                              "seconds": 0.0} for t in todo}}
    try:
        from . import db as _db  # deferred: db imports pg_backend
        with _db.db_conn() as scx:
            raw = getattr(scx, "_cx", scx)   # the seam's connection, unshadowed
            for t in todo:
                t0 = _time.time()
                if not cols.get(t):
                    res = {"source": 0, "copied": 0, "skipped": 0}
                else:
                    try:
                        res = _backfill_table(cx, raw, t, cols[t])
                    except Exception as e:
                        try:
                            cx.rollback()
                        except Exception:
                            pass
                        res = {"error": f"{t}: backfill failed "
                                        f"({type(e).__name__}: {e})"[:300]}
                res["seconds"] = round(_time.time() - t0, 3)
                out[t] = res
    except Exception as e:
        for t in todo:
            out.setdefault(t, {"error": f"sqlite read failed "
                                        f"({type(e).__name__})", "seconds": 0.0})
    finally:
        try:
            cx.close()
        except Exception:
            pass
    return out


def main(argv=None):
    """`python -m bulk_downloader.pg_backend backfill [table ...]` -- the
    one-shot baseline copy the operator runs before shadow-read -- or
    `... parity` for the read-only column report. Prints JSON; exit 1 when
    any table reports an error or any column is missing."""
    import json
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args.pop(0) if args else ""
    if cmd == "backfill":
        res = backfill(args or None)
        print(json.dumps(res, indent=2, sort_keys=True))
        return 1 if any("error" in v for v in res.values()) else 0
    if cmd == "parity":
        res = schema_parity()
        print(json.dumps(res, indent=2, sort_keys=True))
        return 1 if "error" in res or any(
            v["missing"] for v in res.values()) else 0
    print("usage: python -m bulk_downloader.pg_backend {backfill [table ...]"
          "|parity}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
