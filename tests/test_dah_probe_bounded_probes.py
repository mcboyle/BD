"""dah-probe (deep-adapters-http F2 F3 F4 F6 F7): a probe costs headers, tells a page from a file, and keeps
the site's cookies at the site.

F2  tier_probe: the Range GET behind a HEAD 405 was a plain GET -- a server that ignores Range sent the whole
    object into memory -- and any 200 was taken, so the site's HTML "not found" page became the upgraded tier.
F3  multi_conn.probe: the same unstreamed Range GET (8 MiB pulled to learn the answer was not a 206).
F4  tier_probe: the site's cookies (a flat dict, no domain) went to every redirect hop.
F6  tier_probe: every HEAD 403 counted as a signed-URL rejection, ending the cascade at the first absent tier.
F7  extractors_jsonapi: a scene / probe body of any size was read and parsed.

No network: ``ssrf_transport.guarded_transport`` is replaced by an ``httpx.MockTransport`` whose bodies are
streams that count every byte the client actually pulls.
"""
import httpx
import pytest

from bulk_downloader import extractors_jsonapi, multi_conn, ssrf_transport, tier_probe

BD_GATE_SCOPE = "module"

HOME = "93.184.216.34"           # public literals: pass the public-host checks, never contacted
AWAY = "93.184.216.35"
PATTERN = r"_(?P<tier>\d{3,4})p\.mp4"
MIB = 1024 * 1024


class Body(httpx.SyncByteStream):
    """A response body of ``size`` bytes that records how much of it was pulled."""

    def __init__(self, size, pulled, chunk=64 * 1024, fill=b"x"):
        self.size, self.pulled, self.chunk, self.fill = size, pulled, chunk, fill

    def __iter__(self):
        left = self.size
        while left > 0:
            n = min(self.chunk, left)
            self.pulled[0] += n
            left -= n
            yield self.fill * n


@pytest.fixture
def net(monkeypatch):
    """routes: (METHOD, path-or-url-substring) -> callable(request) -> httpx.Response"""
    state = type("Net", (), {})()
    state.routes, state.seen, state.pulled = {}, [], [0]

    def handler(request):
        state.seen.append((request.method, str(request.url), request.headers.get("cookie", ""),
                           request.headers.get("range", "")))
        for (method, needle), make in state.routes.items():
            if method == request.method and needle in str(request.url):
                return make(request)
        return httpx.Response(404)

    monkeypatch.setattr(ssrf_transport, "guarded_transport", lambda *a, **k: httpx.MockTransport(handler))
    state.body = lambda size, **kw: Body(size, state.pulled, **kw)
    return state


def _probe(url=f"http://{HOME}/v_720p.mp4", **kw):
    kw.setdefault("ladder", [4320, 2160, 720])
    return tier_probe.probe_higher_tiers(url, PATTERN, **kw)


# ---------------------------------------------------------------- F2
def test_f2_a_range_the_server_ignores_costs_no_body(net):
    net.routes[("HEAD", "v_4320p")] = lambda r: httpx.Response(405)
    net.routes[("GET", "v_4320p")] = lambda r: httpx.Response(
        200, headers={"content-type": "video/mp4"}, stream=net.body(3 * MIB))
    result = _probe()
    assert result.url.endswith("v_4320p.mp4")          # the object exists: it is the upgraded tier
    assert net.pulled[0] == 0, f"{net.pulled[0]} bytes of the object were pulled for one probe"


def test_f2_a_soft_404_page_is_not_chosen_as_the_upgraded_tier(net):
    page = lambda r: httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                                    stream=net.body(4096, fill=b"<"))
    net.routes[("HEAD", "v_4320p")] = lambda r: httpx.Response(405)
    net.routes[("GET", "v_4320p")] = page              # "not found" HTML served with 200
    net.routes[("HEAD", "v_2160p")] = lambda r: httpx.Response(200, headers={"content-type": "text/html"})
    result = _probe()
    assert result.url.endswith("v_720p.mp4"), result.probed_tiers
    assert result.chosen_tier == 720
    assert [s for _t, s in result.probed_tiers][:2] == ["GET-Range:200-not-media", "200-not-media"]


@pytest.mark.parametrize("answer,label", [
    (lambda r: httpx.Response(206, headers={"content-type": "video/mp4", "content-range": "bytes 0-0/9"}), "GET-Range:206"),
    (lambda r: httpx.Response(206), "GET-Range:206"),
], ids=["206-typed", "206-untyped"])
def test_f2_control_a_real_partial_answer_is_chosen(net, answer, label):
    net.routes[("HEAD", "v_4320p")] = lambda r: httpx.Response(405)
    net.routes[("GET", "v_4320p")] = answer
    result = _probe()
    assert (result.url.endswith("v_4320p.mp4"), result.probed_tiers[0]) == (True, (4320, label))
    assert [rng for m, u, _c, rng in net.seen if m == "GET"] == ["bytes=0-0"]


