"""v3.66.4 — tests for deep_detect item 14:
Tier 2 runtime probes (HEAD probing, meta-refresh follow, async
workflow polling) layered on top of the offline detector.

All tests use a stub HTTP client; no real network calls.
"""
import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

# ── Test HTTP stub. ────────────────────────────────────────────────


class _Resp:
    def __init__(self, status=200, headers=None, json_body=None, url=None):
        self.status_code = status
        self.headers = headers or {}
        self._json = json_body
        self.url = url or ""

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class StubHttp:
    """Minimal httpx.Client stand-in. Records calls for assertion."""

    def __init__(self, head_map=None, get_map=None, post_map=None):
        self.head_map = head_map or {}
        self.get_map = get_map or {}
        self.post_map = post_map or {}
        self.calls = []

    def head(self, url, headers=None, timeout=None,
             follow_redirects=True):
        self.calls.append(("HEAD", url))
        return self.head_map.get(url, _Resp(404))

    def get(self, url, headers=None, timeout=None,
            follow_redirects=True):
        self.calls.append(("GET", url))
        return self.get_map.get(url, _Resp(404))

    def post(self, url, data=None, headers=None, timeout=None,
             follow_redirects=False):
        self.calls.append(("POST", url, dict(data or {})))
        return self.post_map.get(url, _Resp(404))


# ════════════════════════════════════════════════════════════════════
# _parse_content_disposition
# ════════════════════════════════════════════════════════════════════


def test_parse_cd_attachment_with_filename():
    from bulk_downloader.deep_detect import _parse_content_disposition
    r = _parse_content_disposition('attachment; filename="x.zip"')
    assert r["type"] == "attachment"
    assert r["filename"] == "x.zip"


def test_parse_cd_attachment_unquoted_filename():
    from bulk_downloader.deep_detect import _parse_content_disposition
    r = _parse_content_disposition('attachment; filename=x.zip')
    assert r["filename"] == "x.zip"


def test_parse_cd_extended_rfc5987():
    """filename*=UTF-8''percent-encoded-name should be decoded."""
    from bulk_downloader.deep_detect import _parse_content_disposition
    r = _parse_content_disposition(
        "attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf")
    assert "résumé.pdf" in r["filename"]


def test_parse_cd_inline_disposition():
    from bulk_downloader.deep_detect import _parse_content_disposition
    r = _parse_content_disposition('inline; filename="img.png"')
    assert r["type"] == "inline"


def test_parse_cd_empty():
    from bulk_downloader.deep_detect import _parse_content_disposition
    assert _parse_content_disposition("")["type"] == ""
    assert _parse_content_disposition(None)["type"] == ""


# ════════════════════════════════════════════════════════════════════
# _refine_source_type_from_headers
# ════════════════════════════════════════════════════════════════════


def test_refine_attachment_disposition_promotes_to_header_attachment():
    from bulk_downloader.deep_detect import _refine_source_type_from_headers
    refined, reasons = _refine_source_type_from_headers(
        "unknown",
        "application/octet-stream",
        'attachment; filename="x.zip"',
        "https://x.com/api/dl/1",
    )
    assert refined == "header_attachment"
    assert any("attachment" in r.lower() for r in reasons)


def test_refine_hls_mime_promotes_to_hls_manifest():
    from bulk_downloader.deep_detect import _refine_source_type_from_headers
    refined, _ = _refine_source_type_from_headers(
        "unknown", "application/vnd.apple.mpegurl", "",
        "https://x.com/stream/abc",
    )
    assert refined == "hls_manifest"


def test_refine_dash_mime_promotes_to_dash_manifest():
    from bulk_downloader.deep_detect import _refine_source_type_from_headers
    refined, _ = _refine_source_type_from_headers(
        "unknown", "application/dash+xml", "", "https://x.com/v")
    assert refined == "dash_manifest"


def test_refine_keeps_specific_type_unchanged():
    """A candidate already classified as direct_file should not be
    downgraded by a generic content-type."""
    from bulk_downloader.deep_detect import _refine_source_type_from_headers
    refined, _ = _refine_source_type_from_headers(
        "direct_file", "video/mp4", "", "https://x.com/v.mp4")
    assert refined == "direct_file"


