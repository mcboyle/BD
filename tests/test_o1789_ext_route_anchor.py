"""ext-route-anchor r1 (O1789, R233-C A1/A2 + the F7 scorer twin): routing reads the PARSED URL.

A site's url_patterns used to be searched in the raw URL string, so a foreign host that merely
named the site in its query or path ("http://evil.test/?q=siteb.example.com") was routed to the
site -- and route_urls queued it under the site's session (HIGH-step1-egress.md).  Hostname
matching treated a public suffix ("com", "co.uk") as a parent of the site.  Every decoy below is
replayed through all four routing paths: lookup_url, route_preview, route_urls and the internal
router; every control proves the site still routes, so the fix cannot pass by routing nothing.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest
from flask import Flask


BD_GATE_SCOPE = "module"

SITEB_PATTERN = {"name": "Site B", "url_patterns": "siteb\\.example\\.com"}
SITEB_LOGIN = {"name": "Site B", "url_patterns": "", "login_url": "https://siteb.example.com/login"}


class _FakeRunner:
    def __init__(self, urls=()):
        self.jobs = {}
        self.urls = list(urls)  # a busy site: the scorer's tie-break reads len(runner.urls)
        self._lock = threading.Lock()
        self.loaded = []

    def load_urls(self, urls):
        self.loaded.extend(urls)
        return len(urls), 0


class _EmptyHistoryConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, *_args):
        self._count = "COUNT(*)" in sql
        return self

    def fetchone(self):
        return {"c": 0} if self._count else None


@pytest.fixture
def bd(monkeypatch):
    """lookup / preview / route / internal, all reading one in-memory s_cfg + runners."""
    import bulk_downloader.app as A
    from bulk_downloader import app_extension, app_route_preview, app_route_urls
    from bulk_downloader import content_rights as cr

    s_cfg, runners = {}, {}
    for mod in (app_extension, app_route_preview, app_route_urls):
        monkeypatch.setattr(mod, "_app_s_cfg", lambda: s_cfg)
        monkeypatch.setattr(mod, "_app_runners", lambda: runners)
    monkeypatch.setattr(A, "runners", runners)
    monkeypatch.setattr(A, "s_cfg", s_cfg)
    monkeypatch.setattr(app_extension, "db_conn", _EmptyHistoryConnection)
    monkeypatch.setattr(cr, "url_is_blocked", lambda _u: None)

    app = Flask("ext-route-anchor-probe")
    for bp in (app_extension.extension_bp, app_route_preview.route_preview_bp, app_route_urls.route_urls_bp):
        app.register_blueprint(bp)
    client = app.test_client()

    class BD:
        @staticmethod
        def sites(cfg, busy=True):
            s_cfg.clear()
            s_cfg.update(cfg)
            runners.clear()
            for sid in cfg:
                runners[sid] = _FakeRunner(["https://queued.example/x"] * 40 if busy else ())
            return runners

        @staticmethod
        def routes(url):
            """{path: site_id or None} for one URL on all four routing paths."""
            info = client.get("/api/extension/lookup_url", query_string={"url": url}).get_json()
            prev = client.post("/api/route_preview", json={"url": url}).get_json()
            before = {sid: len(r.loaded) for sid, r in runners.items()}
            j = client.post("/api/route_urls", json={"text": url}).get_json()
            queued = [sid for sid, r in runners.items() if len(r.loaded) > before[sid]]
            assert j["ok"] is True and j["total_in"] == 1
            summary, _unrouted = A._route_urls_internal([url])
            internal = next(iter(summary), None)
            return {"lookup": (info.get("would_route_to") or {}).get("site_id"),
                    "preview": prev["decisions"][0]["site_id"],
                    "route_urls": queued[0] if queued else None,
                    "internal": internal}

    return BD


NOWHERE = {"lookup": None, "preview": None, "route_urls": None, "internal": None}
SITEB = {"lookup": "siteB", "preview": "siteB", "route_urls": "siteB", "internal": "siteB"}


# A1 -- url_patterns match the parsed host, then the path; never the raw string --------------------

@pytest.mark.parametrize("url", [
    "http://evil.test/?q=siteb.example.com",          # query-string decoy (R233-C A1)
    "http://evil.test/siteb.example.com/",            # path decoy
    "http://evil.test/#siteb.example.com",            # fragment decoy
    "http://siteb.example.com@evil.test/x",           # userinfo decoy: the host is evil.test
    "http://siteb.example.com.evil.test/x",           # the site's name as a label prefix
    "http://evil.test/redirect?to=https://siteb.example.com/x",
], ids=["query", "path", "fragment", "userinfo", "label-prefix", "redirect-param"])
def test_a_foreign_host_naming_the_site_routes_nowhere(bd, url):
    bd.sites({"siteB": SITEB_PATTERN})
    assert bd.routes(url) == NOWHERE


@pytest.mark.parametrize("url", [
    "https://siteb.example.com/x",
    "https://sub.siteb.example.com/v/1",
    "http://SITEB.example.com:8443/x?q=1",
])
def test_the_sites_own_host_and_subdomains_still_route_by_pattern(bd, url):
    bd.sites({"siteB": SITEB_PATTERN})
    assert bd.routes(url) == SITEB


def test_a_pattern_with_a_path_matches_the_path_only_after_the_host(bd):
    bd.sites({"siteB": {"name": "Site B", "url_patterns": "siteb\\.example\\.com/videos/"}})
    assert bd.routes("https://www.siteb.example.com/videos/7") == SITEB
    assert bd.routes("https://siteb.example.com/photos/7") == NOWHERE
    assert bd.routes("https://siteb.example.com/x/videos/7") == NOWHERE  # the path part is anchored at the path start
    assert bd.routes("https://evil.test/siteb.example.com/videos/7") == NOWHERE


def test_a_pattern_written_with_its_scheme_is_anchored_the_same_way(bd):
    bd.sites({"siteB": {"name": "Site B", "url_patterns": "^https?://(www\\.)?siteb\\.example\\.com/"}})
    assert bd.routes("https://www.siteb.example.com/x") == SITEB
    assert bd.routes("http://evil.test/?u=https://siteb.example.com/") == NOWHERE
    assert bd.routes("http://siteb.example.com.evil.test/x") == NOWHERE


def test_a_scheme_pattern_must_cover_the_whole_host(bd):
    bd.sites({"siteB": {"name": "Site B", "url_patterns": "https?://siteb\\.example\\.com"}})
    assert bd.routes("https://siteb.example.com/x") == SITEB
    assert bd.routes("http://siteb.example.com.evil.test/x") == NOWHERE
    assert bd.routes("http://siteb.example.community/x") == NOWHERE
    bd.sites({"siteB": {"name": "Site B", "url_patterns": "^https://siteb\\.example\\.com/"}})
    assert bd.routes("https://siteb.example.com/x") == SITEB
    assert bd.routes("http://siteb.example.com/x") == NOWHERE  # a scheme part is matched too


def test_a_leading_wildcard_pattern_reads_the_host_not_the_path(bd):
    """PM 1437Z (dotstar LIMIT): host and path are matched separately, so a wildcard cannot reach the path."""
    bd.sites({"siteB": {"name": "Site B", "url_patterns": ".*siteb\\.example\\.com"}})
    assert bd.routes("http://evil.test/siteb.example.com/") == NOWHERE
    assert bd.routes("http://evil.test/?q=siteb.example.com") == NOWHERE
    assert bd.routes("https://cdn.siteb.example.com/x") == SITEB
    bd.sites({"siteB": {"name": "Site B", "url_patterns": ".*siteb"}})
    assert bd.routes("http://evil.test/siteb/") == NOWHERE
    assert bd.routes("https://cdn.siteb/x") == SITEB  # a single label is never a host start


@pytest.mark.parametrize("pat,parts", [
    ("siteb\\.example\\.com", (None, "siteb\\.example\\.com", None)),
    ("siteb\\.example\\.com/videos/", (None, "siteb\\.example\\.com", "/videos/")),
    ("^https?://(www\\.)?siteb\\.example\\.com/", ("^https?", "(www\\.)?siteb\\.example\\.com", "/")),
    ("siteb\\.example\\.com\\/videos", (None, "siteb\\.example\\.com\\/videos", None)),
    ("/videos/", (None, "", "/videos/")),
])
def test_a_pattern_splits_at_its_first_unescaped_slash(pat, parts):
    import bulk_downloader.app as A

    assert A._split_url_pattern(pat) == parts


def test_the_shipped_pattern_shapes_still_route_their_own_site(bd):
    """Migration check: every url_patterns shape the repo ships that contains '/'."""
    import json

    from bulk_downloader.template_extractor_impl.candidates import _patterns_from_url

    root = Path(__file__).resolve().parents[1]
    tpl = json.loads((root / "templates/reviewed/filthykings.com.template.json").read_text(encoding="utf-8"))
    (fk,) = tpl["match"]["url_patterns"]
    fk_site = {k: "fk" for k in NOWHERE}
    bd.sites({"fk": {"name": "FK", "url_patterns": fk}})
    assert bd.routes("https://members.filthykings.com/scene/1") == fk_site
    assert bd.routes("https://www.filthykings.com/") == fk_site
    assert bd.routes("https://evil.test/members.filthykings.com/") == NOWHERE
    # tools/build_template_from_wacz.py: f"^https://{re.escape(netloc)}/", netloc may carry a port
    bd.sites({"fk": {"name": "FK", "url_patterns": f"^https://{re.escape('shop.example.org:8443')}/"}})
    assert bd.routes("https://shop.example.org:8443/v/1") == fk_site
    assert bd.routes("https://evil.test/shop.example.org:8443/") == NOWHERE
    # template_extractor candidates: re.escape(host), no '/'
    bd.sites({"fk": {"name": "FK", "url_patterns": _patterns_from_url("https://www.siteb.example.com/v/1")}})
    assert bd.routes("https://www.siteb.example.com/v/2") == fk_site
    assert bd.routes("https://evil.test/siteb.example.com/") == NOWHERE


def test_a_public_suffix_pattern_names_no_site(bd):
    """A2 for patterns: "co.uk" is never a start, so it cannot claim every .co.uk registrant."""
    bd.sites({"siteB": {"name": "Site B", "url_patterns": "co\\.uk"}})
    assert bd.routes("https://victim.co.uk/x") == NOWHERE


# r2 (R268-B F1) -- the whole URL is parsed, never a truncated copy ------------------------------------

PAD = "." * 4200
SITE_KINDS = {
    "url_patterns": SITEB_PATTERN,
    "dotstar": {"name": "Site B", "url_patterns": ".*siteb\\.example\\.com"},
    "login_url": SITEB_LOGIN,
}


@pytest.mark.parametrize("kind", sorted(SITE_KINDS))
@pytest.mark.parametrize("pad", [PAD, ""], ids=["padded", "short-control"])
def test_a_padded_userinfo_decoy_routes_nowhere(bd, kind, pad):
    """Cut at 4096, "http://siteb.example.com....@evil.test/x" loses its '@': the copy's host
    is siteb.example.com, the queued URL's host is evil.test."""
    bd.sites({"siteB": SITE_KINDS[kind]})
    url = "http://siteb.example.com" + pad + "@evil.test/x"
    assert bd.routes(url) == NOWHERE, ("EXT-ROUTE-TRUNCATED-HOST", kind, len(url))


