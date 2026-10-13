"""at-fix -- the adapters-transport sweep findings (O1789 bughunt, finder S51-B).

One test per FOUND row that this cut fixes; ids (F1.., TU1.., RA1..) are the
sweep's. Every sink is a stand-in: httpx.MockTransport, an in-memory tus
server, stand-in aioquic / StealthyFetcher objects. No socket, no app, no site.
"""
import asyncio
import json
import sys
import types

import flask
import httpx
import pytest

from bulk_downloader import app_flaresolverr as af
from bulk_downloader import app_scrapling as asc
from bulk_downloader import flaresolverr_client as fc
from bulk_downloader import http3_client as h3
from bulk_downloader import rate_adapter as ra
from bulk_downloader import scrapling_adapter as sa
from bulk_downloader import ssrf_transport
from bulk_downloader import tus_client as tc

# Seven named modules and their own inputs: not a repo-wide gate.
BD_GATE_SCOPE = "module"

EP = "http://flaresolverr.fixture:8191/v1"
GOOD_SOLUTION = {"status": 200, "response": "<html>x</html>", "userAgent": "UA",
                 "cookies": [{"name": "cf_clearance", "value": "v", "domain": ".site.example"}]}


# ───────────────────────── flaresolverr_client ─────────────────────────
def _flare(monkeypatch, *, post=None, get=None):
    """Route the client's httpx calls into a MockTransport; returns the seen requests."""
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            return get()
        return httpx.Response(200, json=post)

    monkeypatch.setattr(ssrf_transport, "guarded_transport", lambda *a, **k: httpx.MockTransport(handler))
    fc.reset_stats()
    return seen


def _solve(monkeypatch, body, url="https://site.example/"):
    _flare(monkeypatch, post=body)
    result = fc.solve_cloudflare(url, endpoint=EP, timeout_s=5)
    stats = fc.stats()
    return result, (stats["solves_succeeded"], stats["solves_failed"])


def test_f0_control_a_well_formed_solution_is_a_solve(monkeypatch):
    result, counts = _solve(monkeypatch, {"status": "ok", "solution": GOOD_SOLUTION})
    assert (result.ok, result.status_code, len(result.cookies), result.error) == (True, 200, 1, "")
    assert counts == (1, 0)


@pytest.mark.parametrize("status", ["blocked", [403], {"code": 403}, True, "1e999"])
def test_f1_a_non_integer_solution_status_is_a_counted_failure(monkeypatch, status):
    result, counts = _solve(monkeypatch, {"status": "ok", "solution": {"status": status, "response": "x"}})
    assert result.ok is False and result.error == "bad_solution_status"
    assert counts == (0, 1), "a malformed block must not be counted as a solve"


@pytest.mark.parametrize("body", [{"status": "ok"}, {"status": "ok", "solution": None},
                                  {"status": "ok", "solution": {}}, {"status": "ok", "solution": [1]}])
def test_f2_status_ok_without_a_solution_block_is_not_a_solve(monkeypatch, body):
    result, counts = _solve(monkeypatch, body)
    assert result.ok is False and result.error == "no_solution_block"
    assert counts == (0, 1)


def test_f3_a_403_interstitial_without_clearance_is_not_a_solve(monkeypatch):
    challenge = {"status": 403, "response": "<title>Just a moment...</title>", "cookies": []}
    result, counts = _solve(monkeypatch, {"status": "ok", "solution": challenge})
    assert result.ok is False and result.error == "still_challenged:http_403"
    assert (result.status_code, counts) == (403, (0, 1))
    # controls: the same 403 WITH a clearance cookie, and an ordinary 403 page, stay solves
    cleared = dict(challenge, cookies=[{"name": "cf_clearance", "value": "v", "domain": ".site.example"}])
    assert _solve(monkeypatch, {"status": "ok", "solution": cleared})[0].ok is True
    plain = {"status": 403, "response": "<h1>Members only</h1>", "cookies": []}
    assert _solve(monkeypatch, {"status": "ok", "solution": plain})[0].ok is True


def test_f4_ping_only_calls_a_flaresolverr_live(monkeypatch):
    _flare(monkeypatch, get=lambda: httpx.Response(200, text="<html>It works! nginx</html>",
                                                   headers={"content-type": "text/html"}))
    assert fc.ping(EP) == {"ok": False, "error": "not_flaresolverr"}
    _flare(monkeypatch, get=lambda: httpx.Response(200, json={"msg": "FlareSolverr is ready!", "version": "3.3.21"}))
    assert fc.ping(EP)["ok"] is True and fc.ping(EP)["version"] == "3.3.21"
    _flare(monkeypatch, get=lambda: httpx.Response(200, text="FlareSolverr is ready!"))   # old text-only versions
    assert fc.ping(EP) == {"ok": True, "version": "", "userAgent": ""}


