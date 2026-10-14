"""O1789 bug-hunt hl-library -- library delete and input handling.

F1. DELETE /api/library/<id> read ``bool(body.get("delete_file"))``: the
string "false" is truthy, so a client asking NOT to delete the file had the
video unlinked. Only a JSON boolean is accepted now; anything else is a 400
and nothing is touched.
F2. A hard delete also unlinked ``<base>.jpg`` beside the video even when the
row records no cover_thumb, so a user's own poster/photo named like the video
was lost. The sidecar is removed only when the row records a thumbnail.
F8. /api/library/browse?after_id=<digits beyond SQLite INTEGER> overflowed in
the query and the bare ``except`` returned an empty last page; it is now a
cursor_invalid 400 like every other unusable cursor.
F11. /api/library/<id>/notes stored ``str(None)`` / ``str(dict)``; null now
clears the notes and a non-string is a 400.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe


def _client():
    from bulk_downloader import app as a
    return a.app.test_client()


def _item(cover_thumb: str = ""):
    """A media file, a same-named .jpg beside it, and a library row."""
    from bulk_downloader import library as lib
    d = Path(tempfile.mkdtemp(prefix="o1789_hl_lib_"))
    media = d / "v02.mp4"
    media.write_bytes(b"\0" * 16)
    photo = d / "v02.jpg"
    photo.write_bytes(b"user poster")
    lid = lib.library_record(str(media), cover_thumb=cover_thumb)
    assert lid, "fixture: library_record must return a row id"
    return lid, media, photo


def _row(lid):
    from bulk_downloader import library as lib
    return lib.library_get(lid)


@pytest.mark.parametrize("value", ["false", "0", "no", 0, 1, "true", None, [], {}])
def test_delete_file_must_be_a_json_boolean(value):
    lid, media, _ = _item()
    r = _client().delete(f"/api/library/{lid}", json={"delete_file": value})
    assert r.status_code == 400, (
        "O1789-HL-F1-NON-BOOLEAN-DELETE-FILE-ACCEPTED: delete_file=%r -> %s %s"
        % (value, r.status_code, r.get_data(as_text=True)[:200]))
    assert media.exists(), (
        "O1789-HL-F1-FILE-UNLINKED: delete_file=%r removed the video" % (value,))
    assert _row(lid) is not None, "a refused delete must keep the row"


def test_delete_file_false_keeps_file_true_removes_it():
    lid, media, _ = _item()
    r = _client().delete(f"/api/library/{lid}", json={"delete_file": False})
    assert r.status_code == 200 and r.get_json()["file_removed"] is False
    assert media.exists() and _row(lid) is None
    lid2, media2, _ = _item()
    r = _client().delete(f"/api/library/{lid2}", json={"delete_file": True})
    assert r.status_code == 200 and r.get_json()["file_removed"] is True
    assert not media2.exists()
    lid3, media3, _ = _item()
    r = _client().delete(f"/api/library/{lid3}")  # SPA: no body = row only
    assert r.status_code == 200 and media3.exists() and _row(lid3) is None


def test_hard_delete_keeps_same_named_jpg_when_no_thumbnail_recorded():
    lid, media, photo = _item(cover_thumb="")
    r = _client().delete(f"/api/library/{lid}", json={"delete_file": True})
    body = r.get_json()
    assert r.status_code == 200 and body["file_removed"] is True
    assert photo.exists(), (
        "O1789-HL-F2-JPG-WITHOUT-COVER-UNLINKED: %s removed with cover_thumb='' (%s)"
        % (photo.name, body))
    assert body["thumbs_removed"] == 0


def test_hard_delete_still_removes_recorded_thumbnails():
    d = Path(tempfile.mkdtemp(prefix="o1789_hl_cov_"))
    cover = d / "cover_custom.jpg"
    cover.write_bytes(b"thumb")
    lid, media, sidecar = _item(cover_thumb=str(cover))
    r = _client().delete(f"/api/library/{lid}", json={"delete_file": True})
    body = r.get_json()
    assert not media.exists() and not cover.exists() and not sidecar.exists()
    assert body["thumbs_removed"] == 2, body


@pytest.mark.parametrize("after_id", ["99999999999999999999", str(2 ** 63),
                                      # past int()'s digit limit (cx161 R1)
                                      "9" * 5000,
                                      # isdigit() but not int()-able
                                      "%C2%B2"])
def test_browse_after_id_beyond_sqlite_integer_is_cursor_invalid(after_id):
    _item()
    r = _client().get(f"/api/library/browse?after_id={after_id}")
    assert r.status_code == 400, (
        "O1789-HL-F8-OVERFLOW-CURSOR-SILENT: after_id=%s -> %s %s"
        % (after_id, r.status_code, r.get_data(as_text=True)[:200]))
    assert r.get_json()["error"] == "cursor_invalid"


def test_browse_largest_sqlite_after_id_is_still_a_legacy_cursor():
    _item()
    r = _client().get(f"/api/library/browse?after_id={2 ** 63 - 1}")
    assert r.status_code == 400 and r.get_json()["error"] == "cursor_expired"


@pytest.mark.parametrize("value", [{"a": 1}, [1], 5, True])
def test_notes_non_string_is_refused(value):
    lid, _, _ = _item()
    c = _client()
    assert c.post(f"/api/library/{lid}/notes", json={"notes": "keep"}).status_code == 200
    r = c.post(f"/api/library/{lid}/notes", json={"notes": value})
    assert r.status_code == 400, (
        "O1789-HL-F11-NON-STRING-NOTES-STORED: notes=%r -> %s, stored %r"
        % (value, r.status_code, _row(lid).get("notes")))
    assert _row(lid)["notes"] == "keep"


def test_notes_null_clears_and_string_is_stored():
    lid, _, _ = _item()
    c = _client()
    assert c.post(f"/api/library/{lid}/notes", json={"notes": "hello"}).status_code == 200
    assert _row(lid)["notes"] == "hello"
    r = c.post(f"/api/library/{lid}/notes", json={"notes": None})
    assert r.status_code == 200
    assert _row(lid)["notes"] == "", (
        "O1789-HL-F11-NULL-NOTES-STORED-AS-TEXT: stored %r" % _row(lid)["notes"])
