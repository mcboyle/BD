"""Row 375: history keeps the website's scene name, not the disk filename."""
from __future__ import annotations

from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path

import pytest


BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

_SCENE_URL = "https://members.ultrafilms.example/members/content/item/4f338e1e-with-leo-in-bed"
_OTHER_SCENE_URL = "https://members.ultrafilms.example/members/content/item/another-scene"
_RAW_TITLE = "UltraFilms / Members / Movie / With Leo In Bed"
_OTHER_RAW_TITLE = "UltraFilms / Members / Movie / Another Scene"
_WEBSITE_TITLE = "With Leo In Bed"


class _FixtureDom(HTMLParser):
    """The tiny fixture's DOM facts, exposed to the Playwright-shaped fake."""

    def __init__(self, html: str):
        super().__init__()
        self.og_title = ""
        self.document_title = ""
        self.h1: str | None = None
        self._capture: str | None = None
        self._parts: list[str] = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("property") == "og:title":
            self.og_title = attributes.get("content", "")
        if tag in ("title", "h1"):
            self._capture = tag
            self._parts = []

    def handle_data(self, data):
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag):
        if tag != self._capture:
            return
        value = " ".join("".join(self._parts).split())
        if tag == "title":
            self.document_title = value
        else:
            self.h1 = value
        self._capture = None
        self._parts = []


class _FixturePage:
    def __init__(self, url: str, html: str):
        self.url = url
        self.dom = _FixtureDom(html)
        self.evaluate_calls = 0

    def title(self):
        return self.dom.document_title

    def evaluate(self, _script):
        # This fake does not execute JavaScript, so make its contract
        # selector-sensitive: a production mutation that stops querying any
        # required surface must turn the fixture red instead of receiving the
        # parser values unconditionally.
        assert "meta[property=\"og:title\"]" in _script
        assert "meta[name=\"og:title\"]" in _script
        assert "document.title" in _script
        assert "querySelector('h1')" in _script
        self.evaluate_calls += 1
        return {
            "og_title": self.dom.og_title,
            "document_title": self.dom.document_title,
            "h1": self.dom.h1 or "",
        }


class _DownloadLocator:
    def __init__(self, href: str):
        self.href = href

    def get_attribute(self, name):
        return self.href if name == "href" else None


_PAYLOAD = b"already present"


def _runner_and_page(clean_workdir, *, html: str, filename: str, url: str):
    from bulk_downloader.db import db_init
    from bulk_downloader.migrations import apply_pending
    from bulk_downloader.runner import SiteRunner

    db_init()
    result = apply_pending(backup_first=False)
    assert result["errors"] == 0, result

    download_dir = clean_workdir / "downloads"
    download_dir.mkdir()
    target = download_dir / filename

    runner = SiteRunner(
        "ultrafilms",
        {
            "name": "UltraFilms",
            "download_dir": str(download_dir),
            "filename_template": "{filename}",
            "skip_if_exists": True,
            # This fixture stands in for the transfer itself, so ffprobe and
            # hash verification -- separate contracts, and host-dependent --
            # must not decide whether a completion is recorded. Neither ran
            # before either: the destination used to be pre-created so the
            # skip_if_exists branch stood in for the download, and that branch
            # returned before both checks.
            "verify_integrity": False,
            "verify_hash": False,
            "use_http_dl": True,
            "learned": {
                "download": {
                    "row_selectors": ["a.download"],
                    "url_attribute": "href",
                }
            },
        },
    )

    # THE TRANSFER, faked at the seam. These tests are about what a COMPLETED
    # download records, and they used to obtain a completion by pre-creating
    # the destination and letting the "already have" branch mark the job done.
    # That branch now refuses to claim a completion it cannot attribute -- a
    # file of unknown provenance is UNKNOWN, not this scene -- so the fixture
    # has to supply a real completion instead of borrowing a skip. Every
    # assertion below is unchanged; only the way the bytes arrive is.
    def _http_download(page_url, page_, ctx_, file_url, final_path):
        Path(final_path).parent.mkdir(parents=True, exist_ok=True)
        Path(final_path).write_bytes(_PAYLOAD)
        return len(_PAYLOAD), len(_PAYLOAD)

    def _pw_save(dl, final_path):  # pragma: no cover - pins the HTTP arm
        raise AssertionError("the browser arm ran; this fixture pins the HTTP arm")

    runner._http_download = _http_download
    runner._pw_save = _pw_save

    page = _FixturePage(url, html)
    best = {
        "locator": _DownloadLocator(f"https://cdn.ultrafilms.example/{filename}"),
        "text": "Download 1080p",
        "score": 1080,
        # 0 keeps the Phase 17.20 size-sanity check inert, exactly as the
        # 15-byte pre-created file did (it only fires above 1MB advertised).
        "size": 0,
        "_via_learned": True,
        "_learned_sel": "a.download",
        "_all_candidates": [],
    }
    return runner, page, best, download_dir, target