def test_f5_cookie_translation_drops_a_bool_expiry_and_a_non_string_domain():
    out = fc._translate_cookies_to_playwright([
        {"name": "a", "value": "1", "domain": "d", "expires": True},
        {"name": "b", "value": "1", "domain": ["x"]},
        {"name": "c", "value": "1", "domain": "d", "expires": 1900000000},
    ])
    assert [c["name"] for c in out] == ["a", "c"]
    assert "expires" not in out[0], "expires: true is not a timestamp"
    assert out[1]["expires"] == 1900000000.0


# ───────────────────────── routes ─────────────────────────
@pytest.fixture
def client(monkeypatch):
    for mod in (af, asc):
        monkeypatch.setattr(mod, "_check_csrf", lambda *a, **k: None)
    monkeypatch.setattr(af, "_app__FLARE_AVAILABLE", lambda: True)
    monkeypatch.setattr(af, "_app__flare_client", lambda: fc)
    monkeypatch.setattr(asc, "_app__SCRAP_AVAILABLE", lambda: True)
    monkeypatch.setattr(asc, "_app__scrap_adapter", lambda: sa)
    app = flask.Flask("at-fix")
    app.register_blueprint(af.flaresolverr_bp)
    app.register_blueprint(asc.scrapling_bp)
    return app.test_client()


def _post(client, path, payload):
    response = client.post(path, data=json.dumps(payload), content_type="application/json")
    return response.status_code, response.get_json(silent=True)


@pytest.mark.parametrize("path,payload", [
    ("/api/flaresolverr/test", [1]),
    ("/api/flaresolverr/test", {"url": 5}),
    ("/api/flaresolverr/test", {"url": "https://x.example/", "endpoint": 7}),
    ("/api/scrapling/recover", {"html": "<p>x</p>", "fingerprint": {"tag": "p"}, "min_score": "high"}),
    ("/api/scrapling/recover", {"html": "<p>x</p>", "fingerprint": {"tag": "p"}, "min_score": None}),
    ("/api/scrapling/recover", {"html": 5, "fingerprint": {"tag": "p"}}),
    ("/api/scrapling/fingerprint", "text"),
    ("/api/scrapling/fingerprint", {"html": 5, "selector": "p"}),
    ("/api/scrapling/turnstile_check", [1]),
])
def test_f7_wrong_typed_json_is_a_400_not_a_500(client, path, payload):
    status, body = _post(client, path, payload)
    assert status == 400 and body["ok"] is False and body["error"]


@pytest.mark.parametrize("timeout_s", ["nan", "inf", "-inf", -5, 601])
def test_f8_the_test_route_refuses_an_unusable_timeout(client, monkeypatch, timeout_s):
    seen = _flare(monkeypatch, post={"status": "ok", "solution": GOOD_SOLUTION})
    monkeypatch.setattr(fc, "DEFAULT_ENDPOINT", EP)
    assert _post(client, "/api/flaresolverr/test", {"url": "https://x.example/", "timeout_s": timeout_s}) == (
        400, {"ok": False, "error": "invalid 'timeout_s'"})
    assert seen == [], "a refused timeout must not reach the solver"
    status, body = _post(client, "/api/flaresolverr/test", {"url": "https://x.example/", "timeout_s": 30})
    assert (status, body["ok"], len(seen)) == (200, True, 1)   # control


@pytest.mark.parametrize("min_score", [-1, 1.5, True])
def test_f13_the_recover_route_refuses_a_min_score_outside_0_1(client, min_score):
    status, body = _post(client, "/api/scrapling/recover",
                         {"html": "<p>x</p>", "fingerprint": {"tag": "p"}, "min_score": min_score})
    assert status == 400 and "min_score" in body["error"]


# ───────────────────────── scrapling_adapter ─────────────────────────
def _bypass(monkeypatch, fetch):
    calls = []

    class StealthyFetcher:
        @staticmethod
        def fetch(*args, **kwargs):
            calls.append((args, sorted(kwargs)))
            return fetch(*args, **kwargs)

    monkeypatch.setitem(sys.modules, "scrapling", types.SimpleNamespace(StealthyFetcher=StealthyFetcher))
    monkeypatch.setattr(sa, "is_stealthy_fetcher_available", lambda: True)
    sa.reset_stats()
    result = sa.bypass_turnstile("https://site.example/", timeout_s=2)
    return result, sa.stats(), calls