@pytest.mark.parametrize("headers", [{"content-type": "video/mp4"}, {"content-type": "application/octet-stream"}, {}],
                         ids=["video", "octet-stream", "no-type"])
def test_f2_control_a_head_200_that_is_not_a_page_is_chosen(net, headers):
    net.routes[("HEAD", "v_2160p")] = lambda r: httpx.Response(200, headers=headers)
    assert _probe().url.endswith("v_2160p.mp4")


# ---------------------------------------------------------------- F4
COOKIES = {"session": "SITE-SECRET"}


def _cookie_by_host(net):
    return {u.split("/")[2]: c for _m, u, c, _r in net.seen}


def test_f4_the_sites_cookies_do_not_follow_a_redirect_to_another_host(net):
    net.routes[("HEAD", f"{HOME}/v_4320p")] = lambda r: httpx.Response(302, headers={"location": f"http://{AWAY}/v_4320p.mp4"})
    net.routes[("HEAD", f"{AWAY}/v_4320p")] = lambda r: httpx.Response(200, headers={"content-type": "video/mp4"})
    result = _probe(cookies=COOKIES)
    assert result.url.endswith("v_4320p.mp4")          # the redirect is still followed
    assert _cookie_by_host(net) == {HOME: "session=SITE-SECRET", AWAY: ""}


@pytest.mark.parametrize("location", [f"http://{HOME}:8080/v_4320p.mp4", f"https://{HOME}/v_4320p.mp4"],
                         ids=["other-port", "other-scheme"])
def test_f4_another_port_or_scheme_of_the_same_host_is_another_origin(net, location):
    net.routes[("HEAD", f"http://{HOME}/v_4320p")] = lambda r: httpx.Response(302, headers={"location": location})
    _probe(cookies=COOKIES)
    hops = [(u, c) for _m, u, c, _r in net.seen if "v_4320p" in u]
    assert [c for _u, c in hops] == ["session=SITE-SECRET", ""], hops


def test_f4_the_range_get_fallback_keeps_the_cookies_home_too(net):
    net.routes[("HEAD", "v_4320p")] = lambda r: httpx.Response(405)
    net.routes[("GET", f"{HOME}/v_4320p")] = lambda r: httpx.Response(307, headers={"location": f"http://{AWAY}/v_4320p.mp4"})
    net.routes[("GET", f"{AWAY}/v_4320p")] = lambda r: httpx.Response(206)
    assert _probe(cookies=COOKIES).url.endswith("v_4320p.mp4")
    assert [(u.split("/")[2], c) for m, u, c, _r in net.seen if m == "GET"] == [(HOME, "session=SITE-SECRET"), (AWAY, "")]


def test_f4_control_a_redirect_inside_the_site_keeps_the_cookies(net):
    net.routes[("HEAD", f"{HOME}/v_4320p")] = lambda r: httpx.Response(302, headers={"location": "/moved/v_4320p.mp4"})
    net.routes[("HEAD", "/moved/")] = lambda r: httpx.Response(200)
    assert _probe(cookies=COOKIES).url.endswith("v_4320p.mp4")
    assert [c for _m, u, c, _r in net.seen if "4320" in u] == ["session=SITE-SECRET"] * 2


def test_f4_a_redirect_loop_ends(net):
    net.routes[("HEAD", "v_4320p")] = lambda r: httpx.Response(302, headers={"location": "/v_4320p.mp4"})
    result = _probe()
    assert len([1 for _m, u, _c, _r in net.seen if "4320" in u]) == 6      # the first request + 5 hops
    assert result.probed_tiers[0] == (4320, 302)


# ---------------------------------------------------------------- F6
def test_f6_a_403_on_an_unsigned_url_does_not_end_the_cascade(net):
    net.routes[("HEAD", "v_4320p")] = lambda r: httpx.Response(403)          # the CDN's "no such key"
    net.routes[("HEAD", "v_2160p")] = lambda r: httpx.Response(200)
    result = _probe()
    assert (result.url.endswith("v_2160p.mp4"), result.error) == (True, ""), result.probed_tiers