def _stop_runner(runner):
    try:
        runner.stop()
        runner._stop_auto_retry()
    except Exception:
        pass


def _history_client():
    from flask import Flask
    from bulk_downloader.app_history import register_routes

    app = Flask(__name__)
    assert register_routes(app) == 3
    return app.test_client()


def test_completed_download_records_ultrafilms_og_title_and_source(clean_workdir):
    html = f"""<!doctype html>
    <html><head>
      <title>{_RAW_TITLE}</title>
      <meta property="og:title" content="{_RAW_TITLE}">
    </head><body><div class="movie-player"></div></body></html>"""
    runner, page, best, download_dir, target = _runner_and_page(
        clean_workdir, html=html, filename="download-server-name.mp4", url=_SCENE_URL
    )

    # PRECONDITION: this is the measured UltraFilms shape. An h1-bearing
    # fixture would exercise a different branch and prove nothing about it.
    assert page.dom.og_title == _RAW_TITLE
    assert page.dom.document_title == _RAW_TITLE
    assert page.dom.h1 is None

    try:
        # A distinct detail page proves which leading segments repeat. This is
        # harvested through the same production DOM path, not injected into
        # the learner's private observation map.
        other_html = (
            f"<html><head><title>{_OTHER_RAW_TITLE}</title>"
            f'<meta property="og:title" content="{_OTHER_RAW_TITLE}">'
            "</head><body></body></html>"
        )
        other_page = _FixturePage(_OTHER_SCENE_URL, other_html)
        assert runner._capture_website_title(
            other_page, _OTHER_SCENE_URL
        ) == (_OTHER_RAW_TITLE, "og:title")
        assert other_page.evaluate_calls == 1
        runner._do_download(page, None, _SCENE_URL, best, download_dir, "1080p")
    finally:
        _stop_runner(runner)

    from bulk_downloader.db import db_conn

    with db_conn() as cx:
        history_rows = [dict(row) for row in cx.execute(
            "SELECT * FROM history ORDER BY id"
        ).fetchall()]
        library_rows = [dict(row) for row in cx.execute(
            "SELECT * FROM library ORDER BY id"
        ).fetchall()]

    assert len(history_rows) == 1
    assert len(library_rows) == 1
    assert library_rows[0]["title"] == _WEBSITE_TITLE
    assert library_rows[0]["title"] != target.stem
    assert library_rows[0]["title_source"] == "og:title"
    with db_conn() as cx:
        counts = dict(cx.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(title <> '') AS titled, "
            "SUM(title_source = 'og:title') AS og_sourced "
            "FROM library"
        ).fetchone())
    assert counts == {"total": 1, "titled": 1, "og_sourced": 1}
    assert page.evaluate_calls == 1

    api_rows = _history_client().get("/api/history").get_json()
    assert len(api_rows) == 1
    assert api_rows[0]["filename"] == target.name
    assert api_rows[0]["title"] == _WEBSITE_TITLE
    assert api_rows[0]["title_source"] == "og:title"

    from bulk_downloader import library_final

    regen = library_final.regen_nfos_from_history(
        download_dir=str(download_dir), dry_run=False
    )
    assert regen == {
        "written": 1,
        "skipped": 0,
        "missing_files": 0,
        "errors": 0,
        "ambiguous": 0,
        "unknown": 0,
    }
    nfo = target.with_suffix(".nfo").read_text(encoding="utf-8")
    assert f"<title>{_WEBSITE_TITLE}</title>" in nfo
    assert f"<sorttitle>{_WEBSITE_TITLE}</sorttitle>" in nfo
    assert f"<title>{target.stem}</title>" not in nfo


