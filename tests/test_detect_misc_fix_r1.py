"""detect-extract detect-misc-fix r1: F3 F4 F6 F8 F9.

FINDINGS (O1789 bughunt detect-extract, BASE 2b7ca835):

  * F3 -- ``interstitial.RECURRING_CHARGE_LANGUAGE`` re-scanned a long
    "1,1,1,..." run from every ",<digit>": 16 KB of body text took 6 s in
    ``_prechecked_billing_consent``, which runs before a gate click.
  * F4 -- ``extract_player_configs`` ran ``_REACTPLAYER_URL_RE`` (144 KB:
    19.8 s), ``_JWPLAYER_SETUP_RE`` (7.6 s) and ``_VIDEOJS_SOURCES_RE``
    through finditer, each quadratic on unterminated starts.
  * F6 -- when a real httpx client's ``.stream()`` raised mid-body,
    ``_fetch_manifest_capped`` fell back to ``.get()``, which buffers the
    whole body before the cap (256 MiB pulled against a 1 MiB cap).
  * F8 -- ``parse_hls_master`` passed variant URIs ``file:``,
    ``javascript:`` and a loopback host through as variants.
  * F9 -- ``_control_same_origin`` took "https:///evil.test/join" and
    "https:\\\\evil.test/join" (no netloc to urlsplit) as same-origin; a
    browser opens both on evil.test.

RED on BASE, GREEN after the fix.  Controls pin what must keep working.
"""

# An ordinary module test: its subject is the module under test.
BD_GATE_SCOPE = "module"

import signal

import httpx

from bulk_downloader import interstitial as I
from bulk_downloader.deep_detect import manifests as M
from bulk_downloader.deep_detect import orchestrate as O
from bulk_downloader.deep_detect import providers as P


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
BASE = "https://site.test/v/1"
MP4 = "https://cdn.site.test/media/clip_720.mp4"


# ── F3: recurring-charge scan is linear ─────────────────────────────────────

class _BodyPage:
    url = "https://site.test/store"

    def __init__(self, text, checked=1):
        self.text, self.checked = text, checked

    def inner_text(self, selector, timeout=None):
        return self.text

    def locator(self, selector):
        page = self

        class _L:
            def count(self):
                return page.checked
        return _L()


def test_f3_charge_language_scan_linear_on_digit_comma_run():
    page = _BodyPage("1," * 20000)                              # 40 KB
    try:
        verdict = _run_under(_BUDGET, I._prechecked_billing_consent, page)
    except _Timeout:
        raise AssertionError("DETECT_F3_CHARGE_REGEX_QUADRATIC: "
                             "RECURRING_CHARGE_LANGUAGE is super-linear on '1,1,1,...'")
    assert verdict == ("clear", "")


def test_f3_control_charge_language_still_refuses():
    for text, phrase in (("you agree: $29.97 every 30 days", "$29.97 every 30 days"),
                         ("then 1,299.00 USD per month", "1,299.00 USD per month"),
                         ("1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,"
                          "1,1,1,1,1,999 usd/month", "999 usd/month"),
                         ("billed until you cancel", "until you cancel")):
        verdict = I._prechecked_billing_consent(_BodyPage("Join now. " + text))
        assert verdict[0] == "refuse" and phrase.split()[-1] in verdict[1], (text, verdict)
    assert I._prechecked_billing_consent(_BodyPage("a sale: $5 off")) == ("clear", "")


# ── F4: player-config scans are linear ─────────────────────────────────────

def _configs_under_budget(html, diag):
    try:
        return _run_under(_BUDGET, P.extract_player_configs, html, base_url=BASE)
    except _Timeout:
        raise AssertionError(diag)


def test_f4_reactplayer_scan_linear():
    out = _configs_under_budget("<div>" + "ReactPlayer " * 12000 + "</div>",    # 144 KB
                                "DETECT_F4_REACTPLAYER_QUADRATIC: _REACTPLAYER_URL_RE "
                                "is super-linear on url-less starts")
    assert out == []


def test_f4_reactplayer_scan_linear_on_unclosed_values():
    html = "<div>ReactPlayer " + "url={[ " * 20000 + "</div>"            # 140 KB
    out = _configs_under_budget(html, "DETECT_F4_REACTPLAYER_QUADRATIC: _REACTPLAYER_URL_RE "
                                      "is super-linear on url={[ with no ]")
    assert out == []


def test_f4_jwplayer_scan_linear():
    html = "<script>" + "jwplayer().setup({" * 8000 + "</script>"        # 144 KB
    out = _configs_under_budget(html, "DETECT_F4_JWPLAYER_QUADRATIC: _JWPLAYER_SETUP_RE "
                                      "is super-linear on tail-less setups")
    assert out == []


def test_f4_videojs_scan_linear():
    html = "<script>" + "sources:[" * 22000 + "</script>"               # 198 KB
    out = _configs_under_budget(html, "DETECT_F4_VIDEOJS_QUADRATIC: _VIDEOJS_SOURCES_RE "
                                      "is super-linear on unclosed arrays")
    assert out == []


