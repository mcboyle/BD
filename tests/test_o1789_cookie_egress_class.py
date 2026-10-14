"""O1789 cookie-egress class, part A: clients that sent a site's cookies to
hosts they do not belong to.

  F1  (detect-extract HIGH): the runner's live deep-detect probe sent the site's
      session cookies to any host the page linked to.
  F11 (detect-extract HIGH): the tier probe sent every cookie to the host the
      page's media URL named.
  SAC-F1 (session-auth-capture HIGH): the keeper's httpx heartbeat sent every
      cookie in the jar -- an IdP's SSO cookie too -- to the check host.

The runner and keeper paths run for real; only the network is replaced by an
httpx.MockTransport (installed where the code builds its guarded transport).
Cookie VALUES are dummies and are never printed: requests are recorded as cookie
NAMES only.
"""
import ast
import inspect
import io
import json
import textwrap
from http.cookies import SimpleCookie

import httpx
import pytest
import requests

from bulk_downloader import download_egress, ssrf_transport, tier_probe
from bulk_downloader import runner_extractors as rx
from bulk_downloader import session_keeper as sk


def _cs():
    # Imported per test, not at collection: at BASE the module does not exist and
    # every behaviour test must still run (and fail on its own marker).
    from bulk_downloader import cookie_scope
    return cookie_scope

BD_GATE_SCOPE = "module"

PAGE = "https://site.test/scene/1?token=abc"
DUMMY = "o1789-dummy-not-a-secret"


def _cookie(name, domain, *, path="/", secure=False):
    return {"name": name, "value": DUMMY, "domain": domain, "path": path,
            "secure": secure, "httpOnly": True, "sameSite": "Lax", "expires": -1}


SITE_COOKIES = [_cookie("sessionid", ".site.test"), _cookie("hostonly", "site.test")]


def _html(*hrefs):
    links = "".join(f'<a href="{h}" download>Download video</a>' for h in hrefs)
    return f"<html><body>{links}<p>{'x' * 400}</p></body></html>"


class _Ctx:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return list(self._cookies)


class _Page:
    def __init__(self, html, cookies, url=PAGE):
        self._html = html
        self.url = url
        self.context = _Ctx(cookies)

    def content(self):
        return self._html


def _runner():
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner.__new__(SiteRunner)
    r.site_id = "o1789-site"
    r.config = {"deep_detect_fallback": True, "runner_use_live_dd": True}
    return r


class _Wire:
    """Every request the probe client puts on the wire: host, scheme, path,
    cookie NAMES (never values), Referer."""

    def __init__(self, redirects=None):
        self.seen = []
        self.redirects = redirects or {}

    def __call__(self, req):
        names = sorted(SimpleCookie(req.headers.get("cookie", "")).keys())
        self.seen.append({"host": req.url.host, "scheme": req.url.scheme,
                          "path": req.url.path, "cookies": names,
                          "referer": req.headers.get("referer")})
        loc = self.redirects.get(str(req.url))
        if loc:
            return httpx.Response(302, headers={"location": loc})
        return httpx.Response(200, headers={"content-type": "video/mp4",
                                            "content-length": "1000000"})

    def to(self, host, scheme=None):
        return [s for s in self.seen
                if s["host"] == host and (scheme is None or s["scheme"] == scheme)]


@pytest.fixture
def wire(monkeypatch):
    w = _Wire()
    monkeypatch.setattr(ssrf_transport, "guarded_transport",
                        lambda *a, **k: httpx.MockTransport(w))
    return w


def _run(html, cookies=SITE_COOKIES, url=PAGE):
    _runner()._try_deep_detect_fallback(_Page(html, cookies, url=url), url, {})


# ── 1. cookies stay on their own site ────────────────────────────────────────

def test_cross_site_probe_carries_no_site_cookie(wire):
    _run(_html("https://evil.test/get?id=7", "https://site.test/get?id=8"))
    evil = wire.to("evil.test")
    assert evil, f"probe never reached evil.test: {wire.seen}"
    leaked = [s["cookies"] for s in evil if s["cookies"]]
    assert not leaked, (
        f"O1789-DD-F1-COOKIE-EGRESS: site cookies {leaked} sent to evil.test")


