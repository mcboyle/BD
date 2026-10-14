"""detect-extract spa-extract-fix r1: F2 F5 F7 F10 in spa_media_extract.py.

FINDINGS (O1789 bughunt detect-extract, BASE 2b7ca835):

  * F2 -- ``_PACKER_RE``'s lazy ``.*?`` body re-scans the rest of the text
    from every ``eval(function(p,a,c,k,e,d){`` start that has no valid tail:
    O(n^2) on a hoster frame body (240 KB took over 60 s).
  * F5 -- ``_LD_JSON_SCRIPT_RE`` and ``_VIDEO_ELEMENT_RE`` re-scan to the end
    from every unclosed ``<script type=ld+json>`` / ``<video>``: O(n^2) on
    ``page.content()`` (144 KB took 3.8 s).
  * F7 -- deeply nested JSON-LD raises RecursionError out of
    ``jsonld_scene_video_candidates`` (only ValueError/TypeError were caught),
    so the runner lost every JSON-LD candidate on the page.
  * F10 -- ``_same_site`` took a bare parent that is a public suffix
    (``test``, ``co.uk``) as the page's own site, widening ApiCapture scope.

RED on BASE (timing tests overrun the SIGALRM budget; F7 raises; F10 says
True), GREEN after the fix.  Parity tests pin the matches that must survive.
"""

# An ordinary module test: its subject is the module under test.
BD_GATE_SCOPE = "module"

import json
import signal

from bulk_downloader import spa_media_extract as spa


class _Timeout(Exception):
    pass


def _run_under(budget_s, fn, *a, **k):
    def _boom(*_):
        raise _Timeout
    old = signal.signal(signal.SIGALRM, _boom)
    signal.setitimer(signal.ITIMER_REAL, budget_s)
    try:
        return fn(*a, **k)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


_BUDGET = 2.0
PAGE = "https://site.test/v/1"
MP4 = "https://cdn.site.test/media/clip_720.mp4"


def _packed(payload, words):
    return ("eval(function(p,a,c,k,e,d){while(c--)if(k[c])p=p.replace(new RegExp("
            "'\\\\b'+c.toString(a)+'\\\\b','g'),k[c]);return p}"
            f"('{payload}',36,{len(words)},'{'|'.join(words)}'.split('|')))")


def _ld(obj):
    return f'<script type="application/ld+json">{json.dumps(obj)}</script>'


_VIDEO_OBJ = {"@type": "VideoObject", "url": PAGE, "contentUrl": MP4}
_VIDEO_TAG = f'<video id="p"><source src="{MP4}" type="video/mp4"></video>'


# ── F2: packer scan is linear ───────────────────────────────────────────────

def test_f2_packer_scan_linear_on_tailless_starts():
    html = "eval(function(p,a,c,k,e,d){}('" * 8000          # 240 KB
    try:
        out = _run_under(_BUDGET, spa.packed_player_media_urls, html)
    except _Timeout:
        raise AssertionError("SPA_F2_PACKER_QUADRATIC: packed_player_media_urls "
                             "is super-linear on tail-less packer starts")
    assert out == []


def test_f2_packer_scan_linear_on_starts_before_one_far_tail():
    html = ("eval(function(p,a,c,k,e,d){" * 8000
            + "}('0',36,1,'x'.split('|'))")
    try:
        _run_under(_BUDGET, spa.unpack_packed_scripts, html)
    except _Timeout:
        raise AssertionError("SPA_F2_PACKER_QUADRATIC: unpack_packed_scripts "
                             "is super-linear when many starts share one tail")


def test_f2_packer_parity_two_scripts_and_a_false_tail():
    one = _packed("0 1", ["hello", "world"])
    two = _packed("0", ["bye"])
    # A "}(" inside the function body whose tail does NOT match must not end
    # the search: the lazy body keeps going to the real tail.
    tricky = ("eval(function(p,a,c,k,e,d){var q=f}(1);return p}"
              "('0',36,1,'later'.split('|')))")
    assert spa.unpack_packed_scripts(one + ";" + two + ";" + tricky) == [
        "hello world", "bye", "later"]
    cfg = _packed("0:\"1\"", ["file", MP4])
    assert spa.packed_player_media_urls(cfg) == [MP4]


