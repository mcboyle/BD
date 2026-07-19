"""v3.66.3 — tests for deep_detect items 8-13:
unified blocker scanner, trap-link penalizer, two-step POST reveal
detector, JSON-LD media extractor, deep_detect orchestrator, and the
/api/dev/deep_detect Flask route.
"""
import json
import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

# ════════════════════════════════════════════════════════════════════
# Item 8 — unified blocker scanner
# ════════════════════════════════════════════════════════════════════


def test_scan_blockers_empty_html_returns_clear():
    from bulk_downloader.deep_detect import scan_blockers
    r = scan_blockers("")
    assert r["blocked"] is False
    assert r["bot_defenses"] == []
    assert r["drm_systems"] == []


def test_scan_blockers_clean_login_page_returns_clear():
    from bulk_downloader.deep_detect import scan_blockers
    html = """<form><input type="email"><input type="password">
    <button type="submit">Sign in</button></form>"""
    r = scan_blockers(html)
    assert r["blocked"] is False
    assert r["do_not_auto_submit"] is False


def test_scan_blockers_recaptcha_flags_do_not_auto_submit():
    from bulk_downloader.deep_detect import scan_blockers
    r = scan_blockers("""<div class="g-recaptcha"></div>
    <script>grecaptcha.ready();</script>""")
    assert r["blocked"] is True
    assert r["do_not_auto_submit"] is True
    assert any("recaptcha" in c.lower() for c in r["captchas"])


def test_scan_blockers_widevine_flags_do_not_download():
    from bulk_downloader.deep_detect import scan_blockers
    html = ('<script>navigator.requestMediaKeySystemAccess('
            '"com.widevine.alpha", [{}]);</script>')
    r = scan_blockers(html)
    assert r["drm_or_encryption"] is True
    assert "widevine" in r["drm_systems"]
    assert r["do_not_download"] is True


def test_scan_blockers_playready_detected():
    from bulk_downloader.deep_detect import scan_blockers
    r = scan_blockers('<ContentProtection schemeIdUri="urn:uuid:'
                       '9a04f079-9840-4286-ab92-e65be0885f95" />'
                       '<script>com.microsoft.playready</script>')
    assert r["do_not_download"] is True
    assert "playready" in r["drm_systems"]


def test_scan_blockers_fairplay_detected():
    from bulk_downloader.deep_detect import scan_blockers
    r = scan_blockers('<script>com.apple.fps</script>')
    assert "fairplay" in r["drm_systems"]


def test_scan_blockers_honeypot_summary():
    from bulk_downloader.deep_detect import scan_blockers
    html = """<form><input type="text" name="website" style="display:none">
    <input type="email"><input type="password"></form>"""
    r = scan_blockers(html)
    assert r["honeypot_count"] >= 1


def test_scan_blockers_warning_messages_are_human_readable():
    from bulk_downloader.deep_detect import scan_blockers
    r = scan_blockers("""<div class="cf-turnstile"></div>
    <script>widevine</script>""")
    msg = " ".join(r["warnings"])
    assert "CAPTCHA" in msg or "bot defense" in msg.lower()
    assert "DRM" in msg or "encryption" in msg.lower()


# ════════════════════════════════════════════════════════════════════
# Item 9 — trap-link penalizer
# ════════════════════════════════════════════════════════════════════