def test_ctl_same_site_probe_still_authenticated(wire):
    _run(_html("https://evil.test/get?id=7", "https://site.test/get?id=8"))
    site = wire.to("site.test")
    assert site and all("sessionid" in s["cookies"] for s in site), (
        f"O1789-DD-F1-SAME-SITE-UNAUTH: {site}")


def test_dot_domain_cookie_reaches_subdomain(wire):
    _run(_html("https://sub.site.test/get?id=9"))
    sub = wire.to("sub.site.test")
    assert sub and all("sessionid" in s["cookies"] for s in sub), (
        f"O1789-DD-F1-SUBDOMAIN: {sub}")


def test_secure_cookie_never_goes_over_http(wire):
    cookies = [_cookie("securesid", ".site.test", secure=True),
               _cookie("plainsid", ".site.test")]
    _run(_html("http://site.test/get?id=10", "https://site.test/get?id=11"),
         cookies=cookies)
    plain = wire.to("site.test", "http")
    tls = wire.to("site.test", "https")
    assert plain and tls, f"both probes expected: {wire.seen}"
    assert all("securesid" not in s["cookies"] for s in plain), (
        f"O1789-DD-F1-SECURE-OVER-HTTP: {plain}")
    assert all("plainsid" in s["cookies"] for s in plain)
    assert all("securesid" in s["cookies"] for s in tls)


def test_cookie_redirected_off_site_is_not_sent(monkeypatch):
    w = _Wire(redirects={"https://site.test/get?id=12": "https://evil.test/landing"})
    monkeypatch.setattr(ssrf_transport, "guarded_transport",
                        lambda *a, **k: httpx.MockTransport(w))
    _run(_html("https://site.test/get?id=12"))
    hop = w.to("evil.test")
    assert hop, f"redirect hop expected: {w.seen}"
    assert all(not s["cookies"] for s in hop), (
        f"O1789-DD-F1-COOKIE-EGRESS-REDIRECT: {hop}")
    assert all(s["referer"] == "https://site.test/" for s in hop), (
        f"O1789-DD-F1-REFERER-REDIRECT: {[s['referer'] for s in hop]}")


# ── 2. Referer: origin only off site, unchanged on site ──────────────────────

def test_cross_site_referer_is_origin_only(wire):
    _run(_html("https://evil.test/get?id=7", "https://site.test/get?id=8"))
    evil = wire.to("evil.test")
    assert evil and all(s["referer"] == "https://site.test/" for s in evil), (
        f"O1789-DD-F1-REFERER-FULL-URL: {[s['referer'] for s in evil]}")


def test_ctl_same_site_referer_unchanged(wire):
    _run(_html("https://site.test/get?id=8"))
    site = wire.to("site.test")
    assert site and all(s["referer"] == PAGE for s in site), site


def test_https_page_sends_no_referer_to_http_cross_site(wire):
    _run(_html("http://evil.test/get?id=13"))
    evil = wire.to("evil.test", "http")
    assert evil and all(s["referer"] is None for s in evil), (
        f"O1789-DD-F1-REFERER-DOWNGRADE: {[s['referer'] for s in evil]}")


# ── 3. each layer holds alone ────────────────────────────────────────────────

def test_scoped_jar_alone_keeps_cookies_on_site():
    w = _Wire()
    with httpx.Client(cookies=rx._dd_probe_cookie_jar(SITE_COOKIES),
                      transport=httpx.MockTransport(w)) as c:
        c.head("https://evil.test/a")
        c.head("https://site.test/a")
    assert not w.to("evil.test")[0]["cookies"], "O1789-DD-F1-JAR-UNSCOPED"
    assert "sessionid" in w.to("site.test")[0]["cookies"]


def test_cookie_without_domain_is_dropped():
    jar = rx._dd_probe_cookie_jar([{"name": "nodomain", "value": DUMMY}])
    assert len(jar) == 0, "O1789-DD-F1-UNSCOPABLE-COOKIE-KEPT"