@pytest.mark.parametrize("page", [None, object(), types.SimpleNamespace(cookies=[], html_content="")])
def test_f9_a_fetch_that_returned_nothing_is_not_a_bypass(monkeypatch, page):
    result, stats, _ = _bypass(monkeypatch, lambda *a, **k: page)
    assert result.ok is False and result.error == "bypass_returned_nothing"
    assert (stats["turnstile_bypassed"], stats["turnstile_failed"]) == (0, 1)


def test_f9_control_and_f14_rate_counts_the_bypasses_that_ran(monkeypatch):
    page = types.SimpleNamespace(cookies=[{"name": "cf_clearance", "value": "v"}], html_content="<h1>ok</h1>")
    result, stats, _ = _bypass(monkeypatch, lambda *a, **k: page)
    assert result.ok is True and len(result.cookies) == 1
    # F14: the dispatcher never bumped turnstile_detected here; one bypass of one is 100%, not 0%
    assert (stats["turnstile_detected"], stats["turnstile_bypassed"], stats["turnstile_success_rate"]) == (0, 1, 1.0)
    _, stats, _ = _bypass(monkeypatch, lambda *a, **k: None)
    assert stats["turnstile_success_rate"] == 0.0


@pytest.mark.parametrize("html,expected", [
    ('<div class="g-recaptcha" data-sitekey="6Lc"></div>', False),          # F11: reCAPTCHA
    ('<div class="h-captcha" data-sitekey="10000000"></div>', False),       # F11: hCaptcha
    ('<div class="cf-turnstile" data-sitekey="0x4"></div>', True),          # control: a Turnstile widget
    ('<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>', True),
    ("<h1>plain</h1>", False),
])
def test_f11_a_bare_sitekey_is_not_a_turnstile_signal(html, expected):
    assert sa.is_turnstile_page(html) is expected


PAGE = ('<html><body><section class="ads"><video id="promo2" src="/ad.mp4"></video></section>'
        '<section class="stage"><video id="film" src="/movie.mp4"></video></section></body></html>')


def test_f12_candidates_the_fingerprint_cannot_tell_apart_are_not_a_match():
    pytest.importorskip("scrapling")
    sa.reset_stats()
    result = sa.recover_selector(PAGE, {"tag": "video", "original_selector": "div.player video"})
    assert (result.ok, result.error, result.selector) == (False, "ambiguous_candidates", "")
    assert result.candidates_considered == 2 and sa.stats()["recoveries_succeeded"] == 0
    # control: evidence that singles one element out still recovers it
    links = '<html><body><a class="x" id="n1">Other</a><a class="dl" id="n2">Download now</a></body></html>'
    fingerprint = sa.build_fingerprint(links, "a.dl")
    moved = '<html><body><a class="x" id="n1">Other</a><p><a class="get" id="n9">Download now</a></p></body></html>'
    recovered = sa.recover_selector(moved, fingerprint)
    assert (recovered.ok, recovered.selector) == (True, "#n9")
    # control: one same-tag element is not ambiguous
    single = sa.recover_selector('<html><body><video id="only" src="/m.mp4"></video></body></html>',
                                 {"tag": "video", "original_selector": "div.gone video"})
    assert (single.ok, single.selector) == (True, "#only")


# r2 (lens D169 R1): the tie guard must not refuse copies of ONE target. The fingerprint is of
# <a id="dl-k3j" class="btn dl" href="/f/1.mp4">Download</a>; every page below drifted that selector.
def _button_fingerprint():
    return sa.build_fingerprint(
        '<html><body><a id="dl-k3j" class="btn dl" href="/f/1.mp4">Download</a></body></html>', "#dl-k3j")


def _page(*anchors):
    return "<html><body>" + "<p>t</p>".join(anchors) + "</body></html>"