def test_new_repeated_template_retroactively_normalizes_first_completed_scene(
    clean_workdir,
):
    html = (
        f"<html><head><title>{_RAW_TITLE}</title>"
        f'<meta property="og:title" content="{_RAW_TITLE}">'
        "</head><body></body></html>"
    )
    runner, page, best, download_dir, target = _runner_and_page(
        clean_workdir,
        html=html,
        filename="cold-start-server-name.mp4",
        url=_SCENE_URL,
    )
    try:
        # Before another scene repeats the prefix, stripping it would violate
        # the conservative template rule. The first completion records raw.
        runner._do_download(
            page, None, _SCENE_URL, best, download_dir, "1080p"
        )
        from bulk_downloader.db import db_conn

        with db_conn() as cx:
            before = [dict(row) for row in cx.execute(
                "SELECT title, title_source FROM library"
            ).fetchall()]
        assert before == [{"title": _RAW_TITLE, "title_source": "og:title"}]

        # The next distinct scene supplies the required repeated evidence. The
        # learner safely enriches the prior library row only while its title
        # still equals the raw harvested value (operator edits therefore win).
        other_html = (
            f"<html><head><title>{_OTHER_RAW_TITLE}</title>"
            f'<meta property="og:title" content="{_OTHER_RAW_TITLE}">'
            "</head><body></body></html>"
        )
        other_page = _FixturePage(_OTHER_SCENE_URL, other_html)
        assert runner._capture_website_title(
            other_page, _OTHER_SCENE_URL
        ) == ("Another Scene", "og:title")

        with db_conn() as cx:
            after = [dict(row) for row in cx.execute(
                "SELECT title, title_source FROM library"
            ).fetchall()]
            counts = dict(cx.execute(
                "SELECT COUNT(*) AS total, SUM(title = ?) AS normalized, "
                "SUM(title_source = 'og:title') AS og_sourced FROM library",
                (_WEBSITE_TITLE,),
            ).fetchone())
        assert after == [{
            "title": _WEBSITE_TITLE,
            "title_source": "og:title",
        }]
        assert counts == {"total": 1, "normalized": 1, "og_sourced": 1}
        assert page.evaluate_calls == 1
        assert other_page.evaluate_calls == 1
        assert runner._history_title_fields(_SCENE_URL) == {
            "title": _WEBSITE_TITLE,
            "title_source": "og:title",
        }
        assert target.exists()
    finally:
        _stop_runner(runner)


def test_page_with_no_title_keeps_empty_storage_and_uses_nfo_fallback(clean_workdir):
    html = "<!doctype html><html><head></head><body><div>video</div></body></html>"
    url = "https://members.ultrafilms.example/members/content/item/untitled"
    runner, page, best, download_dir, target = _runner_and_page(
        clean_workdir, html=html, filename="opaque-download-name.mp4", url=url
    )
    runner._website_title_observations = {}

    # NEGATIVE-CONTROL PRECONDITION: every title source is genuinely empty.
    assert page.dom.og_title == ""
    assert page.dom.document_title == ""
    assert page.dom.h1 is None
    assert runner._listing_titles.get(url, "") == ""

    try:
        runner._do_download(page, None, url, best, download_dir, "1080p")
    finally:
        _stop_runner(runner)

    from bulk_downloader.db import db_conn

    with db_conn() as cx:
        rows = [dict(row) for row in cx.execute(
            "SELECT * FROM library ORDER BY id"
        ).fetchall()]
        counts = dict(cx.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(title <> '') AS titled, "
            "SUM(title_source <> '') AS sourced FROM library"
        ).fetchone())
    assert len(rows) == 1
    assert rows[0]["title"] == ""
    assert rows[0]["title"] != target.stem
    assert rows[0]["title_source"] == ""
    assert counts == {"total": 1, "titled": 0, "sourced": 0}
    assert page.evaluate_calls == 1

    from bulk_downloader import library_final

    regen = library_final.regen_nfos_from_history(
        download_dir=str(download_dir), dry_run=False
    )
    assert regen == {
        "written": 1,
        "skipped": 0,
        "missing_files": 0,
        "errors": 0,
        "ambiguous": 0,
        "unknown": 0,
    }
    nfo = target.with_suffix(".nfo").read_text(encoding="utf-8")
    assert f"<title>{target.stem}</title>" in nfo
    assert f"<sorttitle>{target.stem}</sorttitle>" in nfo