@pytest.mark.parametrize("query", ["Signature=abc&Expires=1", "X-Amz-Signature=abc", "token=abc", "Key-Pair-Id=K1"])
def test_f6_control_a_403_on_a_signed_url_still_stops_at_once(net, query):
    net.routes[("HEAD", "v_4320p")] = lambda r: httpx.Response(403)
    net.routes[("HEAD", "v_2160p")] = lambda r: httpx.Response(200)
    result = _probe(url=f"http://{HOME}/v_720p.mp4?{query}")
    assert (result.error, result.chosen_tier) == ("signed_url_rejection", 720)
    assert len(net.seen) == 1


# ---------------------------------------------------------------- F3
def test_f3_multi_conn_probe_pulls_no_body_when_range_is_ignored(net):
    net.routes[("HEAD", "/big.mp4")] = lambda r: httpx.Response(405)
    net.routes[("GET", "/big.mp4")] = lambda r: httpx.Response(200, stream=net.body(8 * MIB))
    result = multi_conn.probe(f"http://{HOME}/big.mp4")
    assert (result.ok, result.error) == (False, "head_405_get_200")
    assert net.pulled[0] == 0, f"{net.pulled[0]} bytes pulled to learn the answer was not a 206"


def test_f3_control_a_partial_answer_reports_the_total(net):
    net.routes[("HEAD", "/big.mp4")] = lambda r: httpx.Response(405)
    net.routes[("GET", "/big.mp4")] = lambda r: httpx.Response(
        206, headers={"content-range": "bytes 0-0/12345", "server": "cdn"}, stream=net.body(1))
    result = multi_conn.probe(f"http://{HOME}/big.mp4")
    assert (result.ok, result.content_length, result.accept_ranges, result.server) == (True, 12345, True, "cdn")
    assert result.final_url == f"http://{HOME}/big.mp4"


def test_f3_control_head_200_needs_no_get(net):
    net.routes[("HEAD", "/big.mp4")] = lambda r: httpx.Response(200, headers={"content-length": "999", "accept-ranges": "bytes"})
    assert multi_conn.probe(f"http://{HOME}/big.mp4").ok is True
    assert [m for m, *_ in net.seen] == ["HEAD"]


# ---------------------------------------------------------------- F7
CAP = 8 * MIB  # extractors_jsonapi.MAX_JSON_BODY_BYTES (asserted below; a literal so the file collects on base)
API = f"http://{HOME}/heresphere"


def test_f7_an_oversized_scene_is_dropped_at_the_cap(net):
    net.routes[("GET", "/heresphere/123")] = lambda r: httpx.Response(200, stream=net.body(40 * MIB, fill=b" "))
    assert extractors_jsonapi.fetch_scene(API, "123") is None
    assert net.pulled[0] <= CAP + 64 * 1024, f"{net.pulled[0]} bytes pulled; the cap is {CAP}"


def test_f7_a_declared_oversize_is_not_read_at_all(net):
    net.routes[("GET", "/heresphere/123")] = lambda r: httpx.Response(
        200, headers={"content-length": str(40 * MIB)}, stream=net.body(40 * MIB, fill=b" "))
    assert extractors_jsonapi.fetch_scene(API, "123") is None
    assert net.pulled[0] == 0


def test_f7_the_site_add_probe_has_the_same_cap(net):
    net.routes[("GET", "/heresphere")] = lambda r: httpx.Response(200, stream=net.body(40 * MIB, fill=b" "))
    outcome = extractors_jsonapi.probe_site(f"http://{HOME}")
    assert outcome.ok is False
    hits = len([1 for m, u, _c, _r in net.seen if "/heresphere" in u])
    assert net.pulled[0] <= hits * (CAP + 64 * 1024)


def test_f7_control_an_ordinary_scene_and_probe_still_parse(net):
    scene = b'{"title": "Sc\xc3\xa8ne", "media": []}'
    net.routes[("GET", "/heresphere")] = lambda r: httpx.Response(200, content=scene)
    assert extractors_jsonapi.fetch_scene(API, "123") == {"title": "Scène", "media": []}
    outcome = extractors_jsonapi.probe_site(f"http://{HOME}")
    assert (outcome.ok, outcome.sample_title) == (True, "Scène")


def test_f7_control_a_body_exactly_at_the_cap_is_read(net):
    body = b'{"title":"t"}' + b" " * (CAP - 13)
    net.routes[("GET", "/heresphere/123")] = lambda r: httpx.Response(200, content=body)
    assert extractors_jsonapi.fetch_scene(API, "123") == {"title": "t"}


def test_f7_the_cap_the_tests_assume_is_the_modules():
    assert extractors_jsonapi.MAX_JSON_BODY_BYTES == CAP