def test_cookieless_client_alone_holds_with_a_flat_jar(monkeypatch):
    # Even if the jar regressed to a flat dict, cross-site goes cookieless.
    monkeypatch.setattr(rx, "_dd_probe_cookie_jar",
                        lambda cs: {c["name"]: c["value"] for c in cs})
    w = _Wire()

    def make(cookies, event_hooks):
        return httpx.Client(cookies=cookies, event_hooks=event_hooks,
                            transport=httpx.MockTransport(w))
    with rx._DDProbeHttp(PAGE, SITE_COOKIES, make) as http:
        http.head("https://evil.test/a")
        http.get("https://evil.test/b")
        with http.stream("GET", "https://evil.test/c") as r:
            r.read()
        http.post("https://evil.test/d")
        http.head("https://site.test/a")
    assert all(not s["cookies"] for s in w.to("evil.test")), (
        f"O1789-DD-F1-XSITE-CLIENT-HAS-COOKIES: {w.to('evil.test')}")
    assert "sessionid" in w.to("site.test")[0]["cookies"]


# ── 4. F11: the tier probe ───────────────────────────────────────────────────

TIER_CFG = {"tier_probe_enabled": True, "tier_probe_ladder": "2160,1080",
            "tier_probe_pattern": r"_(?P<tier>\d{3,4})p\.mp4"}


@pytest.fixture
def tier_wire(wire, monkeypatch):
    # The SSRF hook resolves hosts; *.test never resolves. Not under test here.
    monkeypatch.setattr(tier_probe, "_ssrf_guard_hook", lambda request: None)
    return wire


def _tier(url, cookies=SITE_COOKIES):
    r = _runner()
    r.config = dict(TIER_CFG)
    r.cookies = list(cookies)
    r.log_event = lambda *a, **k: None
    r._site_client_egress = lambda kind: (True, None)
    return r._probe_for_higher_tier(url, referer=PAGE)


def test_f11_tier_probe_to_a_foreign_media_host_carries_no_site_cookie(tier_wire):
    _tier("https://evil.test/v_1080p.mp4")
    evil = tier_wire.to("evil.test")
    assert evil, f"tier probe never reached evil.test: {tier_wire.seen}"
    leaked = [s["cookies"] for s in evil if s["cookies"]]
    assert not leaked, f"O1789-F11-COOKIE-EGRESS: site cookies {leaked} sent to evil.test"


def test_f11_ctl_tier_probe_on_the_site_is_still_authenticated(tier_wire):
    _tier("https://site.test/v_1080p.mp4")
    site = tier_wire.to("site.test")
    assert site and all(s["cookies"] == ["hostonly", "sessionid"] for s in site), (
        f"O1789-F11-SAME-SITE-UNAUTH: {site}")


def test_f11_tier_probe_scopes_like_a_browser(tier_wire):
    # .site.test reaches cdn.site.test; host-only site.test does not.
    _tier("https://cdn.site.test/v_1080p.mp4")
    cdn = tier_wire.to("cdn.site.test")
    assert cdn and all(s["cookies"] == ["sessionid"] for s in cdn), (
        f"O1789-F11-HOST-ONLY-WIDENED: {cdn}")


def test_f11_tier_probe_never_sends_a_secure_cookie_over_http(tier_wire):
    _tier("http://site.test/v_1080p.mp4",
          cookies=[_cookie("securesid", ".site.test", secure=True),
                   _cookie("plainsid", ".site.test")])
    plain = tier_wire.to("site.test", "http")
    assert plain and all(s["cookies"] == ["plainsid"] for s in plain), (
        f"O1789-F11-SECURE-OVER-HTTP: {plain}")


