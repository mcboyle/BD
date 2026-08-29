"""dev_suite.db_tools -- database inspection

Split from the dev_suite.py monolith (v3.66.395, pure code motion; surface preserved
via dev_suite/__init__.py). See kb/decomp/dev_suite/.
"""


from __future__ import annotations
import os
import sys
import threading
from pathlib import Path
import re as _sec_re
import json as _cfg_json
import re as _cfg_re
import os as _dl_os
import re as _dl_re

from ._common import (
    _collect_cred_refs, _human_secs, _resolve_all_site_configs)



# ── 14. WAL checkpoint ─────────────────────────────────────────────

def wal_checkpoint(mode="TRUNCATE") -> dict:
    """Run PRAGMA wal_checkpoint to flush the -wal sidecar back into
    the main DB. Reports sidecar size before/after. Read-committed
    safe — a checkpoint never loses committed data."""
    mode = str(mode or "TRUNCATE").upper()
    if mode not in ("PASSIVE", "FULL", "RESTART", "TRUNCATE"):
        return {"ok": False, "error": f"bad checkpoint mode {mode!r}"}
    from bulk_downloader.constants import DB_PATH  # noqa: F401
    from bulk_downloader import db as _db
    wal = str(_db._resolve_db_path()) + "-wal"
    before = os.path.getsize(wal) if os.path.exists(wal) else 0
    try:
        with _db.db_conn() as cx:
            row = cx.execute(
                f"PRAGMA wal_checkpoint({mode})").fetchone()
        after = os.path.getsize(wal) if os.path.exists(wal) else 0
        # row is (busy, log_frames, checkpointed_frames)
        return {
            "ok": True,
            "mode": mode,
            "busy": row[0] if row else None,
            "wal_frames": row[1] if row else None,
            "checkpointed_frames": row[2] if row else None,
            "wal_bytes_before": before,
            "wal_bytes_after": after,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}



# ── 16b. read-only SQL console ─────────────────────────────────────

# Statements other than a single read-only SELECT are refused outright.
_SQL_FORBIDDEN = ("insert", "update", "delete", "drop", "alter",
                  "create", "replace", "attach", "detach", "pragma",
                  "vacuum", "reindex", "begin", "commit", "rollback")



def sql_console(query, limit=200) -> dict:
    """Run a SINGLE read-only SELECT against the DB and return rows.

    Rejected: anything that is not a lone SELECT — multiple statements,
    any write/DDL keyword, PRAGMA. This is a query window, not a way
    to mutate the DB; mutation goes through the app's own code paths.
    """
    try:
        limit = max(1, min(int(limit), 1000))
    except Exception:
        limit = 200
    q = str(query or "").strip().rstrip(";").strip()
    if not q:
        return {"ok": False, "error": "empty query"}
    if ";" in q:
        return {"ok": False,
                "error": "only a single statement is allowed"}
    low = q.lower()
    if not (low.startswith("select") or low.startswith("with")):
        return {"ok": False,
                "error": "only SELECT (or WITH ... SELECT) is allowed"}
    # token-boundary check so column names like 'updated_at' are fine
    import re
    toks = set(re.findall(r"[a-z_]+", low))
    hit = toks & set(_SQL_FORBIDDEN)
    if hit:
        return {"ok": False,
                "error": f"forbidden keyword(s): {sorted(hit)}"}
    from bulk_downloader import db as _db
    try:
        with _db.db_conn() as cx:
            cur = cx.execute(f"SELECT * FROM ({q}) LIMIT {limit}")
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [list(r) for r in cur.fetchall()]
        return {"ok": True, "columns": cols, "row_count": len(rows),
                "rows": rows, "truncated": len(rows) == limit}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}



# ── 23. DB integrity-check runner (D-3) ────────────────────────────

def integrity_check() -> dict:
    """On-demand DB integrity check — runs PRAGMA quick_check and the
    full PRAGMA integrity_check, each timed. Read-only. The in-app
    sibling of the L22 live test."""
    import time as _t
    from bulk_downloader import db as _db
    out: dict = {}
    for name in ("quick_check", "integrity_check"):
        t0 = _t.time()
        try:
            with _db.db_conn() as cx:
                rows = [r[0] for r in
                        cx.execute(f"PRAGMA {name}").fetchall()]
            problems = [r for r in rows if r != "ok"]
            out[name] = {"ok": not problems,
                         "elapsed_ms": round((_t.time() - t0) * 1000, 1),
                         "problems": problems}
        except Exception as e:
            out[name] = {"ok": False, "elapsed_ms": 0,
                         "error": str(e)[:200]}
    out["ok"] = all(v.get("ok") for v in out.values())
    out["verdict"] = ("database integrity OK" if out["ok"]
                      else "integrity check found problems")
    return out



# ── 24. backup verifier — WAL-sidecar aware (D-9) ──────────────────

def backup_check(path) -> dict:
    """Verify a backup. Wraps backup_verify, and additionally asserts
    the WAL sidecars are accounted for — a .db backed up without its
    -wal sibling is a stale snapshot (the most recent committed pages
    may live only in the WAL). Accepts a .db/.sqlite file or a
    .zip/.tar archive."""
    p = Path(str(path or "")).expanduser()
    if not p.is_file():
        return {"ok": False, "error": f"backup not found: {p}",
                "verdict": "backup not found"}
    from bulk_downloader import backup_verify as _bv
    warnings = []
    name = p.name.lower()
    if name.endswith((".db", ".sqlite")):
        base = _bv.verify_db_dump(str(p))
        sidecars = {sfx: (p.parent / (p.name + sfx)).is_file()
                    for sfx in ("-wal", "-shm")}
        if sidecars["-wal"]:
            warnings.append("a -wal sidecar sits next to this .db — "
                            "back it up too, or checkpoint first, or "
                            "the snapshot may miss recent commits")
        kind = "sqlite-db"
    else:
        base = _bv.verify_tarball(str(p))
        members = []
        try:
            import tarfile
            import zipfile
            if name.endswith(".zip"):
                with zipfile.ZipFile(p) as zf:
                    members = zf.namelist()
            elif name.endswith((".tar", ".tar.gz", ".tgz")):
                with tarfile.open(p, "r:*") as tf:
                    members = tf.getnames()
        except Exception as e:
            warnings.append(f"could not list archive members: {e}"[:160])
        sidecars = {"-wal": any(m.endswith("-wal") for m in members),
                    "-shm": any(m.endswith("-shm") for m in members),
                    ".db": any(m.endswith((".db", ".sqlite"))
                               for m in members)}
        if not sidecars[".db"]:
            warnings.append("no .db/.sqlite file found in the archive")
        elif not sidecars["-wal"]:
            warnings.append("archive has a .db but no -wal sidecar — "
                            "if the DB was in WAL mode this backup may "
                            "miss the most recent committed pages")
        kind = "archive"
    base_ok = bool(base.get("ok"))
    no_db = any("no .db" in w for w in warnings)
    ok = base_ok and not no_db
    if not base_ok:
        verdict = "base verification failed — see base_check"
    elif no_db:
        verdict = "archive contains no database file"
    elif warnings:
        verdict = f"verified, with {len(warnings)} warning(s)"
    else:
        verdict = "backup verified — consistent, sidecars accounted for"
    return {
        "ok": ok,
        "path": str(p),
        "kind": kind,
        "base_check": base,
        "wal_sidecars": sidecars,
        "warnings": warnings,
        "verdict": verdict,
    }