# ── F5: JSON-LD / <video> scans are linear ──────────────────────────────────

def test_f5_jsonld_scan_linear_on_unclosed_scripts():
    html = '<script type="application/ld+json">' * 6000      # 210 KB
    try:
        out = _run_under(_BUDGET, spa.jsonld_scene_video_candidates, PAGE, html)
    except _Timeout:
        raise AssertionError("SPA_F5_LDJSON_QUADRATIC: _LD_JSON_SCRIPT_RE scan "
                             "is super-linear on unclosed ld+json scripts")
    assert out == []


def test_f5_video_scan_linear_on_unclosed_videos():
    html = _ld(_VIDEO_OBJ) + "<video>" * 30000              # 210 KB
    try:
        out = _run_under(_BUDGET, spa.jsonld_scene_video_candidates, PAGE, html)
    except _Timeout:
        raise AssertionError("SPA_F5_VIDEO_QUADRATIC: _VIDEO_ELEMENT_RE scan "
                             "is super-linear on unclosed <video> tags")
    assert out == []


def test_f5_open_tags_without_gt_are_linear():
    for html in ('<script type="application/ld+json" ' * 6000,
                 _ld(_VIDEO_OBJ) + "<video " * 30000):
        try:
            _run_under(_BUDGET, spa.jsonld_scene_video_candidates, PAGE, html)
        except _Timeout:
            raise AssertionError("SPA_F5_OPEN_TAG_QUADRATIC: an open tag with "
                                 "no '>' is re-scanned from every start")


def test_f5_parity_scene_video_still_found():
    html = ("<html><head>" + _ld({"@type": "Thing"})
            + "<SCRIPT data-x=1 TYPE='application/ld+json'>"
            + json.dumps({"@graph": [_VIDEO_OBJ]}) + "</Script></head><body>"
            + '<video id="ad"><source src="https://ads.test/a.mp4"></video>'
            + _VIDEO_TAG + "</body></html>")
    cands = spa.jsonld_scene_video_candidates(PAGE, html)
    assert [c["url"] for c in cands] == [MP4], cands


# ── F7: deep JSON-LD nesting is skipped, not raised ─────────────────────────

def test_f7_deeply_nested_jsonld_is_skipped_and_later_scripts_still_count():
    bomb = '<script type="application/ld+json">' + "[" * 100000 + "]" * 100000 + "</script>"
    try:
        cands = spa.jsonld_scene_video_candidates(PAGE, bomb + _ld(_VIDEO_OBJ) + _VIDEO_TAG)
    except RecursionError:
        raise AssertionError("SPA_F7_RECURSION_ESCAPES: nested JSON-LD raised "
                             "RecursionError out of jsonld_scene_video_candidates")
    assert [c["url"] for c in cands] == [MP4], cands


def test_f7_control_bad_json_is_skipped():
    html = '<script type="application/ld+json">{bad</script>' + _ld(_VIDEO_OBJ) + _VIDEO_TAG
    assert [c["url"] for c in spa.jsonld_scene_video_candidates(PAGE, html)] == [MP4]


# ── F10: a public suffix is nobody's site ───────────────────────────────────

def test_f10_bare_public_suffix_parent_is_not_same_site():
    for page, url in (("https://www.site.test/x", "https://test/api/v"),
                      ("https://shop.example.co.uk/", "https://co.uk/api/x"),
                      ("https://co.uk/", "https://shop.example.co.uk/api/x")):
        assert not spa._same_site(page, url), (
            f"SPA_F10_PUBLIC_SUFFIX_SAME_SITE: {page} ~ {url}")
        assert not spa.is_api_json_response(page, url, "application/json")


def test_f10_control_same_site_still_holds():
    assert spa._same_site("https://site.test/x", "https://api.site.test/api/v")
    assert spa._same_site("https://api.site.test/x", "https://site.test/api/v")
    assert spa._same_site("https://shop.example.co.uk/", "https://example.co.uk/api/x")
    assert spa._same_site("https://Site.Test/x", "https://site.test/api/v")
    assert not spa._same_site("https://www.site.test/x", "https://evil.test/api/v")
    assert spa.is_api_json_response("https://site.test/x", "https://api.site.test/api/v",
                                    "application/json")
