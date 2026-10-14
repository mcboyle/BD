"""O1789 bug-hunt deep-history-library -- dhl-fix r1 (F1-F14).

Source: harness-work/O1789/bughunt/deep-history-library/FINDINGS.md, scenarios
S8 S10-S13 S15 S17 S19-S21 S24-S28 of SCENARIOS.md. Each test carries the
finding's negative control (the "ctl" FINDINGS names) beside the defect.

F1  hard delete unlinked file + sidecar BEFORE the DB DELETE: a failing DB write
    left a file_exists=1 row naming a deleted file. The row now commits first.
F2  an unreadable subdir made the missing pass flip its rows (errors=0).
F3  a download recorded after the walk passed its dir was flipped missing.
F4  a hard delete during a scan came back as a file_exists=0 ghost row.
F5  cancel was honoured per directory only.
F6  a symlinked alias of a scanned dir added a duplicate row per file.
F7  same basename in two dirs: both rows claimed one history id.
F8  library_restore's INSERT OR REPLACE deleted a row re-added since.
F9  a crashed scan worker read as a clean finished scan.
F10 forged cursor value NaN / -Infinity -> silent empty page.
F11 search not Unicode-normalized (NFC q missed the NFD name).
F12 tag remove did not trim like tag add.
F13 two concurrent hard deletes both reported deleted.
F14 DELETE /api/library/<2^63> -> 400 OverflowError (GET 404).
F7r (r2, R131-D) the F7 link preload let a gone row block the live file.
F15 (history prune) is db.py, owned by ddr-fix r2 -- OWED, not here.
"""
from __future__ import annotations

import base64
import contextlib
import errno
import importlib
import os
import sqlite3
import tempfile
import threading
import unicodedata
from pathlib import Path

import pytest

import bulk_downloader.library as lib

BD_GATE_SCOPE = "module"


def _db():
    return importlib.import_module("bulk_downloader.db")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    db = _db()
    monkeypatch.setattr(db, "DB_PATH", os.path.join(
        tempfile.mkdtemp(prefix="dhl_fix_db_"), "queue.db"))
    monkeypatch.setattr(lib, "_SCHEMA_READY", False)
    lib._ensure_schema()
    assert lib._SCHEMA_READY, "fixture: schema did not initialise"


