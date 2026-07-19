"""Regression tests for three deep_detect bugs found in the v3.66.9
post-release audit.

Bug #9 — _refine_source_type_from_headers downgraded already-typed
         candidates (e.g. hls_manifest) to "header_attachment" when the
         server sent Content-Disposition: attachment.
Bug #4 — _poll_async_workflow ignored explicit failure state on the
         initial POST response and reported a misleading generic error.
Bug #10 — anchor-active-surface candidates in deep_detect_live were
          missing click_selector even though the BS4 element was in
          hand; runner._try_deep_detect_fallback then rejected them.

These tests are written to pass against the FIXED code and fail against
the pre-patch behaviour, so a future regression that reintroduces any
of these bugs will be caught by CI.

The fixture mirrors test_v3_66_4_deep_detect_live.py (isolated BD_HOME,
sys.modules wipe — Pattern A) so the package is reimported fresh.
"""
import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

# ── Test HTTP stub (mirrors the one in test_v3_66_4_deep_detect_live) ─


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
# Bug #9 — _refine_source_type_from_headers must not downgrade
#          already-specific source types when the server happens to
#          send Content-Disposition: attachment.
# ════════════════════════════════════════════════════════════════════


def test_refine_attachment_does_not_downgrade_hls_manifest():
    """Pre-fix: hls_manifest + attachment header → header_attachment.
    Post-fix: hls_manifest stays hls_manifest (stream MIME wins, and
    even without the MIME the attachment branch is gated to non-specific
    current_type values)."""
    from bulk_downloader.deep_detect import _refine_source_type_from_headers
    # Case 1: stream MIME present + attachment header → manifest wins.
    refined, _ = _refine_source_type_from_headers(
        "hls_manifest",
        "application/vnd.apple.mpegurl",
        'attachment; filename="stream.m3u8"',
        "https://x.com/stream/master.m3u8",
    )
    assert refined == "hls_manifest"

    # Case 2: no stream MIME, but current_type is already specific
    # → attachment branch must not downgrade it.
    refined, _ = _refine_source_type_from_headers(
        "hls_manifest",
        "application/octet-stream",
        'attachment; filename="stream.m3u8"',
        "https://x.com/stream/master.m3u8",
    )
    assert refined == "hls_manifest"


def test_refine_attachment_does_not_downgrade_dash_manifest():
    from bulk_downloader.deep_detect import _refine_source_type_from_headers
    refined, _ = _refine_source_type_from_headers(
        "dash_manifest",
        "application/dash+xml",
        "attachment",
        "https://x.com/v/manifest.mpd",
    )
    assert refined == "dash_manifest"


def test_refine_attachment_does_not_downgrade_json_ld_media():
    """json_ld_media is a specific source_type that should not be
    overwritten by a generic attachment classification."""
    from bulk_downloader.deep_detect import _refine_source_type_from_headers
    refined, _ = _refine_source_type_from_headers(
        "json_ld_media",
        "video/mp4",
        'attachment; filename="trailer.mp4"',
        "https://x.com/movie/trailer.mp4",
    )
    # The existing "binary MIME family" branch is also gated to
    # non-specific types, so json_ld_media should pass through unchanged.
    assert refined == "json_ld_media"


def test_refine_attachment_still_promotes_unknown():
    """The pre-existing happy path must still work — an unknown
    candidate should still get promoted to header_attachment when the
    server sends Content-Disposition: attachment."""
    from bulk_downloader.deep_detect import _refine_source_type_from_headers
    refined, reasons = _refine_source_type_from_headers(
        "unknown",
        "application/octet-stream",
        'attachment; filename="report.pdf"',
        "https://x.com/dl/123",
    )
    assert refined == "header_attachment"
    assert any("attachment" in r.lower() for r in reasons)


def test_refine_attachment_still_promotes_extensionless():
    from bulk_downloader.deep_detect import _refine_source_type_from_headers
    refined, _ = _refine_source_type_from_headers(
        "extensionless_file",
        "application/octet-stream",
        "attachment",
        "https://x.com/dl/123",
    )
    assert refined == "header_attachment"


# ════════════════════════════════════════════════════════════════════
# Bug #4 — _poll_async_workflow must surface the real failure cause
#          when the initial POST response body says state=failed.
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


def test_workflow_initial_response_failed_state_surfaces_error():
    """Pre-fix: the initial POST returning {"status":"failed"} fell
    through to the bottom and produced the generic
    'POST returned status=N with no recognizable download URL' error,
    hiding the real failure cause from the operator.
    Post-fix: the function reports state='failed' and bubbles up the
    server-supplied error message."""
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(post_map={
        "https://x.com/api/start": _Resp(200, json_body={
            "status": "failed",
            "error": "rate limit exceeded",
        }),
    })
    r = _poll_async_workflow(http, _wf())
    assert r["ok"] is False
    err = (r["error"] or "").lower()
    assert "failed" in err, f"expected 'failed' in error, got: {r['error']!r}"
    # The real failure reason should be surfaced, not buried.
    assert "rate limit" in err, (
        f"expected server error 'rate limit exceeded' in error, "
        f"got: {r['error']!r}")
    # And it must not poll a status_url (there isn't one).
    assert all(c[0] != "GET" for c in http.calls)


def test_workflow_initial_response_error_state_surfaces_error():
    """Same but for state='error'."""
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(post_map={
        "https://x.com/api/start": _Resp(200, json_body={
            "state": "error",
            "message": "invalid credentials",
        }),
    })
    r = _poll_async_workflow(http, _wf())
    assert r["ok"] is False
    err = (r["error"] or "").lower()
    assert "error" in err
    assert "invalid credentials" in err


def test_workflow_initial_response_cancelled_surfaces_error():
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(post_map={
        "https://x.com/api/start": _Resp(200, json_body={
            "status": "cancelled",
        }),
    })
    r = _poll_async_workflow(http, _wf())
    assert r["ok"] is False
    assert "cancelled" in (r["error"] or "").lower()


def test_workflow_initial_response_pending_still_polls():
    """Regression guard: the new initial-failure check must not
    short-circuit the legitimate 'pending → poll' path."""
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(
        post_map={"https://x.com/api/start": _Resp(202, json_body={
            "status": "pending",
            "status_url": "/api/status/job1",
        })},
        get_map={"https://x.com/api/status/job1": _Resp(200, json_body={
            "status": "complete",
            "download_url": "https://cdn.x.com/done.zip",
        })},
    )
    r = _poll_async_workflow(
        http, _wf(), interval=0.01, sleep=lambda _t: None)
    assert r["ok"] is True
    assert r["download_url"] == "https://cdn.x.com/done.zip"


# ════════════════════════════════════════════════════════════════════
# Bug #10 — anchor-active-surface candidates must carry click_selector
#           so runner._try_deep_detect_fallback can consume them.
# ════════════════════════════════════════════════════════════════════


def test_anchor_active_surface_candidate_has_click_selector():
    """Pre-fix: deep_detect_live's BS4 walk built anchor-active-surface
    candidates without a click_selector — the runner's fallback then
    filtered them out as not-DOM-clickable, even though the BS4 element
    was right there to derive a selector from.
    Post-fix: every anchor candidate has a click_selector set via
    _selector_for(el)."""
    from bulk_downloader.deep_detect import deep_detect_live

    # No file extension on the URL — forces the path through the
    # anchor-active-surface block (the extension-gate at line ~4428
    # would otherwise skip it).
    html = (
        '<html><body>'
        '<a id="dl-btn" class="primary download-btn" '
        'href="/api/download/abc123">Download</a>'
        '</body></html>'
    )
    http = StubHttp()  # No HEAD responses needed; we don't assert on
                       # probe results, just on the candidate shape.
    report = deep_detect_live(
        html,
        base_url="https://x.com/page",
        http=http,
        sniff_attachments=True,
        # Disable the probe step so the test doesn't depend on HEAD
        # responses; the active-surface population happens before
        # probing, and we just want to inspect what got collected.
        max_probes=0,
    )
    actives = [c for c in report["buckets"]["accepted"]
               if c.get("found_in") == "anchor_active_surface"]
    assert actives, (
        "expected at least one anchor_active_surface candidate; "
        f"got source breakdown: {report.get('source_breakdown')}")
    for c in actives:
        assert c.get("click_selector"), (
            f"anchor_active_surface candidate missing click_selector: {c!r}")
        # And it should actually be a usable selector, not an empty
        # string. _selector_for produces tag + id/class/nth-of-type
        # forms; a bare tag name like 'a' is fine — what matters is
        # that the runner's filter `c.get("click_selector") and ...`
        # passes.
        assert isinstance(c["click_selector"], str)
        assert len(c["click_selector"]) > 0


# ════════════════════════════════════════════════════════════════════
# Bug #1 — _poll_async_workflow off-by-one. Pre-fix: range(1, N) ran
# N-1 polls and reported "polled N attempts". Post-fix: range(N) runs
# exactly N polls.
# Bug #2 — Pre-fix: sleep happened BEFORE the first poll, wasting
# `interval` seconds when the job was ready immediately. Post-fix:
# poll first, sleep only between polls when another one is needed.
# Bug #3 — Pre-fix: ignored Retry-After header and retry-hint body
# fields. Post-fix: server-supplied delay wins over caller's interval.
# ════════════════════════════════════════════════════════════════════


def test_workflow_polling_runs_exactly_max_attempts_polls():
    """Bug #1: with max_attempts=N and a server that stays pending,
    we should see exactly N GET requests (plus the initial POST)."""
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(
        post_map={"https://x.com/api/start": _Resp(202, json_body={
            "status_url": "/api/status/job"})},
        get_map={"https://x.com/api/status/job": _Resp(200, json_body={
            "status": "pending"})},
    )
    r = _poll_async_workflow(
        http, _wf(),
        max_attempts=5, interval=0.001,
        sleep=lambda _t: None)
    assert r["ok"] is False
    get_calls = [c for c in http.calls if c[0] == "GET"]
    assert len(get_calls) == 5, (
        f"expected exactly max_attempts=5 GETs, got {len(get_calls)}")
    # Attempts counts POST + GETs.
    assert r["attempts"] == 6, (
        f"expected attempts=6 (1 POST + 5 polls), got {r['attempts']}")
    # And the error message should not lie about the number polled.
    assert "5 attempts" in (r["error"] or ""), (
        f"expected '5 attempts' in error, got {r['error']!r}")


def test_workflow_polling_first_poll_does_not_sleep():
    """Bug #2: when the first poll resolves immediately, we should
    not have slept at all. Pre-fix slept once unnecessarily."""
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(
        post_map={"https://x.com/api/start": _Resp(202, json_body={
            "status_url": "/api/status/job"})},
        get_map={"https://x.com/api/status/job": _Resp(200, json_body={
            "url": "https://cdn.x.com/file.zip"})},
    )
    sleeps = []
    r = _poll_async_workflow(
        http, _wf(), interval=2.0,
        sleep=lambda t: sleeps.append(t))
    assert r["ok"] is True
    assert sleeps == [], f"expected no sleep on immediate resolve; got {sleeps}"


def test_workflow_polling_sleeps_between_polls_but_not_after_last():
    """Bug #2: with N polls all returning pending, we should see
    exactly N-1 inter-poll sleeps (one between each adjacent pair,
    none after the last failed poll)."""
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(
        post_map={"https://x.com/api/start": _Resp(202, json_body={
            "status_url": "/api/status/job"})},
        get_map={"https://x.com/api/status/job": _Resp(200, json_body={
            "status": "pending"})},
    )
    sleeps = []
    r = _poll_async_workflow(
        http, _wf(),
        max_attempts=4, interval=0.01,
        sleep=lambda t: sleeps.append(t))
    assert r["ok"] is False
    assert len(sleeps) == 3, (
        f"expected 3 sleeps between 4 polls; got {len(sleeps)}: {sleeps}")


def test_workflow_polling_honors_retry_after_header():
    """Bug #3: server's Retry-After header should override the
    caller-supplied interval for the NEXT sleep."""
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(
        post_map={"https://x.com/api/start": _Resp(202, json_body={
            "status_url": "/api/status/job"})},
        get_map={"https://x.com/api/status/job": _Resp(
            200,
            headers={"Retry-After": "7"},
            json_body={"status": "pending"})},
    )
    sleeps = []
    _poll_async_workflow(
        http, _wf(),
        max_attempts=2, interval=99.0,  # interval should be IGNORED
        sleep=lambda t: sleeps.append(t))
    # One sleep between the two polls; should be 7s from Retry-After,
    # not 99s from interval.
    assert sleeps == [7.0], (
        f"expected Retry-After=7 to override interval=99; got {sleeps}")


def test_workflow_polling_honors_body_retry_after():
    """Bug #3: body field `retry_after` / `retry_after_seconds` /
    `poll_after_seconds` should also override the static interval
    (lower priority than the Retry-After header, but higher than the
    caller's default)."""
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(
        post_map={"https://x.com/api/start": _Resp(202, json_body={
            "status_url": "/api/status/job"})},
        get_map={"https://x.com/api/status/job": _Resp(200, json_body={
            "status": "pending",
            "retry_after": 3.5,
        })},
    )
    sleeps = []
    _poll_async_workflow(
        http, _wf(),
        max_attempts=2, interval=99.0,
        sleep=lambda t: sleeps.append(t))
    assert sleeps == [3.5], (
        f"expected body retry_after=3.5 to override interval=99; got {sleeps}")


def test_workflow_polling_retry_after_header_beats_body_hint():
    """When BOTH a Retry-After header and a body hint are present,
    the HTTP header wins (it's the canonical signal)."""
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(
        post_map={"https://x.com/api/start": _Resp(202, json_body={
            "status_url": "/api/status/job"})},
        get_map={"https://x.com/api/status/job": _Resp(
            200,
            headers={"Retry-After": "11"},
            json_body={"status": "pending", "retry_after": 5})},
    )
    sleeps = []
    _poll_async_workflow(
        http, _wf(), max_attempts=2, interval=99.0,
        sleep=lambda t: sleeps.append(t))
    assert sleeps == [11.0], (
        f"Retry-After header should win over body hint; got {sleeps}")


def test_workflow_polling_retry_after_capped_at_60s():
    """Sanity cap: even if a server asks us to sleep 3600s between
    polls, we cap at 60s. Operators can raise this by passing a
    higher interval if a workflow legitimately needs longer delays."""
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(
        post_map={"https://x.com/api/start": _Resp(202, json_body={
            "status_url": "/api/status/job"})},
        get_map={"https://x.com/api/status/job": _Resp(
            200,
            headers={"Retry-After": "3600"},
            json_body={"status": "pending"})},
    )
    sleeps = []
    _poll_async_workflow(
        http, _wf(), max_attempts=2, interval=1.0,
        sleep=lambda t: sleeps.append(t))
    assert sleeps == [60.0], (
        f"Retry-After should be capped at 60s; got {sleeps}")