def test_f11_runner_hands_tier_probe_the_cookies_with_their_domains(monkeypatch):
    got = {}

    def _record(url, pattern, **kw):
        got.update(kw)
        raise RuntimeError("recorded; the runner keeps the known URL")
    monkeypatch.setattr(tier_probe, "probe_higher_tiers", _record)
    assert _tier("https://site.test/v_1080p.mp4") == "https://site.test/v_1080p.mp4"
    sent = got.get("cookies")
    assert isinstance(sent, list) and sent and all(c.get("domain") for c in sent), (
        f"O1789-F11-RUNNER-FLAT-DICT: {type(sent).__name__}")


# ── 4b. ext-route-anchor r1 link 2 (S75-A p_egress.py, runner_tier decoy) ────
# A misrouted decoy page (http://evil.test/?q=siteb.example.com) lands in siteB's job; siteB's
# real _probe_for_higher_tier then probes the media URL that page exposes.

SITEB_COOKIE = _cookie("sb_session", ".siteb.example.com")
SITEB_DECOY = "http://evil.test/?q=siteb.example.com"


def _siteb_tier(page_url, cookies=(SITEB_COOKIE,)):
    r = _runner()
    r.site_id = "siteB"
    r.config = {"tier_probe_enabled": True, "tier_probe_pattern": "nubiles_suffix"}
    r.cookies = [dict(c) for c in cookies]
    r.log_event = lambda *a, **k: None
    r._update_job = lambda *a, **k: None
    r._site_client_egress = lambda kind: (True, None)
    host = httpx.URL(page_url).host
    r._probe_for_higher_tier(f"https://{host}/media/clip_720.mp4", referer=page_url)


def test_ext_route_decoy_runner_tier_sends_no_siteb_cookie_to_the_page_host(tier_wire):
    _siteb_tier(SITEB_DECOY)
    evil = tier_wire.to("evil.test")
    assert evil, f"decoy tier probe never reached evil.test: {tier_wire.seen}"
    leaked = [s["cookies"] for s in evil if s["cookies"]]
    assert not leaked, f"O1789-EXT-ROUTE-DECOY-COOKIE-EGRESS: siteB cookies {leaked} sent to evil.test"


def test_ext_route_ctl_siteb_host_still_gets_its_cookie(tier_wire):
    # Positive control: the same sink on siteB's own host carries the cookie (the probe can say yes).
    _siteb_tier("https://siteb.example.com/videos/1")
    own = tier_wire.to("siteb.example.com")
    assert own and all(s["cookies"] == ["sb_session"] for s in own), (
        f"O1789-EXT-ROUTE-CTL-SITEB-UNAUTH: {own}")


def test_ext_route_ctl_no_cookie_decoy_still_probes_the_page_host(tier_wire):
    # Negative control: siteB has no cookie; the sink still reaches evil.test, and nothing is sent.
    _siteb_tier(SITEB_DECOY, cookies=())
    evil = tier_wire.to("evil.test")
    assert evil and not any(s["cookies"] for s in evil), f"O1789-EXT-ROUTE-CTL-NOCOOKIE: {evil}"


# ── 5. SAC-F1: the keeper's httpx heartbeat (S61-D test_s21) ──────────────────

def _heartbeat(monkeypatch, tmp_path, stored, check="https://www.site.test/account"):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cookies").mkdir()
    (tmp_path / "cookies" / "siteA.json").write_text(json.dumps(stored))
    w = _Wire()
    monkeypatch.setattr(ssrf_transport, "guarded_transport",
                        lambda *a, **k: httpx.MockTransport(w))
    monkeypatch.setattr(download_egress, "effective_download_proxy", lambda *a, **k: None)
    k = sk.SessionKeeper("siteA", 0, {"success_url": check}, lambda *a: (False, "x"))
    verdict = k._heartbeat_httpx_fallback()
    return w, verdict


def test_sac_f1_heartbeat_sends_no_idp_cookie_to_the_site(monkeypatch, tmp_path):
    w, verdict = _heartbeat(monkeypatch, tmp_path,
                            [_cookie("sess", ".site.test"), _cookie("IDP_SESSION", ".idp.test")])
    assert w.seen, f"heartbeat never sent: {verdict}"
    assert all("IDP_SESSION" not in s["cookies"] for s in w.seen), (
        f"O1789-SAC-F1-IDP-COOKIE-EGRESS: {w.seen}")


