"""v3.66.146 — runtime nav-gate regression tests.

These exercise the ACTUAL runtime download path (the gate function the picker
calls, plus _do_download's routing), not just candidate_filter.classify():

  * runner/detect cannot accept href="/" (or any nav/account/search URL) as a
    direct download — it is refused before filename generation / download.bin.
  * a valid download-resolution / media URL is still accepted.
  * URL-less click-targets are NOT gated (they fall through to expect_download
    — unknown-site fallback stays working).
  * the reviewed reptyle modal-scoped row URL is accepted.
"""
from __future__ import annotations

import bulk_downloader.runner as bd_runner
import bulk_downloader.runner_transport as bd_runner_transport
from bulk_downloader.runner import gate_candidate_url, SiteRunner


HOST_PAGE = "https://app.reptyle.com/movie/123"


class FakeLocator:
    def __init__(self, attrs):
        self._a = dict(attrs)

    def get_attribute(self, name):
        return self._a.get(name)


class FakePage:
    def __init__(self, url):
        self.url = url


# ── pure gate: rejections (the runtime decision, not classify()) ─────────

def test_href_root_rejected_as_direct_download():
    abs_url, reason = gate_candidate_url(
        FakeLocator({"href": "/"}), "https://app.reptyle.com/")
    assert reason, "href='/' must be refused at the runtime gate"
    assert "homepage link" in reason


def test_absolute_homepage_rejected():
    abs_url, reason = gate_candidate_url(
        FakeLocator({"href": "https://app.reptyle.com/"}), HOST_PAGE)
    assert reason and abs_url == "https://app.reptyle.com/"


def test_nav_paths_rejected_at_runtime():
    for path in ("/movies", "/models", "/series", "/search", "/settings",
                 "/logout", "/categories", "/deals", "/account"):
        _, reason = gate_candidate_url(
            FakeLocator({"href": path}), "https://app.reptyle.com/x")
        assert reason, f"{path} must be refused at the runtime gate"


def test_data_href_nav_rejected():
    _, reason = gate_candidate_url(
        FakeLocator({"data-href": "/settings"}), HOST_PAGE)
    assert reason


def test_learned_url_attr_takes_priority_and_is_gated():
    # When the site has a learned url_attribute, that attr is read first.
    _, reason = gate_candidate_url(
        FakeLocator({"data-href": "/", "href": "/movie/123/x.mp4"}),
        HOST_PAGE, url_attr="data-href")
    assert reason  # data-href="/" is the gated value, not the .mp4 href


# ── pure gate: acceptances (must NOT over-reject) ────────────────────────

def test_valid_download_resolution_url_accepted():
    abs_url, reason = gate_candidate_url(
        FakeLocator({"href":
                     "/api/v1/movie/123/download-resolution/2160"}),
        "https://api2.reptyle.com/x", text="2160p")
    assert reason == "", f"valid download URL must pass, got {reason!r}"


def test_valid_media_extension_accepted():
    _, reason = gate_candidate_url(
        FakeLocator({"data-src": "https://cdn.reptyle.com/v/clip-1080p.mp4"}),
        HOST_PAGE)
    assert reason == ""


def test_reptyle_modal_row_url_accepted():
    abs_url, reason = gate_candidate_url(
        FakeLocator({"href":
                     "https://api2.reptyle.com/api/v1/movie/9/download-resolution/1080"}),
        HOST_PAGE, learned_sel='[role="dialog"] a[href*="download-resolution"]',
        text="1080p")
    assert reason == ""


def test_urlless_click_target_not_gated():
    # A bare button with no href/data-* — must fall through (reason="") so the
    # caller can click + expect_download. NOT rejected.
    abs_url, reason = gate_candidate_url(
        FakeLocator({"aria-label": "Download Full Movie"}), HOST_PAGE)
    assert abs_url == "" and reason == ""


def test_fail_open_on_bad_locator():
    class Boom:
        def get_attribute(self, name):
            raise RuntimeError("boom")
    abs_url, reason = gate_candidate_url(Boom(), HOST_PAGE)
    assert abs_url == "" and reason == ""   # never costs a real download


# ── method-level: _do_download routes a nav URL to needs_review ──────────

def test_do_download_routes_nav_url_to_needs_review():
    from bulk_downloader.db import db_init
    db_init()  # create SQLite tables (queue/history/...) the runner reads
    r = SiteRunner("test_navgate_146", {"name": "navgate"})
    captured = {}
    # Direct instance assignment shadows the real (DB-backed) methods reliably.
    r._screenshot = lambda page, url: ""
    r._update_job = (lambda url, status, msg, screenshot=None:
                     captured.update(status=status, msg=msg))
    _orig_db_log = bd_runner_transport.db_log
    bd_runner_transport.db_log = lambda *a, **k: None
    try:
        best = {"locator": FakeLocator({"href": "/"}), "text": "Home",
                "_via_learned": False, "score": 5, "size": 0,
                "_all_candidates": []}
        # ctx/dl_dir/res_lbl are unused before the gate fires.
        r._do_download(FakePage("https://app.reptyle.com/"), None,
                       "https://app.reptyle.com/", best, None, "")
    finally:
        bd_runner_transport.db_log = _orig_db_log
        try:
            r.stop(); r._stop_auto_retry()
        except Exception:
            pass

    assert captured.get("status") == "needs_review"
    assert "homepage link" in captured.get("msg", "")
    # And it must NOT have produced download.bin (returned before download).