# ---------------------------------------------------------------- r2: ONE get = cookies home + cap, every hop
def _hop(location, size=0):
    """A redirect answer; ``size`` bytes of body ride with it (never to be pulled)."""
    return lambda r: httpx.Response(302, headers={"location": location}, stream=net_body[0](size))


net_body = [None]


@pytest.fixture
def hops(net):
    net_body[0] = net.body
    return net


def test_r2_a_redirects_body_is_never_pulled_and_the_final_answer_is_capped(hops):
    hops.routes[("GET", f"{HOME}/heresphere/123")] = _hop(f"http://{AWAY}/big", size=40 * MIB)
    hops.routes[("GET", f"{AWAY}/big")] = lambda r: httpx.Response(200, stream=hops.body(40 * MIB, fill=b" "))
    assert extractors_jsonapi.fetch_scene(API, "123", cookies=COOKIES) is None
    assert [u for _m, u, _c, _r in hops.seen] == [f"http://{HOME}/heresphere/123", f"http://{AWAY}/big"]
    assert hops.pulled[0] <= CAP + 64 * 1024, (
        f"DAHP-R2-HOP-OR-FINAL-BODY-UNBOUNDED {hops.pulled[0]} bytes pulled over 2 hops; the cap is {CAP}")


def test_r2_the_capped_get_keeps_the_sites_cookies_home(hops):
    hops.routes[("GET", f"{HOME}/heresphere/123")] = _hop(f"http://{AWAY}/scene")
    hops.routes[("GET", f"{AWAY}/scene")] = lambda r: httpx.Response(200, content=b'{"title": "t"}')
    assert extractors_jsonapi.fetch_scene(API, "123", cookies=COOKIES) == {"title": "t"}
    sent = {u: c for _m, u, c, _r in hops.seen}
    assert sent == {f"http://{HOME}/heresphere/123": "session=SITE-SECRET", f"http://{AWAY}/scene": ""}, (
        f"DAHP-R2-COOKIE-LEFT-HOME {sent}")


def test_r2_control_a_redirect_inside_the_site_keeps_cookies_and_parses(hops):
    hops.routes[("GET", f"{HOME}/heresphere/123")] = _hop("/moved/123", size=4096)
    hops.routes[("GET", f"{HOME}/moved/123")] = lambda r: httpx.Response(200, content=b'{"title": "t"}')
    assert extractors_jsonapi.fetch_scene(API, "123", cookies=COOKIES) == {"title": "t"}
    assert [c for _m, _u, c, _r in hops.seen] == ["session=SITE-SECRET"] * 2
    assert hops.pulled[0] == 0


def test_r2_a_redirect_loop_ends_with_no_body_read(hops):
    hops.routes[("GET", "/heresphere/123")] = _hop(f"http://{HOME}/heresphere/123", size=MIB)
    assert extractors_jsonapi.fetch_scene(API, "123", cookies=COOKIES) is None
    assert len(hops.seen) == 6 and hops.pulled[0] == 0, f"DAHP-R2-LOOP {len(hops.seen)} requests, {hops.pulled[0]} bytes"


def test_r2_the_site_add_probe_goes_through_the_same_get(hops):
    hops.routes[("GET", f"{HOME}/heresphere")] = _hop(f"http://{AWAY}/big", size=40 * MIB)
    hops.routes[("GET", f"{AWAY}/big")] = lambda r: httpx.Response(200, stream=hops.body(40 * MIB, fill=b" "))
    outcome = extractors_jsonapi.probe_site(f"http://{HOME}", cookies=COOKIES)
    assert outcome.ok is False
    assert [c for _m, u, c, _r in hops.seen if AWAY in u] == [""], "DAHP-R2-PROBE-COOKIE-LEFT-HOME"
    assert hops.pulled[0] <= CAP + 64 * 1024, f"DAHP-R2-PROBE-UNBOUNDED {hops.pulled[0]}"


def test_r2_the_capped_cookie_keeping_get_still_rides_the_proxy(hops, monkeypatch):
    built = []
    mock = ssrf_transport.guarded_transport()                      # the fixture's recording MockTransport

    def recording(policy, **kw):
        built.append(kw.get("proxy"))
        return mock

    monkeypatch.setattr(ssrf_transport, "guarded_transport", recording)
    hops.routes[("GET", f"{HOME}/heresphere/123")] = lambda r: httpx.Response(200, content=b'{"title": "t"}')
    got = extractors_jsonapi.fetch_scene(API, "123", cookies=COOKIES, proxy_url="socks5://127.0.0.1:19999")
    assert got == {"title": "t"} and built == ["socks5://127.0.0.1:19999"], f"DAHP-R2-PROXY-LOST {built}"
