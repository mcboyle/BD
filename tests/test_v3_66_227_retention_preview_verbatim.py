"""F4.2 — watched→retention preview-verbatim binding (v3.66.227).

Pins the destructive-path safety contract on ``retention.apply_retention``:

  * with ``confirm_ids`` set, apply deletes ONLY the intersection of the
    confirmed ids and the freshly computed candidates,
  * a stale / forged id in ``confirm_ids`` can never cause a deletion
    (apply can never exceed what the preview disclosed),
  * a file excluded AFTER the preview is silently skipped (apply may delete
    fewer, never an unintended file),
  * ``dry_run`` with ``confirm_ids`` deletes nothing,
  * ``site_id`` scopes the run to one site,
  * omitting ``confirm_ids`` preserves the legacy unbound sweep.

Per the 224 test-hygiene note, the module shares one DB under
``bd_module_wipe``; each test uses DISJOINT site ids + its own temp files
so there is no cross-test coupling. The destructive assertions check real
files on disk (apply unlinks them), which is the actual guarantee.
"""
from __future__ import annotations

import os
import tempfile

import pytest

pytestmark = pytest.mark.bd_module_wipe

_CFG = {"retention_days": 30}  # everything seeded "old" → age candidate
_OLD_TS = "2020-01-01T00:00:00"


def _mkfile(size: int = 8) -> str:
    fd, path = tempfile.mkstemp(prefix="ret_", suffix=".mp4")
    with os.fdopen(fd, "wb") as f:
        f.write(b"x" * size)
    return path


def _seed(site_id: str, n: int):
    """Insert n 'done' history rows (old ts) for site_id, each backed by a
    real temp file. Returns [(history_id, filepath), ...] in insert order."""
    from bulk_downloader import db as _db, retention as _rt
    _db.db_init()        # create base schema (history) — wipe leaves it empty
    _rt._ensure_tables()  # ensure retention_excluded column exists
    out = []
    with _db.db_conn() as cx:
        for i in range(n):
            path = _mkfile()
            cur = cx.execute(
                """INSERT INTO history(site_id, site_name, url, status,
                                       filename, file_size, ts)
                   VALUES (?,?,?,?,?,?,?)""",
                (site_id, site_id, f"https://x/{site_id}/{i}", "done",
                 path, 8, _OLD_TS),
            )
            out.append((cur.lastrowid, path))
    return out


def _ids(seeded):
    return [hid for hid, _ in seeded]


def test_preview_then_apply_deletes_exactly_confirmed():
    from bulk_downloader import retention as r
    seeded = _seed("f42-exact", 2)
    (i1, f1), (i2, f2) = seeded
    # preview discloses both
    cands = r.find_candidates("f42-exact", _CFG)
    assert set(_ids(seeded)) <= {c["id"] for c in cands}
    # confirm only the first
    res = r.apply_retention({"f42-exact": _CFG}, dry_run=False,
                            confirm_ids=[i1], site_id="f42-exact")
    assert res["preview_bound"] is True
    assert res["scoped_site"] == "f42-exact"
    assert res["total_deleted"] == 1
    assert not os.path.exists(f1)   # confirmed → deleted
    assert os.path.exists(f2)       # not confirmed → untouched


def test_apply_cannot_exceed_preview_with_forged_id():
    from bulk_downloader import retention as r
    seeded = _seed("f42-forge", 2)
    (i1, f1), (i2, f2) = seeded
    # confirm one real id plus a bogus id that is NOT a candidate
    res = r.apply_retention({"f42-forge": _CFG}, dry_run=False,
                            confirm_ids=[i1, 999999], site_id="f42-forge")
    assert res["total_deleted"] == 1   # the forged id deletes nothing
    assert not os.path.exists(f1)
    assert os.path.exists(f2)


def test_excluded_since_preview_is_skipped():
    from bulk_downloader import retention as r
    seeded = _seed("f42-excl", 2)
    (i1, f1), (i2, f2) = seeded
    # operator/auto excludes the second AFTER the preview
    assert r.mark_excluded(i2, True) is True
    res = r.apply_retention({"f42-excl": _CFG}, dry_run=False,
                            confirm_ids=[i1, i2], site_id="f42-excl")
    # i2 is no longer a candidate → skipped even though it was confirmed
    assert res["total_deleted"] == 1
    assert not os.path.exists(f1)
    assert os.path.exists(f2)


def test_dry_run_with_confirm_ids_deletes_nothing():
    from bulk_downloader import retention as r
    seeded = _seed("f42-dry", 2)
    (i1, f1), (i2, f2) = seeded
    res = r.apply_retention({"f42-dry": _CFG}, dry_run=True,
                            confirm_ids=[i1], site_id="f42-dry")
    assert res["dry_run"] is True
    assert res["preview_bound"] is True
    assert res["total_deleted"] == 0
    # intersection of candidates with confirm_ids is counted, not all
    assert res["total_candidates"] == 1
    assert os.path.exists(f1) and os.path.exists(f2)


def test_site_scoping_isolates_other_sites():
    from bulk_downloader import retention as r
    a = _seed("f42-siteA", 1)
    b = _seed("f42-siteB", 1)
    (ia, fa), = a
    (ib, fb), = b
    cfg = {"f42-siteA": _CFG, "f42-siteB": _CFG}
    res = r.apply_retention(cfg, dry_run=False,
                            confirm_ids=[ia], site_id="f42-siteA")
    assert list(res["sites"].keys()) == ["f42-siteA"]
    assert not os.path.exists(fa)   # scoped site, confirmed → deleted
    assert os.path.exists(fb)       # other site never walked


def test_unbound_legacy_sweep_preview_unbound_flag():
    from bulk_downloader import retention as r
    _seed("f42-legacy", 1)
    res = r.apply_retention({"f42-legacy": _CFG}, dry_run=True)
    assert res["preview_bound"] is False
    assert res["scoped_site"] is None
    assert res["total_candidates"] >= 1
    assert res["total_deleted"] == 0