def test_refine_no_promotion_when_inline_disposition():
    """Inline disposition is the browser-display default; not an
    attachment promotion signal."""
    from bulk_downloader.deep_detect import _refine_source_type_from_headers
    refined, _ = _refine_source_type_from_headers(
        "unknown", "text/html", "inline", "https://x.com/page")
    assert refined == "unknown"


# ════════════════════════════════════════════════════════════════════
# _probe_head
# ════════════════════════════════════════════════════════════════════


def test_probe_head_records_basic_response():
    from bulk_downloader.deep_detect import _probe_head
    http = StubHttp(head_map={
        "https://x.com/a.mp4": _Resp(200, headers={
            "content-type": "video/mp4",
            "content-length": "12345",
        })
    })
    r = _probe_head(http, "https://x.com/a.mp4")
    assert r["ok"] is True
    assert r["status"] == 200
    assert r["headers"]["content-type"] == "video/mp4"


def test_probe_head_405_falls_back_to_get_range():
    from bulk_downloader.deep_detect import _probe_head
    http = StubHttp(
        head_map={"https://x.com/x": _Resp(405)},
        get_map={"https://x.com/x": _Resp(206, headers={
            "content-type": "application/octet-stream",
        })},
    )
    r = _probe_head(http, "https://x.com/x")
    assert r["ok"] is True
    assert r["status"] == 206
    assert any(c[0] == "GET" for c in http.calls)


def test_probe_head_handles_exception_gracefully():
    from bulk_downloader.deep_detect import _probe_head

    class _Boom:
        def head(self, *a, **kw):
            raise ConnectionError("network down")

    r = _probe_head(_Boom(), "https://x.com/y")
    assert r["ok"] is False
    assert "ConnectionError" in (r["error"] or "")


def test_probe_head_no_client_returns_error():
    from bulk_downloader.deep_detect import _probe_head
    r = _probe_head(None, "https://x.com/y")
    assert r["ok"] is False
    assert "no http client" in (r["error"] or "")


# ════════════════════════════════════════════════════════════════════
# follow_meta_refresh
# ════════════════════════════════════════════════════════════════════


def test_meta_refresh_basic():
    from bulk_downloader.deep_detect import follow_meta_refresh
    html = ('<meta http-equiv="refresh" '
            'content="0;url=https://cdn.x.com/file.zip">')
    assert follow_meta_refresh(html) == "https://cdn.x.com/file.zip"


def test_meta_refresh_relative_url_resolved():
    from bulk_downloader.deep_detect import follow_meta_refresh
    html = '<meta http-equiv="refresh" content="3;url=/downloads/x">'
    assert follow_meta_refresh(html, base_url="https://x.com/page") == \
        "https://x.com/downloads/x"


def test_meta_refresh_returns_none_when_absent():
    from bulk_downloader.deep_detect import follow_meta_refresh
    assert follow_meta_refresh("<html><body></body></html>") is None
    assert follow_meta_refresh("") is None


def test_meta_refresh_handles_quoted_variants():
    """The content attribute can use single quotes or no quotes."""
    from bulk_downloader.deep_detect import follow_meta_refresh
    html = "<meta http-equiv='refresh' content='0;url=https://x.com/y'>"
    assert follow_meta_refresh(html) == "https://x.com/y"


# ════════════════════════════════════════════════════════════════════
# _poll_async_workflow
# ════════════════════════════════════════════════════════════════════


def _wf(action="https://x.com/api/start"):
    return {
        "source_type": "two_step_post_reveal",
        "action": action,
        "method": "POST",
        "safe_fields": {"_csrf": "abc"},
        "user_fields": [],
        "honeypot_fields": [],
        "submit_selector": "#go",
    }


def test_workflow_redirect_immediate():
    """POST returns 302 with Location → resolved on first attempt."""
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(post_map={
        "https://x.com/api/start": _Resp(302, headers={
            "location": "https://cdn.x.com/file.zip",
        }),
    })
    r = _poll_async_workflow(http, _wf())
    assert r["ok"] is True
    assert r["download_url"] == "https://cdn.x.com/file.zip"
    assert r["attempts"] == 1


def test_workflow_json_url_immediate():
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(post_map={
        "https://x.com/api/start": _Resp(200, json_body={
            "url": "https://cdn.x.com/file.zip",
        }),
    })
    r = _poll_async_workflow(http, _wf())
    assert r["ok"] is True
    assert r["download_url"] == "https://cdn.x.com/file.zip"


