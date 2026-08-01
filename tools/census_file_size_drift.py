#!/usr/bin/env python3
"""Census the Library doctor's size-drift population, split by SIGN.

WHY THIS EXISTS. `history.file_size` is written once by `db_log()` and never
UPDATEd anywhere (7 `UPDATE history` sites, none touches the column), so rows
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

WHY IT USES list_size_drift RATHER THAN SQL. A SQL approximation would answer
a neighbouring question -- `list_size_drift` deliberately excludes rows it
cannot resolve to exactly one file, which a hand-rolled query would silently
include.

TWO PASSES, BECAUSE THEY ANSWER DIFFERENT QUESTIONS. This is the defect
v3.66.827 corrects: the first version ran only the per-site pass and its
docstring claimed "the figures then match what the Library panel actually
shows". Measured FALSE.

  PER SITE      list_size_drift(dir, site_id=sid) -- attributes drift to a
                configured site, which is what a backfill decision needs.
  WHOLE HISTORY list_size_drift(dir, site_id=None) -- what the PANEL does.
                frontend/src/routes/Library.tsx:497 calls the audit with
                {download_dir} and NO site_id, and library_final.audit()
                takes site_id as Optional[str] = None, so it spans EVERY
                history row under that directory.

A probe built a database where the per-site pass printed 0 drift while the
panel's own call returned 3 -- including a real -9899 truncation. Rows whose
site_id is not in sites_config are examined by the per-site pass at NO point;
on the deploy box that was 31 of 31 rows. Both passes are therefore reported,
ALONGSIDE each other. Reading only one of them is how the first version
reported a clean library it had not asked about.

THE DEFAULT DOWNLOAD DIR IS SWEPT TOO. A site whose `download_dir` is blank
still writes somewhere: `app.py:_oi_default_download_dir()` resolves
BD_DOWNLOAD_DIR -> the global config's download_dir -> ~/Downloads, and
`runner.py`'s no-dl-dir branch calls it at write time. Bucketing such a site
UNKNOWN examines nothing while its files sit on disk under the default. The
resolver is IMPORTED, never reimplemented -- a second copy of that order is a
denominator that drifts.

ORPHAN SITES ARE NAMED, NOT COUNTED. The v3.66.826 box closure rested on an
ad-hoc query nobody can re-run, because the report emitted only a count. A
count cannot tell an operator that all 23 orphans were `bdseed fixture site`.

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
    """Total, per-site counts, and (site_id, site_name, n) of done rows
    carrying a recorded size.

    The NAMES are load-bearing: an orphan site_id alone is an opaque hex
    string, and the whole point of listing orphans is that the operator can
    recognise what they are (`bdseed fixture site`) without a second query.
    """
    sql = ("SELECT COUNT(*) FROM history WHERE status='done' "
           "AND filename != '' AND file_size > 0")
    with db.db_conn() as cx:
        total = cx.execute(sql).fetchone()[0]
        named = [(r[0] or "", r[1] or "", r[2]) for r in cx.execute(
            sql.replace("COUNT(*)", "site_id, site_name, COUNT(*)")
            + " GROUP BY site_id, site_name").fetchall()]
    per_site = {}
    for sid, _name, n in named:
        per_site[sid] = per_site.get(sid, 0) + n
    return total, per_site, named


def resolve_default_download_dir() -> str:
    """Where a site with a blank download_dir actually writes.

    Delegates to ``app._oi_default_download_dir`` -- the same resolver
    ``runner.py``'s no-dl-dir branch calls at write time -- rather than
    reimplementing its BD_DOWNLOAD_DIR -> global config -> ~/Downloads order.
    A second copy of that order is a denominator that drifts, and the copy
    nobody updated is the one that decides what the census can see.
    """
    from bulk_downloader.app import _oi_default_download_dir
    return str(_oi_default_download_dir() or "").strip()


def census(sites, db, lf, default_dir=None) -> dict:
    """Split drift rows by sign, per configured site AND across all history.

    Returns a dict with both populations, the per-site lines, the whole-history
    sweep, and the coverage accounting.

    The per-site pass is one pass PER SITE, deliberately not deduped by
    download_dir: ``list_size_drift`` filters by site_id, so skipping a
    repeated directory would drop that site's rows entirely rather than
    merely avoid duplicate work.

    The sweep is one pass per resolvable DIRECTORY with ``site_id=None`` --
    the call the Library panel makes -- so rows whose site_id is not in
    sites_config are examined rather than silently excluded. ``default_dir``
    is the deployment default (see ``resolve_default_download_dir``); it is
    both used for sites that name no directory of their own and swept in its
    own right, because files can be sitting under it with no configured site
    pointing there at all.
    """
    total, per_site, named = _done_row_counts(db)
    default_dir = (default_dir or "").strip()
    if not default_dir:
        default_state = "not resolved -- UNKNOWN, not empty"
    elif os.path.isdir(default_dir):
        default_state = "resolved"
    else:
        default_state = "absent on disk: " + default_dir
    default_ok = default_state == "resolved"

    neg, pos, unknown, capped, lines = [], [], [], [], []
    covered = 0
    sweep_sources: dict = {}
    for sid, cfg in sorted(sites.items()):
        dd = ((cfg or {}).get("download_dir") or "").strip()
        n = per_site.get(sid, 0)
        label = sid
        if not dd:
            if not default_ok:
                unknown.append((sid, n, "no download_dir configured, and the "
                                        "deployment default is " + default_state))
                continue
            # The runner resolves the default at WRITE time, so this site's
            # files really are under it -- examining nothing here would be
            # the census reporting clean over an excluded denominator.
            dd = default_dir
            label = sid + " (via deployment default)"
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
        sweep_sources.setdefault(dd, []).append(label)
    if default_ok and "<deployment default>" not in \
            sweep_sources.setdefault(default_dir, []):
        sweep_sources[default_dir].append("<deployment default>")

    # The panel's own call: no site_id, so it spans every history row that
    # resolves under the directory.
    sweep_lines, sweep_neg, sweep_pos = [], [], []
    for dd in sorted(sweep_sources):
        dn = dp = 0
        for row in lf.list_size_drift(dd, site_id=None, limit=ROW_LIMIT):
            r = dict(row)
            r["_dir"] = dd
            if r["delta_bytes"] < 0:
                sweep_neg.append(r)
                dn += 1
            else:
                sweep_pos.append(r)
                dp += 1
        sweep_lines.append((dd, sorted(sweep_sources[dd]), dn, dp))

    unknown_rows = sum(n for _, n, _ in unknown)
    configured = set(sites)
    # Rows whose site_id is not in sites_config at all -- examined by the
    # per-site pass at no point, and the reason a 0/0 result can be
    # meaningless.
    #
    # HONEST NOTE ABOUT ``orphan_rows`` BELOW. It is summed from these grouped
    # rows, but that is NOT a behavioural difference from writing
    # ``total - covered - unknown_rows``: the loop above puts every configured
    # site in exactly one of ``covered`` or ``unknown``, so
    # covered + unknown_rows == |rows whose site_id IS configured| and the two
    # forms are arithmetically identical on every input. No test can tell them
    # apart, and the reconciliation assertion in the tests is therefore an
    # identity about the LOOP's exhaustiveness, not evidence about this line.
    # An earlier version of this comment claimed the derivation mattered for
    # the count; it does not, and nothing tested it.
    #
    # What the grouped derivation DOES buy -- and subtraction cannot -- is
    # ``orphan_sites``: the ids and names, without which the v3.66.826 box
    # closure had to rest on an ad-hoc query nobody can re-run. That part is
    # pinned behaviourally against an independent recount.
    orphan_sites = sorted(((sid, name, n) for sid, name, n in named
                           if sid not in configured),
                          key=lambda t: (-t[2], t[0]))
    return {
        "total_done_rows": total,
        "truncations": neg,
        "residue": pos,
        "unknown": unknown,
        "unknown_rows": unknown_rows,
        "capped": capped,
        "lines": lines,
        "covered_rows": covered,
        "orphan_sites": orphan_sites,
        "orphan_rows": sum(n for _, _, n in orphan_sites),
        "default_dir": default_dir,
        "default_dir_state": default_state,
        "sweep_lines": sweep_lines,
        "sweep_truncations": sweep_neg,
        "sweep_residue": sweep_pos,
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
        # Named, not merely counted: a count cannot be recognised, and the
        # v3.66.826 box closure had to rest on an ad-hoc query because of it.
        for sid, name, n in rep["orphan_sites"]:
            out.append("            %-26s %-30s rows %d"
                       % (sid[:26], (name or "(no site_name)")[:30], n))
    if rep["capped"]:
        out.append("          LIMIT HIT -- census truncated for: %s"
                   % ", ".join("%s(%d)" % (s, n) for s, n in rep["capped"]))
    if not (rep["unknown_rows"] or rep["orphan_rows"] or rep["capped"]):
        out.append("          complete -- every done row was examined")
    out.append("")
    out.append("WHOLE-HISTORY SWEEP -- list_size_drift(dir, site_id=None), the")
    out.append("call Library.tsx:497 -> audit() actually makes. It spans EVERY")
    out.append("history row resolving under the dir, orphan site_ids included,")
    out.append("so THIS is the figure comparable to what the panel shows.")
    if rep["sweep_lines"]:
        for dd, srcs, dn, dp in rep["sweep_lines"]:
            out.append("  %-38s trunc %-6d residue %-6d [%s]"
                       % (dd[:38], dn, dp, ", ".join(srcs) or "-"))
    else:
        out.append("  no resolvable download dir -- NOTHING was swept, which "
                   "is UNKNOWN, not clean")
    out.append("  SWEEP TRUNCATIONS (delta<0) : %d" % len(rep["sweep_truncations"]))
    out.append("  SWEEP RESIDUE     (delta>0) : %d" % len(rep["sweep_residue"]))
    out.append("  deployment default download dir : %s  (%s)"
               % (rep["default_dir"] or "-", rep["default_dir_state"]))
    # The distribution is emitted for EACH population that has residue, not
    # just the per-site one. Gating both lines on rep["residue"] made them
    # unreachable on the deploy box, where the per-site pass examined 0 of 31
    # rows and the sweep found 27 -- the honesty check blind in exactly the
    # case that has something to be honest about.
    for _label, _rows in (("", rep["residue"]), ("SWEEP ", rep["sweep_residue"])):
        if not _rows:
            continue
        sizes = sorted(abs(r["delta_bytes"]) for r in _rows)
        out.append("")
        out.append("  %sresidue bytes min/median/max : %d / %d / %d"
                   % (_label, sizes[0], sizes[len(sizes) // 2], sizes[-1]))
        out.append("  %sresidue over 64KB (NOT atom-shaped) : %d"
                   % (_label, sum(1 for s in sizes if s > ATOM_CEILING_BYTES)))
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

    default_dir = ""
    try:
        default_dir = resolve_default_download_dir()
    except Exception as exc:
        # Say so rather than proceeding as if the default were empty: an
        # unresolvable default is UNKNOWN, and the sweep below is then
        # narrower than the operator would assume.
        print("  note: could not resolve the deployment default download dir "
              "-- UNKNOWN, not absent: %s: %s" % (type(exc).__name__, exc))

    rep = census(sites, db, lf, default_dir=default_dir)
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
