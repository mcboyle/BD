"""A batch move must preserve unrelated files at its destination."""

import sqlite3
from contextlib import contextmanager

from bulk_downloader import batch_ops, db

BD_GATE_SCOPE = "module"


def _wire_history(tmp_path, monkeypatch, row_id, filename):
    db_path = tmp_path / "history.sqlite"
    with sqlite3.connect(db_path) as cx:
        cx.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, filename TEXT)")
        cx.execute("INSERT INTO history VALUES (?, ?)", (row_id, str(filename)))

    @contextmanager
    def connection():
        cx = sqlite3.connect(db_path)
        try:
            yield cx
            cx.commit()
        finally:
            cx.close()

    monkeypatch.setattr(db, "db_conn", connection)
    monkeypatch.setattr(db, "db_fts_snapshot", lambda _cx, _ids: [])
    monkeypatch.setattr(db, "db_fts_resync", lambda _cx, _old: None)
    monkeypatch.setattr(batch_ops, "_matching_rows", lambda _filter: [{"id": row_id, "filename": str(filename)}])
    return db_path


def test_move_refuses_existing_destination(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    source = source_dir / "same.mp4"
    target = target_dir / "same.mp4"
    source.write_bytes(b"incoming")
    target.write_bytes(b"original")
    db_path = _wire_history(tmp_path, monkeypatch, 1, source)

    result = batch_ops.bulk_move({}, target_dir=str(target_dir), dry_run=False)

    assert result["candidates_matched"] == 1
    assert result["processed"] == 0
    assert result["errors"] == 1
    assert source.read_bytes() == b"incoming"
    assert target.read_bytes() == b"original"
    with sqlite3.connect(db_path) as cx:
        assert cx.execute("SELECT filename FROM history WHERE id = 1").fetchone()[0] == str(source)


def test_move_without_collision_updates_history(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    source = source_dir / "safe.mp4"
    source.write_bytes(b"incoming")
    db_path = _wire_history(tmp_path, monkeypatch, 1, source)

    result = batch_ops.bulk_move({}, target_dir=str(target_dir), dry_run=False)

    target = target_dir / "safe.mp4"
    assert result["processed"] == 1
    assert result["errors"] == 0
    assert not source.exists()
    assert target.read_bytes() == b"incoming"
    with sqlite3.connect(db_path) as cx:
        assert cx.execute("SELECT filename FROM history WHERE id = 1").fetchone()[0] == str(target)


def test_two_sources_with_one_basename_do_not_clobber_each_other(tmp_path, monkeypatch):
    """Lens: the first move of a batch must not be overwritten by a later
    row with the same basename (its history row would point at the wrong bytes)."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    first, second = tmp_path / "a" / "dup.mp4", tmp_path / "b" / "dup.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    db_path = _wire_history(tmp_path, monkeypatch, 1, first)
    with sqlite3.connect(db_path) as cx:
        cx.execute("INSERT INTO history VALUES (?, ?)", (2, str(second)))
    monkeypatch.setattr(batch_ops, "_matching_rows", lambda _f: [
        {"id": 1, "filename": str(first)}, {"id": 2, "filename": str(second)}])

    result = batch_ops.bulk_move({}, target_dir=str(target_dir), dry_run=False)

    assert (result["processed"], result["errors"]) == (1, 1)
    assert (target_dir / "dup.mp4").read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    with sqlite3.connect(db_path) as cx:
        rows = dict(cx.execute("SELECT id, filename FROM history").fetchall())
    assert rows == {1: str(target_dir / "dup.mp4"), 2: str(second)}