# ── 26. stuck-job detector (D-12) ──────────────────────────────────

def stuck_jobs(older_than=1800) -> dict:
    """Queue rows stuck in 'running' with no ts_updated progress for
    more than `older_than` seconds. queue.ts_updated is a UTC ISO
    timestamp, so the cutoff is built in UTC (lesson A2)."""
    import datetime as _dt
    try:
        older_than = max(1, int(older_than))
    except Exception:
        older_than = 1800
    cutoff = (_dt.datetime.now(_dt.timezone.utc)
              - _dt.timedelta(seconds=older_than))
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
    from bulk_downloader import db as _db
    try:
        with _db.db_conn() as cx:
            rows = cx.execute(
                "SELECT site_id, url, status, retries, ts_added, "
                "ts_updated FROM queue WHERE status='running' "
                "AND ts_updated < ? ORDER BY ts_updated",
                (cutoff_iso,)).fetchall()
            running_total = cx.execute(
                "SELECT COUNT(*) FROM queue "
                "WHERE status='running'").fetchone()[0]
    except Exception as e:
        return {"error": str(e)[:200]}
    stuck = [{"site_id": r[0], "url": r[1], "status": r[2],
              "retries": r[3], "ts_added": r[4], "ts_updated": r[5]}
             for r in rows]
    return {
        "threshold_seconds": older_than,
        "cutoff_utc": cutoff_iso,
        "running_total": running_total,
        "stuck_count": len(stuck),
        "stuck_jobs": stuck,
        "verdict": (f"no jobs running longer than {older_than}s"
                    if not stuck
                    else f"{len(stuck)} job(s) in 'running' > "
                         f"{older_than}s with no progress"),
    }



# ── 35. duplicate-site / orphan-row / stale-reference (U16) ────────
#
# Three drift detectors. config_integrity (§12) already inventories
# @cred refs and checks url/base_url dups — these are scoped AROUND
# it, not over it:
#   • duplicate_sites (D-88) — dups by login_url and by display name,
#     normalised. config_integrity checks neither of those keys.
#   • orphan_rows (D-7) — DB rows whose site_id has no config.
#     config_integrity never touches the DB.
#   • stale_references (D-92) — config refs that point at something
#     gone (a missing @cred label, cookie_file, download_dir, or an
#     unknown login_template). config_integrity lists @cred refs but
#     never resolves them.

_DRIFT_DB_TABLES = ("queue", "history", "session_history")



def _norm_ref(s):
    """Normalise a URL or name for duplicate comparison."""
    return str(s or "").strip().lower().rstrip("/")



def duplicate_sites(runners=None, site_configs=None):
    """D-88 — sites that duplicate each other by login_url or by
    display name (both normalised). Read-only."""
    if site_configs is not None:
        configs, source = dict(site_configs), "caller-supplied"
    else:
        configs, source = _resolve_all_site_configs(runners)
    by_url, by_name = {}, {}
    for sid in sorted(configs):
        cfg = configs[sid] or {}
        url = _norm_ref(cfg.get("login_url"))
        name = _norm_ref(cfg.get("name"))
        if url:
            by_url.setdefault(url, []).append(sid)
        if name:
            by_name.setdefault(name, []).append(sid)
    dup_url = [{"login_url": u, "site_ids": s}
               for u, s in sorted(by_url.items()) if len(s) > 1]
    dup_name = [{"name": n, "site_ids": s}
                for n, s in sorted(by_name.items()) if len(s) > 1]
    return {
        "tool": "duplicate_sites",
        "config_source": source,
        "sites_total": len(configs),
        "duplicate_login_url_groups": dup_url,
        "duplicate_name_groups": dup_name,
        "verdict": ("no duplicate sites" if not dup_url and not dup_name
                    else f"{len(dup_url)} login_url and {len(dup_name)} "
                         "name duplicate group(s)"),
    }



