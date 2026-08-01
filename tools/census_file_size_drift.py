#!/usr/bin/env python3
"""Census the Library doctor's size-drift population, split by SIGN.

WHY THIS EXISTS. `history.file_size` is written once by `db_log()` and never
UPDATEd anywhere (8 `UPDATE history` sites, none touches the column), so rows
written before v3.66.820 recorded a PRE-tag size. Once basename resolution
works (v3.66.825, cut25b) those rows surface as POSITIVE drift deltas. The
operator's question is how much of the panel's drift count is that residue
versus a real truncation, and the SPLIT is the answer -- not the total:

    delta < 0   the file on disk is SMALLER than recorded -- a genuine
                truncation, worth investigating whatever is decided about
                the residue.
    delta > 0   the atom residue, ~1-2 KB, and the ONLY population a one-shot
                re-stat should ever touch.

The "over 64KB" line is the honesty check: an atom write is kilobytes, so a
large positive delta is NOT residue and must not be swept up by a re-stat that
assumes it is.

WHY IT USES list_size_drift RATHER THAN SQL. The figures then match what the
Library panel actually shows. A SQL approximation would answer a neighbouring
question -- and `list_size_drift` deliberately excludes rows it cannot resolve
to exactly one file, which a hand-rolled query would silently include.

THE SHAPE TRAP THIS TOOL EXISTS TO NOT REPEAT. `sites_config.json` is a FLAT
mapping of site_id -> cfg. There is no "sites" wrapper: app.py's writer emits
``{sid: dict(cfg)}`` and its loader iterates ``data.items()`` directly. An
earlier hand-written version of this census read ``.get("sites", {})``, which
can only ever return empty -- and it was "tested" against a synthetic fixture
built in the same wrong shape, so the test confirmed the assumption instead of
the behaviour. It reported a clean library it had never looked at.
tests/test_census_file_size_drift.py pins the shape against app.py's own
reader so the fixture cannot drift back.

WHY COVERAGE IS REPORTED, NOT ASSUMED. Rows whose site_id is no longer in
sites_config cannot be resolved to a download_dir, so they are examined by
NOTHING. On the deploy box every one of the 31 done-rows was in that class
(all 23 orphan site_ids are `bdseed fixture site` -- live_seed.py residue,
which is documented there as structurally unremovable because history is
append-only). Without the coverage line the report reads "0 truncations, 0
residue" -- truthfully, and uselessly. UNKNOWN is a third state and it is
printed.

READ-ONLY. Nothing here writes. It refuses to run rather than connect to a
missing database, because connecting would CREATE an empty one and the census
would then report a spotless library it never saw.

Usage:
    venv/bin/python tools/census_file_size_drift.py
"""
from __future__ import annotations

import json
import os
import sys

# Rows examined per site. Far above any real library; reported when hit rather
# than silently truncating, because the returned rows are only the DRIFTING
# ones and len(rows) can never reveal that the cap bit.
ROW_LIMIT = 1000000

# Positive deltas larger than this are not atom-shaped and must not be treated
# as residue by any future re-stat.
ATOM_CEILING_BYTES = 65536


class CensusError(RuntimeError):
    """Raised when the census cannot see its subject and must not guess."""


def resolve_sites_path(env=None) -> str:
    """Where sites_config.json lives, resolved as bulk_downloader.app does.

    Mirrors app.py's ``_resolve_sites_file``: absolute under BD_INSTALL_DIR
    when that is set, else the historical relative path. BD_SITES_CONFIG_PATH
    wins outright when present (app.py:33 honours it the same way).
    """
    env = os.environ if env is None else env
    explicit = (env.get("BD_SITES_CONFIG_PATH") or "").strip()
    if explicit:
        return explicit
    install = (env.get("BD_INSTALL_DIR") or "").strip()
    if install:
        return os.path.join(install, "sites_config.json")
    return "sites_config.json"


def load_sites(path: str):
    """Parse sites_config.json into (sites, ignored_keys).

    The file is a FLAT {site_id: cfg} mapping -- see the module docstring.
    Non-dict top-level values are returned separately rather than dropped,
    so the caller can say what it ignored.
    """
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as exc:  # malformed JSON is a refusal, not an empty census
        raise CensusError("cannot parse %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise CensusError("sites config is a %s, expected a flat "
                          "{site_id: cfg} mapping" % type(data).__name__)
    sites = {k: v for k, v in data.items() if isinstance(v, dict)}
    ignored = sorted(k for k in data if not isinstance(data[k], dict))
    return sites, ignored


def _done_row_counts(db):
    """Total and per-site counts of done rows carrying a recorded size."""
    sql = ("SELECT COUNT(*) FROM history WHERE status='done' "
           "AND filename != '' AND file_size > 0")
    with db.db_conn() as cx:
        total = cx.execute(sql).fetchone()[0]
        per_site = {r[0]: r[1] for r in cx.execute(
            sql.replace("COUNT(*)", "site_id, COUNT(*)")
            + " GROUP BY site_id").fetchall()}
    return total, per_site


