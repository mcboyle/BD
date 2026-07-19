"""v3.66.146 — detection-safety regression tests.

Covers:
  #3 Navigation URL Rejection Engine — candidate_filter rejects nav/listing/
     account paths (and the homepage) as download candidates, while a strong
     media/download signal still overrides, and real reviewed download URLs
     are accepted.
  #4 Selector Safety Linter — generic/nav selectors are flagged (error for row
     roles), scoped selectors pass, and the shipped reptyle reviewed template
     carries no blocking row-selector issues (the "no generic a[href] as a row
     selector" requirement).
"""
from __future__ import annotations

import bulk_downloader.candidate_filter as cf
from bulk_downloader import selector_lint as sl
from bulk_downloader.template_registry import find_template_for_url


HOST = "app.reptyle.com"


def _classify(url, **kw):
    kw.setdefault("selector", "a")
    kw.setdefault("tag", "a")
    kw.setdefault("page_host", HOST)
    return cf.classify(url=url, **kw)


# ── #3 navigation URL rejection ──────────────────────────────────────────

def test_homepage_rejected():
    v = _classify("https://app.reptyle.com/")
    assert v.accepted is False and "homepage link" in v.rejections


def test_known_nav_paths_rejected():
    nav = [
        "/movies", "/models", "/series", "/search", "/settings", "/logout",
        "/categories", "/deals", "/login", "/account", "/browse",
        "/playlists", "/movies/", "/deals?ref=top",
    ]
    for p in nav:
        v = _classify(f"https://app.reptyle.com{p}")
        assert v.accepted is False, f"{p} should be rejected"
        assert ("navigation URL" in v.rejections
                or "homepage link" in v.rejections), f"{p}: {v.rejections}"


def test_strong_signal_overrides_nav_path():
    # A media file under a /videos prefix is still a real download.
    v = _classify("https://cdn.reptyle.com/videos/clip-1080p.mp4",
                  page_host=HOST)
    assert v.accepted is True and v.kind == "download"
    assert "media_extension" in v.positive_signals


def test_real_reviewed_download_url_accepted():
    # Singular /movie/<id>/download-resolution/<res> is NOT nav and carries a
    # strong download/api signal.
    v = cf.classify(
        url="https://api2.reptyle.com/api/v1/movie/123/download-resolution/2160",
        text="2160p", ancestor_text="Download",
        selector='[role="dialog"] a[href*="download-resolution"]',
        tag="a", page_host=HOST)
    assert v.accepted is True and v.kind == "download"


def test_singular_content_path_not_treated_as_nav_listing():
    # /movie/<id> (no download signal) is rejected for lacking a signal, but
    # NOT mislabelled as a plural-listing nav match.
    v = _classify("https://app.reptyle.com/movie/123")
    assert v.accepted is False
    assert "navigation URL" not in v.rejections  # it's "no download signal"


# ── #4 selector safety linter ────────────────────────────────────────────

def test_generic_row_selectors_are_errors():
    for s in ("a[href]", "[href]", "a", "*", "button", ".btn", "body a"):
        issues = sl.lint_selector(s, role="row")
        assert sl.has_blocking_issues(issues), f"{s!r} should be a row error"


def test_scoped_anchor_is_safe():
    issues = sl.lint_selector('[role="dialog"] a[href*="download" i]', role="row")
    assert not sl.has_blocking_issues(issues)
    issues = sl.lint_selector('.ant-modal a[href*="download-resolution" i]', role="row")
    assert not sl.has_blocking_issues(issues)


def test_bare_button_is_warn_as_trigger_not_error():
    issues = sl.lint_selector("button", role="trigger")
    assert issues and not sl.has_blocking_issues(issues)
    assert issues[0].level == "warn"


def test_nav_selector_flagged():
    issues = sl.lint_selector(".navbar a", role="row")
    assert sl.has_blocking_issues(issues)
    assert any(i.code == "nav_selector" for i in issues)


def test_reptyle_reviewed_template_has_no_blocking_row_selectors():
    t = find_template_for_url("https://app.reptyle.com/")
    assert t is not None
    issues = sl.lint_template(t)
    blocking = [i.to_dict() for i in issues if i.level == "error"]
    assert blocking == [], f"reptyle template has unsafe selectors: {blocking}"


def test_lint_template_catches_a_bad_row_selector():
    bad = {"selectors": {"download": {"row_selectors": ["a[href]"]}}}
    assert sl.has_blocking_issues(sl.lint_template(bad))


def test_lint_learned_block():
    issues = sl.lint_learned({"row_selectors": ["a[href]"],
                              "trigger_selectors": ['[aria-label*="quality"]']})
    assert sl.has_blocking_issues(issues)  # the row selector is unsafe