def test_f4_control_player_configs_still_found():
    html = ("<script>jwplayer(\"p\").setup({\"sources\": [{\"file\": \"https://cdn.site.test/jw_1080.mp4\"}]});</script>"
            "<script>var cfg = {sources: [{\"src\": \"https://cdn.site.test/vjs_480.mp4\"}]};</script>"
            f"<div><ReactPlayer controls url=\"{MP4}\" /></div>")
    urls = sorted(c["url"] for c in P.extract_player_configs(html, base_url=BASE))
    assert urls == sorted(["https://cdn.site.test/jw_1080.mp4",
                           "https://cdn.site.test/vjs_480.mp4", MP4]), urls


# ── F6: a failed stream is not retried as a buffered GET ───────────────────

MB = 1 << 20


class _Gen(httpx.SyncByteStream):
    def __init__(self, total, pulled, fail_after=None):
        self.total, self.pulled, self.fail_after = total, pulled, fail_after

    def __iter__(self):
        sent = 0
        while sent < self.total:
            if self.fail_after is not None and sent >= self.fail_after:
                raise httpx.ReadError("peer reset mid-body")
            sent += 65536
            self.pulled[0] += 65536
            yield b"A" * 65536


def _client(pulled, fail_first):
    calls = [0]

    def handler(request):
        calls[0] += 1
        fail = 64 * 1024 if fail_first and calls[0] == 1 else None
        return httpx.Response(200, stream=_Gen(16 * MB, pulled, fail))
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_f6_stream_failure_does_not_buffer_whole_body():
    pulled = [0]
    with _client(pulled, fail_first=True) as c:
        out = O._fetch_manifest_capped(c, "https://cdn.test/a.m3u8", cap_bytes=1 * MB)
    assert pulled[0] <= 2 * MB, (
        f"DETECT_F6_FALLBACK_BUFFERS_BODY: pulled {pulled[0] / MB:.1f} MiB "
        f"against a 1 MiB cap after the stream failed")
    assert out["ok"] is False and "ReadError" in (out["error"] or ""), out["error"]


def test_f6_control_stream_ok_and_stub_fallback_kept():
    pulled = [0]
    with _client(pulled, fail_first=False) as c:
        out = O._fetch_manifest_capped(c, "https://cdn.test/a.m3u8", cap_bytes=1 * MB)
    assert out["ok"] and out["truncated"] and pulled[0] <= 2 * MB

    class _Stub:                       # a test stub whose stream is broken
        def stream(self, *a, **k):
            raise TypeError("not a context manager")

        def get(self, url, **k):
            class R:
                status_code, url, headers, text = 200, "https://cdn.test/a.m3u8", {}, "#EXTM3U\n"
            return R()
    out = O._fetch_manifest_capped(_Stub(), "https://cdn.test/a.m3u8", cap_bytes=1 * MB)
    assert out["ok"] and out["body"] == "#EXTM3U\n" and out["error"] is None


# ── F8: HLS variants are http(s) media of the manifest's site ──────────────

_MASTER = ("#EXTM3U\n"
           "#EXT-X-STREAM-INF:BANDWIDTH=9000000,RESOLUTION=3840x2160\nfile:///etc/passwd\n"
           "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\njavascript:alert(1)\n"
           "#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720\nhttp://127.0.0.1:8080/admin.m3u8\n"
           "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=960x540\nhttp://[::1]/v.m3u8\n"
           "#EXT-X-STREAM-INF:BANDWIDTH=500000,RESOLUTION=640x360\nv360.m3u8\n")


def test_f8_hls_variants_with_bad_scheme_or_local_host_are_dropped():
    out = M.parse_hls_master(_MASTER, base_url="https://cdn.test/master.m3u8")
    urls = [v["url"] for v in out["variants"]]
    assert urls == ["https://cdn.test/v360.m3u8"], (
        f"DETECT_F8_HLS_VARIANT_SCHEME: variants {urls}")
    assert sum("refused" in w for w in out["warnings"]) == 4, out["warnings"]


def test_f8_control_local_manifest_and_relative_variants_kept():
    local = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=1280x720\n"
             "http://127.0.0.1:8080/v720.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=640x360\nv360.m3u8\n")
    out = M.parse_hls_master(local, base_url="http://127.0.0.1:8080/master.m3u8")
    assert [v["url"] for v in out["variants"]] == [
        "http://127.0.0.1:8080/v720.m3u8", "http://127.0.0.1:8080/v360.m3u8"]
    out = M.parse_hls_master(local.replace("http://127.0.0.1:8080/", "https://cdn.test/"))
    # No base: the relative variant keeps the url decode_url gives it ("").
    assert [v["url"] for v in out["variants"]] == ["https://cdn.test/v720.m3u8", ""]


# ── F9: an href a browser opens elsewhere is not same-origin ───────────────

class _UrlPage:
    url = "https://site.test/members"


def test_f9_hostless_absolute_hrefs_are_not_same_origin():
    for href in ("https:///evil.test/join", "https:\\\\evil.test/join",
                 "\\\\evil.test/join", "/\\evil.test/join", " //evil.test/join",
                 "ht\ntps://evil.test/join"):
        assert I._control_same_origin(_UrlPage(), href) is False, (
            f"DETECT_F9_HOSTLESS_HREF_SAME_ORIGIN: {href!r}")


def test_f9_control_same_origin_still_admitted():
    for href in (None, "", "/join", "join?x=1", "#top", "javascript:void(0)",
                 "https://site.test/join", "HTTPS://SITE.TEST/join", " /join "):
        assert I._control_same_origin(_UrlPage(), href) is True, href
    assert I._control_same_origin(_UrlPage(), "https://evil.test/join") is False
