"""MOD-3 cut 2 of 5 -- Postgres dual-write mirror for the history DB.

SQLite remains AUTHORITATIVE. This module mirrors history-DB *writes* to
Postgres so a later cut can shadow-read and compare, then cut over. Nothing
here is on the read path (cut 3) and nothing here backfills (cut 4).

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
# dialect, on purpose (see property 3). Column sets track db.py; a divergence
# here surfaces as a mirror failure, which is exactly where cut 3 will look.
_PG_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS history(
        id BIGSERIAL PRIMARY KEY,
        site_id TEXT, site_name TEXT, url TEXT, status TEXT,
        filename TEXT, file_size BIGINT, message TEXT, screenshot TEXT,
        honeypot_score DOUBLE PRECISION DEFAULT NULL,
        ts TEXT DEFAULT to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS'))""",
    """CREATE TABLE IF NOT EXISTS queue(
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
        filename TEXT DEFAULT '')""",
    """CREATE TABLE IF NOT EXISTS push_subscriptions(
        endpoint TEXT PRIMARY KEY,
        subscription TEXT,
        ts TEXT)""",
    """CREATE TABLE IF NOT EXISTS session_history(
        id BIGSERIAL PRIMARY KEY,
        site_id TEXT, event TEXT, detail TEXT, ts TEXT)""",
    """CREATE TABLE IF NOT EXISTS captures(
        id BIGSERIAL PRIMARY KEY,
        site_id TEXT, url TEXT, path TEXT, ts TEXT)""",
    """CREATE TABLE IF NOT EXISTS host_throughput(
        host TEXT, ts TEXT, bytes BIGINT, seconds DOUBLE PRECISION)""",
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


def ensure_schema():
    """Best-effort bootstrap of the PG-side schema. Returns True when the
    schema is known-present, False when the mirror is unavailable -- never
    raises, and never reports True on an unverified store."""
    cx = _connect()
    if cx is None:
        return False
    try:
        with cx:
            for ddl in _PG_SCHEMA:
                cx.execute(ddl)
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


def is_mirrored(sql):
    """Whether this statement is in scope for the mirror. Public so the gate
    can assert the scope boundary rather than infer it."""
    return _verb(sql) in _MIRRORED_VERBS


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


def mirror(sql, params=()):
    """Best-effort mirror of one DML statement. Returns True if it reached
    Postgres. NEVER raises -- see property 2."""
    if not dual_write_enabled():
        return False
    if not is_mirrored(sql):
        with _lock:
            _stats["skipped"] += 1
        return False
    pg_sql = translate(sql)
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
        cx.execute(pg_sql, tuple(params or ()))
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
           "last_divergence": None}
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
    """Rows from Postgres for a translated SELECT, or None when the shadow
    cannot answer. None means UNKNOWN -- callers must not read it as empty."""
    cx = _connect()
    if cx is None:
        return None
    try:
        cur = cx.execute(sql, tuple(params or ()))
        return cur.fetchall()
    except Exception as e:
        _degrade(f"shadow read failed ({type(e).__name__})")
        return None
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
    pg_sql = translate(sql)
    if pg_sql is None or len(sqlite_rows or []) > _SHADOW_MAX_ROWS:
        with _lock:
            _shadow["skipped"] += 1
        return None
    pg_rows = _shadow_fetch(pg_sql, params)
    if pg_rows is None:
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
        return bool(preflight_cutover()["ok"])
    except Exception:
        return False        # cannot verify -> not engaged


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