def orphan_rows(runners=None, site_configs=None):
    """D-7 — rows in queue/history/session_history whose site_id has
    no matching site config (DB-vs-config drift). Read-only.

    queue orphans are actionable — those jobs cannot run. history /
    session_history orphans are usually just retained records for a
    removed site, and the verdict says so.
    """
    if site_configs is not None:
        configured, source = set(site_configs), "caller-supplied"
    else:
        cfgs, source = _resolve_all_site_configs(runners)
        configured = set(cfgs)
    try:
        from bulk_downloader import db as _db
    except Exception as e:
        return {"tool": "orphan_rows", "ok": False,
                "error": f"db module unavailable: {e}"}
    tables, queue_orphans, other_orphans = [], 0, 0
    try:
        with _db.db_conn() as cx:
            present = {r[0] for r in cx.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for t in _DRIFT_DB_TABLES:
                if t not in present:
                    tables.append({"table": t, "present": False})
                    continue
                rows = cx.execute(
                    f'SELECT site_id, COUNT(*) FROM "{t}" '
                    "GROUP BY site_id").fetchall()
                orphans = [{"site_id": sid, "rows": n}
                           for sid, n in rows if sid not in configured]
                n = sum(o["rows"] for o in orphans)
                if t == "queue":
                    queue_orphans += n
                else:
                    other_orphans += n
                tables.append({"table": t, "present": True,
                               "distinct_site_ids": len(rows),
                               "orphan_site_ids": orphans})
    except Exception as e:
        return {"tool": "orphan_rows", "ok": False,
                "error": f"DB read failed: {e}"}
    if queue_orphans:
        verdict = (f"{queue_orphans} queue row(s) reference "
                   "unconfigured sites — those jobs cannot run")
    elif other_orphans:
        verdict = (f"{other_orphans} retained history/session row(s) "
                   "for removed sites — usually harmless records")
    else:
        verdict = "no orphan rows — DB and config agree"
    return {
        "tool": "orphan_rows",
        "ok": True,
        "config_source": source,
        "configured_sites": len(configured),
        "queue_orphan_rows": queue_orphans,
        "other_orphan_rows": other_orphans,
        "tables": tables,
        "verdict": verdict,
    }



def stale_references(runners=None, site_configs=None):
    """D-92 — config references that point at something gone: an
    unknown @cred vault label, a missing cookie_file or download_dir,
    or an unrecognised login_template. Read-only.

    Resolution is by existence check only — vault labels are matched
    against the backend's key list (no secret is decrypted)."""
    if site_configs is not None:
        configs, source = dict(site_configs), "caller-supplied"
    else:
        configs, source = _resolve_all_site_configs(runners)
    try:
        from bulk_downloader import secrets_store as _ss
        vault_labels = set(_ss.get_backend().list_keys() or [])
        cred_prefix = getattr(_ss, "CRED_PREFIX", "@cred:")
        vault_ok = True
    except Exception:
        vault_labels, cred_prefix, vault_ok = set(), "@cred:", False
    try:
        from bulk_downloader import login_templates_data as _lt
        known_tpl = set()
        for t in _lt.list_login_templates():
            for k in ("id", "name", "host"):
                if t.get(k):
                    known_tpl.add(str(t[k]).strip().lower())
        tpl_ok = True
    except Exception:
        known_tpl, tpl_ok = set(), False

    sites, n_stale = [], 0
    for sid in sorted(configs):
        cfg = configs[sid] or {}
        stale = []
        refs = []
        _collect_cred_refs(cfg, refs)
        for ref in refs:
            label = (ref[len(cred_prefix):]
                     if ref.startswith(cred_prefix) else ref)
            if vault_ok and label not in vault_labels:
                stale.append({"kind": "cred_reference", "value": ref,
                              "detail": "no such label in the vault"})
        ck = cfg.get("cookie_file")
        if isinstance(ck, str) and ck.strip() \
                and not Path(ck).expanduser().exists():
            stale.append({"kind": "cookie_file", "value": ck,
                          "detail": "file does not exist"})
        dd = cfg.get("download_dir")
        if isinstance(dd, str) and dd.strip() \
                and not Path(dd).expanduser().is_dir():
            stale.append({"kind": "download_dir", "value": dd,
                          "detail": "directory does not exist"})
        lt = cfg.get("login_template")
        if isinstance(lt, str) and lt.strip() and tpl_ok \
                and lt.strip().lower() not in known_tpl:
            stale.append({"kind": "login_template", "value": lt,
                          "detail": "no such login template"})
        if stale:
            n_stale += 1
            sites.append({"site_id": sid, "stale_references": stale})
    return {
        "tool": "stale_references",
        "config_source": source,
        "sites_total": len(configs),
        "sites_with_stale_refs": n_stale,
        "vault_checked": vault_ok,
        "login_templates_checked": tpl_ok,
        "sites": sites,
        "verdict": ("no stale references" if n_stale == 0
                    else f"{n_stale} site(s) reference something gone"),
    }



# ── 42. slow-query profiler + index advisor (U23: D-1 + D-2) ───────
#
# D-1 + D-2 — both wrap db.db_conn() over one shared set of
# representative "hot" queries (the filter/sort patterns the app's
# main views run, matched to the indexes db.py creates for them).
#   • slow_query_profiler (D-1) — times each, flags slow ones.
#   • index_advisor (D-2) — EXPLAIN QUERY PLANs each + lists indexes,
#     flagging any query that does a full table scan.
# Both are read-only: SELECT, EXPLAIN QUERY PLAN, and PRAGMA
# index_list/index_info execute or mutate nothing.

_HOT_QUERIES = [
    {"name": "queue_by_status", "table": "queue",
     "sql": "SELECT * FROM queue WHERE status = ?",
     "params": ("pending",)},
    {"name": "queue_by_site_status", "table": "queue",
     "sql": "SELECT * FROM queue WHERE site_id = ? AND status = ?",
     "params": ("_probe_site", "pending")},
    {"name": "queue_by_site_ordered", "table": "queue",
     "sql": "SELECT * FROM queue WHERE site_id = ? ORDER BY ord",
     "params": ("_probe_site",)},
    {"name": "history_by_site_recent", "table": "history",
     "sql": ("SELECT * FROM history WHERE site_id = ? "
             "ORDER BY ts DESC LIMIT 50"),
     "params": ("_probe_site",)},
    {"name": "history_by_status", "table": "history",
     "sql": "SELECT * FROM history WHERE status = ?",
     "params": ("ok",)},
    {"name": "history_by_url", "table": "history",
     "sql": "SELECT * FROM history WHERE url = ?",
     "params": ("https://example.com/probe",)},
    {"name": "session_history_by_site_recent", "table": "session_history",
     "sql": ("SELECT * FROM session_history WHERE site_id = ? "
             "ORDER BY ts DESC LIMIT 50"),
     "params": ("_probe_site",)},
    {"name": "session_history_by_event", "table": "session_history",
     "sql": "SELECT * FROM session_history WHERE event_type = ?",
     "params": ("login",)},
]


_ADVISOR_TABLES = ["history", "queue", "session_history",
                   "push_subscriptions"]



def _explain_query_plan(cx, sql, params):
    """Run EXPLAIN QUERY PLAN and classify the result. Returns
    {steps, uses_index, full_scan}. A step is a full table scan when
    its detail says SCAN without an index (USING INDEX / COVERING
    INDEX / INTEGER PRIMARY KEY all count as indexed access)."""
    rows = cx.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    steps = [str(r["detail"]) for r in rows]
    full_scan = False
    uses_index = False
    for d in steps:
        up = d.upper()
        indexed = ("USING INDEX" in up or "USING COVERING INDEX" in up
                   or "USING INTEGER PRIMARY KEY" in up)
        if indexed:
            uses_index = True
        if "SCAN" in up and not indexed:
            full_scan = True
    return {"steps": steps, "uses_index": uses_index,
            "full_scan": full_scan}



def slow_query_profiler(iterations=5, slow_ms=25.0):
    """D-1 — time each representative hot query against the live DB and
    flag slow ones. Read-only (SELECT only). On an empty DB everything
    is sub-millisecond; the value is on a populated production DB."""
    import time as _t
    import statistics as _st
    from bulk_downloader import db as _db
    try:
        iterations = max(1, min(int(iterations), 50))
    except (TypeError, ValueError):
        iterations = 5
    try:
        slow_ms = float(slow_ms)
    except (TypeError, ValueError):
        slow_ms = 25.0
    results = []
    try:
        with _db.db_conn() as cx:
            for q in _HOT_QUERIES:
                timings = []
                rows = 0
                err = None
                for _ in range(iterations):
                    try:
                        t0 = _t.perf_counter()
                        fetched = cx.execute(
                            q["sql"], q["params"]).fetchall()
                        timings.append(
                            (_t.perf_counter() - t0) * 1000.0)
                        rows = len(fetched)
                    except Exception as qe:
                        err = f"{type(qe).__name__}: {qe}"
                        break
                if err:
                    results.append({"name": q["name"],
                                    "table": q["table"], "error": err})
                    continue
                results.append({
                    "name": q["name"], "table": q["table"],
                    "rows": rows,
                    "min_ms": round(min(timings), 3),
                    "median_ms": round(_st.median(timings), 3),
                    "max_ms": round(max(timings), 3),
                    "slow": _st.median(timings) > slow_ms,
                })
    except Exception as e:
        return {"tool": "slow_query_profiler", "ok": False,
                "error": f"could not open DB: {e}"}
    slow = [r["name"] for r in results if r.get("slow")]
    errors = [r["name"] for r in results if r.get("error")]
    return {
        "tool": "slow_query_profiler",
        "ok": True,
        "iterations": iterations,
        "slow_ms_threshold": slow_ms,
        "queries_profiled": len(results),
        "slow_queries": slow,
        "errored_queries": errors,
        "verdict": ("all hot queries fast" if not slow and not errors
                    else f"{len(slow)} slow, {len(errors)} errored"),
        "results": results,
    }



def index_advisor():
    """D-2 — list every index per table and EXPLAIN QUERY PLAN each hot
    query, flagging any that does a full table scan (a candidate for a
    new index). Read-only — EXPLAIN and PRAGMA mutate nothing."""
    from bulk_downloader import db as _db
    indexes = {}
    plans = []
    try:
        with _db.db_conn() as cx:
            for tbl in _ADVISOR_TABLES:
                try:
                    idx_rows = cx.execute(
                        f"PRAGMA index_list('{tbl}')").fetchall()
                except Exception:
                    indexes[tbl] = {"error": "table missing"}
                    continue
                tbl_idx = []
                for ir in idx_rows:
                    iname = ir["name"]
                    cols = [c["name"] for c in cx.execute(
                        f"PRAGMA index_info('{iname}')").fetchall()]
                    tbl_idx.append({
                        "name": iname,
                        "columns": cols,
                        "unique": bool(ir["unique"]),
                        # origin 'c' = CREATE INDEX; 'u'/'pk' = auto
                        "auto": ir["origin"] != "c",
                    })
                indexes[tbl] = {"index_count": len(tbl_idx),
                                "indexes": tbl_idx}
            for q in _HOT_QUERIES:
                try:
                    plan = _explain_query_plan(
                        cx, q["sql"], q["params"])
                    plans.append({"name": q["name"],
                                  "table": q["table"], **plan})
                except Exception as e:
                    plans.append({"name": q["name"],
                                  "table": q["table"],
                                  "error": f"{type(e).__name__}: {e}"})
    except Exception as e:
        return {"tool": "index_advisor", "ok": False,
                "error": f"could not open DB: {e}"}
    scanning = [p["name"] for p in plans if p.get("full_scan")]
    errors = [p["name"] for p in plans if p.get("error")]
    return {
        "tool": "index_advisor",
        "ok": True,
        "tables": indexes,
        "hot_query_plans": plans,
        "full_scan_queries": scanning,
        "errored_queries": errors,
        "advice": ("every hot query uses an index"
                   if not scanning and not errors
                   else f"{len(scanning)} hot query(ies) do a full "
                        f"table scan — consider an index"),
    }



# ── 43. migration status (U24: D-4) ────────────────────────────────
#
# D-4 — surface the existing migrations.py ledger as a read-only dev
# view: registered migrations, which versions are applied, which are
# pending, and schema drift vs EXPECTED_SCHEMA. This is a thin wrapper
# on migrations.status() (lesson E10 — do not rebuild the migration
# system). Deliberately read-only: it reports pending migrations but
# never applies them — apply_pending() mutates the DB and is not a
# dev-inspection action.

def migration_status():
    """D-4 — migration ledger snapshot: registered vs applied vs
    pending migrations, plus schema-drift detection. Read-only — never
    applies a migration (use migrations.apply_pending for that)."""
    try:
        from bulk_downloader import migrations as _mig
    except Exception as e:
        return {"tool": "migration_status", "ok": False,
                "error": f"migrations module unavailable: {e}"}
    try:
        st = _mig.status()
    except Exception as e:
        return {"tool": "migration_status", "ok": False,
                "error": f"status() failed: {type(e).__name__}: {e}"}
    pending = st.get("pending") or []
    drift = st.get("drift") or {}
    drift_ok = bool(drift.get("ok", True))
    if pending and not drift_ok:
        verdict = (f"{len(pending)} migration(s) pending AND schema "
                   f"drift detected")
    elif pending:
        verdict = f"{len(pending)} migration(s) pending — DB behind code"
    elif not drift_ok:
        verdict = "schema drift detected vs expected manifest"
    else:
        verdict = "DB schema up to date — no pending migrations, no drift"
    return {
        "tool": "migration_status",
        "ok": True,
        "registered_migrations": st.get("registered_migrations", 0),
        "applied_versions": st.get("applied_versions") or [],
        "pending": pending,
        "pending_count": len(pending),
        "drift": drift,
        "drift_ok": drift_ok,
        "verdict": verdict,
    }



# ── 53. queue-table inspector + FTS inspector (T3: D-6 + D-5) ───────
#
# D-6 — read-only inspection of the persisted `queue` table. This is
# NOT db_overview (which only gives a single total row count) and NOT
# runner_state (in-memory runner state — the queue table is the
# on-disk persistence that survives a restart). It surfaces per-site
# / per-status counts, retry pressure, age of the oldest pending row,
# and "stuck" rows whose retry_after is in the past but are still
# pending — the persisted symptom of a wedged job.
#
# D-5 — read-only health of the history_fts FTS5 index. The index is
# a content-less external-content table (content='history',
# content_rowid='id'); it can silently desync from `history` if a
# write path ever skipped the FTS mirror, so row-count drift is the
# headline signal. Also reports FTS5 availability, integrity-check
# result, and the age of the .fts_optimize_last sentinel.

# Columns the queue table is expected to carry (db.py CREATE TABLE).
# Used only to report schema drift — never to mutate.
_QUEUE_EXPECTED_COLUMNS = [
    "site_id", "url", "status", "message", "retries", "retry_after",
    "screenshot", "force_download", "priority", "ord", "filename",
    "listing_title", "file_size", "lane", "depends_on", "ts_added",
    "ts_updated",
]



def queue_table_inspect(site_id=None, sample=10):
    """D-6 — inspect the persisted `queue` table (read-only).

    Optional `site_id` narrows every count/sample to one site.
    `sample` caps how many oldest-pending rows are returned (URL is
    truncated; no other row text is emitted). Returns ok:false with a
    clear error rather than raising if the DB cannot be opened.
    """
    import time as _time
    from bulk_downloader import db as _db
    try:
        sample = max(0, min(int(sample), 100))
    except Exception:
        sample = 10
    out = {"tool": "queue_table_inspect", "ok": True,
           "site_filter": site_id or None}
    try:
        with _db.db_conn() as cx:
            # queue table present?
            if not cx.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='queue'").fetchone():
                return {"tool": "queue_table_inspect", "ok": False,
                        "error": "queue table not present"}

            # schema drift — compare actual columns to expected
            cols = [r["name"] for r in
                    cx.execute("PRAGMA table_info('queue')").fetchall()]
            out["columns"] = cols
            out["missing_columns"] = [c for c in _QUEUE_EXPECTED_COLUMNS
                                      if c not in cols]
            out["unexpected_columns"] = [c for c in cols
                                         if c not in _QUEUE_EXPECTED_COLUMNS]

            where = ""
            params: list = []
            if site_id:
                where = " WHERE site_id = ?"
                params = [site_id]

            out["total_rows"] = cx.execute(
                f"SELECT COUNT(*) FROM queue{where}", params).fetchone()[0]

            # per-status counts
            by_status = {}
            for r in cx.execute(
                    f"SELECT status, COUNT(*) AS n FROM queue{where} "
                    "GROUP BY status ORDER BY n DESC", params).fetchall():
                by_status[r["status"]] = r["n"]
            out["by_status"] = by_status

            # per-site counts (only when not already filtered to one site)
            if not site_id:
                by_site = {}
                for r in cx.execute(
                        "SELECT site_id, COUNT(*) AS n FROM queue "
                        "GROUP BY site_id ORDER BY n DESC").fetchall():
                    by_site[r["site_id"]] = r["n"]
                out["by_site"] = by_site

            # retry pressure — distribution + the worst offenders
            retry_rows = cx.execute(
                f"SELECT retries, COUNT(*) AS n FROM queue{where} "
                "GROUP BY retries ORDER BY retries", params).fetchall()
            out["retry_distribution"] = {str(r["retries"]): r["n"]
                                         for r in retry_rows}
            mx = cx.execute(
                f"SELECT MAX(retries) FROM queue{where}",
                params).fetchone()[0]
            out["max_retries"] = mx or 0

            # stuck rows: still pending, but retry_after is in the past
            # (or zero) — the persisted symptom of a job that should
            # have been picked back up but was not.
            now = _time.time()
            stuck_where = where + (" AND " if where else " WHERE ")
            out["stuck_pending_rows"] = cx.execute(
                f"SELECT COUNT(*) FROM queue{stuck_where}"
                "status = 'pending' AND retry_after > 0 "
                "AND retry_after < ?", params + [now]).fetchone()[0]

            # age of the oldest pending row (ts_added is an ISO-8601
            # UTC string written by strftime('now') — compare as text)
            oldest = cx.execute(
                f"SELECT MIN(ts_added) FROM queue{stuck_where}"
                "status = 'pending'", params).fetchone()[0]
            out["oldest_pending_ts_added"] = oldest
            newest = cx.execute(
                f"SELECT MAX(ts_updated) FROM queue{where}",
                params).fetchone()[0]
            out["newest_ts_updated"] = newest

            # a small sample of the oldest pending rows for eyeballing
            rows = cx.execute(
                f"SELECT site_id, url, retries, retry_after, ts_added "
                f"FROM queue{stuck_where}status = 'pending' "
                "ORDER BY ts_added LIMIT ?",
                params + [sample]).fetchall()
            out["oldest_pending_sample"] = [{
                "site_id": r["site_id"],
                "url": (r["url"][:120] + "…") if r["url"]
                       and len(r["url"]) > 120 else r["url"],
                "retries": r["retries"],
                "retry_after": r["retry_after"],
                "ts_added": r["ts_added"],
            } for r in rows]
    except Exception as e:
        return {"tool": "queue_table_inspect", "ok": False,
                "error": f"could not inspect queue: "
                         f"{type(e).__name__}: {str(e)[:160]}"}

    drift = (out["missing_columns"] or out["unexpected_columns"])
    out["verdict"] = (
        f"{out['total_rows']} queue row(s); "
        f"{out['stuck_pending_rows']} stuck pending"
        + ("; SCHEMA DRIFT" if drift else "")
        + ("; high retry pressure" if out["max_retries"] >= 5 else ""))
    return out



def fts_index_inspect():
    """D-5 — inspect the history_fts FTS5 full-text index (read-only).

    Reports FTS5 availability, whether the virtual table exists, and
    the age of the .fts_optimize_last sentinel.

    The headline metric is index membership, reported as TWO
    SET-DERIVED numbers. history_fts is a CONTENT-LESS external-content
    table (content='history'), so `COUNT(*) FROM history_fts` reads
    straight through to `history` and ALWAYS equals the content-table
    count — it can never reveal a desync.

    A scalar difference cannot replace it, because it cannot separate
    two OPPOSITE error directions:

      unindexed_rows   a `history` row absent from the inverted index.
                       It is UNSEARCHABLE. A write path skipped the FTS
                       mirror.
      orphaned_docs    a doc the inverted index still holds with no
                       `history` row behind it. A delete skipped the
                       FTS mirror and left its terms.

    Each moves `history_count - indexed_count` by one, in OPPOSITE
    directions, so K orphans act as standing credit that understates
    unsearchable rows by exactly K — up to reporting a database with
    permanently unsearchable rows as healthy. `row_count_drift` is
    still reported (it is what older readers consume) but it is that
    cancelling scalar and it does NOT drive the verdict.

    Membership comes from `db._fts_indexed_docs` — the same docset the
    delete-side maintenance (db_fts_forget) derives membership from, so
    there is one definition of "indexed" and not two. Caveat carried
    from that helper: a `history` row with no indexable text in any FTS
    column contributes no term instances and reads as unindexed, so a
    small unindexed count can be benign — the result flags this rather
    than asserting corruption. When the docset cannot be read at all,
    every set-derived number is None and the verdict says UNKNOWN.

    Also runs FTS5's own 'integrity-check' command (read-only). Note
    integrity-check verifies the index's internal structure; it does
    NOT detect a content-table desync, which is why the vocab-based
    drift count is the primary signal.
    """
    import time as _time
    from pathlib import Path as _Path
    from bulk_downloader.constants import DB_PATH
    from bulk_downloader import db as _db
    out = {"tool": "fts_index_inspect", "ok": True,
           "fts5_available": None, "fts_table_present": False}
    try:
        with _db.db_conn() as cx:
            # FTS5 compiled in? probe with a throwaway temp vtable.
            try:
                cx.execute("CREATE VIRTUAL TABLE temp._bd_fts_probe "
                           "USING fts5(x)")
                cx.execute("DROP TABLE temp._bd_fts_probe")
                out["fts5_available"] = True
            except Exception:
                out["fts5_available"] = False

            row = cx.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='history_fts'").fetchone()
            if not row:
                out["verdict"] = (
                    "history_fts not present — search uses the LIKE "
                    "fallback" if out["fts5_available"] is False
                    else "history_fts not present")
                return out
            out["fts_table_present"] = True
            out["create_sql"] = row["sql"]

            hist_count = cx.execute(
                "SELECT COUNT(*) FROM history").fetchone()[0]
            out["history_row_count"] = hist_count

            # COUNT(*) on a content-less external-content table reads
            # through to `history`, so it is NOT a drift signal — keep
            # it only as a sanity field, clearly labelled.
            out["fts_count_star"] = cx.execute(
                "SELECT COUNT(*) FROM history_fts").fetchone()[0]

            # Membership, not a count. db._fts_indexed_docs returns the
            # docset the inverted index actually holds (a transient
            # fts5vocab shadow vtable over the existing index — it
            # writes no data and is dropped in a finally, so this stays
            # read-only). Reused rather than re-probed here: it is the
            # denominator the delete-side maintenance already uses, and
            # two definitions of "indexed" would drift apart.
            #
            # Then TWO set differences, because collapsing them to
            # `hist_count - indexed` lets the two directions CANCEL.
            docs = _db._fts_indexed_docs(cx)
            if docs is None:
                out["indexed_doc_count_error"] = (
                    "fts5vocab probe failed; index membership is UNKNOWN")
                out["indexed_doc_count"] = None
                out["orphaned_docs"] = None
                out["unindexed_rows"] = None
                out["row_count_drift"] = None
            else:
                hist_ids = {r[0] for r in cx.execute(
                    "SELECT id FROM history").fetchall()}
                out["indexed_doc_count"] = len(docs)
                # in the index, no `history` row behind it
                out["orphaned_docs"] = len(docs - hist_ids)
                # in `history`, absent from the index: UNSEARCHABLE
                out["unindexed_rows"] = len(hist_ids - docs)
                # kept for older readers, and only ever the cancelling
                # scalar. Not the verdict input.
                out["row_count_drift"] = hist_count - len(docs)

            # FTS5 integrity-check — verifies the index's internal
            # structure (read-only). Does NOT catch a content desync.
            try:
                cx.execute("INSERT INTO history_fts(history_fts) "
                           "VALUES('integrity-check')")
                out["integrity_check"] = "ok"
            except Exception as e:
                out["integrity_check"] = f"FAILED: {str(e)[:120]}"
    except Exception as e:
        return {"tool": "fts_index_inspect", "ok": False,
                "error": f"could not inspect FTS index: "
                         f"{type(e).__name__}: {str(e)[:160]}"}

    # age of the optimize sentinel (written by db_fts_optimize)
    sentinel = _Path(_db._resolve_db_path()).parent / ".fts_optimize_last"
    if sentinel.exists():
        try:
            last = float(sentinel.read_text(encoding="utf-8").strip())
            out["last_optimize_age_hours"] = round(
                (_time.time() - last) / 3600, 1)
        except (ValueError, OSError):
            out["last_optimize_age_hours"] = None
    else:
        out["last_optimize_age_hours"] = None

    integ_ok = out.get("integrity_check") == "ok"
    idx = out.get("indexed_doc_count")
    orphaned = out.get("orphaned_docs")
    unindexed = out.get("unindexed_rows")
    if orphaned is None or unindexed is None:
        # UNKNOWN is a third state and it FAILS. Reporting health from
        # a denominator that could not be read is the failure mode this
        # tool exists to avoid, so it says so instead.
        out["health"] = "unknown"
        out["verdict"] = (
            "UNKNOWN: FTS index present but its membership could not "
            "be derived (fts5vocab probe failed), so unsearchable rows "
            "and orphaned docs are both unmeasured; integrity="
            f"{out.get('integrity_check')}")
    elif orphaned == 0 and unindexed == 0 and integ_ok:
        out["health"] = "healthy"
        out["verdict"] = (
            f"FTS index healthy: {idx} indexed doc(s), in sync with "
            "history")
    elif not integ_ok:
        out["health"] = "degraded"
        out["verdict"] = (
            f"FTS index issue: integrity={out.get('integrity_check')}; "
            f"{unindexed} unsearchable history row(s), {orphaned} "
            "orphaned doc(s)")
    else:
        out["health"] = "degraded"
        out["verdict"] = (
            f"FTS index drift: {unindexed} history row(s) absent from "
            f"the index (UNSEARCHABLE), {orphaned} orphaned doc(s) held "
            f"by the index with no history row ({idx} indexed vs "
            f"{out['history_row_count']} in history) — a row whose FTS "
            "columns are all empty contributes no terms and reads as "
            "unindexed, so a small unindexed count can be benign; "
            "orphans are terms left behind by a delete that skipped the "
            "FTS mirror")
    return out



# ── 54. DB-growth grapher + queue-throughput meter (T4: D-10 + D-15)─
#
# D-10 — DB growth. There is NO persisted history of past .db file
# sizes (a background sampler would have to run at import time, which
# is forbidden — lesson on module-level work). So this tool does NOT
# fabricate a recorded growth curve. It reports the current on-disk
# size breakdown, per-table row counts, and a forward PROJECTION:
# given the observed history-row arrival rate (rows/day over the last
# N days) and the current average bytes-per-row, it estimates MB
# added per 30 days. The projection is honest about its inputs — it
# is "if the last N days continue", not a measurement of the past.
#
# D-15 — queue throughput. `history` already IS a throughput record:
# every completed job is one row stamped with an ISO-8601 UTC `ts`.
# Bucketing history rows by `ts` (per hour or per day) yields a real
# completed-jobs-over-time series with no sampler needed. Optionally
# split by terminal status so the operator sees done vs error rate.

def _history_day_series(cx, days=14, site_id=None):
    """Rows-per-UTC-day over the last `days` days from `history`,
    as an ordered list of {day, count}. Days with zero rows are
    filled in so the series has no gaps."""
    import datetime as _dt
    where = "WHERE ts >= ?"
    params: list = [
        (_dt.datetime.utcnow()
         - _dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")]
    if site_id:
        where += " AND site_id = ?"
        params.append(site_id)
    raw = {r["day"]: r["n"] for r in cx.execute(
        f"SELECT strftime('%Y-%m-%d', ts) AS day, COUNT(*) AS n "
        f"FROM history {where} GROUP BY day", params).fetchall()}
    today = _dt.datetime.utcnow().date()
    series = []
    for i in range(days, -1, -1):
        d = (today - _dt.timedelta(days=i)).isoformat()
        series.append({"day": d, "count": raw.get(d, 0)})
    return series



def db_growth_report(days=14):
    """D-10 — current DB size breakdown + a forward growth projection
    from the recent history-row arrival rate (read-only).

    `days` is the look-back window used to estimate the arrival rate.
    The projection is "if the last `days` continue", not a recorded
    measurement of past sizes — no such record exists.
    """
    from bulk_downloader.constants import DB_PATH  # noqa: F401
    from bulk_downloader import db as _db
    _resolved = _db._resolve_db_path()
    try:
        days = max(1, min(int(days), 365))
    except Exception:
        days = 14
    out = {"tool": "db_growth_report", "ok": True,
           "lookback_days": days, "db_path": str(_resolved)}

    # current on-disk size breakdown
    sizes = {}
    total = 0
    for suffix, key in (("", "db"), ("-wal", "wal"), ("-shm", "shm")):
        p = str(_resolved) + suffix
        n = os.path.getsize(p) if os.path.exists(p) else 0
        sizes[key] = n
        total += n
    out["size_bytes"] = sizes
    out["total_bytes"] = total
    out["total_mb"] = round(total / (1024 * 1024), 3)

    try:
        with _db.db_conn() as cx:
            tables = [r[0] for r in cx.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "ORDER BY name")]
            row_counts = {}
            for name in tables:
                try:
                    row_counts[name] = cx.execute(
                        f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                except Exception:
                    row_counts[name] = None
            out["row_counts"] = row_counts

            hist_rows = row_counts.get("history") or 0
            series = _history_day_series(cx, days=days)
            recent = sum(d["count"] for d in series)
            # rows/day averaged over the look-back window
            rate = recent / days if days else 0.0
            out["history_rows_total"] = hist_rows
            out["history_rows_in_window"] = recent
            out["history_rows_per_day"] = round(rate, 2)

            # bytes-per-row is a whole-DB approximation: total file
            # size / total history rows. Coarse, but the right order
            # of magnitude and clearly labelled as an estimate.
            if hist_rows > 0:
                bytes_per_row = total / hist_rows
                out["est_bytes_per_history_row"] = round(bytes_per_row, 1)
                out["projected_growth_mb_30d"] = round(
                    rate * 30 * bytes_per_row / (1024 * 1024), 3)
            else:
                out["est_bytes_per_history_row"] = None
                out["projected_growth_mb_30d"] = None
    except Exception as e:
        return {"tool": "db_growth_report", "ok": False,
                "error": f"could not read DB: "
                         f"{type(e).__name__}: {str(e)[:160]}"}

    proj = out.get("projected_growth_mb_30d")
    out["verdict"] = (
        f"DB is {out['total_mb']} MB; "
        + (f"~{proj} MB/30d projected at the current "
           f"{out['history_rows_per_day']} rows/day"
           if proj is not None else
           "no history rows yet — growth not projectable"))
    return out



def queue_throughput(hours=24, bucket="hour", site_id=None):
    """D-15 — completed-job throughput from the `history` table
    (read-only). Every history row is one finished job stamped with a
    UTC `ts`; bucketing those rows IS the throughput series — no
    sampler is needed.

    `bucket` is 'hour' or 'day'. `hours` is the look-back window.
    Result includes the series, peak/mean per bucket, and a per-status
    split (done vs error vs everything else) over the window.
    """
    import datetime as _dt
    from bulk_downloader import db as _db
    try:
        hours = max(1, min(int(hours), 24 * 90))
    except Exception:
        hours = 24
    bucket = "day" if str(bucket).lower() == "day" else "hour"
    fmt = "%Y-%m-%d" if bucket == "day" else "%Y-%m-%dT%H:00"
    out = {"tool": "queue_throughput", "ok": True,
           "lookback_hours": hours, "bucket": bucket,
           "site_filter": site_id or None}
    try:
        with _db.db_conn() as cx:
            cutoff = (_dt.datetime.utcnow()
                      - _dt.timedelta(hours=hours)).strftime(
                          "%Y-%m-%dT%H:%M:%S")
            where = "WHERE ts >= ?"
            params: list = [cutoff]
            if site_id:
                where += " AND site_id = ?"
                params.append(site_id)

            rows = cx.execute(
                f"SELECT strftime('{fmt}', ts) AS bkt, "
                f"COUNT(*) AS n FROM history {where} "
                "GROUP BY bkt ORDER BY bkt", params).fetchall()
            series = [{"bucket": r["bkt"], "count": r["n"]}
                      for r in rows]
            out["series"] = series
            counts = [s["count"] for s in series]
            out["total_completed"] = sum(counts)
            out["bucket_count"] = len(series)
            out["peak_per_bucket"] = max(counts) if counts else 0
            out["mean_per_bucket"] = (
                round(sum(counts) / len(counts), 2) if counts else 0.0)

            # per-status split over the window
            status = {}
            for r in cx.execute(
                    f"SELECT status, COUNT(*) AS n FROM history "
                    f"{where} GROUP BY status ORDER BY n DESC",
                    params).fetchall():
                status[r["status"] or "?"] = r["n"]
            out["by_status"] = status
    except Exception as e:
        return {"tool": "queue_throughput", "ok": False,
                "error": f"could not read history: "
                         f"{type(e).__name__}: {str(e)[:160]}"}

    out["verdict"] = (
        f"{out['total_completed']} job(s) completed in the last "
        f"{hours}h; peak {out['peak_per_bucket']}/"
        f"{bucket}, mean {out['mean_per_bucket']}/{bucket}")
    return out



# ── 55. retry-schedule visualizer + worker-thread profiler +
#        account-pool viewer (T5: D-13 + D-14 + D-17) ───────────────
#
# D-13 — retry schedule. Pairs each site's CONFIGURED auto-retry
# schedule (the auto_retry_schedule string parsed to seconds) and the
# auto_retry_max_attempts cap with the LIVE picture: pending queue
# rows that carry a future retry_after, i.e. jobs actually scheduled
# to retry and when. Read-only.
#
# D-14 — worker-thread profiler. NOT the process-wide thread_dump
# (which dumps every Python thread's stack with no ownership). This
# attributes each runner's _worker_threads list to its site: count,
# alive/dead, daemon, plus _hung_workers, against the configured
# max_concurrent. A per-runner view, not a stack dump.
#
# D-17 — account-pool viewer. Per-site config['accounts']: how many,
# which index is active, and each account's cooldown status
# (cooldown_until vs now). Credentials are NEVER emitted — usernames
# are masked and passwords are not read at all.

def _parse_schedule_str(schedule_str):
    """Mirror of runner._parse_retry_schedule — parse '1h,4h,24h' to
    a list of seconds. Kept as a standalone helper so this read-only
    tool needs no runner instance just to render the config."""
    if not schedule_str:
        return [3600, 14400, 86400]
    out = []
    for tok in str(schedule_str).split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        try:
            if tok.endswith("d"):
                out.append(int(float(tok[:-1]) * 86400))
            elif tok.endswith("h"):
                out.append(int(float(tok[:-1]) * 3600))
            elif tok.endswith("m"):
                out.append(int(float(tok[:-1]) * 60))
            elif tok.endswith("s"):
                out.append(int(float(tok[:-1])))
            else:
                out.append(int(float(tok)))
        except Exception:
            continue
    return out or [3600, 14400, 86400]



def retry_schedule_inspect(runners=None):
    """D-13 — per-site auto-retry schedule + live scheduled retries
    (read-only). `runners` is app.runners, passed in to avoid a
    circular import.
    """
    import time as _time
    from bulk_downloader import db as _db
    now = _time.time()
    out = {"tool": "retry_schedule_inspect", "ok": True, "sites": []}

    # live pending rows with a future retry_after, grouped by site
    scheduled_by_site: dict = {}
    try:
        with _db.db_conn() as cx:
            if cx.execute("SELECT name FROM sqlite_master "
                          "WHERE type='table' AND name='queue'"
                          ).fetchone():
                for r in cx.execute(
                        "SELECT site_id, COUNT(*) AS n, "
                        "MIN(retry_after) AS soonest "
                        "FROM queue WHERE status = 'pending' "
                        "AND retry_after > ? GROUP BY site_id",
                        [now]).fetchall():
                    scheduled_by_site[r["site_id"]] = {
                        "scheduled_retries": r["n"],
                        "soonest_retry_in": _human_secs(
                            max(0, (r["soonest"] or now) - now)),
                    }
    except Exception as e:
        out["queue_read_error"] = str(e)[:160]

    for sid, rn in list((runners or {}).items()):
        try:
            cfg = getattr(rn, "config", {}) or {}
            sched = _parse_schedule_str(cfg.get("auto_retry_schedule", ""))
            entry = {
                "site_id": sid,
                "schedule_seconds": sched,
                "schedule_human": [_human_secs(s) for s in sched],
                "max_attempts": int(
                    cfg.get("auto_retry_max_attempts", 3) or 3),
                "auto_retry_running": bool(
                    getattr(getattr(rn, "_auto_retry_thread", None),
                            "is_alive", lambda: False)()),
            }
            entry.update(scheduled_by_site.get(
                sid, {"scheduled_retries": 0,
                      "soonest_retry_in": None}))
            out["sites"].append(entry)
        except Exception as e:
            out["sites"].append({"site_id": sid,
                                  "error": str(e)[:120]})

    total = sum(s.get("scheduled_retries", 0) for s in out["sites"])
    out["total_scheduled_retries"] = total
    out["verdict"] = (
        f"{len(out['sites'])} site(s); {total} job(s) scheduled "
        "to auto-retry")
    return out



def worker_thread_profile(runners=None):
    """D-14 — per-runner worker-thread attribution (read-only). Counts
    each runner's _worker_threads, their alive/daemon state, and
    _hung_workers, against the configured max_concurrent. Distinct
    from thread_dump, which is a process-wide stack dump with no
    site ownership.
    """
    out = {"tool": "worker_thread_profile", "ok": True, "sites": []}
    total_workers = total_alive = total_hung = 0
    for sid, rn in list((runners or {}).items()):
        try:
            cfg = getattr(rn, "config", {}) or {}
            wt = list(getattr(rn, "_worker_threads", []) or [])
            alive = sum(1 for t in wt
                        if getattr(t, "is_alive", lambda: False)())
            daemon = sum(1 for t in wt if getattr(t, "daemon", False))
            hung = list(getattr(rn, "_hung_workers", []) or [])
            max_conc = int(cfg.get("max_concurrent", 2) or 2)
            total_workers += len(wt)
            total_alive += alive
            total_hung += len(hung)
            out["sites"].append({
                "site_id": sid,
                "worker_threads": len(wt),
                "alive": alive,
                "dead": len(wt) - alive,
                "daemon": daemon,
                "max_concurrent": max_conc,
                "over_configured_max": len(wt) > max_conc,
                "hung_workers": len(hung),
                "worker_names": [getattr(t, "name", "?") for t in wt],
            })
        except Exception as e:
            out["sites"].append({"site_id": sid,
                                  "error": str(e)[:120]})
    out["total_worker_threads"] = total_workers
    out["total_alive"] = total_alive
    out["total_hung_workers"] = total_hung
    out["verdict"] = (
        f"{total_workers} worker thread(s) across "
        f"{len(out['sites'])} site(s); {total_alive} alive"
        + (f", {total_hung} hung" if total_hung else ""))
    return out



def _mask_username(u):
    """Mask a username for display: keep first 2 chars, star the rest.
    The account-pool viewer must never leak a usable credential."""
    u = str(u or "")
    if not u:
        return ""
    if len(u) <= 2:
        return u[0] + "*"
    return u[:2] + "*" * min(len(u) - 2, 8)



def account_pool_inspect(runners=None):
    """D-17 — per-site account pool (read-only). Reports how many
    accounts are configured, which index is active, and each
    account's cooldown status. Credentials are NEVER emitted:
    usernames are masked, passwords are not read.
    """
    import time as _time
    now = _time.time()
    out = {"tool": "account_pool_inspect", "ok": True, "sites": []}
    total_accounts = total_cooled = 0
    for sid, rn in list((runners or {}).items()):
        try:
            cfg = getattr(rn, "config", {}) or {}
            accounts = cfg.get("accounts") or []
            active_idx = int(getattr(rn, "_active_account_idx", 0) or 0)
            acct_view = []
            for i, a in enumerate(accounts):
                a = a if isinstance(a, dict) else {}
                cooldown_until = float(a.get("cooldown_until", 0) or 0)
                cooled = cooldown_until > now
                if cooled:
                    total_cooled += 1
                acct_view.append({
                    "index": i,
                    "username_masked": _mask_username(
                        a.get("username") or a.get("user")),
                    "active": i == active_idx,
                    "cooled_down": cooled,
                    "cooldown_remaining": (
                        _human_secs(cooldown_until - now)
                        if cooled else None),
                })
            total_accounts += len(accounts)
            usable = sum(1 for a in acct_view
                         if not a["cooled_down"])
            out["sites"].append({
                "site_id": sid,
                "account_count": len(accounts),
                "active_index": active_idx if accounts else None,
                "usable_now": usable,
                "accounts": acct_view,
            })
        except Exception as e:
            out["sites"].append({"site_id": sid,
                                  "error": str(e)[:120]})
    out["total_accounts"] = total_accounts
    out["total_cooled_down"] = total_cooled
    out["verdict"] = (
        f"{total_accounts} account(s) across {len(out['sites'])} "
        f"site(s); {total_cooled} in cooldown")
    return out
