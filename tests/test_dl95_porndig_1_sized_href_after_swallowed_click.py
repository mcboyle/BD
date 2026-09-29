"""dl95-porndig-1: a sized download link whose click is swallowed must still download over HTTP.

MEASURED on test2 2026-09-28 (download-95/A5-A/RESULT-porndig.md D1, _review/nr3/NR3__PD.png): porndig 242142
ended needs_review "no dl event; scored ok but no download fired; saw: 4K(911.0 MB) ... 1080p(238.0 MB) ...".
The winner was ``<a class="post_download_link clearfix agepass_check" href="https://videos.porndig.com/download/
index/551568/523/<token>/22/porndig.com_<slug>_UHD4K">UHD 4K : 911 MB</a>``: right tier, right size. The page's
age-pass script consumes the click, so Playwright sees no download event; the href has no media extension, so
the direct-media route never claimed it. A plain GET of that href answers 302 -> 206 video/mp4 (ftypisom),
measured the same hour (PLAN-2040/dl95-porndig-1-negctl/hdrs.txt).

Fix: after the click, popup-grant and revealed-modal paths all come back empty, a winner that ADVERTISED A SIZE
and carries an absolute http(s) href is fetched over the HTTP leg. Unsized or href-less winners still end in
needs_review. Hosts are ``.example``; every request is route-intercepted and no byte leaves the test.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from bulk_downloader import runner_transport as transport

BD_GATE_SCOPE = "module"

_PAGE = "https://www.tube2.example/videos/242142/requested-title.html"
_HREF_1080 = "https://videos.tube2.example/download/index/551568/523/tok/13/tube2.example_requested-title_1080p"

_HTML = f"""<!doctype html><html><head><title>Requested title</title></head><body>
<div class="downloads">
  <a class="post_download_link agepass_check q1080" href="{_HREF_1080}">
    <span class="link_name">1080p :&nbsp;</span><span class="file_size">238 MB</span></a>
  <a class="post_download_link agepass_check qhash" href="#">
    <span class="link_name">720p :&nbsp;</span><span class="file_size">114 MB</span></a>
</div>
<script>
  // the site's age-pass gate: every download click is consumed, nothing navigates or downloads
  document.addEventListener('click', e => {{
    if (e.target.closest('.agepass_check')) {{ e.preventDefault(); e.stopPropagation(); }}
  }}, true);
</script>
</body></html>"""


class _Runner(transport.TransportMixin):
    _FIRST_DOWNLOAD_TIMEOUT_MS = 1500

    def __init__(self):
        self.site_id = "dl95porndig1"
        self.config = {
            "name": "porndig-fixture",
            "use_http_dl": True,
            "verify_hash": False,
            "verify_integrity": False,
        }
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.jobs = {}
        self.log = logging.getLogger("dl95porndig1")
        self.failures = []
        self.updates = []
        self.fetched = []

    def _probe_for_higher_tier(self, url, **_kwargs):
        return url

    def _build_mirror_urls(self, _url):
        return []

    def _run_http_attempts_with_resume(
        self, page_url, page, ctx, file_url, attempt_urls, final_path
    ):
        self.fetched.append(file_url)
        Path(final_path).write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64)
        size = Path(final_path).stat().st_size
        return size, size

    def _update_job(self, url, status, message="", **kwargs):
        self.updates.append((status, message))

    def _handle_failure(self, url, message):
        self.failures.append((url, message))

    def _size_on_disk_after_tagging(self, _path, downloaded_size):
        return downloaded_size

    def _screenshot(self, *_args, **_kwargs):
        return ""


@contextmanager
def _page():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            ctx = br.new_context(accept_downloads=True)
            pg = ctx.new_page()
            pg.route(
                "**/*",
                lambda route: (
                    route.fulfill(status=200, content_type="text/html", body=_HTML)
                    if route.request.url == _PAGE
                    else route.abort()
                ),
            )
            pg.goto(_PAGE, wait_until="domcontentloaded")
            yield ctx, pg
        finally:
            br.close()


@pytest.fixture
def _hermetic(monkeypatch):
    monkeypatch.setattr(transport, "_HTTPX_AVAILABLE", True)
    monkeypatch.setattr(transport, "db_skip_identity", lambda *_a: ("different", ""))
    monkeypatch.setattr(transport, "db_log", lambda *_a, **_k: None)
    monkeypatch.setattr(
        transport.staging_claim,
        "reserve",
        lambda path, _identity: (path, path.with_suffix(".part")),
    )
    monkeypatch.setattr(transport.staging_claim, "release", lambda *_a: None)


def _run(tmp_path, selector, size):
    runner = _Runner()
    with _page() as (ctx, pg):
        best = {
            "locator": pg.locator(selector).first,
            "score": 1080,
            "size": size,
            "text": "1080p : 238 MB",
        }
        runner._do_download(pg, ctx, _PAGE, best, Path(tmp_path), "1080p")
    return runner


def test_sized_winner_with_swallowed_click_is_fetched_over_http(tmp_path, _hermetic):
    # under 1 MB so the size-sanity gate (Phase 17.20) does not judge the 76-byte fixture file
    runner = _run(tmp_path, "a.q1080", 700 * 1024)
    statuses = [s for s, _m in runner.updates]
    assert runner.fetched == [_HREF_1080], (
        f"DL95_PORNDIG_NO_HTTP_FALLBACK: the sized winner's href was never fetched; job updates={runner.updates!r}"
    )
    assert "needs_review" not in statuses and statuses[-1] == "done", runner.updates
    saved = [p.name for p in Path(tmp_path).iterdir() if p.is_file()]
    assert saved == ["tube2.example_requested-title_1080p.mp4"], saved
    assert runner.failures == []


def test_control_unsized_winner_still_needs_review(tmp_path, _hermetic):
    runner = _run(tmp_path, "a.q1080", 0)
    assert runner.fetched == []
    assert runner.updates and runner.updates[-1][0] == "needs_review", runner.updates


def test_control_sized_winner_without_a_real_href_still_needs_review(
    tmp_path, _hermetic
):
    runner = _run(tmp_path, "a.qhash", 114 * 1024 * 1024)
    assert runner.fetched == []
    assert runner.updates and runner.updates[-1][0] == "needs_review", runner.updates


@pytest.mark.parametrize(
    ("href", "size", "expected"),
    [
        (
            _HREF_1080,
            5,
            (
                "https://videos.tube2.example/download/index/551568/523/tok/13/",
                "_1080p.mp4",
            ),
        ),
        ("/dl/clip.webm", 5, ("https://www.tube2.example/dl/clip.webm", "clip.webm")),
        (_HREF_1080, 0, None),
        ("javascript:void(0)", 5, None),
        ("#", 5, None),  # a same-page fragment would "download" the page itself
        ("", 5, None),
    ],
)
def test_sized_href_rule(href, size, expected):
    rule = getattr(transport, "_sized_href_download", None)
    assert rule is not None, (
        "DL95_PORNDIG_NO_RULE: runner_transport has no sized-href fallback"
    )
    got = rule({"size": size}, href, _PAGE)
    if expected is None:
        assert got is None
    else:
        assert got.url.startswith(expected[0]) and got.suggested_filename.endswith(
            expected[1]
        ), (
            got.url,
            got.suggested_filename,
        )
