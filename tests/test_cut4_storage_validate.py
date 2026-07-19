"""Cut 4 — GET /api/storage/validate?path= (read-only FS diagnosis).

Reports whether a path exists / is a dir / is writable / free space, plus a
plain-language problem list and a suggested fix. READ-ONLY: it never creates,
moves, or deletes anything (a missing dir is reported, not made — repair-write
is deferred to Cut 8). Tested with a sessionless client (no CSRF).

RED on pristine 373: the route 404s.

Contract:
    {ok, path, exists, is_dir, writable, free_bytes, problems:[str], suggested_fix}
"""
import os
import tempfile


def test_validate_existing_writable_dir():
    from bulk_downloader import app as A
    c = A.app.test_client()
    with tempfile.TemporaryDirectory() as td:
        r = c.get(f"/api/storage/validate?path={td}")
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d["ok"] is True
        assert d["exists"] is True
        assert d["is_dir"] is True
        assert d["writable"] is True
        assert d["problems"] == []
        assert isinstance(d["free_bytes"], int)


def test_validate_missing_path_is_reported_not_created():
    from bulk_downloader import app as A
    c = A.app.test_client()
    with tempfile.TemporaryDirectory() as td:
        missing = os.path.join(td, "does", "not", "exist")
        r = c.get(f"/api/storage/validate?path={missing}")
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d["exists"] is False
        assert d["problems"]                 # at least one problem reported
        assert d["suggested_fix"]            # an actionable hint
        # READ-ONLY: the path must NOT have been created
        assert not os.path.exists(missing)


def test_validate_requires_path_param():
    from bulk_downloader import app as A
    c = A.app.test_client()
    r = c.get("/api/storage/validate")
    assert r.status_code == 400, r.get_json()


def test_validate_file_not_dir_is_flagged():
    from bulk_downloader import app as A
    c = A.app.test_client()
    with tempfile.NamedTemporaryFile() as tf:
        r = c.get(f"/api/storage/validate?path={tf.name}")
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d["exists"] is True
        assert d["is_dir"] is False
        assert d["problems"]                 # "not a directory" flagged