def test_source_resolution_order_is_exact():
    from bulk_downloader.website_title import harvest_page_title

    cases = [
        (
            "<html><head><title>Document</title>"
            '<meta property="og:title" content="Open Graph"></head>'
            "<body><h1>Heading</h1></body></html>",
            "Listing",
            ("Open Graph", "og:title"),
        ),
        (
            "<html><head><title>Document</title></head>"
            "<body><h1>Heading</h1></body></html>",
            "Listing",
            ("Document", "document.title"),
        ),
        (
            "<html><head></head><body><h1>Heading</h1></body></html>",
            "Listing",
            ("Heading", "h1"),
        ),
        (
            "<html><head></head><body></body></html>",
            "Listing",
            ("Listing", "listing_card"),
        ),
    ]
    assert len(cases) == 4
    for index, (html, listing_title, expected) in enumerate(cases):
        page = _FixturePage(f"https://example.invalid/{index}", html)
        assert harvest_page_title(page, listing_title=listing_title) == expected
        assert page.evaluate_calls == 1


def test_retry_reharvests_a_new_page_but_not_the_same_page(clean_workdir):
    empty_html = "<html><head></head><body></body></html>"
    runner, first_page, _best, _download_dir, _target = _runner_and_page(
        clean_workdir,
        html=empty_html,
        filename="unused-retry-target.mp4",
        url=_SCENE_URL,
    )
    try:
        assert runner._capture_website_title(first_page, _SCENE_URL) == ("", "")
        assert runner._capture_website_title(first_page, _SCENE_URL) == ("", "")
        assert first_page.evaluate_calls == 1

        settled_html = (
            f"<html><head><title>{_RAW_TITLE}</title>"
            f'<meta property="og:title" content="{_RAW_TITLE}">'
            "</head><body></body></html>"
        )
        retry_page = _FixturePage(_SCENE_URL, settled_html)
        assert runner._capture_website_title(
            retry_page, _SCENE_URL
        ) == (_RAW_TITLE, "og:title")
        assert runner._capture_website_title(
            retry_page, _SCENE_URL
        ) == (_RAW_TITLE, "og:title")
        assert retry_page.evaluate_calls == 1
    finally:
        _stop_runner(runner)


def test_only_a_repeated_site_template_is_stripped_and_a_real_dash_survives():
    from bulk_downloader.website_title import strip_repeated_title_template

    assert strip_repeated_title_template(
        _RAW_TITLE,
        {_OTHER_SCENE_URL: _OTHER_RAW_TITLE},
    ) == _WEBSITE_TITLE

    legitimate = "Anna - A Weekend In Paris"
    assert strip_repeated_title_template(
        legitimate,
        {"https://example.invalid/other": "Bea - Midnight Drive"},
    ) == legitimate


