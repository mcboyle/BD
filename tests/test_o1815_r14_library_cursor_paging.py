"""O1815 R14 (P2-9): library_browse cursor paging must follow the active sort.

The cursor used to be `l.id < after_id` for every sort, which only matches
the page order when the sort is id-descending. For any other sort, walking
next_cursor repeats some rows and skips others. These tests walk every
whitelisted sort page by page and require the concatenation to equal the
unpaged listing exactly, also with a filter, with each cursor row
deleted between pages, across a process restart and after more than 1024
deletes. A cursor that cannot be resumed must fail loudly, never page by id.
"""
from __future__ import annotations

import importlib

import pytest

BD_GATE_SCOPE = "module"

pytestmark = pytest.mark.bd_module_wipe

# id -> (file_size, title, rating, watched_at, added_at, site_id). Values
# are deliberately not monotone in id, include ties and include NULLs (in
# the ASC-sorted file_size too), so no sort except id-desc lines up with the
# insertion order. Rows 3 and 5 sit on another site so a site_id filter
# yields a strict subset.
_ROWS = {
    1: (500, "delta", 3, None, 20.0, "s1"),
    2: (None, "alpha", None, 7.0, 10.0, "s1"),
    3: (900, "echo", 5, None, 30.0, "s2"),
    4: (100, "charlie", 3, 9.0, 10.0, "s1"),
    5: (300, "alpha", None, 2.0, 40.0, "s2"),
    6: (None, "bravo", 1, None, 5.0, "s1"),
    7: (300, "foxtrot", 5, 9.0, 30.0, "s1"),
}

_SORTS = ("added_at_desc", "added_at_asc", "file_size_desc", "file_size_asc",
          "title_asc", "rating_desc", "watched_at_desc")


@pytest.fixture(autouse=True)
def _bd_boot_app():
    from bulk_downloader.app import boot_once
    boot_once()


@pytest.fixture
def lib(clean_workdir):
    from bulk_downloader.db import db_conn, db_init
    from bulk_downloader.migrations import apply_pending
    from bulk_downloader import library as lib

    db_init()
    apply_pending()
    ids = {}
    for key, row in _ROWS.items():
        ids[key] = lib.library_record(str(clean_workdir / f"v{key}.mp4"),
                                      site_id=row[5])
    with db_conn() as cx:
        for key, (size, title, rating, watched_at, added_at,
                  _site) in _ROWS.items():
            cx.execute(
                "UPDATE library SET file_size=?, title=?, rating=?, "
                "watched_at=?, added_at=? WHERE id=?",
                (size, title, rating, watched_at, added_at, ids[key]))
    assert sorted(ids.values()) == list(ids.values()), ids
    return lib


def _walk(lib, sort, page, *, delete_cursor=False, between=None,
          **filters):
    out, cursor, pages = [], None, 0
    while True:
        rows, cursor = lib.library_browse(sort=sort, limit=page,
                                          after_id=cursor, **filters)
        out.extend(r["id"] for r in rows)
        pages += 1
        assert pages <= len(_ROWS) + 2, f"{sort}: cursor never ended {out}"
        if cursor is None:
            return out
        if delete_cursor:
            # The product path behind api_library_delete.
            assert lib.library_delete(rows[-1]["id"])["deleted_row"], rows
        if between is not None:
            lib = between(lib)


def test_the_unpaged_listing_holds_every_row(lib):
    for sort in _SORTS:
        rows, cursor = lib.library_browse(sort=sort, limit=100)
        assert len(rows) == len(_ROWS) == 7, (sort, rows)
        assert cursor is None


def test_the_fixture_orders_differ_from_id_desc(lib):
    """Positive control: a sort whose order equals id-desc cannot expose the
    defect, so at least the non-added_at sorts must differ from it."""
    id_desc = sorted((r["id"] for r in lib.library_browse(limit=100)[0]),
                     reverse=True)
    differing = [s for s in _SORTS
                 if [r["id"] for r in lib.library_browse(sort=s, limit=100)[0]]
                 != id_desc]
    assert len(differing) == len(_SORTS), differing


@pytest.mark.parametrize("page", [1, 2, 3])
@pytest.mark.parametrize("sort", _SORTS)
def test_paging_matches_the_unpaged_order(lib, sort, page):
    full = [r["id"] for r in lib.library_browse(sort=sort, limit=100)[0]]
    paged = _walk(lib, sort, page)
    assert paged == full, (
        f"sort={sort} page={page}: cursor paging returned {paged}, "
        f"unpaged order is {full}")


@pytest.mark.parametrize("page", [1, 2])
@pytest.mark.parametrize("sort", _SORTS)
def test_paging_survives_the_cursor_row_being_deleted(lib, sort, page):
    """Each page's cursor row is deleted before the next page is fetched.
    The rows already returned stay returned, and every surviving row must
    still arrive exactly once, in the unpaged order."""
    full = [r["id"] for r in lib.library_browse(sort=sort, limit=100)[0]]
    paged = _walk(lib, sort, page, delete_cursor=True)
    assert paged == full, (
        f"sort={sort} page={page} deleting each cursor row: cursor paging "
        f"returned {paged}, unpaged order is {full}")