def test_workflow_polling_to_complete():
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(
        post_map={"https://x.com/api/start": _Resp(202, json_body={
            "status": "pending",
            "status_url": "/api/status/job1",
        })},
        get_map={"https://x.com/api/status/job1": _Resp(200, json_body={
            "status": "complete",
            "downloadUrl": "https://cdn.x.com/done.zip",
        })},
    )
    sleeps = []
    r = _poll_async_workflow(
        http, _wf(), interval=0.01,
        sleep=lambda t: sleeps.append(t))
    assert r["ok"] is True
    assert r["download_url"] == "https://cdn.x.com/done.zip"
    # POST + first poll = 2 attempts. The first poll resolves the URL,
    # so no inter-poll sleep happens (poll-then-sleep ordering, fixed
    # in v3.66.10 audit — see test_v3_66_9_deep_detect_audit_fixes.py).
    assert r["attempts"] == 2
    assert sleeps == [], (
        "no sleep should happen when the first poll resolves; "
        f"got {sleeps}")


def test_workflow_polling_failure_state():
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(
        post_map={"https://x.com/api/start": _Resp(202, json_body={
            "status_url": "/api/status/jobX",
        })},
        get_map={"https://x.com/api/status/jobX": _Resp(200, json_body={
            "status": "failed",
        })},
    )
    r = _poll_async_workflow(
        http, _wf(), interval=0.01, sleep=lambda t: None)
    assert r["ok"] is False
    assert "failed" in (r["error"] or "").lower()


def test_workflow_polling_exhausts_attempts():
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(
        post_map={"https://x.com/api/start": _Resp(202, json_body={
            "status_url": "/api/status/jobY",
        })},
        get_map={"https://x.com/api/status/jobY": _Resp(200, json_body={
            "status": "pending",
        })},
    )
    r = _poll_async_workflow(
        http, _wf(),
        max_attempts=3, interval=0.01,
        sleep=lambda t: None)
    assert r["ok"] is False
    assert "polled" in (r["error"] or "").lower()


def test_workflow_drops_honeypots_from_post_body():
    """If the workflow had a honeypot_field, the POST body must NOT
    contain it. We're never the ones who fill a honeypot."""
    from bulk_downloader.deep_detect import _poll_async_workflow
    wf = _wf()
    wf["safe_fields"] = {"_csrf": "abc"}
    wf["honeypot_fields"] = ["website"]
    http = StubHttp(post_map={
        "https://x.com/api/start": _Resp(302, headers={
            "location": "https://cdn.x.com/file.zip"})})
    _poll_async_workflow(http, wf)
    # The POST call's data dict should have _csrf but NOT 'website'.
    post_call = next(c for c in http.calls if c[0] == "POST")
    assert "_csrf" in post_call[2]
    assert "website" not in post_call[2]


def test_workflow_no_http_returns_error():
    from bulk_downloader.deep_detect import _poll_async_workflow
    r = _poll_async_workflow(None, _wf())
    assert r["ok"] is False
    assert "no http" in (r["error"] or "").lower()


# ════════════════════════════════════════════════════════════════════
# deep_detect_live — end-to-end
# ════════════════════════════════════════════════════════════════════


def test_live_promotes_attachment_anchor():
    from bulk_downloader.deep_detect import deep_detect_live
    html = '<a href="/api/download/123">Download</a>'
    http = StubHttp(head_map={
        "https://x.com/api/download/123": _Resp(200, headers={
            "content-type": "application/zip",
            "content-disposition": 'attachment; filename="r.zip"',
        }),
    })
    r = deep_detect_live(
        html, base_url="https://x.com/", http=http,
        follow_meta_refresh_redirects=False)
    assert r["buckets"]["best"]["source_type"] == "header_attachment"
    assert r["buckets"]["best"].get("filename_hint") == "r.zip"
    assert r["probes"]["probes_performed"] == 1