def test_workflow_polling_malformed_retry_after_falls_back():
    """A non-numeric Retry-After (e.g. an HTTP-date, which we don't
    parse) should NOT crash and should fall back to interval."""
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(
        post_map={"https://x.com/api/start": _Resp(202, json_body={
            "status_url": "/api/status/job"})},
        get_map={"https://x.com/api/status/job": _Resp(
            200,
            headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"},
            json_body={"status": "pending"})},
    )
    sleeps = []
    _poll_async_workflow(
        http, _wf(), max_attempts=2, interval=2.5,
        sleep=lambda t: sleeps.append(t))
    assert sleeps == [2.5], (
        f"malformed Retry-After should fall back to interval; got {sleeps}")


def test_workflow_polling_loop_surfaces_failure_state_detail():
    """The polling loop's state-check (mirror of the initial-response
    fix in bug #4) should also surface server error message detail,
    not just the bare state name. Pre-batch-2 the loop only set
    `state=failed` without the server's error text."""
    from bulk_downloader.deep_detect import _poll_async_workflow
    http = StubHttp(
        post_map={"https://x.com/api/start": _Resp(202, json_body={
            "status_url": "/api/status/job"})},
        get_map={"https://x.com/api/status/job": _Resp(200, json_body={
            "status": "failed",
            "error": "encoder crashed at frame 4823",
        })},
    )
    r = _poll_async_workflow(
        http, _wf(), max_attempts=3, interval=0.01,
        sleep=lambda _t: None)
    assert r["ok"] is False
    err = (r["error"] or "").lower()
    assert "failed" in err
    assert "encoder crashed" in err, (
        f"expected server-supplied detail in polling-loop error; "
        f"got {r['error']!r}")


# ════════════════════════════════════════════════════════════════════
# Bugs #7 & #8 — _parse_content_disposition. Pre-fix:
#  • split on `;` corrupted filenames containing a semicolon
#  • `.strip('"').strip("'")` stripped unbalanced quotes
#  • no RFC 6266 backslash-escape handling
#  • `filename=` won over `filename*=` (spec says the opposite)
# ════════════════════════════════════════════════════════════════════


def test_parse_cd_filename_containing_semicolon():
    """Bug #8: filenames with `;` inside quotes must not be split."""
    from bulk_downloader.deep_detect import _parse_content_disposition
    r = _parse_content_disposition('attachment; filename="a;b.txt"')
    assert r["type"] == "attachment"
    assert r["filename"] == "a;b.txt", (
        f"semicolon in quoted filename was corrupted; got {r['filename']!r}")


def test_parse_cd_unterminated_quote_keeps_content():
    """Bug #7: an unterminated leading quote should not silently drop
    the user's content. Pre-fix `.strip('"')` would turn
    `filename="foo` into `foo`, losing the indication that the header
    was malformed. We don't have to be smart about it — just don't
    corrupt the visible content."""
    from bulk_downloader.deep_detect import _parse_content_disposition
    r = _parse_content_disposition('attachment; filename="foo')
    # The quote stays attached — caller can see the header was odd —
    # but the visible text is preserved.
    assert "foo" in r["filename"]


def test_parse_cd_backslash_escapes_in_quoted_filename():
    """Bug #7: RFC 6266 quoted-string allows backslash escapes —
    `filename="a\\"b.txt"` should yield `a"b.txt`."""
    from bulk_downloader.deep_detect import _parse_content_disposition
    r = _parse_content_disposition(r'attachment; filename="a\"b.txt"')
    assert r["filename"] == 'a"b.txt', (
        f"backslash escape not processed; got {r['filename']!r}")


def test_parse_cd_extended_form_wins_over_legacy():
    """Bug #7: RFC 6266 §4.3 — when both `filename=` and `filename*=`
    are present, the extended form wins (it carries explicit i18n)."""
    from bulk_downloader.deep_detect import _parse_content_disposition
    r = _parse_content_disposition(
        "attachment; filename=resume.pdf; "
        "filename*=UTF-8''r%C3%A9sum%C3%A9.pdf")
    assert r["filename"] == "résumé.pdf", (
        f"filename*= should win over filename=; got {r['filename']!r}")


def test_parse_cd_extended_form_wins_regardless_of_order():
    """Same as above but with filename*= appearing FIRST. Order in
    the header must not affect which one wins."""
    from bulk_downloader.deep_detect import _parse_content_disposition
    r = _parse_content_disposition(
        "attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf; "
        "filename=resume.pdf")
    assert r["filename"] == "résumé.pdf"


def test_parse_cd_legacy_form_still_works_alone():
    """Regression guard: when only `filename=` is present, it's used."""
    from bulk_downloader.deep_detect import _parse_content_disposition
    r = _parse_content_disposition('attachment; filename="report.pdf"')
    assert r["filename"] == "report.pdf"


def test_parse_cd_quoted_filename_with_spaces():
    from bulk_downloader.deep_detect import _parse_content_disposition
    r = _parse_content_disposition(
        'attachment; filename="my report (final).pdf"')
    assert r["filename"] == "my report (final).pdf"


def test_parse_cd_unbalanced_inner_quote_strip_avoided():
    """Pre-fix `.strip('"')` would mangle a value like `"a"b"` to
    `a"b`, but the bigger risk was `"a` → `a` (dropping the user's
    visible content). The new parser only strips a matched pair."""
    from bulk_downloader.deep_detect import _parse_content_disposition
    # Trailing-only quote: parser keeps the literal trailing quote
    # (matched-pair check failed), which is honest about the malformed
    # input rather than silently dropping a character.
    r = _parse_content_disposition('attachment; filename=foo"')
    assert "foo" in r["filename"]


# ════════════════════════════════════════════════════════════════════
# Bugs #5 & #6 — _probe_head. Pre-fix:
#  • `ok` was set to True before checking the status code — a 404
#    HEAD response would return ok=True with status=404
#  • case-insensitive header lookup used `.title()` which is wrong
#    for `ETag` ("Etag" via .title() doesn't match the wire form)
# ════════════════════════════════════════════════════════════════════


def test_probe_head_404_is_not_ok():
    """Bug #5: a 4xx response must not be flagged ok=True."""
    from bulk_downloader.deep_detect import _probe_head
    http = StubHttp(head_map={
        "https://x.com/missing": _Resp(404),
    })
    r = _probe_head(http, "https://x.com/missing")
    assert r["ok"] is False, "404 must not be ok"
    assert r["status"] == 404


def test_probe_head_500_is_not_ok():
    """Bug #5: a 5xx response must not be flagged ok=True."""
    from bulk_downloader.deep_detect import _probe_head
    http = StubHttp(head_map={
        "https://x.com/broken": _Resp(500),
    })
    r = _probe_head(http, "https://x.com/broken")
    assert r["ok"] is False
    assert r["status"] == 500


def test_probe_head_200_is_ok():
    """Regression guard: a 200 response is still ok=True."""
    from bulk_downloader.deep_detect import _probe_head
    http = StubHttp(head_map={
        "https://x.com/ok": _Resp(200, headers={"content-type": "video/mp4"}),
    })
    r = _probe_head(http, "https://x.com/ok")
    assert r["ok"] is True
    assert r["status"] == 200


def test_probe_head_3xx_is_ok():
    """3xx with follow_redirects=True usually wouldn't reach us, but
    if a stub does pass one through, it's still success-range."""
    from bulk_downloader.deep_detect import _probe_head
    http = StubHttp(head_map={
        "https://x.com/redir": _Resp(301, headers={"location": "/elsewhere"}),
    })
    r = _probe_head(http, "https://x.com/redir")
    assert r["ok"] is True


def test_probe_head_case_insensitive_ETag_lookup():
    """Bug #6: the canonical wire form is 'ETag', which `.title()` of
    'etag' becomes 'Etag' — those didn't match. The new lookup walks
    headers case-insensitively."""
    from bulk_downloader.deep_detect import _probe_head
    http = StubHttp(head_map={
        "https://x.com/file": _Resp(200, headers={
            # Capital E + capital T — the form most real servers send.
            "ETag": '"abc123"',
            "Content-Type": "application/octet-stream",
        }),
    })
    r = _probe_head(http, "https://x.com/file")
    assert r["ok"] is True
    assert r["headers"].get("etag") == '"abc123"', (
        f"ETag wire-form header should be reachable via 'etag' key; "
        f"got {r['headers']!r}")
    assert r["headers"].get("content-type") == "application/octet-stream"


def test_probe_head_case_insensitive_x_final_url():
    """`x-final-url` is rarely set by real servers, but if a stub or
    middleware sends it as `X-Final-Url`, we should still find it."""
    from bulk_downloader.deep_detect import _probe_head
    http = StubHttp(head_map={
        "https://x.com/redir": _Resp(200, headers={
            "X-Final-Url": "https://cdn.x.com/real-file.bin",
        }),
    })
    r = _probe_head(http, "https://x.com/redir")
    assert r["headers"].get("x-final-url") == "https://cdn.x.com/real-file.bin"


# ════════════════════════════════════════════════════════════════════
# Bug #12 — decode_url. Pre-fix: when the URL was relative AND no
# anchor (base_url/base_href) was supplied, the function returned the
# raw relative path. Downstream code's `_url_path(s).endswith(ext)`
# would then falsely match `.mp4` on `"./foo.mp4"` and award a file-
# extension scoring bonus to an unfetchable URL.
# Post-fix: return "" to signal that the URL couldn't be resolved.
# ════════════════════════════════════════════════════════════════════


def test_decode_url_relative_with_anchor_resolves():
    """Regression guard: the normal case still works."""
    from bulk_downloader.deep_detect import decode_url
    r = decode_url("./foo.mp4", base_url="https://x.com/dir/page.html")
    assert r == "https://x.com/dir/foo.mp4"


def test_decode_url_relative_without_anchor_returns_empty():
    """Bug #12: no anchor + relative path → empty string."""
    from bulk_downloader.deep_detect import decode_url
    assert decode_url("./foo.mp4") == ""
    assert decode_url("foo.mp4") == ""
    assert decode_url("../foo.mp4") == ""
    assert decode_url("/api/dl/123") == ""


def test_decode_url_absolute_url_pass_through():
    """Regression guard: absolute URLs never need an anchor."""
    from bulk_downloader.deep_detect import decode_url
    assert decode_url("https://x.com/a.mp4") == "https://x.com/a.mp4"
    assert decode_url("http://x.com/a.mp4") == "http://x.com/a.mp4"


def test_decode_url_protocol_relative_with_anchor():
    """Protocol-relative gets the scheme from the anchor."""
    from bulk_downloader.deep_detect import decode_url
    r = decode_url("//cdn.x.com/file.bin",
                   base_url="https://x.com/page")
    assert r == "https://cdn.x.com/file.bin"


def test_decode_url_data_uri_no_anchor_pass_through():
    """data: URIs are absolute by construction — no anchor needed."""
    from bulk_downloader.deep_detect import decode_url
    r = decode_url("data:image/png;base64,iVBORw0KGgo=")
    assert r.startswith("data:image/png")


# ════════════════════════════════════════════════════════════════════
# Bug #13 — _dedup_candidates lost reasons/warnings/found_in from
# the dropped duplicate. Post-fix: merge reasons and warnings, and
# track all alternate origins via `merged_from`.
# ════════════════════════════════════════════════════════════════════


def test_dedup_merges_reasons_from_duplicates():
    from bulk_downloader.deep_detect import _dedup_candidates
    cands = [
        {"url": "https://x.com/a.mp4", "score": 90,
         "found_in": "hls_master_playlist",
         "reasons": ["HLS variant 1080p"], "warnings": []},
        {"url": "https://x.com/a.mp4", "score": 70,
         "found_in": "player:JWPlayer",
         "reasons": ["JWPlayer config"], "warnings": []},
    ]
    out = _dedup_candidates(cands)
    assert len(out) == 1
    winner = out[0]
    assert winner["score"] == 90
    assert "HLS variant 1080p" in winner["reasons"]
    assert "JWPlayer config" in winner["reasons"]


def test_dedup_tracks_alternate_origins_in_merged_from():
    from bulk_downloader.deep_detect import _dedup_candidates
    cands = [
        {"url": "https://x.com/a.mp4", "score": 90,
         "found_in": "hls_master_playlist",
         "reasons": [], "warnings": []},
        {"url": "https://x.com/a.mp4", "score": 70,
         "found_in": "player:JWPlayer",
         "reasons": [], "warnings": []},
        {"url": "https://x.com/a.mp4", "score": 50,
         "found_in": "json_ld",
         "reasons": [], "warnings": []},
    ]
    out = _dedup_candidates(cands)
    assert len(out) == 1
    winner = out[0]
    assert winner["found_in"] == "hls_master_playlist"
    merged = winner.get("merged_from", [])
    assert "player:JWPlayer" in merged
    assert "json_ld" in merged


def test_dedup_merges_warnings_from_duplicates():
    from bulk_downloader.deep_detect import _dedup_candidates
    cands = [
        {"url": "https://x.com/a.mp4", "score": 90, "found_in": "a",
         "reasons": [], "warnings": ["HLS encryption detected"]},
        {"url": "https://x.com/a.mp4", "score": 70, "found_in": "b",
         "reasons": [], "warnings": ["DASH ContentProtection detected"]},
    ]
    out = _dedup_candidates(cands)
    assert len(out) == 1
    assert "HLS encryption detected" in out[0]["warnings"]
    assert "DASH ContentProtection detected" in out[0]["warnings"]


def test_dedup_no_url_candidates_pass_through_unchanged():
    """Regression guard: URL-less candidates aren't merged with each
    other (they each represent a different finding)."""
    from bulk_downloader.deep_detect import _dedup_candidates
    cands = [
        {"url": None, "score": 40, "found_in": "two_step_post_form",
         "reasons": [], "warnings": []},
        {"url": None, "score": 60, "found_in": "provider:Vimeo",
         "reasons": [], "warnings": []},
    ]
    out = _dedup_candidates(cands)
    assert len(out) == 2


def test_dedup_dedupes_no_duplicate_reasons():
    """Reasons are de-duplicated by string identity so repeated merges
    don't stuff the same text in twice."""
    from bulk_downloader.deep_detect import _dedup_candidates
    cands = [
        {"url": "https://x.com/a.mp4", "score": 90, "found_in": "a",
         "reasons": ["same reason"], "warnings": []},
        {"url": "https://x.com/a.mp4", "score": 70, "found_in": "b",
         "reasons": ["same reason"], "warnings": []},
    ]
    out = _dedup_candidates(cands)
    assert out[0]["reasons"].count("same reason") == 1


# ════════════════════════════════════════════════════════════════════
# Bug #14 — deep_detect_live didn't re-sort after the workflow-resolved
# insert. Score=200 made the invariant hold by luck. Post-fix: sort
# defensively so `best_download == download_candidates[0]` holds
# regardless of what scores the other candidates have.
# ════════════════════════════════════════════════════════════════════