def test_listing_card_title_is_extracted_and_survives_queue_restart(
    clean_workdir, monkeypatch,
):
    from bulk_downloader import cloak, session_keeper
    from bulk_downloader.db import db_conn, db_init
    from bulk_downloader.migrations import apply_pending
    from bulk_downloader.runner import SiteRunner

    db_init()
    result = apply_pending(backup_first=False)
    assert result["errors"] == 0, result

    listing_url = "https://members.example.invalid/playlist/latest"
    url = "https://members.example.invalid/scene/listing-fallback"
    listing_title = "The Name On The Listing Card"

    class _ListingPage:
        def __init__(self):
            self.goto_urls = []
            self.evaluate_calls = 0

        def goto(self, target, **_kwargs):
            self.goto_urls.append(target)

        def evaluate(self, script):
            # Prove the production DOM bridge asks for each declared card-name
            # surface before returning the fixture's browser-shaped payload.
            assert "getAttribute('aria-label')" in script
            assert "getAttribute('title')" in script
            assert "textContent" in script
            self.evaluate_calls += 1
            return [
                {"url": f"{url}#player", "title": f"  {listing_title}  "},
                {
                    "url": "https://members.example.invalid/category/not-a-scene",
                    "title": "Navigation",
                },
            ]

    listing_page = _ListingPage()

    @contextmanager
    def _fake_cloaked_page(**_kwargs):
        yield listing_page

    monkeypatch.setattr(cloak, "cloaked_page", _fake_cloaked_page)
    monkeypatch.setattr(session_keeper, "pause_site_keepers", lambda _site_id: None)
    config = {
        "name": "Listing fallback",
        "download_dir": str(clean_workdir / "downloads"),
        "use_playlist_extractor": True,
        "playlist_scene_link_selector": ".scene-card",
        "playlist_url_patterns": [r"/playlist/"],
        "url_patterns": [r"/scene/"],
    }
    first = SiteRunner("listing-fallback", config)
    try:
        loaded = first.load_urls([listing_url])
        assert loaded == (1, 0, 0)
        assert listing_page.goto_urls == [listing_url]
        assert listing_page.evaluate_calls == 1
        assert len(first.urls) == 1
        assert len(first.jobs) == 1
        assert first.urls == [url]
        assert first.jobs[url]["listing_title"] == listing_title
        with db_conn() as cx:
            queued = [dict(row) for row in cx.execute(
                "SELECT url, listing_title FROM queue WHERE site_id=?",
                (first.site_id,),
            ).fetchall()]
        assert queued == [{
            "url": url,
            "listing_title": listing_title,
        }]
    finally:
        _stop_runner(first)

    restored = SiteRunner("listing-fallback", config)
    try:
        assert len(restored.urls) == 1
        assert len(restored.jobs) == 1
        assert restored.jobs[url]["listing_title"] == listing_title
        assert restored._history_title_fields(url) == {
            "title": listing_title,
            "title_source": "listing_card",
        }
    finally:
        _stop_runner(restored)


def test_plugin_download_without_detail_page_uses_listing_fallback(clean_workdir):
    from pathlib import Path

    from bulk_downloader import plugins
    from bulk_downloader.db import db_conn, db_init
    from bulk_downloader.migrations import apply_pending
    from bulk_downloader.runner import SiteRunner

    db_init()
    result = apply_pending(backup_first=False)
    assert result["errors"] == 0, result

    url = "https://members.example.invalid/scene/plugin-download"
    listing_title = "Website Card Scene Name"
    download_dir = clean_workdir / "downloads"
    runner = SiteRunner(
        "plugin-title",
        {"name": "Plugin Title", "download_dir": str(download_dir)},
    )
    runner._listing_titles[url] = listing_title
    assert runner.load_urls([url]) == (1, 0, 0)

    def _direct_download(*, page_url, file_url, output_path, referer=""):
        assert page_url == url
        assert file_url == "https://cdn.example.invalid/server-object.mp4"
        assert referer == url
        Path(output_path).write_bytes(b"plugin payload")
        return True

    runner._do_direct_http_download = _direct_download
    plugins.register_extractor(
        runner.site_id,
        lambda _url, _context: {
            "video_url": "https://cdn.example.invalid/server-object.mp4",
            "title": "opaque-plugin-filename",
            "ext": "mp4",
        },
    )
    try:
        assert runner._try_plugin_extractor(url) is True
        with db_conn() as cx:
            rows = [dict(row) for row in cx.execute(
                "SELECT h.status, h.filename, l.title, l.title_source "
                "FROM history h JOIN library l ON l.id=h.library_id"
            ).fetchall()]
            counts = dict(cx.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(l.title <> h.filename) AS distinct_names, "
                "SUM(l.title_source = 'listing_card') AS listing_sourced "
                "FROM history h JOIN library l ON l.id=h.library_id"
            ).fetchone())
        assert rows == [{
            "status": "done",
            "filename": "opaque-plugin-filename.mp4",
            "title": listing_title,
            "title_source": "listing_card",
        }]
        assert counts == {
            "total": 1,
            "distinct_names": 1,
            "listing_sourced": 1,
        }
    finally:
        plugins.unregister_extractor(runner.site_id)
        _stop_runner(runner)