def test_live_drm_markers_set_disclaimer_but_probes_still_run():
    """v3.66.5: DRM markers no longer block probing. The detection is
    heuristic (the word 'widevine' in a blog post triggers the same
    pattern as a real Widevine player), and the user is the one who
    chose to point the tool at this page. Instead of a hard gate, the
    report carries an advisory `disclaimer` field that a UI can show
    as a banner."""
    from bulk_downloader.deep_detect import deep_detect_live
    html = """<a href="/api/dl/1">Download</a>
    <script>navigator.requestMediaKeySystemAccess(
        "com.widevine.alpha", [{}]);</script>"""
    http = StubHttp(head_map={
        "https://x.com/api/dl/1": _Resp(200, headers={
            "content-type": "application/zip",
            "content-disposition": 'attachment; filename="r.zip"',
        }),
    })
    r = deep_detect_live(html, base_url="https://x.com/", http=http)
    # Probe DID run
    assert r["probes"]["probes_performed"] == 1
    assert any(c[0] == "HEAD" for c in http.calls)
    # Disclaimer IS present so the UI can show a banner
    assert r["probes"]["disclaimer"] is not None
    assert "widevine" in r["probes"]["disclaimer"].lower()
    # And the underlying blocker info is still in scan_blockers output
    assert r["blockers"]["drm_or_encryption"] is True


def test_live_captcha_markers_set_disclaimer():
    """CAPTCHA markers add a disclaimer noting auto-submit won't work
    through them, but don't gate HEAD/Content-Type probing on other
    URLs on the same page."""
    from bulk_downloader.deep_detect import deep_detect_live
    html = """<a href="/api/dl/x">Download</a>
    <div class="g-recaptcha" data-sitekey="abc"></div>
    <script>grecaptcha.ready();</script>"""
    http = StubHttp(head_map={
        "https://x.com/api/dl/x": _Resp(200, headers={
            "content-type": "application/octet-stream",
        }),
    })
    r = deep_detect_live(html, base_url="https://x.com/", http=http)
    assert r["probes"]["disclaimer"] is not None
    assert "captcha" in r["probes"]["disclaimer"].lower()
    # Probe still ran
    assert r["probes"]["probes_performed"] == 1


def test_live_clean_page_has_no_disclaimer():
    """A page with no DRM or CAPTCHA markers should NOT carry a
    disclaimer — banners only appear when there's something to warn
    about."""
    from bulk_downloader.deep_detect import deep_detect_live
    html = '<a href="/api/dl/1">Download</a>'
    http = StubHttp(head_map={
        "https://x.com/api/dl/1": _Resp(200, headers={
            "content-type": "application/zip",
        }),
    })
    r = deep_detect_live(html, base_url="https://x.com/", http=http)
    assert r["probes"]["disclaimer"] is None


def test_live_meta_refresh_recorded():
    from bulk_downloader.deep_detect import deep_detect_live
    html = ('<meta http-equiv="refresh" '
            'content="0;url=/downloads/x.zip">')
    r = deep_detect_live(
        html, base_url="https://x.com/", http=StubHttp())
    # v3.66.11 added `self_loop` to the probe record so downstream
    # tooling can detect meta-refresh loops without re-parsing.
    assert r["probes"]["meta_refresh"] == {
        "from": "https://x.com/",
        "to": "https://x.com/downloads/x.zip",
        "self_loop": False,
    }


def test_live_workflow_polling_off_by_default():
    """When poll_async_workflows is False (default), the workflow
    POST never fires even if a workflow_required candidate exists."""
    from bulk_downloader.deep_detect import deep_detect_live
    html = """<form action="/api/export" method="POST">
    <input type="hidden" name="_csrf" value="x">
    <button type="submit">Generate download link</button>
    </form>"""
    http = StubHttp()
    r = deep_detect_live(html, base_url="https://x.com/", http=http)
    assert all(c[0] != "POST" for c in http.calls)
    assert r["probes"]["workflow_result"] is None


def test_live_workflow_polling_on_resolves_to_url():
    from bulk_downloader.deep_detect import deep_detect_live
    html = """<form action="/api/export" method="POST">
    <input type="hidden" name="_csrf" value="x">
    <button type="submit">Generate download link</button>
    </form>"""
    http = StubHttp(post_map={
        "https://x.com/api/export": _Resp(200, json_body={
            "url": "https://cdn.x.com/job.zip",
        }),
    })
    r = deep_detect_live(
        html, base_url="https://x.com/", http=http,
        poll_async_workflows=True, _sleep=lambda t: None)
    assert r["probes"]["workflow_result"]["ok"] is True
    assert r["buckets"]["best"]["url"] == "https://cdn.x.com/job.zip"
    assert r["buckets"]["best"]["found_in"] == "workflow_resolved"
    assert r["workflow_required"] is None