def test_sac_f1_ctl_heartbeat_still_sends_the_site_cookie(monkeypatch, tmp_path):
    w, _ = _heartbeat(monkeypatch, tmp_path,
                      [_cookie("sess", ".site.test"), _cookie("IDP_SESSION", ".idp.test")])
    assert w.seen and all("sess" in s["cookies"] for s in w.seen), (
        f"O1789-SAC-F1-SITE-UNAUTH: {w.seen}")


def test_sac_f1_legacy_flat_file_goes_to_the_check_host_only(monkeypatch, tmp_path):
    w, _ = _heartbeat(monkeypatch, tmp_path, {"sess": DUMMY})
    assert w.seen and all(s["cookies"] == ["sess"] for s in w.seen), (
        f"O1789-SAC-F1-LEGACY-FILE-DROPPED: {w.seen}")
    jar = sk.SessionKeeper("siteA", 0, {"success_url": "https://www.site.test/a"},
                           lambda *a: (False, "x"))._load_cookies()
    got = [(getattr(c, "domain", c), getattr(c, "domain_specified", None)) for c in jar]
    assert got == [("www.site.test", False)], (
        "O1789-SAC-F1-LEGACY-FILE-UNSCOPED")


# ── 6. guard: no flat cookie dict at a part-A sink ───────────────────────────

def _flat_cookie_shapes(src):
    """Lines where `src` flattens cookies: {c["name"]: c["value"]},
    d[c["name"]] = c["value"], or "; ".join(f"{k}={v}" ...)."""
    def key(node, want):
        return (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                and node.slice.value == want)
    hits = []
    for n in ast.walk(ast.parse(textwrap.dedent(src))):
        if isinstance(n, ast.DictComp) and key(n.key, "name") and key(n.value, "value"):
            hits.append(n.lineno)
        elif (isinstance(n, ast.Assign) and len(n.targets) == 1
              and isinstance(n.targets[0], ast.Subscript) and key(n.targets[0].slice, "name")
              and key(n.value, "value")):
            hits.append(n.lineno)
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "join" and isinstance(n.func.value, ast.Constant)
              and ";" in str(n.func.value.value)
              and any(isinstance(v, ast.JoinedStr) for a in n.args for v in ast.walk(a))):
            hits.append(n.lineno)
    return hits


PART_A_SINKS = [
    ("runner_extractors._try_deep_detect_fallback", lambda: rx.ExtractorsMixin._try_deep_detect_fallback),
    ("runner_extractors._probe_for_higher_tier", lambda: rx.ExtractorsMixin._probe_for_higher_tier),
    ("runner_extractors._DDProbeHttp", lambda: rx._DDProbeHttp),
    ("tier_probe", lambda: tier_probe),
    ("cookie_scope", lambda: _cs()),
    ("session_keeper._load_cookies", lambda: sk.SessionKeeper._load_cookies),
    ("session_keeper._heartbeat_httpx_fallback", lambda: sk.SessionKeeper._heartbeat_httpx_fallback),
]


@pytest.mark.parametrize("name,obj", PART_A_SINKS, ids=[n for n, _ in PART_A_SINKS])
def test_guard_no_flat_cookie_dict_at_a_part_a_sink(name, obj):
    hits = _flat_cookie_shapes(inspect.getsource(obj()))
    assert not hits, f"O1789-COOKIE-FLAT-DICT-SINK: {name} flattens cookies at +{hits}"


BASE_SHAPES = {   # the shapes the sinks had at BASE 2b7ca835 (F1, F11 runner, F11 tier_probe, SAC-F1)
    "f1": 'cookies_dict = {c["name"]: c["value"] for c in page.context.cookies()}',
    "f11-runner": 'for c in cs:\n    cookies_dict[c["name"]] = c["value"]',
    "f11-tier": 'cookie = "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())',
    "sac-f1": 'x = {c["name"]: c["value"] for c in data if isinstance(c, dict)}',
}