def _mk(path, n=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_bytes(b"x" * (100 + n))
    return str(path)


def _rows(sql, args=()):
    with _db().db_conn() as cx:
        return [dict(r) for r in cx.execute(sql, args).fetchall()]


def _ex(sql, args=()):
    with _db().db_conn() as cx:
        cx.execute(sql, args)


def _state(fp):
    return _rows("SELECT id, file_exists, history_id FROM library "
                 "WHERE file_path=?", (fp,))


def _scan(*roots):
    state = lib.ScanState()
    lib._scan_worker([str(r) for r in roots], state)
    assert state.finished_at is not None
    return state


def _tree(dirs=("a", "b", "c"), n=5):
    root = Path(tempfile.mkdtemp(prefix="dhl_fix_media_"))
    for d in dirs:
        for i in range(n):
            _mk(root / d / f"{d}{i}.mp4", i)
    return root


def _client():
    from bulk_downloader import app as a
    return a.app.test_client()


# -- F1 (S24, S25) ------------------------------------------------------------

def _thumbed_item():
    d = Path(tempfile.mkdtemp(prefix="dhl_fix_del_"))
    media = Path(_mk(d / "v.mp4"))
    jpg = d / "v.jpg"
    jpg.write_bytes(b"thumb")
    lid = lib.library_record(str(media), cover_thumb=str(jpg))
    assert lid
    return lid, media, jpg


def test_f1_failed_db_delete_keeps_file_and_sidecar():
    lid, media, jpg = _thumbed_item()
    _ex("CREATE TRIGGER dhl_no_delete BEFORE DELETE ON library "
        "BEGIN SELECT RAISE(ABORT, 'dhl trigger'); END")
    try:
        out = lib.library_delete(lid, also_delete_file=True)
    finally:
        _ex("DROP TRIGGER dhl_no_delete")
    assert not out["ok"] and "dhl trigger" in out.get("error", ""), out
    assert media.exists() and jpg.exists(), (
        "O1789-DHL-F1-UNLINK-BEFORE-DB-DELETE: the DB delete failed (%r) but "
        "file=%s sidecar=%s are gone while the row stays %r"
        % (out, media.exists(), jpg.exists(), lib.library_get(lid)))
    assert lib.library_get(lid)["file_exists"] == 1
    # ctl: no trigger -> 200-shape ok, file + sidecar + row gone.
    out = lib.library_delete(lid, also_delete_file=True)
    assert out["ok"] and out["file_removed"] and out["thumbs_removed"] == 1
    assert not media.exists() and not jpg.exists()
    assert lib.library_get(lid) is None


def test_f1_locked_database_keeps_the_file():
    lid, media, jpg = _thumbed_item()
    other = sqlite3.connect(_db()._resolve_db_path(), timeout=0,
                            isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    try:
        out = lib.library_delete(lid, also_delete_file=True)
    finally:
        other.execute("ROLLBACK")
        other.close()
    assert not out["ok"] and "locked" in out.get("error", ""), out
    assert media.exists() and jpg.exists(), (
        "O1789-DHL-F1-LOCKED-DB-FILE-LOST: %r" % (out,))
    assert lib.library_get(lid)["file_exists"] == 1


# -- F13 (S10) -----------------------------------------------------------------

@pytest.mark.parametrize("attempt", range(10))
def test_f13_concurrent_hard_deletes_exactly_one_succeeds(attempt):
    # A race: on BASE one attempt reproduced 2 of 3 runs, so ten attempts.
    lid, media, _ = _thumbed_item()
    barrier = threading.Barrier(2)
    results = []

    def go():
        barrier.wait()
        results.append(lib.library_delete(lid, also_delete_file=True))

    ts = [threading.Thread(target=go) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    deleted = [r for r in results if r.get("deleted_row")]
    assert len(results) == 2
    assert len(deleted) == 1, (
        "O1789-DHL-F13-DOUBLE-DELETE-REPORTED: %r" % (results,))
    loser = [r for r in results if not r.get("deleted_row")][0]
    assert loser.get("error") == "library row not found", loser
    assert not media.exists() and lib.library_get(lid) is None


# -- F14 (S26) -----------------------------------------------------------------

@pytest.mark.parametrize("big", [2**63, 2**64])
def test_f14_delete_of_an_id_past_sqlite_integer_is_404(big):
    c = _client()
    assert c.get(f"/api/library/{big}").status_code == 404
    r = c.delete(f"/api/library/{big}", json={"delete_file": False})
    assert r.status_code == 404, (
        "O1789-DHL-F14-DELETE-OVERFLOW: %s %s"
        % (r.status_code, r.get_data(as_text=True)[:200]))
    # ctl: id 0 -> 404; a real row -> 200.
    assert c.delete("/api/library/0", json={"delete_file": False}).status_code == 404
    lid = lib.library_record("/nowhere/dhl-f14.mp4")
    assert c.delete(f"/api/library/{lid}",
                    json={"delete_file": False}).status_code == 200


# -- F4 (S11) ------------------------------------------------------------------

def test_f4_hard_delete_during_scan_leaves_no_ghost(monkeypatch):
    root = _tree()
    s0 = _scan(root)
    assert s0.added == 15
    target = str(root / "b" / "b2.mp4")
    tid = _state(target)[0]["id"]
    real = lib.library_record
    fired = []

    def hook(fp, **kw):
        if fp == target and not fired:
            fired.append(lib.library_delete(tid, also_delete_file=True))
        return real(fp, **kw)

    monkeypatch.setattr(lib, "library_record", hook)
    s = _scan(root)
    assert fired and fired[0]["ok"] and not os.path.exists(target)
    assert _state(target) == [], (
        "O1789-DHL-F4-GHOST-ROW: deleted path re-inserted by the scan: %r "
        "(added=%d)" % (_state(target), s.added))
    assert s.errors == 0 and s.added == 0
    # ctl: every other row is still there and present.
    left = _rows("SELECT COUNT(*) c, SUM(file_exists) e FROM library")[0]
    assert left["c"] == 14 and left["e"] == 14, left


# -- F3 (S12) ------------------------------------------------------------------

def test_f3_download_finished_after_the_walk_is_not_flipped(monkeypatch):
    root = _tree()
    _scan(root)
    gone = str(root / "c" / "c0.mp4")
    os.remove(gone)
    fresh = str(root / "a" / "fresh.mp4")
    real_walk = os.walk

    def walk(top, *a, **k):
        yield from real_walk(top, *a, **k)
        _mk(fresh)
        lib.library_record(fresh, site_id="s1")  # runner completion

    monkeypatch.setattr(lib.os, "walk", walk)
    s = _scan(root)
    monkeypatch.setattr(lib.os, "walk", real_walk)
    assert _state(fresh)[0]["file_exists"] == 1, (
        "O1789-DHL-F3-FRESH-DOWNLOAD-FLIPPED-MISSING: %r on disk=%s "
        "missing_marked=%d" % (_state(fresh), os.path.exists(fresh),
                                s.missing_marked))
    # ctl: a file really removed is still flipped by the same pass.
    assert _state(gone)[0]["file_exists"] == 0 and s.missing_marked == 1


# -- F2 (S13), ctl S14 -------------------------------------------------------------

def test_f2_unreadable_subdir_rows_not_flipped_and_error_reported():
    root = _tree()
    _scan(root)
    c = root / "c"
    os.chmod(c, 0)
    try:
        if os.access(c, os.R_OK):
            pytest.skip("COULD NOT LOOK: chmod 000 does not bind (root?)")
        s = _scan(root)
    finally:
        os.chmod(c, 0o755)
    cr = _rows("SELECT file_exists FROM library WHERE file_path LIKE ?",
               (f"{c}/%",))
    assert len(cr) == 5 and all(r["file_exists"] == 1 for r in cr), (
        "O1789-DHL-F2-UNREADABLE-DIR-FLIPPED: %r errors=%d"
        % (cr, s.errors))
    assert s.errors >= 1 and any(str(c) in x for x in s.error_samples), (
        "O1789-DHL-F2-WALK-ERROR-SILENT: errors=%d samples=%r"
        % (s.errors, s.error_samples))


def test_f2_ctl_s14_emptied_dir_rows_are_flipped():
    root = _tree()
    _scan(root)
    for f in (root / "a").iterdir():
        f.unlink()
    s = _scan(root)
    ar = _rows("SELECT file_exists FROM library WHERE file_path LIKE ?",
               (f"{root}/a/%",))
    assert len(ar) == 5 and not any(r["file_exists"] for r in ar)
    assert s.missing_marked == 5 and s.errors == 0


# -- F5 (S15) ------------------------------------------------------------------

def test_f5_cancel_is_honoured_at_the_next_file(monkeypatch):
    big = Path(tempfile.mkdtemp(prefix="dhl_fix_big_"))
    for i in range(400):
        (big / f"f{i:04d}.mp4").write_bytes(b"")
    state = lib.ScanState()
    real = lib.library_record

    def hook(fp, **kw):
        if state.seen >= 20:
            state.cancelled = True
        return real(fp, **kw)

    monkeypatch.setattr(lib, "library_record", hook)
    lib._scan_worker([str(big)], state)
    assert state.finished_at is not None
    assert state.seen <= 21, (
        "O1789-DHL-F5-CANCEL-PER-DIRECTORY: cancelled at seen=20, walked %d"
        % state.seen)
    # ctl: uncancelled scan walks all 400.
    monkeypatch.setattr(lib, "library_record", real)
    assert _scan(big).seen == 400


# -- F6 (S17) ------------------------------------------------------------------

def test_f6_symlinked_alias_adds_no_duplicate_rows():
    root = _tree()
    _scan(root)
    alias = Path(tempfile.mkdtemp(prefix="dhl_fix_alias_")) / "alias"
    os.symlink(root, alias)
    s = _scan(alias)
    n = _rows("SELECT COUNT(*) c FROM library")[0]["c"]
    assert n == 15 and s.added == 0, (
        "O1789-DHL-F6-ALIAS-DUPLICATES: rows %d added=%d" % (n, s.added))
    assert s.updated == 15
    # ctl: a real new dir still adds its rows.
    other = _tree(dirs=("z",), n=3)
    assert _scan(other).added == 3


# -- F7 (S20) ------------------------------------------------------------------

def test_f7_same_basename_in_two_dirs_one_history_link():
    root = _tree(dirs=(), n=0)
    p1 = _mk(root / "d1" / "same.mp4")
    p2 = _mk(root / "d2" / "same.mp4")
    with _db().db_conn() as cx:
        hid = cx.execute("INSERT INTO history(status, filename) "
                         "VALUES ('done', 'same.mp4')").lastrowid
    _scan(root)
    claim = [r for r in (_state(p1) + _state(p2)) if r["history_id"] == hid]
    assert len(claim) == 1, (
        "O1789-DHL-F7-SHARED-HISTORY-ID: %r" % (_state(p1) + _state(p2),))
    back = _rows("SELECT library_id FROM history WHERE id=?", (hid,))[0]
    assert back["library_id"] == claim[0]["id"]
    # Deleting the unlinked row keeps the back-ref of the linked one.
    loser = [r for r in (_state(p1) + _state(p2)) if r["history_id"] != hid][0]
    assert lib.library_delete(loser["id"])["ok"]
    back = _rows("SELECT library_id FROM history WHERE id=?", (hid,))[0]
    assert back["library_id"] == claim[0]["id"], back


def test_f7_ctl_single_file_is_linked():
    root = _tree(dirs=(), n=0)
    p = _mk(root / "only.mp4")
    with _db().db_conn() as cx:
        hid = cx.execute("INSERT INTO history(status, filename) "
                         "VALUES ('done', 'only.mp4')").lastrowid
    _scan(root)
    assert _state(p)[0]["history_id"] == hid


# -- F8 (S28) ------------------------------------------------------------------

def test_f8_restore_never_deletes_a_row_readded_since():
    fp = "/nowhere/dhl-f8.mp4"
    lid = lib.library_record(fp)
    assert lib.library_add_tag(lid, "old")
    snap = lib.library_snapshot(lid)
    assert lib.library_delete(lid)["ok"]
    new = lib.library_record(fp)
    assert new and new != lid
    assert lib.library_add_tag(new, "newtag")
    out = lib.library_restore(snap)
    row = lib.library_get(new)
    assert row is not None and [t["name"] for t in row["tags"]] == ["newtag"], (
        "O1789-DHL-F8-RESTORE-REPLACED-ROW: restore=%r new row now %r"
        % (out, row))
    assert not out["ok"] and "not restored" in out.get("error", "")
    orphans = _rows("SELECT COUNT(*) c FROM library_tags WHERE library_id "
                    "NOT IN (SELECT id FROM library)")[0]["c"]
    assert orphans == 0


def test_f8_ctl_plain_restore_brings_back_tags_and_backref():
    fp = "/nowhere/dhl-f8-ctl.mp4"
    with _db().db_conn() as cx:
        hid = cx.execute("INSERT INTO history(status, filename) "
                         "VALUES ('done', 'x')").lastrowid
    lid = lib.library_record(fp, history_id=hid)
    assert lib.library_add_tag(lid, "keep")
    snap = lib.library_snapshot(lid)
    assert lib.library_delete(lid)["ok"]
    out = lib.library_restore(snap)
    assert out["ok"] and out["restored_id"] == lid, out
    assert [t["name"] for t in lib.library_get(lid)["tags"]] == ["keep"]
    assert _rows("SELECT library_id FROM history WHERE id=?",
                 (hid,))[0]["library_id"] == lid


# -- F9 (S27) ------------------------------------------------------------------

def test_f9_crashed_scan_reports_an_error(monkeypatch):
    root = _tree()

    def boom(*a, **k):
        raise RuntimeError("dhl injected walk crash")
        yield  # pragma: no cover

    real_walk = os.walk
    monkeypatch.setattr(lib.os, "walk", boom)
    s = lib.ScanState()
    try:
        lib._scan_worker([str(root)], s)
    except RuntimeError as e:
        pytest.fail("O1789-DHL-F9-CRASH-READS-CLEAN: the worker raised %r "
                    "(a thread traceback) and left errors=%d samples=%r"
                    % (e, s.errors, s.error_samples))
    finally:
        monkeypatch.setattr(lib.os, "walk", real_walk)
    assert s.finished_at is not None
    assert s.errors >= 1 and any("dhl injected walk crash" in x
                                 for x in s.error_samples), (
        "O1789-DHL-F9-CRASH-READS-CLEAN: errors=%d samples=%r"
        % (s.errors, s.error_samples))
    # ctl: the same tree scans clean.
    s = _scan(root)
    assert s.errors == 0 and s.added == 15


# -- F10 (S8) ------------------------------------------------------------------

def _b64(raw):
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def test_f10_non_finite_cursor_value_is_invalid():
    for i in range(12):
        lib.library_record(f"/nowhere/dhl-f10-{i}.mp4")
    some = _rows("SELECT id FROM library ORDER BY id DESC LIMIT 1 OFFSET 5")[0]["id"]
    for v in ("NaN", "-Infinity", "Infinity"):
        cur = _b64(f'["added_at_desc",{v},{some}]')
        with pytest.raises(lib.BrowseCursorError, match="cursor_invalid"):
            rows, nxt = lib.library_browse(limit=5, after_id=cur)
            pytest.fail("O1789-DHL-F10-NONFINITE-CURSOR-ACCEPTED: %s -> "
                        "%d rows next=%r" % (v, len(rows), nxt))
    # ctl: a finite cursor resumes; a list value stays invalid.
    rows, _ = lib.library_browse(
        limit=5, after_id=_b64(f'["added_at_desc",1e18,{some}]'))
    assert len(rows) == 5
    with pytest.raises(lib.BrowseCursorError):
        lib.library_browse(limit=5, after_id=_b64(f'["added_at_desc",[1],{some}]'))


# -- F11 (S19) -----------------------------------------------------------------

def test_f11_search_matches_both_unicode_forms():
    nfc = unicodedata.normalize("NFC", "/nowhere/café.mp4")
    nfd = unicodedata.normalize("NFD", "/nowhere/Café2.mp4")
    lib.library_record(nfc)
    lib.library_record(nfd)
    for form in ("NFC", "NFD"):
        q = unicodedata.normalize(form, "café")
        hits, _ = lib.library_browse(query=q, limit=50)
        assert {h["file_path"] for h in hits} == {nfc, nfd}, (
            "O1789-DHL-F11-UNNORMALIZED-SEARCH: q(%s) hit %r"
            % (form, [ascii(h["file_path"]) for h in hits]))
    # ctl: an unrelated query still finds nothing; ASCII query unaffected.
    assert lib.library_browse(query="zzz-none", limit=50)[0] == []
    assert len(lib.library_browse(query="Caf", limit=50)[0]) == 2


# -- F12 (S21) -----------------------------------------------------------------

def _tags(c, lid):
    return [t["name"] for t in c.get(f"/api/library/{lid}").get_json()["row"]["tags"]]


def test_f12_tag_remove_trims_like_add():
    c = _client()
    lid = lib.library_record("/nowhere/dhl-f12.mp4")
    assert c.post(f"/api/library/{lid}/tags", json={"tag": " x "}).status_code == 200
    assert _tags(c, lid) == ["x"]
    assert c.delete(f"/api/library/{lid}/tags/%20x%20").status_code == 200
    assert _tags(c, lid) == [], (
        "O1789-DHL-F12-REMOVE-NOT-TRIMMED: ' x ' still attached: %r"
        % _tags(c, lid))
    # ctl: plain "x" round-trips.
    c.post(f"/api/library/{lid}/tags", json={"tag": "x"})
    c.delete(f"/api/library/{lid}/tags/x")
    assert _tags(c, lid) == []


def test_f12_slash_tag_is_attached_measurement():
    """FINDINGS F12 also says add 'a/b' -> ok:true but not attached. pa.py
    printed the tags only AFTER removing 'a/b' via the path route, so that
    evidence cannot show it. Measured here: it attaches, browse finds it,
    and the <path:> route removes it."""
    c = _client()
    lid = lib.library_record("/nowhere/dhl-f12b.mp4")
    assert c.post(f"/api/library/{lid}/tags", json={"tag": "a/b"}).status_code == 200
    assert _tags(c, lid) == ["a/b"]
    assert c.get("/api/library/browse?tag=a%2Fb").get_json()["count"] == 1
    assert c.delete(f"/api/library/{lid}/tags/a/b").status_code == 200
    assert _tags(c, lid) == []


# -- r2: R131-D F7r and the r1 review's surviving mutants ----------------------

def _hist(fn):
    with _db().db_conn() as cx:
        return cx.execute("INSERT INTO history(status, filename) "
                          "VALUES ('done', ?)", (fn,)).lastrowid


def _backref(hid):
    return _rows("SELECT library_id FROM history WHERE id=?", (hid,))[0][
        "library_id"]


def test_f7r_moved_file_relinks_at_its_new_path():
    root = _tree(dirs=(), n=0)
    p1 = _mk(root / "d1" / "mv.mp4")
    hid = _hist("mv.mp4")
    _scan(root)
    assert _state(p1)[0]["history_id"] == hid, "setup: d1 file not linked"
    p2 = str(root / "d2" / "mv.mp4")
    os.makedirs(os.path.dirname(p2))
    os.rename(p1, p2)
    _scan(root)
    _scan(root)
    new = _state(p2)
    assert new and new[0]["history_id"] == hid and _backref(hid) == new[0]["id"], (
        "O1789-DHL-F7R-MOVED-FILE-UNLINKED: new %r backref %r"
        % (new, _backref(hid)))


def test_f7r_stale_holder_does_not_keep_the_backref():
    # A pre-fix DB: a gone low-id row and the live row both hold the
    # history id, and the back-ref still names the gone one.
    root = _tree(dirs=(), n=0)
    hid = _hist("x.mp4")
    stale = _mk(root / "old" / "x.mp4")
    sid = lib.library_record(stale, history_id=hid)
    live = _mk(root / "new" / "x.mp4")
    lid = lib.library_record(live, history_id=hid)
    os.remove(stale)
    _ex("UPDATE history SET library_id=? WHERE id=?", (sid, hid))
    _scan(root)
    assert _backref(hid) == lid, (
        "O1789-DHL-F7R-STALE-HOLDER-KEEPS-BACKREF: backref %r, live row %r, "
        "gone row %r" % (_backref(hid), lid, sid))


def test_f7r_two_live_holders_newest_keeps_the_backref():
    root = _tree(dirs=(), n=0)
    hid = _hist("y.mp4")
    a = lib.library_record(_mk(root / "a" / "y.mp4"), history_id=hid)
    b = lib.library_record(_mk(root / "b" / "y.mp4"), history_id=hid)
    _ex("UPDATE history SET library_id=? WHERE id=?", (a, hid))
    _scan(root)
    assert b > a and _backref(hid) == b, (
        "O1789-DHL-F7R-HOLDER-ORDER: backref %r, rows %r %r"
        % (_backref(hid), a, b))


def _stat_eio(monkeypatch, bad):
    real = os.stat

    def stat(p, *a, **k):
        if os.fspath(p) == bad:
            raise OSError(errno.EIO, "Input/output error", bad)
        return real(p, *a, **k)
    monkeypatch.setattr(os, "stat", stat)


def test_f7r_unreadable_holder_keeps_its_link(monkeypatch):
    # EIO is not "gone": like the missing pass, the holder is kept.
    root = _tree(dirs=(), n=0)
    hid = _hist("z.mp4")
    held = _mk(root / "old" / "z.mp4")
    lib.library_record(held, history_id=hid)
    os.remove(held)
    other = _mk(root / "new" / "z.mp4")
    _stat_eio(monkeypatch, held)
    _scan(root)
    got = _state(other)
    assert got and got[0]["history_id"] is None, (
        "O1789-DHL-F7R-EIO-HOLDER-DROPPED: %r" % (got,))


def test_f2_eio_on_stat_does_not_flip_the_row(monkeypatch):
    root = _tree(dirs=("a",), n=2)
    ghost = _mk(root / "a" / "ghost.mp4")
    assert lib.library_record(ghost)
    os.remove(ghost)
    _stat_eio(monkeypatch, ghost)
    _scan(root)
    assert _state(ghost)[0]["file_exists"] == 1, (
        "O1789-DHL-F2-EIO-FLIPPED: %r" % (_state(ghost),))


def test_f2_ctl_enoent_on_stat_flips_the_row():
    root = _tree(dirs=("a",), n=2)
    ghost = _mk(root / "a" / "ghost.mp4")
    assert lib.library_record(ghost)
    os.remove(ghost)
    _scan(root)
    assert _state(ghost)[0]["file_exists"] == 0


def test_f13_other_connection_delete_between_read_and_write(monkeypatch):
    # Another process deletes the row between library_delete's read and its
    # write. BEGIN IMMEDIATE holds the write lock across both, so exactly
    # one of the two deletes reports the row.
    lid, media, _ = _thumbed_item()
    real = lib.db_conn
    other = []

    class _Cx:
        def __init__(self, cx):
            self._cx = cx

        def __getattr__(self, name):
            return getattr(self._cx, name)

        def execute(self, sql, *a):
            if sql.lstrip().startswith("DELETE FROM library") and not other:
                o = sqlite3.connect(_db()._resolve_db_path(), timeout=0,
                                    isolation_level=None)
                try:
                    other.append(o.execute("DELETE FROM library WHERE id=?",
                                           (lid,)).rowcount)
                except sqlite3.OperationalError as e:
                    other.append(str(e))
                finally:
                    o.close()
            return self._cx.execute(sql, *a)

    @contextlib.contextmanager
    def conn(*a, **k):
        with real(*a, **k) as cx:
            yield _Cx(cx)

    monkeypatch.setattr(lib, "db_conn", conn)
    out = lib.library_delete(lid, also_delete_file=True)
    assert other, "probe: the other connection never ran"
    winners = (other[0] == 1) + bool(out.get("deleted_row"))
    assert winners == 1, (
        "O1789-DHL-F13-CROSS-CONN-DOUBLE-DELETE: other %r ours %r"
        % (other, out))