@pytest.mark.parametrize("anchors,selector", [
    # D169 probe: header + footer copies of the button, distinct ids, same href
    (('<a id="dl-x9" class="btn dl" href="/f/1.mp4">Download</a>',
      '<a id="dl-y7" class="btn dl" href="/f/1.mp4">Download</a>'), "#dl-x9"),
    # D169 probe: copies told apart only by a position class, same href
    (('<a class="btn dl top" href="/f/1.mp4">Download</a>',
      '<a class="btn dl bottom" href="/f/1.mp4">Download</a>'), "a.top"),
    # the copies moved to a new URL together: still one target
    (('<a id="dl-x9" class="btn dl" href="/f/9.mp4">Download</a>',
      '<a id="dl-y7" class="btn dl" href="/f/9.mp4">Download</a>'), "#dl-x9"),
    # a look-alike for another file comes FIRST: the stored href picks the right one
    (('<a id="other" class="btn dl" href="/f/2.mp4">Download</a>',
      '<a id="mine" class="btn dl" href="/f/1.mp4">Download</a>'), "#mine"),
])
def test_r2_a_tie_between_copies_of_one_target_is_recovered(anchors, selector):
    pytest.importorskip("scrapling")
    result = sa.recover_selector(_page(*anchors), _button_fingerprint())
    assert (result.ok, result.selector, result.error) == (True, selector, "")


@pytest.mark.parametrize("anchors", [
    # two different files, neither the stored one
    ('<a id="a2" class="btn dl" href="/f/2.mp4">Download</a>', '<a id="a3" class="btn dl" href="/f/3.mp4">Download</a>'),
    # nothing to compare: no href on either
    ('<a id="a2" class="btn dl">Download</a>', '<a id="a3" class="btn dl">Download</a>'),
    # one has the target stripped: not provably the same thing
    ('<a id="a2" class="btn dl" href="/f/2.mp4">Download</a>', '<a id="a3" class="btn dl">Download</a>'),
])
def test_r2_a_tie_between_different_targets_is_still_refused(anchors):
    pytest.importorskip("scrapling")
    result = sa.recover_selector(_page(*anchors), _button_fingerprint())
    assert (result.ok, result.selector, result.error) == (False, "", "ambiguous_candidates")


def test_r2_control_a_single_button_still_recovers():
    pytest.importorskip("scrapling")
    page = _page('<a id="dl-x9" class="btn dl" href="/f/1.mp4">Download</a>', '<a class="nav">Home</a>')
    result = sa.recover_selector(page, _button_fingerprint())
    assert (result.ok, result.selector) == (True, "#dl-x9")


@pytest.mark.parametrize("min_score", [float("nan"), float("inf"), -1, 1.01, True, "0.5"])
def test_f13_recover_selector_refuses_a_threshold_outside_0_1(min_score):
    pytest.importorskip("scrapling")
    result = sa.recover_selector(PAGE, {"tag": "video"}, min_score=min_score)
    assert (result.ok, result.error, result.selector) == (False, "invalid_min_score", "")


def test_f14_a_still_working_selector_is_not_counted_as_a_recovery():
    pytest.importorskip("scrapling")
    links = '<html><body><a class="dl" id="n2">Download now</a></body></html>'
    fingerprint = sa.build_fingerprint(links, "a.dl")
    sa.reset_stats()
    result = sa.recover_selector(links, fingerprint)
    assert (result.ok, result.selector) == (True, "a.dl")
    stats = sa.stats()
    assert (stats["recoveries_attempted"], stats["recoveries_succeeded"]) == (0, 0)
    # control: a real recovery counts once on each side
    moved = '<html><body><p><a class="get" id="n9">Download now</a></p></body></html>'
    assert sa.recover_selector(moved, fingerprint).ok is True
    stats = sa.stats()
    assert (stats["recoveries_attempted"], stats["recoveries_succeeded"]) == (1, 1)


# ───────────────────────── http3_client ─────────────────────────
def test_f15_two_transfers_to_one_destination_do_not_share_a_temp_file(tmp_path):
    dest = tmp_path / "media.bin"
    with h3._atomic_destination(dest) as first:
        first.write(b"A" * 8)
        with h3._atomic_destination(dest) as second:
            second.write(b"B" * 4)
            first.flush()
            second.flush()
            assert first.name != second.name
        assert dest.read_bytes() == b"B" * 4          # the inner transfer published its own bytes
    assert dest.read_bytes() == b"A" * 8              # then the outer one, whole
    assert sorted(p.name for p in tmp_path.iterdir()) == ["media.bin"], "no temp file left behind"


def test_f15_control_a_failed_transfer_leaves_the_published_file_alone(tmp_path):
    dest = tmp_path / "media.bin"
    dest.write_bytes(b"complete")
    with pytest.raises(RuntimeError):
        with h3._atomic_destination(dest) as fh:
            fh.write(b"partial")
            raise RuntimeError("transfer died")
    assert dest.read_bytes() == b"complete" and [p.name for p in tmp_path.iterdir()] == ["media.bin"]