def _make_el(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser").find()


def test_score_link_legit_mp4_passes():
    from bulk_downloader.deep_detect import score_download_link
    el = _make_el('<a href="https://x.com/v.mp4">Download 1080p</a>')
    r = score_download_link(el, base_url="https://x.com/page")
    assert r["rejected"] is False
    assert r["score"] > 0


def test_score_link_doubleclick_rejected():
    from bulk_downloader.deep_detect import score_download_link
    el = _make_el(
        '<a href="https://doubleclick.net/click?id=1">Download</a>')
    r = score_download_link(el, base_url="https://x.com/page")
    assert r["rejected"] is True
    assert any("doubleclick" in reason.lower() for reason in r["reasons"])


def test_score_link_googleadservices_rejected():
    from bulk_downloader.deep_detect import score_download_link
    el = _make_el(
        '<a href="https://googleadservices.com/pagead/aclk">Download</a>')
    r = score_download_link(el, base_url="https://x.com/page")
    assert r["rejected"] is True


def test_score_link_hash_href_rejected_with_no_url():
    from bulk_downloader.deep_detect import score_download_link
    el = _make_el('<a href="#" onclick="alert(1)">Download</a>')
    r = score_download_link(el, base_url="https://x.com/page")
    assert r["rejected"] is True
    assert r["url"] is None


def test_score_link_javascript_href_rejected():
    from bulk_downloader.deep_detect import score_download_link
    el = _make_el('<a href="javascript:void(0)">Download</a>')
    r = score_download_link(el, base_url="https://x.com/page")
    assert r["rejected"] is True
    assert r["url"] is None


def test_score_link_hidden_via_style_rejected():
    """A hidden link with download text and a file extension is a
    classic decoy — should always reject, even with bonuses."""
    from bulk_downloader.deep_detect import score_download_link
    el = _make_el(
        '<a href="/file.zip" style="display:none">Download free</a>')
    r = score_download_link(el, base_url="https://x.com/page")
    assert r["rejected"] is True


def test_score_link_html5_download_attr_bonus():
    from bulk_downloader.deep_detect import score_download_link
    el = _make_el('<a href="/v.mp4" download>Save video</a>')
    r = score_download_link(el, base_url="https://x.com/")
    assert any("download attribute" in reason
               for reason in r["reasons"])


def test_score_link_same_domain_bonus():
    from bulk_downloader.deep_detect import score_download_link
    el = _make_el(
        '<a href="https://cdn.x.com/v.mp4">Download</a>')
    r = score_download_link(el, base_url="https://x.com/page")
    # cdn.x.com is a subdomain of x.com → same-registered-domain bonus
    assert any("same-domain" in reason for reason in r["reasons"])


def test_score_link_tracker_path_penalty():
    from bulk_downloader.deep_detect import score_download_link
    el = _make_el(
        '<a href="https://x.com/out?url=https://y.com/v.mp4">Download</a>')
    r = score_download_link(el, base_url="https://x.com/")
    assert any("tracker" in reason.lower() for reason in r["reasons"])


def test_scan_links_for_traps_returns_only_rejected():
    from bulk_downloader.deep_detect import scan_links_for_traps
    html = """<html><body>
    <a href="https://x.com/v.mp4">Real download</a>
    <a href="https://doubleclick.net/click?id=1">Fake</a>
    <a href="#" onclick="alert(1)">Trap</a>
    </body></html>"""
    rej = scan_links_for_traps(html, base_url="https://x.com/")
    assert len(rej) == 2
    urls = {r["url"] for r in rej}
    assert "https://x.com/v.mp4" not in urls


# ════════════════════════════════════════════════════════════════════
# Item 10 — two-step POST reveal detector
# ════════════════════════════════════════════════════════════════════


def test_post_reveal_basic_form():
    from bulk_downloader.deep_detect import detect_post_reveal_forms
    html = """<form action="/api/download/prepare" method="POST">
    <input type="hidden" name="_csrf" value="abc">
    <input type="hidden" name="file_id" value="123">
    <button type="submit">Generate download link</button>
    </form>"""
    forms = detect_post_reveal_forms(html, base_url="https://x.com/")
    assert len(forms) == 1
    f = forms[0]
    assert f["source_type"] == "two_step_post_reveal"
    assert f["method"] == "POST"
    assert f["action"] == "https://x.com/api/download/prepare"
    assert f["safe_fields"]["_csrf"] == "abc"
    assert f["safe_fields"]["file_id"] == "123"


def test_post_reveal_skips_login_form():
    """A form with a password field is a login, not a workflow."""
    from bulk_downloader.deep_detect import detect_post_reveal_forms
    html = """<form action="/auth/login" method="POST">
    <input type="email" name="email">
    <input type="password" name="password">
    <button type="submit">Download history</button>
    </form>"""
    assert detect_post_reveal_forms(html) == []


def test_post_reveal_skips_get_forms():
    from bulk_downloader.deep_detect import detect_post_reveal_forms
    html = """<form action="/dl" method="GET">
    <button type="submit">Download</button></form>"""
    assert detect_post_reveal_forms(html) == []


def test_post_reveal_button_text_required():
    """A POST form whose submit button does NOT say something
    download-y is not a workflow candidate."""
    from bulk_downloader.deep_detect import detect_post_reveal_forms
    html = """<form action="/api/foo" method="POST">
    <input type="hidden" name="_csrf" value="abc">
    <button type="submit">Subscribe to newsletter</button>
    </form>"""
    assert detect_post_reveal_forms(html) == []


def test_post_reveal_honeypots_separated():
    from bulk_downloader.deep_detect import detect_post_reveal_forms
    html = """<form action="/api/prepare" method="POST">
    <input type="hidden" name="_csrf" value="abc">
    <input type="text" name="website" style="display:none">
    <button type="submit">Get download link</button>
    </form>"""
    forms = detect_post_reveal_forms(html)
    assert "website" in forms[0]["honeypot_fields"]
    assert "website" not in forms[0]["safe_fields"]


def test_post_reveal_user_fields_separated():
    from bulk_downloader.deep_detect import detect_post_reveal_forms
    html = """<form action="/api/prepare" method="POST">
    <input type="hidden" name="_csrf" value="abc">
    <input type="email" name="notify_email">
    <button type="submit">Prepare download</button>
    </form>"""
    forms = detect_post_reveal_forms(html)
    assert "notify_email" in forms[0]["user_fields"]
    assert "_csrf" in forms[0]["safe_fields"]


def test_post_reveal_confidence_boost_when_action_matches():
    from bulk_downloader.deep_detect import detect_post_reveal_forms
    html_action = """<form action="/api/download/prepare" method="POST">
    <button type="submit">Generate</button></form>"""
    html_noaction = """<form action="/api/x" method="POST">
    <button type="submit">Generate</button></form>"""
    a = detect_post_reveal_forms(html_action)
    n = detect_post_reveal_forms(html_noaction)
    assert a[0]["confidence"] > n[0]["confidence"]


# ════════════════════════════════════════════════════════════════════
# Item 11 — JSON-LD media extractor
# ════════════════════════════════════════════════════════════════════


def test_jsonld_videoobject_extracted():
    from bulk_downloader.deep_detect import extract_jsonld_media
    html = """<script type="application/ld+json">
    {"@type":"VideoObject","name":"T","contentUrl":"https://x.com/v.mp4",
     "uploadDate":"2026-01-01","duration":"PT1M","width":1920,"height":1080}
    </script>"""
    items = extract_jsonld_media(html)
    assert len(items) == 1
    item = items[0]
    assert item["type"] == "VideoObject"
    assert item["content_url"] == "https://x.com/v.mp4"
    assert item["upload_date"] == "2026-01-01"
    assert item["width"] == 1920
    assert item["height"] == 1080


def test_jsonld_audioobject_extracted():
    from bulk_downloader.deep_detect import extract_jsonld_media
    html = """<script type="application/ld+json">
    {"@type":"AudioObject","contentUrl":"https://x.com/a.mp3"}
    </script>"""
    items = extract_jsonld_media(html)
    assert items[0]["content_url"] == "https://x.com/a.mp3"


def test_jsonld_graph_array_extracted():
    """JSON-LD often nests items inside a top-level @graph array."""
    from bulk_downloader.deep_detect import extract_jsonld_media
    html = """<script type="application/ld+json">
    {"@context":"https://schema.org",
     "@graph":[
       {"@type":"WebPage","name":"X"},
       {"@type":"VideoObject","contentUrl":"https://x.com/v.mp4"}
     ]}
    </script>"""
    items = extract_jsonld_media(html)
    assert len(items) == 1
    assert items[0]["content_url"] == "https://x.com/v.mp4"


def test_jsonld_non_media_types_skipped():
    from bulk_downloader.deep_detect import extract_jsonld_media
    html = """<script type="application/ld+json">
    {"@type":"WebPage","name":"Just a page"}
    </script>"""
    assert extract_jsonld_media(html) == []


def test_jsonld_malformed_skipped_silently():
    from bulk_downloader.deep_detect import extract_jsonld_media
    html = """<script type="application/ld+json">
    {not valid json
    </script>
    <script type="application/ld+json">
    {"@type":"VideoObject","contentUrl":"https://x.com/good.mp4"}
    </script>"""
    items = extract_jsonld_media(html)
    assert len(items) == 1
    assert items[0]["content_url"] == "https://x.com/good.mp4"


def test_jsonld_multiple_types_list():
    """@type can be a list — picks the first matching media type."""
    from bulk_downloader.deep_detect import extract_jsonld_media
    html = """<script type="application/ld+json">
    {"@type":["MediaObject","VideoObject"],
     "contentUrl":"https://x.com/v.mp4"}
    </script>"""
    items = extract_jsonld_media(html)
    assert len(items) == 1


def test_jsonld_empty_returns_empty():
    from bulk_downloader.deep_detect import extract_jsonld_media
    assert extract_jsonld_media("") == []
    assert extract_jsonld_media("<html></html>") == []


# ════════════════════════════════════════════════════════════════════
# Item 12 — deep_detect orchestrator
# ════════════════════════════════════════════════════════════════════


_RICH_HTML = """<html><body>
<form action="/auth/login" method="POST">
  <input type="email" name="email">
  <input type="password" name="password">
  <input type="hidden" name="_csrf" value="x">
  <button type="submit">Sign in</button>
</form>

<div class="card">
  <button data-href="/dl/4320.mp4"><span>7680 x 4320</span></button>
  <div>8K HEVC 60fps 4.85GB</div>
</div>
<div class="card">
  <button data-href="/dl/2160.mp4"><span>3840 x 2160</span></button>
  <div>4K H264 60fps 5.54GB</div>
</div>
<div class="card">
  <button data-href="/dl/1080.mp4"><span>1920 x 1080</span></button>
  <div>FHD H264 60fps 3.21GB</div>
</div>

<iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>

<a href="https://doubleclick.net/click?id=1">Download Free</a>

<form action="/api/export/prepare" method="POST">
  <input type="hidden" name="_csrf" value="y">
  <button type="submit">Generate download link</button>
</form>

<script type="application/ld+json">
{"@type":"VideoObject","contentUrl":"https://cdn.x.com/jsonld.mp4",
 "uploadDate":"2026-01-01","width":1920,"height":1080}
</script>
</body></html>"""


def test_deep_detect_finds_best_login():
    from bulk_downloader.deep_detect import deep_detect
    r = deep_detect(_RICH_HTML, base_url="https://x.com/page")
    assert r["best_login"] is not None
    assert r["best_login"]["confidence"] >= 80


def test_deep_detect_picks_highest_resolution_as_best_download():
    from bulk_downloader.deep_detect import deep_detect
    r = deep_detect(_RICH_HTML, base_url="https://x.com/page")
    assert r["buckets"]["best"]["resolution"]["label"] == "8k"


def test_deep_detect_collects_provider_embeds():
    from bulk_downloader.deep_detect import deep_detect
    r = deep_detect(_RICH_HTML, base_url="https://x.com/page")
    assert any(e["provider"] == "youtube" for e in r["provider_embeds"])


def test_deep_detect_surfaces_workflow_required():
    from bulk_downloader.deep_detect import deep_detect
    r = deep_detect(_RICH_HTML, base_url="https://x.com/page")
    assert r["workflow_required"] is not None
    assert "/api/export/prepare" in r["workflow_required"]["action"]


def test_deep_detect_rejects_trap_links():
    from bulk_downloader.deep_detect import deep_detect
    r = deep_detect(_RICH_HTML, base_url="https://x.com/page")
    # doubleclick link must end up in the rejected list, not in the
    # candidates list.
    rejected_urls = {c.get("url") for c in r["buckets"]["rejected"]}
    assert any("doubleclick" in (u or "") for u in rejected_urls)


def test_deep_detect_extracts_jsonld_into_candidates():
    from bulk_downloader.deep_detect import deep_detect
    r = deep_detect(_RICH_HTML, base_url="https://x.com/")
    urls = {c.get("url") for c in r["buckets"]["accepted"]}
    assert "https://cdn.x.com/jsonld.mp4" in urls


def test_deep_detect_source_breakdown_populated():
    from bulk_downloader.deep_detect import deep_detect
    r = deep_detect(_RICH_HTML, base_url="https://x.com/")
    assert sum(r["source_breakdown"].values()) >= 4
    assert "resolution_download_card" in r["source_breakdown"]


def test_deep_detect_prefers_specific_resolution_when_asked():
    """When prefer_resolution='1080p' is passed, the 1080p variant
    should rank above 4K/8K despite the lower base rank."""
    from bulk_downloader.deep_detect import deep_detect
    r = deep_detect(_RICH_HTML, base_url="https://x.com/",
                    prefer_resolution="1080p")
    assert r["buckets"]["best"]["resolution"]["label"] == "1080p"


def test_deep_detect_hls_manifest_input():
    from bulk_downloader.deep_detect import deep_detect
    hls = ("#EXTM3U\n"
           "#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720\n"
           "v720.m3u8\n"
           "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=3840x2160\n"
           "v2160.m3u8\n")
    r = deep_detect(hls, base_url="https://cdn.x.com/")
    assert r["manifests"]["hls"]["kind"] == "hls_master"
    assert r["buckets"]["best"]["resolution"]["label"] == "4k"


def test_deep_detect_dash_manifest_input():
    from bulk_downloader.deep_detect import deep_detect
    dash = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
 <Period><AdaptationSet mimeType="video/mp4">
  <Representation id="1" width="1920" height="1080" bandwidth="1"/>
  <Representation id="2" width="3840" height="2160" bandwidth="2"/>
 </AdaptationSet></Period></MPD>"""
    r = deep_detect(dash, base_url="https://cdn.x.com/")
    assert r["manifests"]["dash"]["kind"] == "dash_mpd"
    # Best download is the 4K representation
    assert r["buckets"]["best"]["resolution"]["label"] == "4k"


def test_deep_detect_empty_input_returns_clear_report():
    from bulk_downloader.deep_detect import deep_detect
    r = deep_detect("")
    assert r["best_login"] is None
    assert r["buckets"]["best"] is None
    assert r["buckets"]["accepted"] == []


def test_deep_detect_drm_page_annotates_candidates():
    """When the page has DRM markers, every download candidate gets a
    warning attached and the page-level blocker flag is set."""
    from bulk_downloader.deep_detect import deep_detect
    html = ('<video src="https://x.com/v.mp4"></video>'
            '<script>navigator.requestMediaKeySystemAccess'
            '("com.widevine.alpha", [{}]);</script>')
    r = deep_detect(html)
    assert r["blockers"]["do_not_download"] is True


def test_deep_detect_dedups_url_across_surfaces():
    """The same URL appearing as both a Video.js source AND inside a
    Next.js state blob should produce ONE candidate."""
    from bulk_downloader.deep_detect import deep_detect
    html = """<html><body>
    <video class="video-js"
        data-setup='{"sources":[{"src":"https://x.com/v.mp4","type":"video/mp4"}]}'>
    </video>
    <script id="__NEXT_DATA__" type="application/json">
    {"video":{"playbackUrl":"https://x.com/v.mp4"}}
    </script>
    </body></html>"""
    r = deep_detect(html)
    urls = [c.get("url") for c in r["buckets"]["accepted"]]
    assert urls.count("https://x.com/v.mp4") == 1


# ════════════════════════════════════════════════════════════════════
# Item 13 — Flask /api/dev/deep_detect route
# ════════════════════════════════════════════════════════════════════


def _make_dev_client():
    """Create a Flask test client in dev-mode + auth so the
    _dev_mode_guard + _check_csrf both pass."""
    from bulk_downloader import app as app_mod
    # Dev-mode guard reads the BD_DEV_MODE env var (via dev_tools)
    os.environ["BD_DEV_MODE"] = "1"
    # Auth disabled for the duration of the test
    app_mod._app_cfg["auth_token"] = ""
    os.environ.pop("BD_AUTH_TOKEN", None)
    return app_mod.app.test_client()


def test_route_rejects_when_dev_mode_off():
    """Without dev_mode, the route refuses with the standard guard
    response. Since v3.47.7 the dev surface is on by default; the
    kill-switch is BD_DEV_MODE_DISABLE=1, not BD_DEV_MODE=0.
    _dev_mode_guard returns 404."""
    from bulk_downloader import app as app_mod
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    app_mod._app_cfg["auth_token"] = ""
    client = app_mod.app.test_client()
    try:
        resp = client.post(
            "/api/dev/deep_detect",
            json={"html": "<html></html>"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 404
        assert "dev mode" in resp.get_json()["error"].lower()
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)


def test_route_accepts_basic_html_and_returns_report():
    client = _make_dev_client()
    html = """<form action="/auth/login" method="POST">
    <input type="email" name="email"><input type="password" name="password">
    <button type="submit">Sign in</button></form>"""
    resp = client.post(
        "/api/dev/deep_detect",
        json={"html": html, "base_url": "https://x.com/"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert "report" in payload
    assert payload["report"]["best_login"] is not None


def test_route_rejects_non_string_html():
    client = _make_dev_client()
    resp = client.post(
        "/api/dev/deep_detect",
        json={"html": 12345},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_route_rejects_oversized_html():
    """Flask has a global MAX_CONTENT_LENGTH of 4 MB applied before
    any route runs — so a 5 MB body is rejected with status 413
    regardless of what our route does. We only assert the status
    here; the error message comes from Flask's body parser."""
    client = _make_dev_client()
    huge = "<html>" + ("a" * (5 * 1024 * 1024)) + "</html>"
    resp = client.post(
        "/api/dev/deep_detect",
        json={"html": huge},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 413


def test_route_passes_prefer_resolution_through():
    client = _make_dev_client()
    html = """<div>
      <button data-href="/dl/8k.mp4"><span>7680 x 4320</span></button>
      <div>8K HEVC 4.85GB</div>
    </div>
    <div>
      <button data-href="/dl/1080.mp4"><span>1920 x 1080</span></button>
      <div>FHD H264 3.21GB</div>
    </div>"""
    # Default — highest wins
    resp = client.post(
        "/api/dev/deep_detect",
        json={"html": html, "base_url": "https://x.com/"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.get_json()["report"]["buckets"]["best"][
        "resolution"]["label"] == "8k"
    # Prefer 1080p
    resp = client.post(
        "/api/dev/deep_detect",
        json={"html": html, "base_url": "https://x.com/",
              "prefer_resolution": "1080p"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.get_json()["report"]["buckets"]["best"][
        "resolution"]["label"] == "1080p"


def test_route_returns_500_on_internal_error():
    """If deep_detect raises, the route should surface the type +
    truncated message, NOT a 200 with garbage."""
    from bulk_downloader import app as app_mod, deep_detect as dd
    client = _make_dev_client()
    saved = dd.deep_detect

    def _boom(*a, **k):
        raise RuntimeError("intentional test failure")

    dd.deep_detect = _boom
    try:
        resp = client.post(
            "/api/dev/deep_detect",
            json={"html": "<html></html>"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 500
        payload = resp.get_json()
        assert payload["ok"] is False
        assert "RuntimeError" in payload["error"]
        assert "intentional" in payload["error"]
    finally:
        dd.deep_detect = saved
