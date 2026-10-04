"""O1826 C06 -- library scanner history link, tagged delete, swallowed causes.

M096/M095. ``_scan_worker`` linked each file to history with
``filename LIKE '%' || basename``: any history filename merely ENDING with the
basename matched (``1.mp4`` took ``21.mp4``'s row) and ``_``/``%`` in a name
acted as wildcards. It now matches the exact basename through one
basename -> history_id map built once per scan.

M094. ``library_delete`` relied on ON DELETE CASCADE for ``library_tags``, but
foreign keys are never enabled, so a tagged item's junction rows outlived it.

M091/M092. ``_ensure_schema`` and ``library_record`` swallowed every exception;
the scanner could only report ``record_failed <path>`` with no cause.
"""
from __future__ import annotations

import importlib
import logging
import os
import tempfile
from pathlib import Path

import bulk_downloader.library as lib

BD_GATE_SCOPE = "module"


def _db():
    return importlib.import_module("bulk_downloader.db")


def _fresh(monkeypatch):
    db = _db()
    monkeypatch.setattr(db, "DB_PATH", os.path.join(
        tempfile.mkdtemp(prefix="o1826c06_db_"), "queue.db"))
    monkeypatch.setattr(lib, "_SCHEMA_READY", False)
    lib._ensure_schema()
    assert lib._SCHEMA_READY, "fixture: schema did not initialise"


def _vid(dirpath, name):
    p = Path(dirpath) / name
    p.write_bytes(b"\0" * 8)
    return str(p)


def _history(filename, status="done"):
    with _db().db_conn() as cx:
        return cx.execute(
            "INSERT INTO history(status, filename) VALUES (?, ?)",
            (status, filename)).lastrowid


def _scan(root):
    state = lib.ScanState()
    lib._scan_worker([root], state)
    return state


def _linked(path):
    with _db().db_conn() as cx:
        r = cx.execute("SELECT history_id FROM library WHERE file_path=?",
                       (path,)).fetchone()
    assert r is not None, "no library row for %r" % path
    return r["history_id"]


def test_name_that_ends_with_another_is_not_linked(monkeypatch):
    _fresh(monkeypatch)
    d = tempfile.mkdtemp(prefix="o1826c06_lib_")
    one, twenty_one = _vid(d, "1.mp4"), _vid(d, "21.mp4")
    h21 = _history("/elsewhere/21.mp4")
    state = _scan(d)
    assert state.errors == 0, state.error_samples
    assert _linked(one) is None, (
        "O1826C06-M096: 1.mp4 was linked to history row %r, whose file is "
        "21.mp4 (suffix match)" % _linked(one))
    assert _linked(twenty_one) == h21


def test_like_wildcards_in_a_name_match_nothing_else(monkeypatch):
    _fresh(monkeypatch)
    d = tempfile.mkdtemp(prefix="o1826c06_lib_")
    under, pct = _vid(d, "a_b.mp4"), _vid(d, "c%d.mp4")
    _history("axb.mp4")
    _history("cZZd.mp4")
    _scan(d)
    assert (_linked(under), _linked(pct)) == (None, None), (
        "O1826C06-M096: '_'/'%%' acted as LIKE wildcards: a_b.mp4 -> %r, "
        "c%%d.mp4 -> %r" % (_linked(under), _linked(pct)))


def test_exact_name_still_links_newest_done_row(monkeypatch):
    _fresh(monkeypatch)
    d = tempfile.mkdtemp(prefix="o1826c06_lib_")
    bare, nested, under = (_vid(d, "x.mp4"), _vid(d, "y.mkv"),
                           _vid(d, "p_q.mp4"))
    _history("x.mp4")
    hx = _history("/dl/site/x.mp4")
    _history("/dl/site/x.mp4", status="failed")
    hy = _history("C:\\dl\\site\\y.mkv")
    hu = _history("/dl/p_q.mp4")
    _scan(d)
    assert (_linked(bare), _linked(nested), _linked(under)) == (hx, hy, hu)


def test_delete_of_tagged_item_removes_its_tag_rows(monkeypatch):
    _fresh(monkeypatch)
    d = tempfile.mkdtemp(prefix="o1826c06_lib_")
    keep = lib.library_record(_vid(d, "keep.mp4"))
    gone = lib.library_record(_vid(d, "gone.mp4"))
    for rid in (keep, gone):
        assert lib.library_add_tag(rid, "fav")
    out = lib.library_delete(gone)
    assert out["ok"], out
    with _db().db_conn() as cx:
        left = cx.execute(
            "SELECT library_id FROM library_tags ORDER BY library_id"
        ).fetchall()
    assert [r["library_id"] for r in left] == [keep], (
        "O1826C06-M094: library_tags rows %r remain for deleted library id %r"
        % ([r["library_id"] for r in left], gone))


def test_scanner_record_failure_carries_its_cause(monkeypatch, caplog):
    _fresh(monkeypatch)
    d = tempfile.mkdtemp(prefix="o1826c06_lib_")
    _vid(d, "z.mp4")
    with _db().db_conn() as cx:
        cx.execute(
            "CREATE TRIGGER o1826c06_refuse BEFORE INSERT ON library "
            "BEGIN SELECT RAISE(ABORT, 'o1826c06 insert refused'); END")
    with caplog.at_level(logging.WARNING, logger=lib.__name__):
        state = _scan(d)
    assert state.errors == 1, state.error_samples
    assert "o1826c06 insert refused" in state.error_samples[0], (
        "O1826C06-M092: scanner error has no cause: %r" % state.error_samples)
    assert "o1826c06 insert refused" in caplog.text


def test_schema_failure_is_logged_with_its_cause(monkeypatch, caplog):
    def _boom(*_a, **_k):
        raise RuntimeError("o1826c06 schema boom")

    monkeypatch.setattr(lib, "_SCHEMA_READY", False)
    monkeypatch.setattr(_db(), "db_init", _boom)
    with caplog.at_level(logging.WARNING, logger=lib.__name__):
        lib._ensure_schema()
    assert lib._SCHEMA_READY is False
    assert "o1826c06 schema boom" in caplog.text, (
        "O1826C06-M091: db_init failure swallowed without a log record")
