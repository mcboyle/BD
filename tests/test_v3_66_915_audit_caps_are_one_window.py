"""audit() reported two sibling counts taken over two DIFFERENT windows.

Item 12(c). `library_final.audit()` returns `missing` and `size_drift` side by
side in one dict, as if they described the same population. They do not, and
nothing said so:

    list_missing_from_disk(limit=500)   ORDER BY id DESC LIMIT 500
    list_size_drift(limit=1000)         ORDER BY id DESC LIMIT 1000

Both are SQL caps over the SAME `history` table with the SAME `status='done'`
predicate, and `audit()` passed NEITHER -- so both ceilings were unreachable
from any caller and the asymmetry was invisible at the call site.

THE CONSEQUENCE IS NOT "A COUNT IS A BIT LOW". A history row can sit INSIDE
drift's window and OUTSIDE missing's, so one `audit()` call can examine a file
for size drift and, in the same returned dict, report that nothing is missing --
about that very file. That is the operator-facing harm, and it is what the
first test below pins. Measured on the fixture: pristine source returns
`missing == 0` with 500 genuinely absent files sitting in the population it
just read.

CLAUDE.md section 0: a check whose denominator structurally excludes its
subject reports clean, truthfully and uselessly. Here the two denominators
differ by 500 rows and the dict presents the results as comparable.

SCOPE -- deliberately internal. Operator decision 2026-08-06: `audit()`'s
returned KEY SET does not move. Disclosing "this count is a floor" would need
a new key, and tests/test_library_audit_panel_contract.py pins the exact key
set alongside frontend/src/types/api-types.ts, so that is a contract change and
is filed with 12(d) rather than smuggled in here. The fix is one explicit,
shared cap, documented. `test_the_returned_key_set_does_not_move` holds that
line: it fails if a later edit adds disclosure here instead of there.

WHY A SHARED KNOB RATHER THAN TWO EQUAL DEFAULTS. Raising
`list_missing_from_disk`'s module default from 500 to 1000 would also make the
fixture below green, and would leave the two free to drift apart again the next
time either default is touched -- the numbers would agree by coincidence, not
by construction. `test_one_limit_drives_both_counts` fails that repair: it
drives `audit(limit=...)` to a value matching neither old default and requires
BOTH counts to move with it.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from bulk_downloader import db
from bulk_downloader import library_final as lf

# Insert order is oldest -> newest, and `id` is AUTOINCREMENT, so the tail of
# this list is what `ORDER BY id DESC LIMIT n` reaches first.
#
#   FILLER  oldest 200   file present, size matches   -> resolved, no drift
#   ABSENT  middle 500   file NOT on disk             -> absent
#   DRIFT   newest  500  file present, size differs   -> resolved, drift
#
# So the newest 500 rows are exactly the DRIFT block and the newest 1000 are
# DRIFT + ABSENT. Pristine `missing` therefore sees a window containing no
# absent row at all, while `size_drift` reads all 500 of them and skips them.
_FILLER, _ABSENT, _DRIFT = 200, 500, 500
_RECORDED = 5
_DISK_DRIFTED = 50


def _seed(download_dir: str) -> None:
    """Write the three blocks into history, oldest first, and lay out the disk."""
    rows = []
    for i in range(_FILLER):
        fn = "filler_%04d.mp4" % i
        (Path(download_dir) / fn).write_bytes(b"\0" * _RECORDED)
        rows.append(fn)
    for i in range(_ABSENT):
        rows.append("absent_%04d.mp4" % i)          # deliberately not created
    for i in range(_DRIFT):
        fn = "drift_%04d.mp4" % i
        (Path(download_dir) / fn).write_bytes(b"\0" * _DISK_DRIFTED)
        rows.append(fn)
    with db.db_conn() as cx:
        cx.executemany(
            "INSERT INTO history(site_id, site_name, url, status, filename, "
            "file_size) VALUES(?,?,?,?,?,?)",
            [("s", "S", "http://x/%s" % fn, "done", fn, _RECORDED)
             for fn in rows])


def _audited(seed, **kw):
    """Run a real audit() against an isolated DB and a real directory.

    db.DB_PATH is set to an ABSOLUTE temp path -- rung 1 of
    db._resolve_db_path, so nothing here can touch a real
    downloader_history.db. CLAUDE.md section 5: a probe that leaves DB_PATH
    relative writes into the repo, gitignored, and the rows then accumulate
    across runs.
    """
    d = tempfile.mkdtemp(prefix="audit_window_")
    saved = db.DB_PATH
    db.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="audit_window_db_"), "queue.db")
    try:
        db.db_init()
        seed(d)
        return lf.audit(download_dir=d, **kw)
    finally:
        db.DB_PATH = saved


def _small(download_dir: str) -> None:
    """Ten rows, three of them absent -- a library far under either cap."""
    rows = []
    for i in range(7):
        fn = "have_%02d.mp4" % i
        (Path(download_dir) / fn).write_bytes(b"\0" * _RECORDED)
        rows.append(fn)
    rows += ["gone_%02d.mp4" % i for i in range(3)]
    with db.db_conn() as cx:
        cx.executemany(
            "INSERT INTO history(site_id, site_name, url, status, filename, "
            "file_size) VALUES(?,?,?,?,?,?)",
            [("s", "S", "http://x/%s" % fn, "done", fn, _RECORDED)
             for fn in rows])


def test_a_row_drift_examined_is_not_invisible_to_missing():
    """RED on pristine source: missing == 0 while 500 files are gone.

    This is the whole item in one assertion. The 500 absent rows are inside
    size_drift's 1000-row window and outside missing's 500-row window, so the
    same call reads them, decides they cannot be size-compared BECAUSE THEY ARE
    ABSENT, and then reports that nothing is absent.
    """
    rep = _audited(_seed)
    assert rep["size_drift"] == _DRIFT, (
        "fixture drifted: expected %d drift rows, got %r"
        % (_DRIFT, rep["size_drift"]))
    assert rep["missing"] == _ABSENT, (
        "audit() reported missing=%r while %d recorded files are absent from "
        "disk. Both counts come from `history` WHERE status='done'; they must "
        "be taken over ONE window, not 500 rows and 1000 rows."
        % (rep["missing"], _ABSENT))


# IT TAKES TWO PROBES, AND ONE WAS NOT ENOUGH -- MEASURED.
#
# The first version of this asserted BOTH counts at limit=600 in one test. It
# caught an unwired `missing` and let an unwired `size_drift` ESCAPE a mutation
# battery: at 600 the fixture's 500 drift rows fit inside the wired window AND
# inside the unwired 1000-row default, so `size_drift == 500` held either way.
# The assertion could not distinguish its own subject -- CLAUDE.md section 0,
# in a test written to close an instance of section 0.
#
# No single limit fixes it. Discriminating `missing` needs L > 500 (only then
# does the window reach an absent row); discriminating `size_drift` needs
# L < 500 (only then is it narrower than the unwired default). The two
# requirements do not intersect, so the probes are split.

def test_the_limit_is_wired_to_the_missing_count():
    """L=600: above missing's old default, so the window reaches absent rows.

    The newest 600 rows are the 500 DRIFT rows plus the newest 100 ABSENT ones.
    Unwired, `missing` falls back to 500 and sees no absent row at all -- 0,
    not 100. This also fails a repair that merely raised
    list_missing_from_disk's module default, since 600 matches neither 500
    nor 1000.
    """
    rep = _audited(_seed, limit=600)
    assert rep["missing"] == 100, (
        "audit(limit=600) should reach 100 absent rows; got %r -- the limit is "
        "not wired to list_missing_from_disk" % (rep["missing"],))


def test_the_limit_is_wired_to_the_drift_count():
    """L=300: BELOW drift's old default, which is the only way to see it.

    The newest 300 rows are all DRIFT rows, so a wired limit gives exactly 300.
    Unwired, size_drift keeps its 1000-row default and reports all 500. Any
    L >= 500 returns 500 in both states and proves nothing -- which is exactly
    how the single-probe version let this escape.
    """
    rep = _audited(_seed, limit=300)
    assert rep["size_drift"] == 300, (
        "audit(limit=300) should see exactly 300 drift rows; got %r -- the "
        "limit is not wired to list_size_drift" % (rep["size_drift"],))


def test_a_small_library_is_unchanged():
    """OVER-CORRECTION GUARD, and it passes before AND after.

    Every real library under either cap must audit identically. A "fix" that
    changed what a normal-sized library reports would be a regression wearing
    the item's clothes. This is the direction the FIX could break, not the
    defect being fixed.
    """
    rep = _audited(_small)
    assert rep["missing"] == 3, rep["missing"]
    assert rep["size_drift"] == 0, rep["size_drift"]
    assert rep["orphans"] == 0, rep["orphans"]


def test_the_returned_key_set_does_not_move():
    """The contract set, made mechanical.

    Written when 12(c) was scoped internal, to fail if a later edit added a
    disclosure key without moving test_library_audit_panel_contract.py and
    api-types.ts in the same cut. It did exactly that at v3.66.957 -- the
    saturation keys landed and this fired -- so the three below were added
    only after the panel and the TS interface had moved with them.

    The pin stays: it is still the thing that stops a key appearing in one of
    the four statements of this contract and nowhere else.
    """
    big = _audited(_seed)
    small = _audited(_small)
    assert set(big) == set(small), (
        "audit()'s key set varies with the data: %r"
        % (set(big) ^ set(small),))
    assert set(big) == {
        "orphans", "missing", "duplicate_groups", "duplicate_reclaimable_gb",
        "size_drift", "orphan_size_gb", "sample_orphans", "sample_missing",
        "sample_duplicates", "sample_size_drift",
        "missing_saturated", "size_drift_saturated", "audit_row_limit",
    }, (
        "audit()'s key set moved: %r. That is a CONTRACT change -- "
        "tests/test_library_audit_panel_contract.py, api-types.ts "
        "LibraryAuditResult and frontend/src/routes/Library.tsx pin this set "
        "too -- move all of them in the SAME cut." % (sorted(big),))