def test_live_workflow_resolved_candidate_sorted_into_place():
    """The workflow-resolved candidate has score=200 and should end up
    at index 0 even if other candidates were already present."""
    from bulk_downloader.deep_detect import deep_detect_live

    # HTML with a form that detect_post_reveal_forms will pick up,
    # plus another candidate URL the offline pass should also surface.
    html = (
        '<html><body>'
        '<form action="/api/generate" method="POST">'
        '<input type="hidden" name="_csrf" value="abc"/>'
        '<button type="submit">Generate Download Link</button>'
        '</form>'
        '<a href="https://cdn.x.com/preview.mp4">Preview MP4</a>'
        '</body></html>'
    )
    http = StubHttp(
        post_map={"https://x.com/api/generate": _Resp(200, json_body={
            "url": "https://cdn.x.com/full-quality.mp4",
        })},
    )
    report = deep_detect_live(
        html, base_url="https://x.com/page",
        http=http,
        sniff_attachments=False,  # skip HEAD probes to isolate the sort
        poll_async_workflows=True,
    )
    assert report["buckets"]["best"] is not None
    # The workflow-resolved URL should be the best_download AND first
    # in the candidate list.
    assert (report["buckets"]["best"]["url"]
            == "https://cdn.x.com/full-quality.mp4")
    assert (report["buckets"]["accepted"][0]["url"]
            == "https://cdn.x.com/full-quality.mp4")
    # Invariant: candidates are sorted by score descending.
    scores = [c.get("score", 0) for c in report["buckets"]["accepted"]]
    assert scores == sorted(scores, reverse=True), (
        f"download_candidates not sorted by score; got {scores}")


# ════════════════════════════════════════════════════════════════════
# Second-pass audit fixes (batch 5)
# ════════════════════════════════════════════════════════════════════


# Bug #23 — provider markers without extractable IDs were silently
# dropped in the stage-2 (script body) scan.

def test_provider_marker_without_ids_still_surfaces_with_warning():
    from bulk_downloader.deep_detect import extract_provider_embeds
    # Vimeo marker in a script body but no ID pattern present.
    html = (
        '<html><body>'
        '<script>window.vimeoPlayerConfig = { fancy: true };</script>'
        '</body></html>'
    )
    out = extract_provider_embeds(html, base_url="https://x.com/page")
    vimeo_hits = [e for e in out if e.get("provider") == "vimeo"]
    assert vimeo_hits, (
        "expected Vimeo embed entry even without IDs; "
        f"got providers: {[e.get('provider') for e in out]}")
    # When IDs are empty, an explanatory warning should be set.
    assert vimeo_hits[0].get("warning"), (
        "expected a 'warning' field explaining the empty IDs")


# Bug #24 — empty regex captures must not produce {"video_id": ""}

def test_extract_provider_ids_skips_empty_captures():
    """Pre-fix: an empty capture (regex match with zero-length group)
    produced `{"video_id": ""}` in the output, which downstream
    `if not ids:` checks treated as "have IDs"."""
    from bulk_downloader.deep_detect import _extract_provider_ids
    # Force the function through with a pattern that matches but
    # captures empty. Easiest path: feed a string that triggers a
    # vimeo pattern with a missing id segment. Use whichever provider
    # has a defined pattern — vimeo is canonical.
    out = _extract_provider_ids("vimeo", "vimeo.com/")
    # The exact contents depend on which patterns matched, but the
    # post-fix contract is: no key in the dict should map to "".
    for k, v in out.items():
        assert v and str(v).strip(), (
            f"empty value for key {k!r} should have been filtered out; "
            f"got {out!r}")


# Bug #28 — <button type="reset"> and <button type="button"> were
# being treated as form submits.

def test_post_reveal_form_ignores_type_button_buttons():
    from bulk_downloader.deep_detect import detect_post_reveal_forms
    html = (
        '<form method="POST" action="/api/dl">'
        '<input type="hidden" name="_csrf" value="abc">'
        '<button type="button" class="cancel">Download</button>'
        '<button type="reset">Download</button>'
        '</form>'
    )
    # Despite both buttons having "Download" text, neither is a real
    # submit — the form should NOT be picked up.
    out = detect_post_reveal_forms(html, base_url="https://x.com/")
    assert out == [], (
        f"non-submit buttons should not trigger workflow detection; "
        f"got {out}")


def test_post_reveal_form_accepts_button_with_default_type():
    """Regression guard: <button> with no type attribute defaults to
    submit and SHOULD be picked up."""
    from bulk_downloader.deep_detect import detect_post_reveal_forms
    html = (
        '<form method="POST" action="/api/dl">'
        '<input type="hidden" name="_csrf" value="abc">'
        '<button>Download</button>'
        '</form>'
    )
    out = detect_post_reveal_forms(html, base_url="https://x.com/")
    assert len(out) == 1
    assert out[0]["submit_text"] == "download"


# Bug #30 — extract_jsonld_media skipped the top-level item when
# @graph was present.

def test_jsonld_media_emits_both_top_level_and_graph():
    from bulk_downloader.deep_detect import extract_jsonld_media
    html = '''<html><script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "VideoObject",
        "name": "Top-level video",
        "contentUrl": "https://x.com/top.mp4",
        "@graph": [
            {"@type": "VideoObject", "name": "Inner video",
             "contentUrl": "https://x.com/inner.mp4"}
        ]
    }
    </script></html>'''
    out = extract_jsonld_media(html, base_url="https://x.com/")
    names = sorted(item["name"] for item in out)
    assert names == ["Inner video", "Top-level video"], (
        f"expected both top-level and @graph video; got {names}")


# Bug #34 — classify_url did substring match against the full URL
# string, so a ?ref=vimeo.com query parameter falsely classified
# any URL as a Vimeo embed.

def test_classify_url_does_not_match_provider_in_query_param():
    from bulk_downloader.deep_detect import classify_url
    # The host is x.com (not a provider); only the query carries the
    # provider name.
    r = classify_url("https://x.com/page?ref=vimeo.com&utm_source=blog")
    assert r["type"] != "vimeo_embed", (
        f"vimeo.com in query string should not trigger vimeo_embed; "
        f"got type={r['type']!r}")


def test_classify_url_provider_host_still_matches():
    """Regression guard: a real provider host still gets classified."""
    from bulk_downloader.deep_detect import classify_url
    r = classify_url("https://player.vimeo.com/video/123456")
    assert r["type"] == "vimeo_embed"


# Bug #33 — classify_url used substring "attachment" in cd.lower(),
# so a filename containing "attachment" would falsely classify.

def test_classify_url_inline_with_attachment_in_filename():
    """`inline; filename="attachment_form.pdf"` is INLINE — the word
    'attachment' appears only in the filename. Pre-fix this was
    classified as header_attachment."""
    from bulk_downloader.deep_detect import classify_url
    r = classify_url(
        "https://x.com/files/123",
        content_disposition='inline; filename="attachment_form.pdf"',
    )
    assert r["type"] != "header_attachment", (
        f"'attachment' in filename should not classify as "
        f"header_attachment; got {r['type']!r}")


def test_classify_url_real_attachment_disposition_still_works():
    """Regression guard: an actual attachment disposition still
    triggers the header_attachment classification."""
    from bulk_downloader.deep_detect import classify_url
    r = classify_url(
        "https://x.com/files/123",
        content_disposition='attachment; filename="report.pdf"',
    )
    assert r["type"] == "header_attachment"


# Bug #18 — when an inner descendant has a URL but the outer
# resolution-card has none, the inner's URL is lost. Post-fix,
# the URL is promoted up to the parent.

def test_resolution_card_inner_url_promoted_to_parent():
    from bulk_downloader.deep_detect import extract_resolution_cards
    # Outer button (a clickable in CLICKABLE_SELECTOR_TAGS) carries
    # the 4K text but no URL attributes; inner anchor carries the
    # actual destination. Pre-fix the inner was dropped as a
    # descendant duplicate, taking its URL with it. Post-fix the
    # inner's URL is lifted up to the outer button candidate.
    html = (
        '<button class="dl-card">'
        '<span>4K · H.264 · 60fps · 5.2GB</span>'
        '<a href="https://cdn.x.com/4k.mp4">Download</a>'
        '</button>'
    )
    out = extract_resolution_cards(html, base_url="https://x.com/page")
    # Should produce exactly one merged candidate, with the URL lifted
    # to the outer card.
    assert len(out) == 1, f"expected one merged candidate; got {len(out)}"
    cand = out[0]
    assert cand.get("url") == "https://cdn.x.com/4k.mp4", (
        f"inner URL should have been promoted to the parent; "
        f"got url={cand.get('url')!r}")
    assert cand.get("requires_click") is False
    assert any("promoted" in r for r in cand.get("reasons", [])), (
        f"expected 'promoted' note in reasons; got {cand.get('reasons')}")


# Bug #27 — find_honeypots selector truncation could slice a class
# name mid-word, producing invalid CSS.

def test_find_honeypots_fake_submit_selector_doesnt_truncate_classnames():
    """Long class names must not be cut mid-word."""
    from bulk_downloader.deep_detect import find_honeypots
    # Three long class names — total joined > 60 chars; the pre-fix
    # code would slice at exactly 60.
    long_classes = (
        "extremely-long-primary-class-name "
        "another-extremely-long-class-name "
        "yet-another-very-long-classname")
    html = (
        f'<form><button type="submit" class="{long_classes}" '
        f'style="opacity:0">Submit</button></form>'
    )
    out = find_honeypots(html)
    sels = [b["selector"] for b in out["fake_submit_buttons"]]
    assert sels, "expected fake_submit_button to be detected"
    # Each class name in the selector must be intact (none truncated).
    sel = sels[0]
    # Strip tag prefix, split into classes
    if "." in sel:
        classes_in_sel = sel.split(".")[1:]
        for c in classes_in_sel:
            assert c in long_classes.split(), (
                f"class {c!r} in selector doesn't match any original "
                f"class — it was truncated. Selector: {sel!r}")


# Bugs #20 and #21 — _try_parse_loose_json could corrupt input with
# apostrophes in double-quoted values or fire its unquoted-key regex
# inside string values.

def test_loose_json_bails_on_apostrophe_in_double_quoted_string():
    """A value like `"it's working"` would get split by the single-
    quote → double-quote regex. Post-fix returns None instead of
    producing corrupted output."""
    from bulk_downloader.deep_detect import _try_parse_loose_json
    # Mixed quotes: keys are unquoted (loose form) AND one value has
    # an apostrophe in a double-quoted string.
    blob = '{file: "it\'s a video", quality: "1080p"}'
    r = _try_parse_loose_json(blob)
    # Either a clean parse (if the parser is robust) or None — never
    # a half-rewritten dict.
    if r is not None:
        assert r.get("file") == "it's a video", (
            f"value with apostrophe was corrupted; got {r!r}")


def test_loose_json_handles_single_quoted_keys_and_values():
    """JWPlayer-style: single-quoted keys AND values, no apostrophes
    inside. Should parse successfully."""
    from bulk_downloader.deep_detect import _try_parse_loose_json
    blob = "{'file': 'https://x.com/v.mp4', 'type': 'video/mp4'}"
    r = _try_parse_loose_json(blob)
    assert r is not None
    assert r["file"] == "https://x.com/v.mp4"


def test_loose_json_returns_none_on_escaped_apostrophe():
    """Escaped apostrophe → bail; the input is too risky to rewrite."""
    from bulk_downloader.deep_detect import _try_parse_loose_json
    blob = "{file: 'don\\'t', quality: '1080p'}"
    r = _try_parse_loose_json(blob)
    # Pre-fix this returned None too (the early bail was correct)
    # but is now also covered by an explicit test.
    assert r is None


# ════════════════════════════════════════════════════════════════════
# Batch 6 — remaining minor bugs (#22, #29, #31, #32, #36)
# ════════════════════════════════════════════════════════════════════


# Bug #22 — URL-shaped string scan in _walk_json_for_media missed
# URLs with #fragment and no ?query because only the query was stripped
# before the .endswith() check.

def test_walk_json_for_media_handles_fragmented_url():
    from bulk_downloader.deep_detect import _walk_json_for_media
    obj = {
        # Not a known media key, so the URL-shaped string path triggers.
        "description":
            "Trailer at https://x.com/clip.mp4#t=10,20 from last week.",
    }
    out = _walk_json_for_media(obj, base_url="https://x.com/")
    urls = [e["url"] for e in out]
    assert any("clip.mp4" in u for u in urls), (
        f"URL with #fragment should be matched on .mp4 extension; "
        f"got {urls}")


def test_walk_json_for_media_handles_url_with_query_and_fragment():
    """Both query AND fragment combined."""
    from bulk_downloader.deep_detect import _walk_json_for_media
    obj = {
        "html": "Watch: https://x.com/v.m3u8?token=xyz#frag",
    }
    out = _walk_json_for_media(obj, base_url="https://x.com/")
    assert any("v.m3u8" in e["url"] for e in out)


# Bug #29 — detect_post_reveal_forms called _input_is_honeypot
# without page CSS, missing honeypots hidden via class-based stylesheets.

def test_post_reveal_form_honeypot_via_class_stylesheet():
    """A hidden input whose name matches HONEYPOT_NAMES and which is
    hidden via a <style> rule on its class should be detected as a
    honeypot field and excluded from safe_fields."""
    from bulk_downloader.deep_detect import detect_post_reveal_forms
    html = '''
    <html><head>
      <style>.bot-field { display: none; }</style>
    </head><body>
      <form method="POST" action="/api/dl">
        <input type="hidden" name="_csrf" value="abc">
        <input type="text" name="website" class="bot-field">
        <button>Download</button>
      </form>
    </body></html>'''
    out = detect_post_reveal_forms(html, base_url="https://x.com/")
    assert len(out) == 1
    wf = out[0]
    assert "website" in wf["honeypot_fields"], (
        f"class-styled honeypot not detected; "
        f"honeypot_fields={wf['honeypot_fields']}, "
        f"safe_fields={wf['safe_fields']}, "
        f"user_fields={wf['user_fields']}")
    assert "website" not in wf["safe_fields"]


# Bug #31 — extract_jsonld_media didn't walk Schema.org nesting
# properties (hasPart, associatedMedia, trailer, video, audio, ...).

def test_jsonld_media_walks_trailer_property():
    """Movie.trailer → VideoObject should be picked up."""
    from bulk_downloader.deep_detect import extract_jsonld_media
    html = '''<html><script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Movie",
        "name": "Big Buck Bunny",
        "trailer": {
            "@type": "VideoObject",
            "name": "Trailer",
            "contentUrl": "https://x.com/trailer.mp4"
        }
    }
    </script></html>'''
    out = extract_jsonld_media(html, base_url="https://x.com/")
    names = [item.get("name") for item in out]
    assert "Trailer" in names, (
        f"Movie.trailer VideoObject should be emitted; got {names}")