@pytest.mark.parametrize("shape", sorted(BASE_SHAPES))
def test_guard_ctl_detector_fires_on_every_base_shape(shape):
    assert _flat_cookie_shapes(BASE_SHAPES[shape]), f"guard blind to {shape}"


# ── 7. M1-M5 (detect-extract/ev/p_cookie_class.log): a flat dict leaks in every
#       client shape; the scoped jar does not ───────────────────────────────

def _httpx_shape(cookies, per_request=False):
    """evil.test requests (cookie sent?) for a 302 site.test -> evil.test, then a direct call."""
    w = _Wire(redirects={"https://site.test/r": "https://evil.test/landing"})
    client_kw = {} if per_request else {"cookies": cookies}
    req_kw = {"cookies": cookies} if per_request else {}
    with httpx.Client(transport=httpx.MockTransport(w), follow_redirects=True, **client_kw) as c:
        c.get("https://site.test/r", **req_kw)
        c.get("https://evil.test/direct", **req_kw)
    return [bool(s["cookies"]) for s in w.to("evil.test")]


FLAT = {"sessionid": DUMMY}


def test_m1_m2b_httpx_client_flat_dict_leaks_and_the_scoped_jar_does_not():
    assert _httpx_shape(FLAT, per_request=False) == [True, True]          # M1 direct, M2b hop
    assert _httpx_shape(_cs().scoped_jar(SITE_COOKIES), per_request=False) == [False, False]


def test_m2_httpx_per_request_flat_dict_hop_clean_direct_leaks():
    with pytest.warns(DeprecationWarning):
        got = _httpx_shape(FLAT, per_request=True)
    assert got == [False, True]   # M2: the 302 hop is clean, a direct call is not


def _requests_shape(session_setup):
    seen = []

    class _Adapter(requests.adapters.BaseAdapter):
        def send(self, request, **kw):
            seen.append((requests.utils.urlparse(request.url).hostname,
                         bool(request.headers.get("Cookie"))))
            resp = requests.Response()
            resp.status_code = 302 if request.url == "https://site.test/r" else 200
            if resp.status_code == 302:
                resp.headers["location"] = "https://evil.test/landing"
            resp.url, resp.request, resp.raw = request.url, request, io.BytesIO(b"")
            resp._content, resp._content_consumed = b"", True
            return resp

        def close(self):
            pass

    with requests.Session() as s:
        s.mount("https://", _Adapter())
        per_request = session_setup(s)
        s.get("https://site.test/r", cookies=per_request)
        s.get("https://evil.test/direct", cookies=per_request)
    return [sent for host, sent in seen if host == "evil.test"]


def test_m3_m5_requests_flat_dict_leaks_and_the_scoped_jar_does_not():
    assert _requests_shape(lambda s: FLAT) == [True, True]                 # M3 per-request
    assert _requests_shape(lambda s: s.cookies.update(FLAT)) == [True, True]  # M5 session
    assert _requests_shape(
        lambda s: s.cookies.update(_cs().scoped_jar(SITE_COOKIES))) == [False, False]


def test_scoped_header_matches_the_browser_rules():
    jar = SITE_COOKIES + [_cookie("sec", ".site.test", secure=True),
                          _cookie("deep", "site.test", path="/a")]
    names = lambda url: sorted(SimpleCookie(_cs().cookie_header_for(jar, url)).keys())
    assert names("https://site.test/a/b") == ["deep", "hostonly", "sec", "sessionid"]  # control
    assert names("http://site.test/x") == ["hostonly", "sessionid"], (
        f"O1789-SCOPE-SECURE-OR-PATH: {names('http://site.test/x')}")
    assert names("https://sub.site.test/x") == ["sec", "sessionid"], (
        f"O1789-SCOPE-HOST-ONLY-WIDENED: {names('https://sub.site.test/x')}")
    assert names("https://notsite.test/") == [], "O1789-SCOPE-SUFFIX-MATCH"
    assert names("https://evil.test/") == [], "O1789-SCOPE-OTHER-SITE"