@pytest.mark.parametrize("kind", sorted(SITE_KINDS))
def test_a_long_url_on_the_sites_own_host_still_routes(bd, kind):
    bd.sites({"siteB": SITE_KINDS[kind]})
    assert bd.routes("https://siteb.example.com/v/" + "a" * 5000 + "?q=" + "b" * 5000) == SITEB, (
        "EXT-ROUTE-LONG-OWN-HOST-DROPPED", kind)


def test_a_host_longer_than_a_dns_name_routes_nowhere(bd):
    bd.sites({"siteB": SITEB_PATTERN})
    assert bd.routes("https://" + "x." * 120 + "siteb.example.com/v") == NOWHERE, "EXT-ROUTE-HOST-UNBOUNDED"  # 257
    assert bd.routes("https://" + "x." * 115 + "siteb.example.com/v") == SITEB        # 247: control


def test_the_path_a_pattern_reads_is_bounded():
    import bulk_downloader.app as A

    target = A._route_target("https://siteb.example.com/" + "a" * 10000 + "?q=1")
    assert target[1] == "siteb.example.com" and len(target[2]) == 4096, ("EXT-ROUTE-PATH-UNBOUNDED", len(target[2]))


# A2 + F7 -- a public suffix is never a parent -------------------------------------------------------