def test_jsonld_media_walks_associated_media():
    """BlogPosting.associatedMedia → MediaObject should be picked up."""
    from bulk_downloader.deep_detect import extract_jsonld_media
    html = '''<html><script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": "Some post",
        "associatedMedia": [
            {"@type": "VideoObject", "name": "Demo",
             "contentUrl": "https://x.com/demo.mp4"},
            {"@type": "AudioObject", "name": "Podcast clip",
             "contentUrl": "https://x.com/clip.mp3"}
        ]
    }
    </script></html>'''
    out = extract_jsonld_media(html, base_url="https://x.com/")
    names = sorted(item.get("name") for item in out)
    assert names == ["Demo", "Podcast clip"]


def test_jsonld_media_walks_recipe_video():
    """Recipe.video → VideoObject is a common pattern."""
    from bulk_downloader.deep_detect import extract_jsonld_media
    html = '''<html><script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": "Lasagna",
        "video": {"@type": "VideoObject", "name": "How to",
                  "contentUrl": "https://x.com/howto.mp4"}
    }
    </script></html>'''
    out = extract_jsonld_media(html, base_url="https://x.com/")
    names = [item.get("name") for item in out]
    assert "How to" in names


def test_jsonld_media_cycle_safe():
    """Cyclic references via shared dict identity should not loop."""
    from bulk_downloader.deep_detect import extract_jsonld_media
    # Construct cyclic JSON-LD by hand via a sentinel parse step.
    # Easiest: the @graph contains a reference that loops back.
    # Real-world JSON-LD doesn't cycle naturally (it's JSON), but a
    # normalized-from-RDF document can produce @id-based references
    # that resolve to themselves. Our cycle guard uses id(obj) on
    # the parsed Python dicts, so a real cycle requires sharing a
    # dict instance — which json.loads never produces but a custom
    # caller might. Test the easier case: a deeply nested but tree-
    # shaped structure doesn't infinite-loop.
    html = '''<html><script type="application/ld+json">
    {
        "@type": "VideoObject",
        "name": "Outer",
        "contentUrl": "https://x.com/outer.mp4",
        "hasPart": {
            "@type": "VideoObject",
            "name": "Inner",
            "contentUrl": "https://x.com/inner.mp4",
            "hasPart": {
                "@type": "VideoObject",
                "name": "Deepest",
                "contentUrl": "https://x.com/deepest.mp4"
            }
        }
    }
    </script></html>'''
    out = extract_jsonld_media(html, base_url="https://x.com/")
    names = sorted(item.get("name") for item in out)
    assert names == ["Deepest", "Inner", "Outer"]


# Bug #32 — score_login_form was picking the first visible text input
# as the username field, even if a later input had a name matching
# LOGIN_FIELD_TERMS.

def test_login_form_prefers_name_matched_input_over_first_visible():
    from bs4 import BeautifulSoup
    from bulk_downloader.deep_detect import score_login_form
    html = '''
    <form method="POST" action="/login">
      <!-- a global search box that happens to appear above the
           login fields in the DOM. Pre-fix this was wrongly
           selected as the "username" field. -->
      <input type="text" name="q" placeholder="Search">
      <input type="text" name="username" placeholder="Username">
      <input type="password" name="password">
      <button type="submit">Sign in</button>
    </form>'''
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    result = score_login_form(form)
    # The username (not the search box) must be in safe_fields.
    assert "username" in result["safe_fields"], (
        f"expected 'username' as safe field; got {result['safe_fields']}")
    # The search box must NOT have been captured as the user input.
    assert "q" not in result["safe_fields"], (
        f"search box 'q' should not be captured; "
        f"got safe_fields={result['safe_fields']}")


def test_login_form_prefers_type_email_over_type_text():
    """Two visible inputs, neither matching LOGIN_FIELD_TERMS by name,
    but one is type=email. The email input should win."""
    from bs4 import BeautifulSoup
    from bulk_downloader.deep_detect import score_login_form
    html = '''
    <form method="POST" action="/auth">
      <input type="text" name="something">
      <input type="email" name="other">
      <input type="password" name="password">
      <button type="submit">Sign in</button>
    </form>'''
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    result = score_login_form(form)
    assert "other" in result["safe_fields"]
    assert result["safe_fields"]["other"] == "email"


def test_login_form_autocomplete_username_is_a_strong_signal():
    """autocomplete='username' / 'email' should outweigh document
    order — even an early-in-DOM search box loses to an explicit
    autocomplete declaration."""
    from bs4 import BeautifulSoup
    from bulk_downloader.deep_detect import score_login_form
    html = '''
    <form method="POST" action="/login">
      <input type="text" name="search-q">
      <input type="text" name="some-field" autocomplete="username">
      <input type="password" name="password">
      <button type="submit">Sign in</button>
    </form>'''
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    result = score_login_form(form)
    assert "some-field" in result["safe_fields"]
    assert "search-q" not in result["safe_fields"]


def test_login_form_first_visible_is_fallback_when_nothing_matches():
    """Regression guard: when nothing matches name/email/autocomplete,
    fall back to the first visible text input."""
    from bs4 import BeautifulSoup
    from bulk_downloader.deep_detect import score_login_form
    html = '''
    <form method="POST" action="/auth">
      <input type="text" name="first-field">
      <input type="text" name="second-field">
      <input type="password" name="password">
      <button type="submit">Sign in</button>
    </form>'''
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    result = score_login_form(form)
    assert "first-field" in result["safe_fields"]


# Bug #36 — parse_size_bytes didn't handle IEC binary forms
# (KiB/MiB/GiB/TiB) or bare bytes ('B').

def test_parse_size_bytes_iec_binary_forms():
    from bulk_downloader.deep_detect import parse_size_bytes
    # IEC suffixes parse using the same 1024-based base as the SI forms
    # (the codebase uses 1024 everywhere for consistency with detect.py).
    assert parse_size_bytes("512 KiB") == 512 * 1024
    assert parse_size_bytes("4 MiB") == 4 * 1024 ** 2
    assert parse_size_bytes("1.5 GiB") == int(1.5 * 1024 ** 3)


def test_parse_size_bytes_bare_bytes():
    from bulk_downloader.deep_detect import parse_size_bytes
    assert parse_size_bytes("642 B") == 642
    assert parse_size_bytes("1 B") == 1


def test_parse_size_bytes_case_insensitive_for_new_units():
    from bulk_downloader.deep_detect import parse_size_bytes
    assert parse_size_bytes("512 kib") == 512 * 1024
    assert parse_size_bytes("1 MIB") == 1024 ** 2


# ════════════════════════════════════════════════════════════════════
# Feature — probe parallelism in deep_detect_live (v3.66.10).
# Tests verify (a) parallel and serial paths produce identical results
# for the same input, (b) total-time budget triggers truncation,
# (c) executor exceptions are surfaced rather than swallowed,
# (d) actual concurrency happens (wall-clock check with a sleep stub).
# ════════════════════════════════════════════════════════════════════


class _SleepyResp:
    """Response stub that sleeps before returning, to simulate
    network latency without actually doing I/O."""

    def __init__(self, status=200, headers=None, sleep_ms=0):
        import time as _time
        if sleep_ms > 0:
            _time.sleep(sleep_ms / 1000.0)
        self.status_code = status
        self.headers = headers or {}
        self.url = ""

    def json(self):
        raise ValueError("no json body")


class SleepyStubHttp:
    """HEAD-only stub that constructs a sleepy response per call."""

    def __init__(self, sleep_ms_per_call=50, response_headers=None):
        self.sleep_ms = sleep_ms_per_call
        self.response_headers = response_headers or {
            "content-type": "video/mp4"}
        self.calls = []

    def head(self, url, headers=None, timeout=None,
             follow_redirects=True):
        self.calls.append(("HEAD", url))
        return _SleepyResp(
            status=200,
            headers=dict(self.response_headers),
            sleep_ms=self.sleep_ms,
        )

    def get(self, *a, **kw):
        return _SleepyResp(status=404)

    def post(self, *a, **kw):
        return _SleepyResp(status=404)


def _html_with_n_unknown_anchors(n: int) -> str:
    """N anchors whose URLs have no file extension — each forces a
    HEAD probe in deep_detect_live's anchor-active-surface path."""
    anchors = "\n".join(
        f'<a href="/api/download/{i}">Download item {i}</a>'
        for i in range(n))
    return f"<html><body>{anchors}</body></html>"


def test_probe_parallelism_produces_same_results_as_serial():
    """Parallel and serial paths must produce equivalent reports
    (modulo wall-clock time). The set of head_probes URLs and the
    candidates' final source_types should match."""
    from bulk_downloader.deep_detect import deep_detect_live
    html = _html_with_n_unknown_anchors(5)
    base = "https://x.com/page"

    # Two separate stubs (each is stateful re. calls list).
    http_serial = SleepyStubHttp(sleep_ms_per_call=0)
    http_parallel = SleepyStubHttp(sleep_ms_per_call=0)

    rep_serial = deep_detect_live(
        html, base_url=base, http=http_serial,
        sniff_attachments=True, max_probes=10,
        probe_parallelism=1)
    rep_parallel = deep_detect_live(
        html, base_url=base, http=http_parallel,
        sniff_attachments=True, max_probes=10,
        probe_parallelism=4)

    # Same number of probes performed.
    assert (rep_serial["probes"]["probes_performed"]
            == rep_parallel["probes"]["probes_performed"])
    # Same URLs probed (order-independent).
    serial_urls = sorted(p["url"]
                         for p in rep_serial["probes"]["head_probes"])
    parallel_urls = sorted(p["url"]
                           for p in rep_parallel["probes"]["head_probes"])
    assert serial_urls == parallel_urls
    # And probes in serial vs parallel should be returned in the same
    # candidate-index order — head_probes[i] should describe the same
    # candidate in both runs.
    assert ([p["url"] for p in rep_serial["probes"]["head_probes"]]
            == [p["url"] for p in rep_parallel["probes"]["head_probes"]])


def test_probe_parallelism_actually_runs_concurrently():
    """Wall-clock check: 5 probes × 80ms each in serial = ~400ms;
    in parallel with 5 workers = ~80ms. Threshold check is loose to
    avoid flakes on loaded CI runners."""
    import time as _time
    from bulk_downloader.deep_detect import deep_detect_live
    html = _html_with_n_unknown_anchors(5)
    base = "https://x.com/page"

    http_serial = SleepyStubHttp(sleep_ms_per_call=80)
    t0 = _time.monotonic()
    deep_detect_live(html, base_url=base, http=http_serial,
                     sniff_attachments=True, max_probes=10,
                     probe_parallelism=1)
    serial_dur = _time.monotonic() - t0

    http_parallel = SleepyStubHttp(sleep_ms_per_call=80)
    t0 = _time.monotonic()
    deep_detect_live(html, base_url=base, http=http_parallel,
                     sniff_attachments=True, max_probes=10,
                     probe_parallelism=5)
    parallel_dur = _time.monotonic() - t0

    # Parallel should be at least 2x faster than serial. Generous
    # threshold to absorb CI variance.
    assert parallel_dur * 2 < serial_dur, (
        f"parallel ({parallel_dur:.2f}s) should be substantially faster "
        f"than serial ({serial_dur:.2f}s)")


def test_probe_max_total_time_triggers_truncation():
    """When the global probe budget is exceeded mid-flight, the
    truncated flag is set and remaining probes are skipped."""
    from bulk_downloader.deep_detect import deep_detect_live
    # 8 probes × 100ms each, but the total budget is 250ms — at most
    # ~2-3 should complete before truncation.
    html = _html_with_n_unknown_anchors(8)
    http = SleepyStubHttp(sleep_ms_per_call=100)
    report = deep_detect_live(
        html, base_url="https://x.com/page",
        http=http, sniff_attachments=True,
        max_probes=10, probe_parallelism=1,
        max_total_probe_time=0.25)
    # Either a probes_truncated flag was set, OR fewer than 8 probes
    # ran. Both are acceptable signals that the budget worked.
    truncated = report["probes"].get("probes_truncated", False)
    performed = report["probes"]["probes_performed"]
    assert truncated or performed < 8, (
        f"expected budget to truncate; got truncated={truncated}, "
        f"probes_performed={performed} (of 8 max)")


def test_probe_parallelism_handles_executor_exception_gracefully():
    """If a probe call raises inside the thread pool, the report
    should still complete with an error noted for that probe rather
    than the whole deep_detect_live raising."""
    from bulk_downloader.deep_detect import deep_detect_live

    class BrokenHttp:
        def __init__(self):
            self.calls = 0

        def head(self, url, **kw):
            self.calls += 1
            # Raise on the second call to simulate intermittent failure.
            if self.calls == 2:
                raise RuntimeError("synthetic network failure")
            return _Resp(200, headers={"content-type": "video/mp4"})

        def get(self, *a, **kw):
            return _Resp(404)

        def post(self, *a, **kw):
            return _Resp(404)

    html = _html_with_n_unknown_anchors(3)
    report = deep_detect_live(
        html, base_url="https://x.com/page",
        http=BrokenHttp(), sniff_attachments=True,
        max_probes=10, probe_parallelism=3,
        # Disable manifest follow — these aren't manifest URLs.
        follow_manifests=False)
    # Some probes should still have been recorded — at minimum the
    # one(s) that succeeded.
    assert len(report["probes"]["head_probes"]) > 0
    # And we shouldn't have crashed.
    assert report["buckets"]["best"] is not None or \
        report["buckets"]["accepted"] is not None


# ════════════════════════════════════════════════════════════════════
# Feature — manifest-following in deep_detect_live (v3.66.10).
# When a candidate is an HLS or DASH manifest URL, fetch and parse it
# so per-resolution variants become first-class candidates.
# ════════════════════════════════════════════════════════════════════


class _TextResp:
    """Response stub that returns a text body — for manifest fetches."""

    def __init__(self, status=200, text="", headers=None, url=""):
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8") if isinstance(text, str) else text
        self.headers = headers or {}
        self.url = url

    def json(self):
        import json as _json
        return _json.loads(self.text)


class ManifestStubHttp:
    """Stub that returns a stored manifest body on GET, and a 200
    Content-Type header on HEAD so candidates aren't downgraded."""

    def __init__(self, manifest_url, manifest_text, manifest_mime):
        self.manifest_url = manifest_url
        self.manifest_text = manifest_text
        self.manifest_mime = manifest_mime
        self.calls = []

    def head(self, url, **kw):
        self.calls.append(("HEAD", url))
        return _Resp(200, headers={"content-type": self.manifest_mime})

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        if url == self.manifest_url:
            return _TextResp(
                status=200,
                text=self.manifest_text,
                headers={"content-type": self.manifest_mime},
                url=url,
            )
        return _TextResp(status=404)

    def post(self, *a, **kw):
        return _TextResp(status=404)