def _quic(monkeypatch, tmp_path, url, status, chunks):
    class Protocol:
        async def wait_connected(self):
            return None

        async def get(self, _url):
            async def body():
                for chunk in chunks:
                    yield chunk
            return status, body()

    class Context:
        async def __aenter__(self):
            return Protocol()

        async def __aexit__(self, *exc):
            return False

    seen = {}

    def connect(host, port, **_kw):
        seen.update(host=host, port=port)
        return Context()

    fake = {"aioquic.asyncio": types.SimpleNamespace(connect=connect),
            "aioquic.quic.configuration": types.SimpleNamespace(QuicConfiguration=lambda **k: None)}
    real_import = h3.importlib.import_module
    monkeypatch.setattr(h3.importlib, "import_module", lambda name: fake[name] if name in fake else real_import(name))
    monkeypatch.setattr(h3, "_guard_quic_destination", lambda host: None)
    monkeypatch.setattr(h3, "_aioquic_h3_protocol_class", lambda read_timeout=5.0: object)
    dest = tmp_path / "q.bin"
    try:
        outcome = asyncio.run(h3._quic_fetch(httpx.URL(url), dest, 2.0))
    except h3.Http3TransportError as error:
        outcome = error
    return outcome, seen, (dest.read_bytes() if dest.exists() else None)


@pytest.mark.parametrize("status", [302, 0, 199, 304])
def test_f16_only_a_2xx_quic_response_is_published(monkeypatch, tmp_path, status):
    outcome, seen, published = _quic(monkeypatch, tmp_path, "https://cdn.example/v.mp4", status, [b"<html>moved</html>"])
    assert isinstance(outcome, h3.Http3TransportError) and f"HTTP/3 status {status}" in str(outcome)
    assert published is None and seen == {"host": "cdn.example", "port": 443}


def test_f16_control_a_200_is_published(monkeypatch, tmp_path):
    outcome, _, published = _quic(monkeypatch, tmp_path, "https://cdn.example/v.mp4", 200, [b"me", b"dia"])
    assert outcome[0] == 5 and published == b"media"


@pytest.mark.parametrize("url", ["http://cdn.example/v.mp4", "http://cdn.example:8080/v.mp4"])
def test_f17_an_http_url_is_never_fetched_over_quic(monkeypatch, tmp_path, url):
    outcome, seen, published = _quic(monkeypatch, tmp_path, url, 200, [b"ok"])
    assert isinstance(outcome, h3.Http3TransportError) and "https URL" in str(outcome)
    assert seen == {} and published is None, "no UDP connect for a non-https URL"


# ───────────────────────── tus_client ─────────────────────────
class FakeTus:
    """In-memory tus server. fail_patch: PATCH ordinals answered 503 (not committed)."""

    def __init__(self, length=None, fail_patch=(), accept_max=None, on_patch=None, state_length=True):
        self.length, self.data, self.patches, self.fail_patch = length, b"", 0, set(fail_patch)
        self.accept_max, self.on_patch, self.sent, self.state_length = accept_max, on_patch, [], state_length

    async def request(self, method, url, headers, body=b""):
        if method == "POST":
            self.length = int(headers["Upload-Length"])
            return tc.TusResponse(201, {"Location": "/up/1"})
        if method == "HEAD":
            reply = {"Upload-Offset": str(len(self.data))}
            if self.state_length:
                reply["Upload-Length"] = str(self.length)
            return tc.TusResponse(200, reply)
        self.patches += 1
        self.sent.append(len(body))
        if self.on_patch:
            self.on_patch(self.patches)
        if self.patches in self.fail_patch:
            return tc.TusResponse(503, {})
        take = body if self.accept_max is None else body[: self.accept_max]
        self.data += take
        return tc.TusResponse(204, {"Upload-Offset": str(len(self.data))})


async def _nosleep(_seconds):
    return None


def _upload(source, server, *, resume=False, **kw):
    client = tc.TusClient("http://storage.fixture/up", transport=server, sleep=_nosleep, **kw)
    return asyncio.run(client.upload(source, resource_url="http://storage.fixture/up/1" if resume else ""))


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "src.bin"
    path.write_bytes(bytes(range(256)) * 2 + bytes(88))        # 600 bytes
    return path