def census(sites, db, lf) -> dict:
    """Walk every configured site and split its drift rows by sign.

    Returns a dict with the two populations, the per-site lines, and the
    coverage accounting. One pass PER SITE, deliberately not deduped by
    download_dir: ``list_size_drift`` filters by site_id, so skipping a
    repeated directory would drop that site's rows entirely rather than
    merely avoid duplicate work.
    """
    total, per_site = _done_row_counts(db)
    neg, pos, unknown, capped, lines = [], [], [], [], []
    covered = 0
    for sid, cfg in sorted(sites.items()):
        dd = ((cfg or {}).get("download_dir") or "").strip()
        n = per_site.get(sid, 0)
        if not dd:
            unknown.append((sid, n, "no download_dir configured"))
            continue
        if not os.path.isdir(dd):
            unknown.append((sid, n, "download_dir absent: " + dd))
            continue
        if n >= ROW_LIMIT:
            capped.append((sid, n))
        rows = lf.list_size_drift(dd, site_id=sid, limit=ROW_LIMIT)
        covered += n
        for row in rows:
            (neg if row["delta_bytes"] < 0 else pos).append(row)
        lines.append((sid, n, len(rows), dd))
    unknown_rows = sum(n for _, n, _ in unknown)
    return {
        "total_done_rows": total,
        "truncations": neg,
        "residue": pos,
        "unknown": unknown,
        "unknown_rows": unknown_rows,
        "capped": capped,
        "lines": lines,
        "covered_rows": covered,
        # Rows whose site_id is not in sites_config at all -- examined by
        # nothing, and the reason a 0/0 result can be meaningless.
        "orphan_rows": total - covered - unknown_rows,
    }


def format_report(rep: dict) -> str:
    out = []
    for sid, n, drift, dd in rep["lines"]:
        out.append("  %-24s rows %-7d drift %-6d %s" % (sid, n, drift, dd))
    for sid, n, why in rep["unknown"]:
        out.append("  %-24s rows %-7d UNKNOWN -- %s" % (sid, n, why))
    out.append("")
    out.append("=" * 62)
    out.append("TRUNCATIONS  (delta<0) : %d" % len(rep["truncations"]))
    out.append("ATOM RESIDUE (delta>0) : %d" % len(rep["residue"]))
    out.append("")
    out.append("COVERAGE  rows examined : %d of %d"
               % (rep["covered_rows"], rep["total_done_rows"]))
    if rep["unknown_rows"]:
        out.append("          rows UNKNOWN  : %d  (%d site(s) unresolvable)"
                   % (rep["unknown_rows"], len(rep["unknown"])))
    if rep["orphan_rows"]:
        out.append("          rows whose site_id is not in sites_config : %d"
                   % rep["orphan_rows"])
    if rep["capped"]:
        out.append("          LIMIT HIT -- census truncated for: %s"
                   % ", ".join("%s(%d)" % (s, n) for s, n in rep["capped"]))
    if not (rep["unknown_rows"] or rep["orphan_rows"] or rep["capped"]):
        out.append("          complete -- every done row was examined")
    if rep["residue"]:
        sizes = sorted(abs(r["delta_bytes"]) for r in rep["residue"])
        out.append("")
        out.append("  residue bytes min/median/max : %d / %d / %d"
                   % (sizes[0], sizes[len(sizes) // 2], sizes[-1]))
        out.append("  residue over 64KB (NOT atom-shaped) : %d"
                   % sum(1 for s in sizes if s > ATOM_CEILING_BYTES))
    if rep["truncations"]:
        out.append("")
        out.append("  worst truncations (delta, recorded, disk, filename):")
        for r in sorted(rep["truncations"], key=lambda x: x["delta_bytes"])[:10]:
            out.append("    %-12d %-12d %-12d %s"
                       % (r["delta_bytes"], r["recorded_bytes"],
                          r["disk_bytes"], (r["filename"] or "")[:60]))
    out.append("=" * 62)
    return "\n".join(out)


def main(argv=None) -> int:
    sys.path.insert(0, os.getcwd())
    cfg_path = resolve_sites_path()
    if not os.path.isfile(cfg_path):
        print("FATAL: sites config not found at %r (cwd=%s)"
              % (cfg_path, os.getcwd()))
        return 2

    import bulk_downloader.db as db

    db_path = db._resolve_db_path()
    if not os.path.isfile(db_path):
        print("FATAL: history db not found at %r -- refusing to connect, "
              "because connecting would create an empty one and this census "
              "would then report a clean library it never looked at" % db_path)
        return 2

    from bulk_downloader import library_final as lf

    print("cwd        : " + os.getcwd())
    print("sites cfg  : " + os.path.abspath(cfg_path))
    print("history db : " + os.path.abspath(db_path))
    print("")
    try:
        sites, ignored = load_sites(cfg_path)
    except CensusError as exc:
        print("FATAL: %s" % exc)
        return 2
    if ignored:
        print("  note: %d non-dict top-level key(s) ignored: %s"
              % (len(ignored), ignored[:5]))
    if not sites:
        print("FATAL: sites config parsed but contains no site entries -- "
              "empty denominator")
        return 2

    rep = census(sites, db, lf)
    print("done rows with a recorded size : %d" % rep["total_done_rows"])
    print("sites in config                : %d" % len(sites))
    print("")
    if rep["total_done_rows"] == 0:
        print("FATAL: no done rows carry a recorded size -- nothing to census")
        return 2
    print(format_report(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