def test_v9_library_upgrade_adds_source_without_backfilling_titles(clean_workdir):
    from bulk_downloader.db import db_conn, db_init
    from bulk_downloader.migrations import _m10

    db_init()
    with db_conn() as cx:
        cx.execute(
            "CREATE TABLE library("
            "id INTEGER PRIMARY KEY, file_path TEXT UNIQUE, "
            "title TEXT DEFAULT '')"
        )
        cx.executemany(
            "INSERT INTO library(id, file_path, title) VALUES(?,?,?)",
            [
                (1, "/downloads/opaque-one.mp4", ""),
                (2, "/downloads/opaque-two.mp4", "Known Website Name"),
            ],
        )
        _m10(cx)
        _m10(cx)  # The live-upgrade step remains idempotent.
        source_columns = [
            row[1]
            for row in cx.execute("PRAGMA table_info(library)").fetchall()
            if row[1] == "title_source"
        ]
        rows = [dict(row) for row in cx.execute(
            "SELECT id, title, title_source FROM library ORDER BY id"
        ).fetchall()]
        counts = dict(cx.execute(
            "SELECT COUNT(*) AS total, SUM(title <> '') AS titled, "
            "SUM(title_source <> '') AS sourced FROM library"
        ).fetchone())

    assert source_columns == ["title_source"]
    assert rows == [
        {"id": 1, "title": "", "title_source": ""},
        {"id": 2, "title": "Known Website Name", "title_source": ""},
    ]
    assert counts == {"total": 2, "titled": 1, "sourced": 0}


def test_completion_enriches_a_library_row_that_the_scanner_created_first(
    clean_workdir,
):
    from bulk_downloader.db import db_conn, db_init, db_log, db_search
    from bulk_downloader.library import library_record
    from bulk_downloader.migrations import apply_pending

    db_init()
    result = apply_pending(backup_first=False)
    assert result["errors"] == 0, result

    target = clean_workdir / "opaque-file-name.mp4"
    target.write_bytes(b"existing scanner file")
    scanned_id = library_record(str(target))
    assert isinstance(scanned_id, int)

    db_log(
        "scanner-first",
        "Scanner First",
        "https://example.invalid/scene/scanner-first",
        "done",
        filename=target.name,
        file_size=target.stat().st_size,
        file_path=str(target),
        title="The Website Scene Name",
        title_source="document.title",
    )

    with db_conn() as cx:
        history_rows = [dict(row) for row in cx.execute(
            "SELECT * FROM history ORDER BY id"
        ).fetchall()]
        library_rows = [dict(row) for row in cx.execute(
            "SELECT * FROM library ORDER BY id"
        ).fetchall()]
    assert len(history_rows) == 1
    assert len(library_rows) == 1
    assert library_rows[0]["id"] == scanned_id
    assert history_rows[0]["library_id"] == scanned_id
    assert library_rows[0]["title"] == "The Website Scene Name"
    assert library_rows[0]["title_source"] == "document.title"

    search_rows = db_search(site_id="scanner-first", limit=10)
    assert len(search_rows) == 1
    assert search_rows[0]["filename"] == target.name
    assert search_rows[0]["title"] == "The Website Scene Name"
    assert search_rows[0]["title_source"] == "document.title"