def test_tu1_the_retry_budget_is_per_chunk(source):
    server = FakeTus(fail_patch={2, 4, 6, 8})
    result = _upload(source, server, chunk_size=100, max_retries=3)
    assert (result.offset, result.retries, server.data) == (600, 4, source.read_bytes())
    # control: a chunk that never goes through still exhausts its own budget
    stuck = FakeTus(fail_patch=set(range(2, 50)))
    with pytest.raises(tc.TusUploadError, match="retry limit exhausted"):
        _upload(source, stuck, chunk_size=100, max_retries=3)
    assert len(stuck.data) == 100 and stuck.patches == 5       # 1 good, then 1 + 3 retries of chunk two


def test_tu2_resuming_into_an_upload_of_another_length_is_refused(source):
    server = FakeTus(length=1000)
    with pytest.raises(tc.TusUploadError, match="server upload length 1000 does not match the source size 600"):
        _upload(source, server, resume=True, chunk_size=100)
    assert server.sent == [], "nothing may be sent into the wrong upload"
    # controls: the right length resumes; a server that states no length is not refused
    assert _upload(source, FakeTus(length=600), resume=True, chunk_size=100).offset == 600
    assert _upload(source, FakeTus(length=600, state_length=False), resume=True, chunk_size=100).offset == 600


def test_tu3_a_file_that_grows_is_never_sent_past_its_declared_length(tmp_path):
    grow = tmp_path / "grow.bin"
    grow.write_bytes(b"g" * 250)
    server = FakeTus(on_patch=lambda n: n == 1 and grow.open("ab").write(b"x" * 500))
    with pytest.raises(tc.TusUploadError, match="source file changed during upload"):
        _upload(grow, server, chunk_size=100)
    assert server.sent == [100, 100, 50] and len(server.data) == 250 == server.length


def test_tu4_a_partly_confirmed_chunk_continues_from_the_confirmed_offset(source):
    import hashlib
    server = FakeTus(accept_max=60)
    result = _upload(source, server, chunk_size=100)
    assert server.data == source.read_bytes() and result.offset == 600
    assert result.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()

    class Stalled(FakeTus):                                    # control: a 204 that confirms nothing is fatal
        async def request(self, method, url, headers, body=b""):
            if method == "PATCH":
                return tc.TusResponse(204, {"Upload-Offset": headers["Upload-Offset"]})
            return await super().request(method, url, headers, body)

    with pytest.raises(tc.TusUploadError, match="did not confirm the expected offset"):
        _upload(source, Stalled(), chunk_size=100)


# ───────────────────────── rate_adapter ─────────────────────────
RETRY_AFTER_CAP_S = 86400.0        # the cap this cut introduces (rate_adapter.MAX_RETRY_AFTER_S)


def test_ra1_the_cap_is_one_day():
    assert ra.MAX_RETRY_AFTER_S == RETRY_AFTER_CAP_S


@pytest.mark.parametrize("value,expected", [
    ("99999999999", RETRY_AFTER_CAP_S),
    ("Wed, 21 Oct 2099 07:28:00 GMT", RETRY_AFTER_CAP_S),
    ("1e300", 0.0), ("1_0", 0.0), (" 0x10", 0.0), ("+5", 0.0),
    ("5", 5.0), ("2.5", 2.5), (str(int(RETRY_AFTER_CAP_S)), RETRY_AFTER_CAP_S),   # controls
])
def test_ra1_retry_after_is_capped_and_only_delay_seconds_or_a_date_parse(value, expected):
    adapter = ra.RateLimitAdapter()
    now = 1.8e9
    assert adapter.observe("d", 429, {"Retry-After": value}, now=now) == expected
    assert adapter.delay_for("d", now=now + RETRY_AFTER_CAP_S + 1) == 0, "no permanent block"


def test_ra2_a_503_honours_retry_after_and_never_clears_a_standing_deadline():
    adapter = ra.RateLimitAdapter()
    assert adapter.observe("d", 429, {"Retry-After": "60"}, now=0.0) == 60
    assert adapter.observe("d", 503, {}, now=2.0) == 0             # an error is not a recovery
    assert adapter.delay_for("d", now=3.0) == 57
    assert adapter.observe("d", 503, {"Retry-After": "120"}, now=3.0) == 120
    assert adapter.delay_for("d", now=4.0) == 119
    assert adapter.observe("d", 500, {}, now=5.0) == 0 and adapter.delay_for("d", now=5.0) == 118
    assert adapter.observe("d", 200, {}, now=6.0) == 0 and adapter.delay_for("d", now=6.0) == 0   # control