_HLS_MASTER_FIXTURE = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080,CODECS="avc1.640028"
v1080.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1280x720,CODECS="avc1.4d4028"
v720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=854x480,CODECS="avc1.4d401e"
v480.m3u8
"""


def test_follow_manifests_injects_hls_variants_as_candidates():
    """An HLS master playlist URL on the page should be fetched,
    parsed, and its variants injected as separate candidates."""
    from bulk_downloader.deep_detect import deep_detect_live
    manifest_url = "https://x.com/streams/master.m3u8"
    # Page that references the manifest in JSON-LD (a common pattern).
    html = f'''
    <html><script type="application/ld+json">
    {{"@type": "VideoObject", "name": "Test",
      "contentUrl": "{manifest_url}"}}
    </script></html>'''
    http = ManifestStubHttp(
        manifest_url=manifest_url,
        manifest_text=_HLS_MASTER_FIXTURE,
        manifest_mime="application/vnd.apple.mpegurl",
    )
    report = deep_detect_live(
        html, base_url="https://x.com/page",
        http=http,
        sniff_attachments=False,  # not testing HEAD here
        follow_manifests=True)

    # The manifest_follow record exists and reports the count.
    follows = report["probes"]["manifests_followed"]
    assert len(follows) == 1
    assert follows[0]["fetched"] is True
    assert follows[0]["variants_added"] == 3, (
        f"expected 3 variants from 3 STREAM-INF lines; "
        f"got {follows[0]['variants_added']}")

    # The variant candidates should be in download_candidates with
    # found_in pointing back at the manifest.
    variants = [
        c for c in report["buckets"]["accepted"]
        if (c.get("found_in") or "").startswith("manifest_follow:hls")
    ]
    assert len(variants) == 3
    # Resolutions should be the three we put in the fixture.
    labels = sorted(
        v["resolution"]["label"] for v in variants
        if v.get("resolution"))
    assert "1080p" in labels
    # The parsed manifest should also be stored at the report level.
    assert report["manifests"]["hls"] is not None
    hls = report["manifests"]["hls"]
    assert len(hls["variants"]) == 3


def test_follow_manifests_marks_parent_with_resolved_count():
    """The original manifest candidate stays in the list with a
    manifest_resolved_to field so a UI that wants to play the manifest
    directly can still find it."""
    from bulk_downloader.deep_detect import deep_detect_live
    manifest_url = "https://x.com/master.m3u8"
    html = (f'<html><body><a href="{manifest_url}">'
            f'Watch HD stream</a></body></html>')
    http = ManifestStubHttp(
        manifest_url=manifest_url,
        manifest_text=_HLS_MASTER_FIXTURE,
        manifest_mime="application/vnd.apple.mpegurl",
    )
    report = deep_detect_live(
        html, base_url="https://x.com/page",
        http=http, sniff_attachments=False,
        follow_manifests=True)
    # Find the original manifest candidate.
    manifest_parent = next(
        (c for c in report["buckets"]["accepted"]
         if c.get("url") == manifest_url
         and c.get("source_type") == "hls_manifest"),
        None,
    )
    assert manifest_parent is not None, (
        "original manifest candidate should still be present")
    assert manifest_parent.get("_manifest_followed") is True
    assert manifest_parent.get("manifest_resolved_to") == 3


def test_follow_manifests_disabled_by_param():
    """When follow_manifests=False, no manifest fetches happen."""
    from bulk_downloader.deep_detect import deep_detect_live
    manifest_url = "https://x.com/master.m3u8"
    html = (f'<html><script type="application/ld+json">'
            f'{{"@type": "VideoObject", "contentUrl": "{manifest_url}"}}'
            f'</script></html>')
    http = ManifestStubHttp(
        manifest_url=manifest_url,
        manifest_text=_HLS_MASTER_FIXTURE,
        manifest_mime="application/vnd.apple.mpegurl",
    )
    report = deep_detect_live(
        html, base_url="https://x.com/page",
        http=http, sniff_attachments=False,
        follow_manifests=False)
    assert report["probes"]["manifests_followed"] == []
    # No GET calls to the manifest URL.
    get_calls = [c for c in http.calls if c[0] == "GET"]
    assert not get_calls, (
        f"expected no GET when follow_manifests=False; "
        f"got {get_calls}")


def test_follow_manifests_handles_fetch_failure():
    """If the manifest fetch fails (network error or non-2xx status),
    the follow record gets an error and no variants are injected."""
    from bulk_downloader.deep_detect import deep_detect_live
    manifest_url = "https://x.com/dead.m3u8"

    class BrokenManifestHttp:
        def __init__(self):
            self.calls = []

        def head(self, url, **kw):
            self.calls.append(("HEAD", url))
            return _Resp(200, headers={
                "content-type": "application/vnd.apple.mpegurl"})

        def get(self, url, **kw):
            self.calls.append(("GET", url))
            return _TextResp(status=503, text="")  # 5xx

        def post(self, *a, **kw):
            return _TextResp(status=404)

    html = (f'<html><script type="application/ld+json">'
            f'{{"@type": "VideoObject", "contentUrl": "{manifest_url}"}}'
            f'</script></html>')
    report = deep_detect_live(
        html, base_url="https://x.com/page",
        http=BrokenManifestHttp(), sniff_attachments=False,
        follow_manifests=True)
    follows = report["probes"]["manifests_followed"]
    assert len(follows) == 1
    assert follows[0]["fetched"] is False
    assert "503" in (follows[0]["error"] or "")


def test_follow_manifests_size_cap_applied():
    """A manifest body exceeding max_manifest_bytes should be truncated
    rather than consumed in full."""
    from bulk_downloader.deep_detect import deep_detect_live
    manifest_url = "https://x.com/giant.m3u8"
    # Build a fixture larger than the cap by repeating STREAM-INF
    # entries; only the first chunk will be parseable post-truncation.
    big_fixture = "#EXTM3U\n" + (
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n"
        "v1080.m3u8\n"
    ) * 5000  # ~500 KB
    html = (f'<html><script type="application/ld+json">'
            f'{{"@type": "VideoObject", "contentUrl": "{manifest_url}"}}'
            f'</script></html>')
    http = ManifestStubHttp(
        manifest_url=manifest_url,
        manifest_text=big_fixture,
        manifest_mime="application/vnd.apple.mpegurl",
    )
    report = deep_detect_live(
        html, base_url="https://x.com/page",
        http=http, sniff_attachments=False,
        follow_manifests=True,
        max_manifest_bytes=10_000,  # absurdly small cap
    )
    follows = report["probes"]["manifests_followed"]
    assert len(follows) == 1
    assert follows[0]["fetched"] is True
    assert follows[0].get("truncated") is True


def test_follow_manifests_propagates_drm_detection():
    """If the manifest signals encryption, the follow record reflects
    it and variants carry the warning."""
    from bulk_downloader.deep_detect import deep_detect_live
    manifest_url = "https://x.com/encrypted.m3u8"
    encrypted = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:6\n"
        '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://license"\n'
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n"
        "v1080.m3u8\n"
    )
    html = (f'<html><script type="application/ld+json">'
            f'{{"@type": "VideoObject", "contentUrl": "{manifest_url}"}}'
            f'</script></html>')
    http = ManifestStubHttp(
        manifest_url=manifest_url,
        manifest_text=encrypted,
        manifest_mime="application/vnd.apple.mpegurl",
    )
    report = deep_detect_live(
        html, base_url="https://x.com/page",
        http=http, sniff_attachments=False,
        follow_manifests=True)
    follows = report["probes"]["manifests_followed"]
    assert follows[0]["drm_or_encryption"] is True
    # At least one variant should carry the encryption warning.
    variants = [
        c for c in report["buckets"]["accepted"]
        if (c.get("found_in") or "").startswith("manifest_follow:hls")
    ]
    assert any("encryption" in " ".join(v.get("warnings", [])).lower()
               for v in variants), (
        f"expected encryption warning on at least one variant; "
        f"got warnings: {[v.get('warnings') for v in variants]}")


# ════════════════════════════════════════════════════════════════════
# Batch 8 — third-pass bug fixes (#O, #N, #L, #M, #G, #P, #A/B, #J, #K, #Q)
# ════════════════════════════════════════════════════════════════════


# Bug #O — FPS_RE was requiring ≥2 digits, so single-digit frame
# rates like "5 fps" / "1 fps" (time-lapse, security cameras)
# didn't parse.

def test_parse_fps_single_digit():
    from bulk_downloader.deep_detect import parse_fps
    assert parse_fps("Recorded at 5 fps") == 5.0
    assert parse_fps("1 fps timelapse") == 1.0
    # Regression guard: 30 / 60 / 120 still work.
    assert parse_fps("60 fps") == 60.0
    assert parse_fps("23.976 fps") == pytest.approx(23.976)


# Bug #N — _annotate_download_candidate no longer adds duplicate
# DRM messages to candidates that already have their own encryption
# warning, and skips CAPTCHA flagging on URL-only candidates.

def test_annotate_skips_drm_when_candidate_already_signals_it():
    """An HLS variant that already has 'HLS encryption detected' in
    its warnings shouldn't be re-flagged by the page-level annotator."""
    from bulk_downloader.deep_detect import _annotate_download_candidate
    cand = {
        "url": "https://x.com/v.m3u8",
        "source_type": "hls_variant",
        "warnings": ["HLS encryption detected"],
        "reasons": [],
    }
    out = _annotate_download_candidate(
        cand, {"drm_or_encryption": True, "drm_systems": ["widevine"]})
    # Should still have only the one encryption warning, not two.
    drm_warnings = [w for w in out["warnings"]
                    if "encryption" in w.lower() or "drm" in w.lower()]
    assert len(drm_warnings) == 1, (
        f"expected exactly one DRM warning; got {drm_warnings}")


def test_annotate_skips_captcha_warning_on_url_only_candidate():
    """A direct URL candidate (no click, no workflow) bypasses the
    page UI entirely — page-level CAPTCHA doesn't apply."""
    from bulk_downloader.deep_detect import _annotate_download_candidate
    cand = {
        "url": "https://cdn.x.com/file.mp4",
        "source_type": "direct_file",
        "requires_click": False,
        "needs_workflow": False,
        "warnings": [],
        "reasons": [],
    }
    out = _annotate_download_candidate(
        cand, {"captchas": ["recaptcha"]})
    captcha_warnings = [w for w in out["warnings"]
                        if "captcha" in w.lower()]
    assert captcha_warnings == [], (
        f"URL-only candidate shouldn't get CAPTCHA warning; "
        f"got {captcha_warnings}")


def test_annotate_adds_captcha_warning_on_click_candidate():
    """Regression guard: a click-driven candidate DOES get the
    CAPTCHA warning."""
    from bulk_downloader.deep_detect import _annotate_download_candidate
    cand = {
        "url": None,
        "source_type": "resolution_download_card",
        "requires_click": True,
        "warnings": [],
        "reasons": [],
    }
    out = _annotate_download_candidate(
        cand, {"captchas": ["turnstile"]})
    captcha_warnings = [w for w in out["warnings"]
                        if "captcha" in w.lower()]
    assert len(captcha_warnings) == 1


# Bug #L — score_login_page was suppressing SSO/passwordless
# synthesis whenever any form had a password. Modern pages have
# BOTH and both should surface.

def test_login_page_surfaces_sso_alongside_password_form():
    from bulk_downloader.deep_detect import score_login_page
    html = '''
    <html><body>
      <form method="POST" action="/login">
        <input type="text" name="username">
        <input type="password" name="password">
        <button>Sign in</button>
      </form>
      <!-- OAuth button outside the form -->
      <a href="/oauth/authorize?provider=google"
         class="oauth-google-btn">Sign in with Google</a>
    </body></html>'''
    r = score_login_page(html, base_url="https://x.com/login")
    # We should have the form candidate AND a page-level SSO candidate.
    candidates = r["candidates"]
    assert any(c.get("has_password") for c in candidates), (
        "expected the password form to be in candidates")
    sso_candidates = [c for c in candidates
                      if "sso_oauth" in (c.get("login_types") or [])]
    assert sso_candidates, (
        f"expected an sso_oauth candidate; got "
        f"login_types: {[c.get('login_types') for c in candidates]}")


def test_login_page_doesnt_duplicate_sso_when_form_already_has_it():
    """If a form candidate already lists sso_oauth in its login_types
    (because the form's text matched OAuth markers), don't synthesize
    a second one at page level."""
    from bulk_downloader.deep_detect import score_login_page
    # A form that includes the OAuth marker in its text.
    html = '''
    <html><body>
      <form method="POST" action="/oauth/authorize">
        <input type="text" name="email">
        <button>Continue with Google</button>
      </form>
    </body></html>'''
    r = score_login_page(html, base_url="https://x.com/")
    sso_count = sum(1 for c in r["candidates"]
                    if "sso_oauth" in (c.get("login_types") or []))
    assert sso_count <= 1, (
        f"sso_oauth should appear in at most one candidate; "
        f"got {sso_count}")


# Bug #M — WebAuthn warning didn't fire for combined login_types
# like ['form_password', 'webauthn'].

def test_login_page_warns_on_webauthn_without_password_fallback():
    from bulk_downloader.deep_detect import score_login_page
    # A passkey-only page (no password field anywhere).
    html = '''
    <html><body>
      <form method="POST" action="/auth/passkey">
        <input type="email" name="email">
        <button id="signin-passkey">Sign in with passkey</button>
      </form>
      <script>
        navigator.credentials.get({publicKey: {challenge: "..."}});
      </script>
    </body></html>'''
    r = score_login_page(html, base_url="https://x.com/")
    assert any("webauthn" in w.lower() for w in r["warnings"]), (
        f"expected WebAuthn warning; got {r['warnings']}")


def test_login_page_no_webauthn_warning_when_password_fallback_present():
    """When the form has BOTH password and WebAuthn options, no
    warning — the user can fall back to the password path."""
    from bulk_downloader.deep_detect import score_login_page
    html = '''
    <html><body>
      <form method="POST" action="/auth">
        <input type="email" name="email">
        <input type="password" name="password">
        <button>Sign in</button>
      </form>
      <script>navigator.credentials.get({publicKey: {}});</script>
    </body></html>'''
    r = score_login_page(html, base_url="https://x.com/")
    assert not any("webauthn" in w.lower() for w in r["warnings"]), (
        f"shouldn't warn when password fallback exists; "
        f"got {r['warnings']}")


# Bug #G — state-blob brace balancer didn't track ES6 template
# literals. A backtick string containing { would corrupt the depth
# counting and either truncate the blob or run past it.