def test_live_max_probes_cap_respected():
    from bulk_downloader.deep_detect import deep_detect_live
    # 5 anchors, but max_probes=2 — only the first two get HEAD'd.
    html = "".join(
        f'<a href="/api/dl/{i}">Download item {i}</a>'
        for i in range(5)
    )
    http = StubHttp()  # nothing in map; all probes return 404
    r = deep_detect_live(
        html, base_url="https://x.com/", http=http,
        max_probes=2, follow_meta_refresh_redirects=False)
    head_count = sum(1 for c in http.calls if c[0] == "HEAD")
    assert head_count == 2


def test_live_skips_urls_with_known_extensions():
    """A URL ending in .mp4 was already classified offline — don't
    waste a HEAD on it."""
    from bulk_downloader.deep_detect import deep_detect_live
    html = """<a href="/dl/x.mp4">Download video</a>
    <a href="/api/dl/123">Download metadata</a>"""
    http = StubHttp()
    r = deep_detect_live(
        html, base_url="https://x.com/", http=http,
        follow_meta_refresh_redirects=False)
    # Only the extensionless one should be probed
    head_urls = [c[1] for c in http.calls if c[0] == "HEAD"]
    assert all("/api/dl/123" in u for u in head_urls)
    assert all("x.mp4" not in u for u in head_urls)


def test_live_passes_probe_headers_through():
    """probe_headers (cookies, auth, etc.) should reach the stub."""
    from bulk_downloader.deep_detect import deep_detect_live

    captured = {}

    class _RecordingStub(StubHttp):
        def head(self, url, headers=None, timeout=None,
                 follow_redirects=True):
            captured["headers"] = headers
            return _Resp(200, headers={})

    http = _RecordingStub()
    deep_detect_live(
        '<a href="/api/dl/1">Download</a>',
        base_url="https://x.com/", http=http,
        probe_headers={"Cookie": "sess=abc", "X-Test": "1"},
        follow_meta_refresh_redirects=False,
    )
    assert captured["headers"].get("Cookie") == "sess=abc"
    assert captured["headers"].get("X-Test") == "1"


def test_live_empty_html_returns_clean():
    from bulk_downloader.deep_detect import deep_detect_live
    r = deep_detect_live("", http=StubHttp())
    assert r["buckets"]["best"] is None
    assert r["probes"]["probes_performed"] == 0


def test_live_http_none_skips_probing_but_returns_offline_report():
    """If no http client is available and none was injected, the
    function should still return the offline report — just without
    any probe activity."""
    from bulk_downloader.deep_detect import deep_detect_live
    html = """<form action="/auth/login" method="POST">
    <input type="email" name="email"><input type="password" name="password">
    <button type="submit">Sign in</button></form>"""
    # Force http=None and patch the default builder to also return
    # None so we exercise the no-client path.
    from bulk_downloader import deep_detect as dd
    saved = dd._build_default_http_client
    dd._build_default_http_client = lambda **kw: None
    try:
        r = deep_detect_live(html, base_url="https://x.com/", http=None)
        assert r["best_login"] is not None
        assert r["probes"]["probes_performed"] == 0
    finally:
        dd._build_default_http_client = saved


# ════════════════════════════════════════════════════════════════════
# Flask route — live=true mode
# ════════════════════════════════════════════════════════════════════


def _make_dev_client():
    from bulk_downloader import app as app_mod
    app_mod._app_cfg["auth_token"] = ""
    return app_mod.app.test_client()


def test_route_live_mode_returns_probes_field():
    """The /api/dev/deep_detect route with live=true should return a
    report whose `probes` field exists. We patch _build_default_http_client
    so no real network is touched."""
    from bulk_downloader import deep_detect as dd
    saved = dd._build_default_http_client
    dd._build_default_http_client = lambda **kw: None
    try:
        client = _make_dev_client()
        resp = client.post(
            "/api/dev/deep_detect",
            json={"html": "<html></html>", "live": True},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["ok"] is True
        assert "probes" in payload["report"]
    finally:
        dd._build_default_http_client = saved


def test_route_default_mode_has_no_probes_field():
    """Without live=true (the default), the report should NOT have
    a `probes` key — that's how offline-vs-live is signaled to the
    client."""
    client = _make_dev_client()
    resp = client.post(
        "/api/dev/deep_detect",
        json={"html": "<html></html>"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert "probes" not in payload["report"]
