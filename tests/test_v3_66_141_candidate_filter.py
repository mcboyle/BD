"""v3.66.x — candidate_filter: reject non-download links, require positive
site-provided media/download signals."""
from bulk_downloader import candidate_filter as cf


def _acc(**kw):
    return cf.classify(**kw)


# ── positive signals are accepted as downloads ───────────────────────────

def test_media_extension_accepted():
    v = _acc(url="/get/123/8k.mp4", text="Download", selector="a.dl[href$='.mp4']")
    assert v.accepted and v.kind == "download"
    assert "media_extension" in v.positive_signals


def test_manifest_url_accepted():
    assert _acc(url="https://cdn.site.com/hls/master.m3u8").accepted
    assert _acc(url="https://cdn.site.com/dash/manifest.mpd").accepted


def test_download_path_accepted():
    assert _acc(url="https://site.com/download/9981", text="Get file").accepted
    assert _acc(url="https://site.com/videoplayback?id=9").accepted


def test_resolution_label_accepted():
    v = _acc(url="https://site.com/v/9?q=1080p", text="1080p")
    assert v.accepted and "resolution_label" in v.positive_signals


def test_api_pattern_accepted():
    assert _acc(url="https://site.com/api/v2/media/source?id=9").accepted


# ── hard rejections ───────────────────────────────────────────────────────

def test_generic_href_only_selector_rejected():
    v = _acc(url="https://site.com/somepage", selector="a[href]")
    assert not v.accepted
    assert "generic href-only selector" in v.rejections


def test_bare_bracket_href_selector_rejected():
    assert not _acc(url="https://site.com/x", selector="[href]").accepted


def test_homepage_link_rejected():
    v = _acc(url="https://site.com/", text="Home", selector="a.logo")
    assert not v.accepted and "homepage link" in v.rejections
    assert not _acc(url="https://site.com/index.html", text="Home").accepted


def test_nav_header_footer_rejected():
    assert not _acc(url="/about", classes="footer-link", text="About").accepted
    assert not _acc(url="/x", ancestor_text="site-header nav", text="Menu").accepted


def test_search_settings_login_logout_rejected():
    assert not _acc(url="/login", text="Log in").accepted
    assert not _acc(url="/logout", text="Sign out").accepted
    assert not _acc(url="/settings", text="Settings").accepted
    assert not _acc(url="/search?q=x", text="Search").accepted


def test_share_favorite_comment_vote_rejected():
    for t in ("Share", "Add to favorites", "Comment", "Upvote", "Like", "Tweet"):
        assert not _acc(url="/x", text=t).accepted, t


def test_external_unrelated_service_rejected():
    # external analytics/social with no media signal -> rejected
    v = _acc(url="https://www.google-analytics.com/collect",
             page_host="site.com")
    assert not v.accepted and "external/unrelated link" in v.rejections
    assert not _acc(url="https://facebook.com/sharer", page_host="site.com").accepted


def test_external_media_cdn_is_allowed():
    # a different host serving real media is NOT an "unrelated" link
    v = _acc(url="https://media-cdn.example.net/v/123/1080.mp4",
             page_host="site.com")
    assert v.accepted and v.kind == "download"


def test_plain_internal_link_without_signal_rejected():
    # the core download.bin bug: a normal internal page link, no media signal
    v = _acc(url="https://site.com/v/other-456", text="Related video here",
             page_host="site.com")
    assert not v.accepted


# ── triggers (URL-less quality/download menu openers) survive ─────────────

def test_quality_menu_trigger_accepted():
    v = _acc(url="", text="Choose Quality", classes="quality-menu", tag="button")
    assert v.accepted and v.kind == "trigger"


def test_download_menu_trigger_accepted():
    v = _acc(url="#", text="Download", classes="dl-menu", tag="button")
    assert v.accepted and v.kind == "trigger"


def test_urlless_nonsense_button_rejected():
    assert not _acc(url="", text="Home", classes="logo", tag="button").accepted


# ── extractor calibration: matches test_v3_43_45 expectations ─────────────

def test_calibration_dl_button_accepted_related_rejected_menu_is_trigger():
    dl = cf.classify_candidate(
        {"tag": "a", "href": "/get/123/8k.mp4", "classes": "dl-btn quality-8k",
         "text": "Download 8K MP4 (5.7 GB)"})
    assert dl.accepted and dl.kind == "download"

    related = cf.classify_candidate(
        {"tag": "a", "href": "/v/other-456", "classes": "",
         "text": "Related video here", "ancestor_text": "related-videos"})
    assert not related.accepted

    menu = cf.classify_candidate(
        {"tag": "button", "href": "", "classes": "quality-menu",
         "text": "Choose Quality"})
    assert menu.accepted and menu.kind == "trigger"


# ── partition helper ──────────────────────────────────────────────────────

def test_filter_candidates_partitions():
    cands = [
        {"tag": "a", "href": "/get/1080.mp4", "text": "Download 1080p"},
        {"tag": "a", "href": "/", "text": "Home"},
        {"tag": "a", "href": "/login", "text": "Log in"},
    ]
    kept, rejected = cf.filter_candidates(cands, page_host="site.com")
    assert len(kept) == 1 and len(rejected) == 2
    assert kept[0]["filter_verdict"]["kind"] == "download"
    assert all("reject_reason" in r for r in rejected)