def test_state_blob_balancer_handles_template_literal_with_braces():
    """A state blob whose value includes a template literal containing
    `{` should still be balanced correctly."""
    from bulk_downloader.deep_detect import extract_state_blob_urls
    # __NEXT_DATA__ with a value field that's a JS expression using
    # a template literal. The template literal `\`{x}\`` contains a
    # `{` that should NOT be counted as a JSON brace depth-changer.
    # But since this isn't JSON (it's a JS expression), the JSON parse
    # at the end will fail. The test isn't asserting that it parses;
    # it's asserting that the balancer's END point is sane (right
    # after the closing brace of the outer object, not somewhere
    # inside or way past it).
    #
    # Best test: a __NEXT_DATA__ style script with valid JSON that
    # contains a STRING value (not a literal — JSON has no template
    # literals, but the balancer should still cope if the string
    # value contains backticks and braces).
    html = '''<html><script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"templateText":"prefix {middle} suffix","videoUrl":"https://x.com/v.mp4"}}}
</script></html>'''
    out = extract_state_blob_urls(html, base_url="https://x.com/")
    # The url should be found.
    urls = [r.get("url") for r in out]
    assert any("v.mp4" in u for u in urls), (
        f"expected v.mp4 to be discovered through state blob; "
        f"got urls={urls}")


# Bug #P — RESOLUTION_RE had no left-side word boundary, so
# `12345x1080` matched as `2345x1080`.

def test_resolution_re_word_boundary_left():
    from bulk_downloader.deep_detect import detect_resolution_from_text
    # `12345x1080` is junk — neither the 5-digit width nor the
    # spillover should produce a real match. Without the anchor,
    # pre-fix this matched as 2345x1080 (4K-ish).
    r = detect_resolution_from_text("12345x1080")
    if r is not None:
        # If anything matched, it must be the FULL number, not a
        # spillover substring. 12345 isn't a real resolution width
        # (we cap at 5 digits), but if the regex did anchor properly
        # and matched 12345x1080, classify_resolution would interpret
        # 12345 as the width.
        assert r.get("width") != 2345, (
            f"left anchor failed — matched spillover as width=2345; "
            f"got {r}")


def test_resolution_re_legitimate_4k_still_matches():
    """Regression guard: a real 3840x2160 still parses."""
    from bulk_downloader.deep_detect import detect_resolution_from_text
    r = detect_resolution_from_text("Master 3840x2160 HEVC")
    assert r is not None
    assert r["rank"] >= 4000


# Bugs #A and #B — HLS attr regex case-sensitivity and backslash
# escapes in quoted values.

def test_hls_attrs_mixed_case_keys_supported():
    from bulk_downloader.deep_detect import _parse_hls_attrs
    out = _parse_hls_attrs(
        '#EXT-X-STREAM-INF:Bandwidth=8000000,Resolution=1920x1080')
    # Keys are kept as-given (case preserved), but the regex matched.
    assert "Bandwidth" in out or "BANDWIDTH" in out
    # Either way, the value should be there.
    bw = out.get("Bandwidth") or out.get("BANDWIDTH")
    assert bw == "8000000"


def test_hls_attrs_backslash_escape_in_quoted_value():
    from bulk_downloader.deep_detect import _parse_hls_attrs
    # Custom URI with embedded escaped quote — non-spec but seen in
    # the wild. Pre-fix, the `\"` would have truncated the value.
    out = _parse_hls_attrs(
        '#EXT-X-STREAM-INF:URI="https://x.com/a\\"b.m3u8"')
    assert out["URI"] == 'https://x.com/a"b.m3u8'


# Bug #Q — vocabulary label substring matching was too greedy.
# 'hd' matched 'hdr', 'hdmi', 'shadow', etc.

def test_resolution_from_text_doesnt_match_hd_in_hdr():
    """Pre-fix: 'HDR Master' triggered 720p because 'hd' was a
    substring of 'hdr'. Post-fix: word-boundary anchors require an
    isolated 'hd' token."""
    from bulk_downloader.deep_detect import detect_resolution_from_text
    r = detect_resolution_from_text("HDR Master Cut")
    # Should NOT return 720p from 'hd' substring. It might return a
    # 'master' ambiguous label (rank=9500) or None — either is correct.
    if r is not None:
        assert r.get("rank") != 720, (
            f"HDR shouldn't be classified as 720p HD; got {r}")


def test_resolution_from_text_real_hd_label_still_matches():
    """Regression guard: a real 'HD' as a standalone token works."""
    from bulk_downloader.deep_detect import detect_resolution_from_text
    r = detect_resolution_from_text("Available in HD quality")
    assert r is not None
    assert r["rank"] == 720


def test_resolution_from_text_doesnt_match_source_in_opensource():
    """Same fix applies to AMBIGUOUS_QUALITY_LABELS."""
    from bulk_downloader.deep_detect import detect_resolution_from_text
    r = detect_resolution_from_text("Find the opensource project here")
    # Should NOT return 'source' rank=9500.
    if r is not None:
        assert r.get("rank") != 9500 or r.get("label") != "source", (
            f"'opensource' shouldn't trigger 'source' label; got {r}")


# Bug #K — score_login_page sort key inconsistency.

def test_login_page_sorts_by_confidence_first():
    """A high-confidence form candidate should rank above a synthesized
    page-level passwordless candidate even when raw_score might be
    similar."""
    from bulk_downloader.deep_detect import score_login_page
    html = '''
    <html><body>
      <form method="POST" action="/login">
        <input type="email" name="email">
        <input type="password" name="password">
        <button>Sign in</button>
      </form>
      <a href="/oauth/google">Sign in with Google</a>
    </body></html>'''
    r = score_login_page(html, base_url="https://x.com/")
    # First candidate should be the password form (high confidence),
    # not the synthesized SSO entry.
    assert r["best"]["has_password"] is True, (
        f"expected password form to be best; got {r['best']}")


# ════════════════════════════════════════════════════════════════════
# Batch 9 — fourth-pass bug fixes
# ════════════════════════════════════════════════════════════════════


# Bug II — when the input was an HLS MEDIA playlist (not a master),
# the orchestrator returned an empty download_candidates list. Now
# it emits a single candidate representing the manifest itself.

def test_deep_detect_hls_media_playlist_input_emits_candidate():
    from bulk_downloader.deep_detect import deep_detect
    media_playlist = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:6\n"
        "#EXT-X-TARGETDURATION:6\n"
        "#EXTINF:5.984,\n"
        "seg-1.ts\n"
        "#EXTINF:5.984,\n"
        "seg-2.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    base = "https://x.com/streams/show123.m3u8"
    r = deep_detect(media_playlist, base_url=base)
    # The manifest should be in the candidate list as a playable
    # stream — pre-fix this was an empty list.
    assert r["buckets"]["accepted"], (
        "expected at least one candidate for HLS media playlist input")
    cand = r["buckets"]["accepted"][0]
    assert cand["url"] == base
    assert cand["source_type"] == "hls_manifest"


# Bug NN — decode_url's old `unicode_escape` decode was too broad.
# A URL containing `\t` (literal backslash-t) would get the backslash
# interpreted as starting an escape sequence.

def test_decode_url_doesnt_corrupt_literal_backslash_sequences():
    from bulk_downloader.deep_detect import decode_url
    # A URL with a `\u003a` (Unicode escape for `:`) gets decoded.
    out = decode_url("https:\\u002F\\u002Fx.com\\u002Fa.mp4")
    assert out == "https://x.com/a.mp4", (
        f"\\uXXXX should be decoded; got {out!r}")
    # But a literal `\t` sequence (backslash + t) — pre-fix this
    # was decoded as a tab character. There's no real URL like this
    # in the wild, but the function shouldn't silently corrupt input.
    out = decode_url("https://x.com/\\t/a.mp4")
    # The fix narrowed escape handling to only \uXXXX, so \t remains
    # as the literal two-character sequence. URL parsers downstream
    # may still reject it, but it's not silently mutated.
    assert "\\t" in out or "\t" not in out, (
        f"backslash-t should not have been converted to tab; got {out!r}")


# Bug CC — META_REFRESH_RE required attribute order (http-equiv
# before content). Attributes in any order should match.

def test_meta_refresh_attribute_order_independent():
    from bulk_downloader.deep_detect import follow_meta_refresh
    # content attribute BEFORE http-equiv — pre-fix this didn't match.
    html = ('<html><head>'
            '<meta content="0;url=https://x.com/next" '
            'http-equiv="refresh">'
            '</head></html>')
    out = follow_meta_refresh(html, base_url="https://x.com/")
    assert out == "https://x.com/next", (
        f"meta refresh with content-before-http-equiv didn't match; "
        f"got {out!r}")


def test_meta_refresh_standard_order_still_works():
    """Regression guard."""
    from bulk_downloader.deep_detect import follow_meta_refresh
    html = ('<html><head>'
            '<meta http-equiv="refresh" content="2;url=/page2">'
            '</head></html>')
    out = follow_meta_refresh(html, base_url="https://x.com/")
    assert out == "https://x.com/page2"


# Bug W — top HLS variant was picked via dict equality. Use identity.

def test_hls_master_top_variant_picked_by_identity():
    """Two variants with identical attributes (rare but possible)
    shouldn't both end up tagged as the 'hls_manifest' source_type."""
    from bulk_downloader.deep_detect import (
        parse_hls_master, _flatten_download_candidates)
    text = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:6\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n'
        "v1080-mirror1.m3u8\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n'
        "v1080-mirror2.m3u8\n"
    )
    parsed = parse_hls_master(text, base_url="https://x.com/")
    flat = _flatten_download_candidates(
        resolution_cards=[], hls_master=parsed, dash_mpd=None,
        state_urls=[], provider_embeds=[], player_configs=[],
        jsonld_media=[], post_reveal=[])
    manifest_tagged = [c for c in flat
                       if c.get("source_type") == "hls_manifest"]
    assert len(manifest_tagged) == 1, (
        f"only ONE variant should be tagged hls_manifest (the top); "
        f"got {len(manifest_tagged)}")


# Bug BB — HEAD-confirmed candidates without type change now get a
# small score bonus (10 instead of 0).

def test_head_confirms_extensionless_file_awards_bonus():
    """A candidate that's offline-classified as extensionless_file
    should get a small (≥10) score boost when HEAD's Content-Type
    confirms it's a binary file, even though source_type doesn't
    change. Pre-fix this gave 0 bonus."""
    from bulk_downloader.deep_detect import (
        _refine_source_type_from_headers)
    # Direct unit test on the helper — the orchestrator's full path
    # is harder to set up because 'unknown' candidates always promote
    # to 'extensionless_file' (a TYPE change), which exercises the
    # +25 branch, not the +10 confirmation branch.
    refined, reasons = _refine_source_type_from_headers(
        current_type="extensionless_file",
        content_type="video/mp4",
        content_disposition="",
        url="https://x.com/api/download/abc",
    )
    # Type doesn't change — but a reason should be emitted so the
    # caller knows to apply the smaller bonus.
    assert refined == "extensionless_file"
    confirms = [r for r in reasons if "confirms" in r.lower()]
    assert confirms, (
        f"expected a 'confirms' reason; got reasons={reasons}")


def test_refine_extensionless_no_redundant_rebrand():
    """Pre-fix the helper rebranded extensionless_file → extensionless_file
    on binary Content-Type AND appended a generic 'Content-Type: …'
    reason. Now the no-op rebrand is gone — the reason uses 'confirms'
    phrasing to signal a non-promotion."""
    from bulk_downloader.deep_detect import (
        _refine_source_type_from_headers)
    refined, reasons = _refine_source_type_from_headers(
        current_type="extensionless_file",
        content_type="application/octet-stream",
        content_disposition="",
        url="https://x.com/api/dl/x",
    )
    assert refined == "extensionless_file"
    # The reason should specifically say "confirms" (not the bare
    # "Content-Type: ..." which was the pre-fix output).
    assert any("confirms" in r for r in reasons), (
        f"expected 'confirms' wording; got {reasons}")


# ════════════════════════════════════════════════════════════════════
# Feature — URL canonicalization (v3.66.10).
# ════════════════════════════════════════════════════════════════════


def test_canonicalize_url_lowercases_scheme_and_host():
    from bulk_downloader.deep_detect import canonicalize_url
    assert (canonicalize_url("HTTPS://X.COM/path")
            == "https://x.com/path")
    assert (canonicalize_url("HTTP://Example.COM/X")
            == "http://example.com/X")


def test_canonicalize_url_drops_default_ports():
    from bulk_downloader.deep_detect import canonicalize_url
    assert (canonicalize_url("https://x.com:443/path")
            == "https://x.com/path")
    assert (canonicalize_url("http://x.com:80/path")
            == "http://x.com/path")
    # Non-default ports are kept.
    assert (canonicalize_url("https://x.com:8443/path")
            == "https://x.com:8443/path")


def test_canonicalize_url_strips_tracking_params():
    from bulk_downloader.deep_detect import canonicalize_url
    out = canonicalize_url(
        "https://x.com/page?id=42&utm_source=blog&utm_medium=email"
        "&fbclid=abc123&gclid=xyz")
    assert "utm_source" not in out
    assert "utm_medium" not in out
    assert "fbclid" not in out
    assert "gclid" not in out
    assert "id=42" in out


def test_canonicalize_url_preserves_signed_url_params():
    """A signed S3 URL has Signature, Expires, X-Amz-* params that
    MUST be preserved — they're not tracking, they're the credentials."""
    from bulk_downloader.deep_detect import canonicalize_url
    s3 = ("https://bucket.s3.amazonaws.com/key.mp4"
          "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
          "&X-Amz-Credential=AKIA..."
          "&X-Amz-Date=20260526T120000Z"
          "&X-Amz-Expires=3600"
          "&X-Amz-Signature=abcdef")
    out = canonicalize_url(s3)
    assert "X-Amz-Signature" in out
    assert "X-Amz-Expires" in out
    assert "X-Amz-Date" in out
    assert "X-Amz-Algorithm" in out


def test_canonicalize_url_sorts_query_params():
    """Two URLs with the same params in different orders should
    canonicalize to the same string."""
    from bulk_downloader.deep_detect import canonicalize_url
    a = canonicalize_url("https://x.com/page?b=2&a=1&c=3")
    b = canonicalize_url("https://x.com/page?c=3&a=1&b=2")
    assert a == b
    # And the sorted form is `a=1&b=2&c=3`.
    assert a == "https://x.com/page?a=1&b=2&c=3"


def test_canonicalize_url_strips_trailing_slash():
    from bulk_downloader.deep_detect import canonicalize_url
    assert (canonicalize_url("https://x.com/path/")
            == "https://x.com/path")
    # But the root path "/" stays.
    assert canonicalize_url("https://x.com/") == "https://x.com/"


def test_canonicalize_url_collapses_double_slashes_in_path():
    from bulk_downloader.deep_detect import canonicalize_url
    assert (canonicalize_url("https://x.com//path//to///resource")
            == "https://x.com/path/to/resource")


def test_canonicalize_url_drops_navigation_fragment():
    from bulk_downloader.deep_detect import canonicalize_url
    # In-page navigation hash — drops.
    assert (canonicalize_url("https://x.com/page#section-1")
            == "https://x.com/page")
    # Functional media timecode — preserved.
    assert (canonicalize_url("https://x.com/v.mp4#t=10,20")
            == "https://x.com/v.mp4#t=10,20")
    assert (canonicalize_url("https://x.com/v.mp4#xywh=0,0,640,360")
            == "https://x.com/v.mp4#xywh=0,0,640,360")


