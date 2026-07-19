"""v3.66.669 -- LIB-6: complete-delete cascade extends to thumbnails.

library_delete(also_delete_file=True) removes the media file + DB row + tags
(via CASCADE) + the history back-ref. The maximalist LIB-6 gap: the item's
thumbnail artifacts (the sidecar <base>.jpg and any stored cover_thumb) were
left orphaned on disk. This cut unlinks them in the SAME operation and reports
a `thumbs_removed` count. Row-only delete (also_delete_file=False) must NOT
touch any file (the operator's data-safety invariant is unchanged).

Note: the captures index is host/site-scoped + restart-ephemeral (reconcile-
pruned once the file is gone), not per-library-item, so it is deliberately out
of scope here -- there is no clean 1:1 media-row -> capture-row link to cascade.

Sandbox-safe: isolated temp DB via db.DB_PATH, temp files, zero-arg tests.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import bulk_downloader.db as db
from bulk_downloader import library as lib
from bulk_downloader import thumbnail_gen as tg


def _isolated_db():
    dbf = tempfile.mktemp(prefix="lib6_", suffix=".db")
    db.DB_PATH = dbf
    lib._SCHEMA_READY = False   # force the library schema onto this fresh DB
    db.db_init()
    lib._ensure_schema()
    return dbf


def _seed_item(dirpath):
    """Create a media file + its sidecar thumb + a separate cover_thumb, and a
    library row pointing at the media with cover_thumb set. Returns the ids/paths."""
    media = Path(dirpath) / "scene.mp4"
    media.write_bytes(b"\0" * 2048)
    sidecar = Path(tg.resolve_output_path(str(media), mode="sidecar"))  # scene.jpg
    sidecar.write_bytes(b"\xff\xd8thumb")
    cover = Path(dirpath) / "cover_custom.jpg"
    cover.write_bytes(b"\xff\xd8cover")
    lid = lib.library_record(str(media), cover_thumb=str(cover))
    assert lid, "library_record must return a row id"
    return lid, media, sidecar, cover


def test_delete_with_file_also_removes_thumbnails():
    saved = db.DB_PATH
    try:
        _isolated_db()
        d = tempfile.mkdtemp(prefix="lib6_media_")
        lid, media, sidecar, cover = _seed_item(d)

        out = lib.library_delete(lid, also_delete_file=True)

        assert out["ok"] and out["deleted_row"], out
        assert out["file_removed"], "the media file must be removed"
        assert not media.exists(), "media file should be gone"
        assert not sidecar.exists(), "sidecar thumbnail must be removed in the same op"
        assert not cover.exists(), "stored cover_thumb must be removed in the same op"
        assert out.get("thumbs_removed", 0) >= 1, (
            f"thumbs_removed must count the removed thumbnails; got {out}")
    finally:
        db.DB_PATH = saved


def test_row_only_delete_leaves_all_files_untouched():
    saved = db.DB_PATH
    try:
        _isolated_db()
        d = tempfile.mkdtemp(prefix="lib6_media_")
        lid, media, sidecar, cover = _seed_item(d)

        out = lib.library_delete(lid, also_delete_file=False)

        assert out["ok"] and out["deleted_row"], out
        assert not out["file_removed"], "row-only delete must not touch the media file"
        assert media.exists(), "media file must survive a row-only delete"
        assert sidecar.exists(), "sidecar thumb must survive a row-only delete"
        assert cover.exists(), "cover_thumb must survive a row-only delete"
        assert out.get("thumbs_removed", 0) == 0, "no thumbs removed on a row-only delete"
    finally:
        db.DB_PATH = saved