@pytest.mark.parametrize("page", [1, 2])
@pytest.mark.parametrize("sort", _SORTS)
def test_paging_keeps_the_filter(lib, sort, page):
    everything = {r["id"] for r in lib.library_browse(sort=sort,
                                                      limit=100)[0]}
    full = [r["id"] for r in lib.library_browse(sort=sort, limit=100,
                                                site_id="s1")[0]]
    assert len(full) == 5 and set(full) < everything, (full, everything)
    paged = _walk(lib, sort, page, site_id="s1")
    assert paged == full, (
        f"sort={sort} page={page} site_id=s1: cursor paging returned "
        f"{paged}, filtered unpaged order is {full}")


def _restart(lib):
    """A server restart: the module starts over with no process memory."""
    return importlib.reload(lib)


def _delete_1100_other_rows(lib):
    """More than 1024 deletes land between two page fetches."""
    from bulk_downloader.db import db_conn

    with db_conn() as cx:
        cx.executemany("INSERT INTO library(file_path) VALUES (?)",
                       [(f"/filler/{i}.mp4",) for i in range(1100)])
        filler = [r[0] for r in cx.execute(
            "SELECT id FROM library WHERE file_path LIKE '/filler/%'")]
    assert len(filler) == 1100
    for i in filler:
        assert lib.library_delete(i)["deleted_row"], i
    return lib


@pytest.mark.parametrize("sort", _SORTS)
def test_paging_survives_a_restart_after_the_cursor_row_is_deleted(lib,
                                                                   sort):
    """Fixture order is not id order, so resuming by id alone would repeat
    or skip rows here (file_size_desc stopped after 2 of 7)."""
    full = [r["id"] for r in lib.library_browse(sort=sort, limit=100)[0]]
    paged = _walk(lib, sort, 2, delete_cursor=True, between=_restart)
    assert paged == full, (
        f"sort={sort} deleting each cursor row and restarting: cursor "
        f"paging returned {paged}, unpaged order is {full}")


@pytest.mark.parametrize("sort", ["file_size_desc", "title_asc"])
def test_paging_survives_more_than_1024_deletes_between_pages(lib, sort):
    full = [r["id"] for r in lib.library_browse(sort=sort, limit=100)[0]]
    calls = []

    def once(lib):
        if not calls:
            calls.append(_delete_1100_other_rows(lib))
        return lib

    paged = _walk(lib, sort, 2, delete_cursor=True, between=once)
    assert calls and paged == full, (
        f"sort={sort} after 1100 deletes: cursor paging returned {paged}, "
        f"unpaged order is {full}")


def test_a_legacy_row_id_cursor_still_resumes(lib):
    """Callers that pass a bare row id (the old next_cursor) keep working
    while that row exists."""
    full = [r["id"] for r in lib.library_browse(sort="file_size_desc",
                                                limit=100)[0]]
    for cursor in (full[1], str(full[1])):
        rows, _ = lib.library_browse(sort="file_size_desc", limit=100,
                                     after_id=cursor)
        assert [r["id"] for r in rows] == full[2:], cursor


def test_a_cursor_that_cannot_resume_raises(lib):
    rows, cursor = lib.library_browse(sort="file_size_desc", limit=2)
    gone = rows[-1]["id"]
    assert lib.library_delete(gone)["deleted_row"]
    with pytest.raises(lib.BrowseCursorError, match="cursor_expired"):
        lib.library_browse(sort="file_size_desc", limit=2, after_id=gone)
    for bad in ("not-a-cursor", "e30", "W10"):
        with pytest.raises(lib.BrowseCursorError, match="cursor_invalid"):
            lib.library_browse(sort="file_size_desc", limit=2, after_id=bad)
    with pytest.raises(lib.BrowseCursorError, match="cursor_invalid"):
        lib.library_browse(sort="title_asc", limit=2, after_id=cursor)


def test_the_api_walks_the_cursor_and_reports_an_expired_one(lib):
    """The /api/library/browse consumer (tools/live_seed.py pattern: pass
    next_cursor back unencoded as after_id)."""
    from bulk_downloader import app as a

    c = a.app.test_client()
    full = [r["id"] for r in lib.library_browse(sort="file_size_desc",
                                                limit=100)[0]]
    out, cursor = [], None
    for _ in range(len(_ROWS) + 2):
        path = "/api/library/browse?sort=file_size_desc&limit=2"
        if cursor is not None:
            path = f"{path}&after_id={cursor}"
        body = c.get(path).get_json()
        out.extend(r["id"] for r in body["rows"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert lib.library_delete(out[-1])["deleted_row"]
    assert out == full, (out, full)
    assert lib.library_delete(full[0])["deleted_row"]
    r = c.get(f"/api/library/browse?sort=file_size_desc&after_id={full[0]}")
    assert r.status_code == 400 and r.get_json() == {
        "ok": False, "error": "cursor_expired"}, (r.status_code, r.data)