@pytest.mark.parametrize("site,url", [
    ({"name": "Shop", "url_patterns": "", "login_url": "https://shop.co.uk/login"}, "http://co.uk/anything"),
    ({"name": "Shop", "url_patterns": "", "login_url": "https://shop.co.uk/login"}, "http://other.co.uk/x"),
    (SITEB_LOGIN, "http://com/anything"),           # F7: the bare-suffix twin in the shared scorer
    (SITEB_LOGIN, "http://example.com.evil.test/x"),
    ({"name": "Bare", "url_patterns": "", "login_url": "https://co.uk/login"}, "https://shop.co.uk/x"),
], ids=["co.uk-parent", "co.uk-sibling", "bare-com", "suffix-host", "suffix-site"])
def test_a_public_suffix_parent_routes_nowhere(bd, site, url):
    bd.sites({"siteB": site})
    assert bd.routes(url) == NOWHERE


@pytest.mark.parametrize("url", [
    "https://siteb.example.com/x",        # exact host
    "https://sub.siteb.example.com/x",    # under the site's host
    "https://example.com/x",              # the site's registrable domain (apex)
])
def test_the_sites_host_subdomain_and_apex_still_route_by_hostname(bd, url):
    bd.sites({"siteB": SITEB_LOGIN})
    assert bd.routes(url) == SITEB