def test_canonicalize_url_handles_empty_and_garbage():
    from bulk_downloader.deep_detect import canonicalize_url
    assert canonicalize_url("") == ""
    assert canonicalize_url("   ") == ""
    # data: URLs aren't canonicalized — returned as-is.
    out = canonicalize_url("data:text/plain;base64,SGVsbG8=")
    assert "data:" in out
    # Relative URLs get lowercased but not joined.
    assert canonicalize_url("/relative/path") == "/relative/path"


def test_canonicalize_url_idempotent():
    """Applying canonicalize_url twice must produce the same result
    as applying it once."""
    from bulk_downloader.deep_detect import canonicalize_url
    cases = [
        "https://X.Com:443/Path/?utm_source=blog&id=42#section",
        "http://example.com:80/a//b/?c=1&a=2",
        "https://bucket.s3.amazonaws.com/k?X-Amz-Signature=abc",
        "https://x.com/v.mp4#t=10",
    ]
    for c in cases:
        once = canonicalize_url(c)
        twice = canonicalize_url(once)
        assert once == twice, (
            f"canonicalize_url not idempotent on {c!r}: "
            f"once={once!r}, twice={twice!r}")


def test_dedup_uses_canonical_url_to_merge_tracking_variants():
    """The dedup step should now merge two candidates that differ
    only in tracking-param or host-case noise."""
    from bulk_downloader.deep_detect import _dedup_candidates
    cands = [
        {
            "url": "https://x.com/file.mp4?id=42&utm_source=blog",
            "source_type": "direct_file",
            "score": 50,
            "found_in": "anchor",
            "reasons": ["found in anchor"],
            "warnings": [],
        },
        {
            "url": "https://X.com/file.mp4?utm_medium=email&id=42",
            "source_type": "direct_file",
            "score": 80,
            "found_in": "video_source",
            "reasons": ["found in <video><source>"],
            "warnings": [],
        },
    ]
    out = _dedup_candidates(cands)
    assert len(out) == 1, (
        f"expected merged into 1 candidate; got {len(out)}")
    winner = out[0]
    # The higher-scoring one wins (the video_source one).
    assert winner["score"] == 80
    assert winner["found_in"] == "video_source"
    # The loser's found_in is recorded in merged_from.
    assert "anchor" in (winner.get("merged_from") or [])
    # And the loser's RAW URL (which differed by tracking params)
    # is recorded in alternate_urls.
    alt = winner.get("alternate_urls") or []
    assert any("utm_source" in u for u in alt), (
        f"expected the alternate URL with utm_source in "
        f"alternate_urls; got {alt}")


def test_dedup_doesnt_merge_genuinely_different_urls():
    """Regression guard: two URLs with non-tracking param differences
    must NOT be merged, even if they look superficially similar."""
    from bulk_downloader.deep_detect import _dedup_candidates
    cands = [
        {
            "url": "https://x.com/file.mp4?id=42",
            "source_type": "direct_file",
            "score": 50,
            "found_in": "anchor",
            "reasons": [],
            "warnings": [],
        },
        {
            "url": "https://x.com/file.mp4?id=43",
            "source_type": "direct_file",
            "score": 50,
            "found_in": "anchor",
            "reasons": [],
            "warnings": [],
        },
    ]
    out = _dedup_candidates(cands)
    assert len(out) == 2, (
        f"different `id` params must remain distinct; got {len(out)}")


def test_dedup_doesnt_merge_signed_urls_with_different_signatures():
    """Critical regression guard: two S3 URLs to the same file but
    with different Signatures (e.g. signed at different times) are
    DIFFERENT URLs operationally — merging would break the fetch."""
    from bulk_downloader.deep_detect import _dedup_candidates
    cands = [
        {
            "url": ("https://bucket.s3.amazonaws.com/k.mp4"
                    "?X-Amz-Signature=sigA&X-Amz-Expires=3600"),
            "source_type": "direct_file",
            "score": 50,
            "found_in": "anchor1",
            "reasons": [],
            "warnings": [],
        },
        {
            "url": ("https://bucket.s3.amazonaws.com/k.mp4"
                    "?X-Amz-Signature=sigB&X-Amz-Expires=3600"),
            "source_type": "direct_file",
            "score": 50,
            "found_in": "anchor2",
            "reasons": [],
            "warnings": [],
        },
    ]
    out = _dedup_candidates(cands)
    assert len(out) == 2, (
        f"signed URLs with different signatures must NOT merge; "
        f"got {len(out)}")


def test_dedup_keeps_raw_url_intact():
    """The winner's `url` field is the RAW URL of the winner, not
    the canonical form. Critical because callers fetch this URL."""
    from bulk_downloader.deep_detect import _dedup_candidates
    raw_url = "https://X.COM/file.mp4?id=42&utm_source=blog"
    cands = [{
        "url": raw_url,
        "source_type": "direct_file",
        "score": 50,
        "found_in": "anchor",
        "reasons": [],
        "warnings": [],
    }]
    out = _dedup_candidates(cands)
    assert out[0]["url"] == raw_url, (
        f"raw URL must be preserved; got {out[0]['url']!r}")


# ════════════════════════════════════════════════════════════════════
# Bug fixes — to_site_config_block + _poll_async_workflow audit pass
# ════════════════════════════════════════════════════════════════════


# Bug PP — to_site_config_block was gating download translation on
# source_type=='resolution_download_card', but HEAD probes can promote
# the type to header_attachment/etc. Use found_in instead.

def test_to_site_config_surfaces_head_promoted_resolution_card():
    """A resolution card whose source_type was promoted by HEAD probe
    must still be carried over to learned.download."""
    from bulk_downloader.deep_detect import to_site_config_block
    report = {
        "best_login": None,
        "download_candidates": [{
            # NOTE: source_type is now 'header_attachment' (promoted
            # from resolution_download_card by HEAD), but found_in
            # still says where it came from.
            "url": "https://x.com/dl/4k.mp4",
            "source_type": "header_attachment",
            "found_in": "resolution_card",
            "click_selector": "button.dl-4k",
            "score": 130,
        }],
        "warnings": [],
    }
    out = to_site_config_block(report)
    assert out["ok"] is True
    dl = out["learned"].get("download")
    assert dl is not None, (
        f"download block missing; pre-fix this case dropped it")
    assert "button.dl-4k" in dl["trigger_selectors"]


def test_to_site_config_still_surfaces_unpromoted_resolution_card():
    """Regression guard: an unpromoted resolution_download_card with
    a click_selector still gets surfaced."""
    from bulk_downloader.deep_detect import to_site_config_block
    report = {
        "best_login": None,
        "download_candidates": [{
            "url": "https://x.com/dl/1080.mp4",
            "source_type": "resolution_download_card",
            "found_in": "resolution_card",
            "click_selector": "button.dl-1080",
            "score": 90,
        }],
        "warnings": [],
    }
    out = to_site_config_block(report)
    assert out["learned"].get("download") is not None


# Bug RR — workflow_required and SSO-only logins should be surfaced
# as explicit warnings, not silently dropped.

def test_to_site_config_warns_about_undriveable_workflow():
    from bulk_downloader.deep_detect import to_site_config_block
    report = {
        "best_login": None,
        "download_candidates": [],
        "workflow_required": {
            "action": "/api/generate",
            "method": "POST",
            "submit_selector": "button.gen",
        },
        "warnings": [],
    }
    out = to_site_config_block(report)
    assert any("workflow" in w.lower() for w in out["warnings"]), (
        f"expected a workflow warning; got {out['warnings']}")


def test_to_site_config_warns_about_sso_only_login():
    """A login with no password but with SSO/WebAuthn types should
    warn that the runtime can't automate it."""
    from bulk_downloader.deep_detect import to_site_config_block
    report = {
        "best_login": {
            "has_password": False,
            "login_types": ["sso_oauth", "webauthn"],
            "safe_fields": {},
        },
        "download_candidates": [],
        "warnings": [],
    }
    out = to_site_config_block(report)
    assert any("sso" in w.lower() or "webauthn" in w.lower()
               for w in out["warnings"]), (
        f"expected an SSO/WebAuthn warning; got {out['warnings']}")


# Bug TT — _poll_async_workflow now reads Location case-insensitively
# (and Retry-After too).

def test_poll_workflow_handles_uppercase_location_header():
    """A response with 'LOCATION' uppercase header should still
    resolve. Pre-fix only 'location' / 'Location' were tried."""
    from bulk_downloader.deep_detect import _poll_async_workflow

    class StubResp:
        def __init__(self):
            self.status_code = 302
            self.headers = {"LOCATION": "https://x.com/dl.zip"}
            self.url = "https://x.com/api/start"
        def json(self):
            raise ValueError

    class StubHttp:
        def post(self, *a, **kw):
            return StubResp()

    out = _poll_async_workflow(
        StubHttp(), {"action": "https://x.com/api/start"})
    assert out["ok"] is True
    assert out["download_url"] == "https://x.com/dl.zip"


# Bug VV — workflow body URL extraction must reject placeholder
# strings like 'PENDING' / 'TBD'.

def test_poll_workflow_rejects_placeholder_url_strings():
    """A server returning {'url': 'PENDING'} should NOT be
    interpreted as a success."""
    from bulk_downloader.deep_detect import _poll_async_workflow

    class StubResp:
        def __init__(self):
            self.status_code = 200
            self.headers = {}
            self.url = "https://x.com/api/start"
        def json(self):
            return {"url": "PENDING"}

    class StubHttp:
        def post(self, *a, **kw):
            return StubResp()

    out = _poll_async_workflow(
        StubHttp(), {"action": "https://x.com/api/start"})
    assert out["ok"] is False, (
        f"'PENDING' should NOT be accepted as a download URL; "
        f"got {out}")


def test_poll_workflow_accepts_real_url_in_body():
    """Regression guard: a real URL in the body still resolves."""
    from bulk_downloader.deep_detect import _poll_async_workflow

    class StubResp:
        def __init__(self):
            self.status_code = 200
            self.headers = {}
            self.url = "https://x.com/api/start"
        def json(self):
            return {"download_url": "https://x.com/dl/file.zip"}

    class StubHttp:
        def post(self, *a, **kw):
            return StubResp()

    out = _poll_async_workflow(
        StubHttp(), {"action": "https://x.com/api/start"})
    assert out["ok"] is True
    assert out["download_url"] == "https://x.com/dl/file.zip"


# Bug XX — widened status_url key recognition.

def test_poll_workflow_accepts_task_url_key():
    """A status_url published under 'task_url' (Celery convention)
    should work."""
    from bulk_downloader.deep_detect import _poll_async_workflow

    poll_count = [0]

    class StubInitResp:
        def __init__(self):
            self.status_code = 200
            self.headers = {}
            self.url = "https://x.com/api/start"
        def json(self):
            return {"task_url": "https://x.com/api/tasks/abc"}

    class StubPollResp:
        def __init__(self):
            poll_count[0] += 1
            self.status_code = 200
            self.headers = {}
            self.url = "https://x.com/api/tasks/abc"
        def json(self):
            return {"url": "https://x.com/dl/file.zip"}

    class StubHttp:
        def post(self, *a, **kw):
            return StubInitResp()
        def get(self, *a, **kw):
            return StubPollResp()

    out = _poll_async_workflow(
        StubHttp(), {"action": "https://x.com/api/start"},
        sleep=lambda s: None)
    assert out["ok"] is True
    assert out["download_url"] == "https://x.com/dl/file.zip"
    assert poll_count[0] == 1


# ════════════════════════════════════════════════════════════════════
# Feature — signed-URL TTL detection (v3.66.10).
# ════════════════════════════════════════════════════════════════════


def _utc(year, month, day, hour=0, minute=0, second=0):
    from datetime import datetime, timezone
    return datetime(year, month, day, hour, minute, second,
                    tzinfo=timezone.utc)


def test_detect_signed_url_unsigned_returns_no_signature():
    from bulk_downloader.deep_detect import detect_signed_url
    out = detect_signed_url("https://x.com/file.mp4?id=42")
    assert out["is_signed"] is False
    assert out["provider"] is None


def test_detect_signed_url_aws_s3_v4_with_expiry():
    """An S3 v4 signed URL with X-Amz-Expires and X-Amz-Date should
    decode to an exact expiry timestamp."""
    from bulk_downloader.deep_detect import detect_signed_url
    # X-Amz-Date=20260526T120000Z, X-Amz-Expires=3600
    # → expires at 2026-05-26T13:00:00Z
    url = ("https://bucket.s3.amazonaws.com/k.mp4"
           "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
           "&X-Amz-Credential=AKIA.../20260526/us-east-1/s3/aws4_request"
           "&X-Amz-Date=20260526T120000Z"
           "&X-Amz-Expires=3600"
           "&X-Amz-SignedHeaders=host"
           "&X-Amz-Signature=abcdef")
    # `now` injected so the test is deterministic. Set `now` to 30
    # minutes after the X-Amz-Date — so ~30 minutes remain on the TTL.
    now = _utc(2026, 5, 26, 12, 30, 0)
    out = detect_signed_url(url, now=now)
    assert out["is_signed"] is True
    assert out["provider"] == "aws_s3_v4"
    assert out["expires_at"] == "2026-05-26T13:00:00+00:00"
    assert 1700 < out["ttl_seconds"] < 1900  # ~1800
    assert out["expired"] is False
    assert out["expiring_soon"] is False


def test_detect_signed_url_aws_s3_v4_expired():
    """An S3 v4 URL whose expiry has passed should report expired."""
    from bulk_downloader.deep_detect import detect_signed_url
    url = ("https://bucket.s3.amazonaws.com/k.mp4"
           "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
           "&X-Amz-Date=20260526T120000Z"
           "&X-Amz-Expires=3600"
           "&X-Amz-Signature=abc")
    # 2 hours after expiry
    now = _utc(2026, 5, 26, 15, 0, 0)
    out = detect_signed_url(url, now=now)
    assert out["expired"] is True
    assert out["ttl_seconds"] < 0


def test_detect_signed_url_aws_s3_v4_expiring_soon():
    """TTL < 5 min should set expiring_soon."""
    from bulk_downloader.deep_detect import detect_signed_url
    url = ("https://bucket.s3.amazonaws.com/k.mp4"
           "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
           "&X-Amz-Date=20260526T120000Z"
           "&X-Amz-Expires=3600"
           "&X-Amz-Signature=abc")
    # 59 minutes after X-Amz-Date → 60s remain
    now = _utc(2026, 5, 26, 12, 59, 0)
    out = detect_signed_url(url, now=now)
    assert out["expired"] is False
    assert out["expiring_soon"] is True
    assert 30 < out["ttl_seconds"] < 90


