"""O1671 r8b F003: History search must find a row by its website title.

THE DEFECT, measured on v3.66.1706 (UIUX-20260928 F003): the History tab shows
"Test Scene 002" in the Website name column, yet /api/search?q=Test returns 0.
The column is ``library.title``, reached through ``history.library_id`` by
_history_title_projection; history_fts indexes site_name/url/filename/message
only, so db_search_fts can never match it.

The title lives in ``library`` and is rewritten there by library.py and the
template learner, after the FTS row is written, so it is matched at query time
rather than copied into the external-content index, where it would go stale.
Every test drives the shipped db_log -> library_record -> db_search_fts chain.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import pytest

pytestmark = pytest.mark.bd_module_wipe

_TITLE = "Test Scene 002"


@pytest.fixture
def seeded(clean_workdir):
    from bulk_downloader.db import db_init, db_log
    from bulk_downloader.migrations import apply_pending

    db_init()
    result = apply_pending(backup_first=False)
    assert result["errors"] == 0, result

    def _log(site_id, url, fname, title, status="done"):
        target = clean_workdir / fname
        target.write_bytes(b"x")
        db_log(site_id, "Acme Studio", url, status, filename=fname,
               file_size=1, file_path=str(target), title=title,
               title_source="document.title")

    _log("siteA", "https://a.example/scene/2", "opaque-qzx.mp4", _TITLE)
    _log("siteA", "https://a.example/scene/3", "other-qzx.mp4", "Beach Day")
    return _log


def _titles(query, **kw):
    from bulk_downloader.db import db_search_fts
    return [r["title"] for r in db_search_fts(query, **kw)]


def test_fixture_title_is_what_the_history_tab_shows(seeded):
    """Positive control: the row carries the title the Website name column reads."""
    from bulk_downloader.db import db_search
    assert _TITLE in [r["title"] for r in db_search(limit=10)]
    # and the index itself still works for an indexed column
    assert _titles("opaque") == [_TITLE]


@pytest.mark.parametrize("query", ["Test", "002", "test", '"Scene 002"', "Scene 002"])
def test_title_text_matches(seeded, query):
    assert _titles(query) == [_TITLE], (
        f"F003: search {query!r} must find the row titled {_TITLE!r}")


def test_title_term_and_indexed_term_together(seeded):
    """Terms are AND-ed across the row, title included, as FTS does across columns."""
    assert _titles("Test opaque") == [_TITLE]
    assert _titles("Test other") == []


def test_row_matched_by_index_and_title_is_returned_once(seeded):
    # 'scene' is in the url (indexed) of both rows and in one title.
    titles = _titles("Scene")
    assert sorted(titles) == sorted([_TITLE, "Beach Day"])


def test_negative_control_absent_text_matches_nothing(seeded):
    assert _titles("Zebra") == []


def test_filters_still_apply_to_title_matches(seeded):
    assert _titles("Test", site_id="siteB") == []
    assert _titles("Test", status="failed") == []
    assert _titles("Test", site_id="siteA", status="done") == [_TITLE]


def test_not_operator_is_not_inverted_by_the_title_match(seeded):
    # FTS operators the title match cannot honour must not widen results.
    assert _TITLE not in _titles("opaque NOT opaque")


def test_limit_is_respected(seeded):
    seeded("siteA", "https://a.example/x/9", "z9.mp4", "Test Again")
    assert len(_titles("Test", limit=1)) == 1


def test_title_html_is_never_returned_as_markup(seeded):
    from bulk_downloader.db import db_search_fts
    seeded("siteA", "https://a.example/x/7", "z7.mp4", "<b>Xss</b> Test")
    for r in db_search_fts("Xss"):
        for k in ("snippet_url", "snippet_filename", "snippet_message"):
            assert "<b>" not in (r.get(k) or "")