def test_a_co_uk_registrant_still_routes_to_its_own_site(bd):
    bd.sites({"shop": {"name": "Shop", "url_patterns": "", "login_url": "https://www.shop.co.uk/login"}})
    shop = {k: "shop" for k in NOWHERE}
    assert bd.routes("https://shop.co.uk/x") == shop
    assert bd.routes("https://cdn.www.shop.co.uk/x") == shop


# A queue size is not a match on any path ------------------------------------------------------------

def test_a_busy_site_does_not_win_an_unmatched_url(bd):
    bd.sites({"siteB": SITEB_PATTERN}, busy=True)
    assert bd.routes("https://unrelated.org/v/1") == NOWHERE
    bd.sites({"siteB": SITEB_PATTERN, "siteC": {"name": "C", "url_patterns": "siteb\\.example\\.com"}}, busy=False)
    import bulk_downloader.app as A

    A.runners["siteC"].urls = ["u"] * 5  # both match; the busier one wins the tie
    assert bd.routes("https://siteb.example.com/x") == {k: "siteC" for k in NOWHERE}


# One matcher -- the grep guard ----------------------------------------------------------------------

_PKG = Path(__file__).resolve().parents[1] / "bulk_downloader"


@pytest.mark.parametrize("name", ["app_route_urls.py", "app_extension.py", "app_route_preview.py"])
def test_every_routing_path_scores_with_the_one_shared_matcher(name):
    src = (_PKG / name).read_text(encoding="utf-8")
    assert "_score_url_against_sites" in src, f"{name} no longer calls the shared scorer"
    assert not re.search(r"\bre\.(search|match|fullmatch|compile)\(", src), (
        f"{name} matches a URL with its own regex again -- route through app._score_url_against_sites")
    assert "url_patterns\")" not in src and "url_patterns')" not in src, f"{name} reads url_patterns itself"


def test_the_shared_scorer_never_searches_the_raw_url():
    src = (_PKG / "app.py").read_text(encoding="utf-8")
    start = src.index("def _score_url_against_sites(")
    body = src[start:src.index("\ndef ", start + 1)]
    body = "\n".join(line.split("#", 1)[0] for line in body.splitlines())  # code, not comments
    assert "_url_pattern_matches(" in body and "_host_match_score(" in body
    assert "re.search(" not in body and ".endswith(" not in body
    assert "url[:" not in body, "the scorer routes a truncated copy of the url (r2 F1)"