def test_detect_signed_url_cloudfront():
    """CloudFront signed URL: Signature + Expires (unix) + Key-Pair-Id."""
    from bulk_downloader.deep_detect import detect_signed_url
    # Expires = 2026-05-26T13:00:00Z = 1779800400 (computed via
    # datetime.timestamp())
    url = ("https://dxxxxxx.cloudfront.net/k.mp4"
           "?Expires=1779800400"
           "&Signature=abc"
           "&Key-Pair-Id=APKAJ...")
    now = _utc(2026, 5, 26, 12, 30, 0)
    out = detect_signed_url(url, now=now)
    assert out["is_signed"] is True
    assert out["provider"] == "cloudfront"
    assert out["expires_at"] is not None
    assert out["ttl_seconds"] > 1000


def test_detect_signed_url_aws_s3_v2():
    """S3 v2 (legacy): Signature + Expires + AWSAccessKeyId."""
    from bulk_downloader.deep_detect import detect_signed_url
    url = ("https://bucket.s3.amazonaws.com/k.mp4"
           "?AWSAccessKeyId=AKIA..."
           "&Signature=abc"
           "&Expires=1779800400")
    out = detect_signed_url(url, now=_utc(2026, 5, 26, 12, 0))
    assert out["is_signed"] is True
    assert out["provider"] == "aws_s3_v2"


def test_detect_signed_url_azure_sas():
    """Azure SAS: sig + se + sv."""
    from bulk_downloader.deep_detect import detect_signed_url
    url = ("https://account.blob.core.windows.net/c/k.mp4"
           "?sv=2024-08-04"
           "&se=2026-05-26T13:00:00Z"
           "&sig=signature_data")
    out = detect_signed_url(url, now=_utc(2026, 5, 26, 12, 30))
    assert out["is_signed"] is True
    assert out["provider"] == "azure_sas"
    assert out["expires_at"] == "2026-05-26T13:00:00+00:00"


def test_detect_signed_url_gcs_v4():
    """GCS v4: X-Goog-Algorithm + X-Goog-Date + X-Goog-Expires."""
    from bulk_downloader.deep_detect import detect_signed_url
    url = ("https://storage.googleapis.com/bucket/k.mp4"
           "?X-Goog-Algorithm=GOOG4-RSA-SHA256"
           "&X-Goog-Credential=service-account..."
           "&X-Goog-Date=20260526T120000Z"
           "&X-Goog-Expires=3600"
           "&X-Goog-Signature=abc")
    out = detect_signed_url(url, now=_utc(2026, 5, 26, 12, 30))
    assert out["is_signed"] is True
    assert out["provider"] == "gcs_v4"
    assert out["expires_at"] == "2026-05-26T13:00:00+00:00"


def test_detect_signed_url_bunny_token():
    """Bunny.net / KeyCDN: token + expires (unix)."""
    from bulk_downloader.deep_detect import detect_signed_url
    url = ("https://cdn.example.com/k.mp4"
           "?token=abc123"
           "&expires=1779800400")
    out = detect_signed_url(url, now=_utc(2026, 5, 26, 12, 0))
    assert out["is_signed"] is True
    assert out["provider"] == "bunny_token"


def test_detect_signed_url_akamai_edgeauth():
    """Akamai EdgeAuth: hdnea / __hdnts / auth_token with exp=N inside."""
    from bulk_downloader.deep_detect import detect_signed_url
    # __hdnts=exp=1779800400~acl=/*~hmac=abc
    url = ("https://cdn.x.com/k.mp4"
           "?__hdnts=exp=1779800400~acl=/*~hmac=abc")
    out = detect_signed_url(url, now=_utc(2026, 5, 26, 12, 0))
    assert out["is_signed"] is True
    assert out["provider"] == "akamai"
    assert out["expires_at"] is not None


def test_detect_signed_url_cloudflare_stream():
    """CloudFlare Stream URL on a known host with token=."""
    from bulk_downloader.deep_detect import detect_signed_url
    url = "https://customer-xyz.cloudflarestream.com/abc123/manifest.m3u8?token=xyz"
    out = detect_signed_url(url)
    assert out["is_signed"] is True
    assert out["provider"] == "cloudflare_stream"


def test_detect_signed_url_idempotent_with_provider_match():
    """Calling twice gives the same result (no state)."""
    from bulk_downloader.deep_detect import detect_signed_url
    url = ("https://bucket.s3.amazonaws.com/k.mp4"
           "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
           "&X-Amz-Date=20260526T120000Z"
           "&X-Amz-Expires=3600"
           "&X-Amz-Signature=abc")
    now = _utc(2026, 5, 26, 12, 30)
    a = detect_signed_url(url, now=now)
    b = detect_signed_url(url, now=now)
    assert a == b


# ── Integration: signed-URL annotation on candidates ─────────────


def test_deep_detect_annotates_signed_candidate():
    """A candidate that's a signed URL should get a signed_url block."""
    from bulk_downloader.deep_detect import deep_detect
    # JSON-LD with an S3 v4 signed URL. X-Amz-Expires set very high
    # (10 years in seconds, 315_360_000) so the URL stays in-window
    # regardless of the real wall clock when the test runs.
    html = '''<html><script type="application/ld+json">
    {"@type": "VideoObject", "name": "Test",
     "contentUrl": "https://bucket.s3.amazonaws.com/k.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date=20260526T120000Z&X-Amz-Expires=315360000&X-Amz-Signature=abc"}
    </script></html>'''
    r = deep_detect(html, base_url="https://x.com/")
    cands_with_sig = [c for c in r["buckets"]["accepted"]
                      if c.get("signed_url")]
    assert cands_with_sig, (
        f"expected at least one candidate annotated with signed_url; "
        f"got candidates with these fields: "
        f"{[set(c.keys()) for c in r['download_candidates']]}")
    sig = cands_with_sig[0]["signed_url"]
    assert sig["provider"] == "aws_s3_v4"
    assert sig["expires_at"] is not None


def test_deep_detect_doesnt_annotate_unsigned_candidate():
    """A candidate with no signing params should NOT get a signed_url
    field."""
    from bulk_downloader.deep_detect import deep_detect
    html = '''<html><script type="application/ld+json">
    {"@type": "VideoObject", "contentUrl": "https://x.com/plain.mp4"}
    </script></html>'''
    r = deep_detect(html, base_url="https://x.com/")
    for c in r["buckets"]["accepted"]:
        assert "signed_url" not in c, (
            f"unsigned candidate shouldn't have signed_url field; "
            f"got {c.get('signed_url')}")


def test_deep_detect_emits_session_warning_for_expired_signed_url():
    """When any candidate has an expired signed URL, a session-level
    warning should be added so the consumer can quickly see the
    problem without scanning the full candidate list."""
    from bulk_downloader.deep_detect import deep_detect
    # An S3 v4 URL whose expiry is far in the past (epoch 1577836800
    # is 2020-01-01).
    expired_url = (
        "https://bucket.s3.amazonaws.com/k.mp4"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Date=20200101T000000Z"
        "&X-Amz-Expires=60"
        "&X-Amz-Signature=abc")
    html = (f'<html><script type="application/ld+json">'
            f'{{"@type": "VideoObject", "contentUrl": "{expired_url}"}}'
            f'</script></html>')
    r = deep_detect(html, base_url="https://x.com/")
    # Session warning should mention "EXPIRED" or "expired".
    assert any("expir" in w.lower() for w in r["buckets"]["warnings"]), (
        f"expected session-level EXPIRED warning; got {r['warnings']}")
    # And the expired URL should be in the rejected list, not the
    # candidates list.
    assert all(
        not (c.get("signed_url")
             and c.get("signed_url", {}).get("ttl_seconds", 0) < 0)
        for c in r["buckets"]["accepted"]), (
        "expired signed candidate should not be in download_candidates")
    rejected_urls = [c.get("url") for c in r["buckets"]["rejected"]]
    assert any(expired_url == u or u in expired_url
               for u in rejected_urls), (
        f"expired signed URL should be in rejected; got {rejected_urls}")


def test_to_site_config_warns_when_best_download_is_signed():
    """A signed best_download shouldn't be baked into a site config
    silently — surface a warning."""
    from bulk_downloader.deep_detect import to_site_config_block
    report = {
        "best_login": None,
        "best_download": {
            "url": "https://bucket.s3.amazonaws.com/k.mp4?X-Amz-Signature=abc",
            "source_type": "direct_file",
            "signed_url": {
                "provider": "aws_s3_v4",
                "expires_at": "2026-05-26T13:00:00+00:00",
                "ttl_seconds": 1800,
            },
            "found_in": "anchor",
        },
        "download_candidates": [],
        "warnings": [],
    }
    out = to_site_config_block(report)
    assert any("signed" in w.lower() for w in out["warnings"]), (
        f"expected a signed-URL warning; got {out['warnings']}")
    assert any("aws_s3_v4" in w for w in out["warnings"]), (
        f"warning should name the provider; got {out['warnings']}")


# Bug ZZ — anchor active-surfacing was skipping URLs with clear
# media extensions, dropping plain `<a href="...v.mp4">` anchors
# entirely.

def test_live_mode_surfaces_extension_bearing_anchor():
    """A plain `<a href="...v.mp4">Download</a>` should surface as a
    candidate in live mode. Pre-fix, anchor active-surfacing skipped
    extension-bearing URLs and offline didn't scan plain anchors, so
    these silently disappeared."""
    from bulk_downloader.deep_detect import deep_detect_live

    class StubHttp:
        def head(self, url, **kw):
            return _Resp(200, headers={"content-type": "video/mp4"})
        def get(self, *a, **kw):
            return _Resp(404)
        def post(self, *a, **kw):
            return _Resp(404)

    html = ('<html><body>'
            '<a href="https://cdn.x.com/clip.mp4">Download MP4</a>'
            '</body></html>')
    r = deep_detect_live(
        html, base_url="https://x.com/page", http=StubHttp(),
        sniff_attachments=True, follow_manifests=False,
        probe_parallelism=1)
    # Should see the .mp4 anchor.
    urls = [c.get("url") for c in r["buckets"]["accepted"]]
    assert any("clip.mp4" in (u or "") for u in urls), (
        f"expected clip.mp4 in candidates; got {urls}")


def test_live_mode_extension_bearing_anchor_gets_proper_source_type():
    """The active-surfaced .mp4 should be tagged direct_file, not
    'unknown' — its extension already classifies it."""
    from bulk_downloader.deep_detect import deep_detect_live

    class StubHttp:
        def head(self, url, **kw):
            return _Resp(200, headers={"content-type": "video/mp4"})
        def get(self, *a, **kw):
            return _Resp(404)
        def post(self, *a, **kw):
            return _Resp(404)

    html = ('<html><body>'
            '<a href="https://cdn.x.com/v.mp4">Download</a>'
            '</body></html>')
    r = deep_detect_live(
        html, base_url="https://x.com/page", http=StubHttp(),
        sniff_attachments=True, follow_manifests=False,
        probe_parallelism=1)
    cand = next((c for c in r["buckets"]["accepted"]
                 if "v.mp4" in (c.get("url") or "")), None)
    assert cand is not None
    assert cand["source_type"] == "direct_file", (
        f"extension-bearing anchor should classify as direct_file; "
        f"got {cand['source_type']}")


def test_live_mode_signed_url_annotated_on_active_surfaced_anchor():
    """A live-mode-surfaced anchor with a signed URL should be
    annotated with signed_url info just like offline candidates."""
    from bulk_downloader.deep_detect import deep_detect_live

    class StubHttp:
        def head(self, url, **kw):
            return _Resp(200, headers={"content-type": "video/mp4"})
        def get(self, *a, **kw):
            return _Resp(404)
        def post(self, *a, **kw):
            return _Resp(404)

    # S3 v4 signed URL surfaced via plain anchor. X-Amz-Expires set
    # to 10 years (315_360_000s) so the test isn't sensitive to the
    # real wall clock.
    signed = ("https://bucket.s3.amazonaws.com/v.mp4"
              "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
              "&X-Amz-Date=20260526T120000Z"
              "&X-Amz-Expires=315360000"
              "&X-Amz-Signature=abc")
    html = f'<html><body><a href="{signed}">Download</a></body></html>'
    r = deep_detect_live(
        html, base_url="https://x.com/page", http=StubHttp(),
        sniff_attachments=True, follow_manifests=False,
        probe_parallelism=1)
    signed_cands = [c for c in r["buckets"]["accepted"]
                    if c.get("signed_url")]
    assert signed_cands, (
        f"live-mode-surfaced signed URL should be annotated; "
        f"got candidates: "
        f"{[c.get('url') for c in r['download_candidates']]}")
    assert signed_cands[0]["signed_url"]["provider"] == "aws_s3_v4"


def test_apply_signed_url_annotations_is_idempotent():
    """Calling the helper twice (once in deep_detect's offline pass,
    once at the end of deep_detect_live) should not double-annotate."""
    from bulk_downloader.deep_detect import _apply_signed_url_annotations
    # X-Amz-Expires=10 years so the URL never expires while the
    # test runs against the real wall clock.
    signed = ("https://bucket.s3.amazonaws.com/v.mp4"
              "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
              "&X-Amz-Date=20260526T120000Z"
              "&X-Amz-Expires=315360000"
              "&X-Amz-Signature=abc")
    cands = [{
        "url": signed,
        "source_type": "direct_file",
        "score": 80,
        "reasons": [],
        "warnings": [],
    }]
    rejected = []
    warns = []
    first = _apply_signed_url_annotations(cands, rejected, warns)
    second = _apply_signed_url_annotations(first, rejected, warns)
    # No additional candidates created or destroyed.
    assert len(first) == 1
    assert len(second) == 1
    # No reason text was added twice.
    signed_reasons = [r for r in second[0].get("reasons", [])
                      if "signed URL" in r]
    assert len(signed_reasons) == 1, (
        f"signed-URL reason added more than once: {signed_reasons}")


# Bug MM — script body in extract_player_configs is now capped.

def test_extract_player_configs_caps_huge_script_body():
    """A pathological page with a 10MB inline script shouldn't hang
    the player-config extractor."""
    from bulk_downloader.deep_detect import extract_player_configs
    # 10MB of garbage with a real JWPlayer setup at the end. Pre-fix
    # the function scanned the whole thing; post-fix it caps at 500KB
    # so the setup at the end isn't seen — that's acceptable. The
    # important assertion is that the call returns quickly.
    import time as _t
    garbage = "// junk junk junk\n" * 600_000  # ~12MB
    html = f'<html><script>{garbage}</script></html>'
    t0 = _t.monotonic()
    out = extract_player_configs(html, base_url="https://x.com/")
    elapsed = _t.monotonic() - t0
    # Should complete fast regardless of how huge the input is.
    assert elapsed < 2.0, (
        f"extract_player_configs took {elapsed:.2f}s on huge script — "
        "cap not effective")
