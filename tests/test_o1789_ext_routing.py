"""ext-routing r1 (O1789 extension-bridge F2-F8): lookup_url and route_urls route like route_preview.

Each defect arm replays an S57-B probe shape (S6-S19) through the real blueprints with in-memory
sites and runners.  Each paired control proves the measured, healthy path still routes, so a fix
cannot pass by routing nothing.
"""
from __future__ import annotations

import threading

import pytest
from flask import Flask


BD_GATE_SCOPE = "module"


class _FakeRunner:
    def __init__(self, urls=()):
        self.jobs = {}
        self.urls = list(urls)  # the scorer's tie-break reads len(runner.urls)
        self._lock = threading.Lock()
        self.loaded = []

    def load_urls(self, urls):
        self.loaded.extend(urls)
        return len(urls), 0


class _BrokenConnection:
    def __enter__(self):
        raise OSError("history store unreadable")

    def __exit__(self, *_args):
        return False


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
    """A Flask app with the three routing blueprints reading one in-memory s_cfg / runners."""
    import bulk_downloader.app as A
    from bulk_downloader import app_extension, app_route_preview, app_route_urls
    from bulk_downloader import content_rights as cr

    state = {"s_cfg": {}, "runners": {}}
    for mod in (app_extension, app_route_preview, app_route_urls):
        monkeypatch.setattr(mod, "_app_s_cfg", lambda: state["s_cfg"])
        monkeypatch.setattr(mod, "_app_runners", lambda: state["runners"])
    monkeypatch.setattr(A, "runners", state["runners"])  # the shared scorer's tie-break
    monkeypatch.setattr(app_extension, "db_conn", _EmptyHistoryConnection)
    monkeypatch.setattr(cr, "url_is_blocked", lambda _u: None)

    def sites(cfg, runners_for=None, urls=None):
        state["s_cfg"].clear()
        state["s_cfg"].update(cfg)
        state["runners"].clear()
        for sid in (cfg if runners_for is None else runners_for):
            state["runners"][sid] = _FakeRunner((urls or {}).get(sid, ()))
        return state["runners"]

    app = Flask("ext-routing-probe")
    for bp in (app_extension.extension_bp, app_route_preview.route_preview_bp, app_route_urls.route_urls_bp):
        app.register_blueprint(bp)
    client = app.test_client()

    def lookup(url):
        r = client.get("/api/extension/lookup_url", query_string={"url": url})
        assert r.status_code == 200, r.get_data(as_text=True)
        return r.get_json()

    def preview(url):
        r = client.post("/api/route_preview", json={"url": url})
        assert r.status_code == 200, r.get_data(as_text=True)
        return r.get_json()["decisions"][0]["site_id"]

    def route(text):
        return client.post("/api/route_urls", json={"text": text})

    return type("BD", (), {"sites": staticmethod(sites), "lookup": staticmethod(lookup),
                           "preview": staticmethod(preview), "route": staticmethod(route),
                           "ext": app_extension})


def _routed_to(info):
    return (info.get("would_route_to") or {}).get("site_id")


# F2 / F3 -- lookup_url ranks with the shared scorer ------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://other.test/x", "siteB"),
    ("https://unrelated.org/watch/1", None),
    ("https://example.com/v/7", "siteA"),
])
def test_lookup_reads_url_patterns_as_a_string_like_preview(bd, url, expected):
    """S6: url_patterns is a newline/comma string; lookup iterated it as a list, one regex per character."""
    bd.sites({"siteA": {"name": "Site A", "url_patterns": "example\\.com"},
              "siteB": {"name": "Site B", "url_patterns": "other\\.test"}})
    assert bd.preview(url) == expected
    assert _routed_to(bd.lookup(url)) == expected


def test_lookup_routes_a_hostname_only_site_like_preview(bd):
    """S9: a site with only login_url is routed by hostname."""
    bd.sites({"siteH": {"name": "Host only", "url_patterns": "", "login_url": "https://hostonly.test/login"}})
    assert bd.preview("https://hostonly.test/v/1") == "siteH"
    info = bd.lookup("https://hostonly.test/v/1")
    assert info["would_route_to"] == {"site_id": "siteH", "site_name": "Host only"}
    assert "would_route_to" not in bd.lookup("https://elsewhere.test/v/1")


def test_lookup_does_not_route_on_queue_size_alone(bd):
    """A site that matches nothing is not a route, however many URLs it holds (scorer tie-break < 1)."""
    bd.sites({"siteA": {"name": "Site A", "url_patterns": "example\\.com"}}, urls={"siteA": ["u"] * 50})
    assert "would_route_to" not in bd.lookup("https://unrelated.org/x")
    assert _routed_to(bd.lookup("https://example.com/x")) == "siteA"


# F4 -- one bad site entry does not fail the route_urls batch ----------------------------------------

@pytest.mark.parametrize("bad", [
    {"name": "List patterns", "url_patterns": ["other\\.test"]},
    {"name": "List login", "url_patterns": "", "login_url": ["https://other.test/"]},
    "corrupt-string-entry",
], ids=["list-patterns", "list-login", "not-a-dict"])
def test_route_urls_skips_a_malformed_site_and_keeps_the_batch(bd, bad):
    """S7/S15: the scorer's type checks -- a wrong-typed entry is skipped, not a 400 for every URL."""
    runners = bd.sites({"siteBad": bad, "siteA": {"name": "Site A", "url_patterns": "example\\.com"}})
    r = bd.route("https://example.com/1\nhttps://other.test/2")
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert runners["siteA"].loaded == ["https://example.com/1"]
    assert j["unrouted"] == ["https://other.test/2"]
    assert bd.preview("https://example.com/1") == "siteA"


# F5 -- a matched URL with no runner is reported as unrouted -----------------------------------------

def test_route_urls_reports_a_matched_but_unqueued_url_as_unrouted(bd):
    """S16: siteA matched but has no runner; the URL was neither queued nor reported."""
    runners = bd.sites({"siteA": {"name": "Site A", "url_patterns": "example\\.com"},
                        "siteB": {"name": "Site B", "url_patterns": "other\\.test"}}, runners_for=["siteB"])
    j = bd.route("https://example.com/lost\nhttps://other.test/kept").get_json()
    assert j["unrouted"] == ["https://example.com/lost"] and j["unrouted_count"] == 1
    lost = next(x for x in j["results"] if x["url"] == "https://example.com/lost")
    assert lost["matched"] is False and lost["site_id"] is None and "siteA" in lost["reason"]
    kept = next(x for x in j["results"] if x["url"] == "https://other.test/kept")
    assert kept["matched"] is True and kept["site_id"] == "siteB"
    assert runners["siteB"].loaded == ["https://other.test/kept"]
    assert list(j["summary"]) == ["siteB"]


# F6 -- route_urls takes only real http(s) URLs ------------------------------------------------------

@pytest.mark.parametrize("text", ["httpfoo-not-a-url", "http:example.com", "https:/example.com/x",
                                  "http://", "ftp://example.com/x", "http://[::1"])
def test_route_urls_refuses_text_that_only_starts_with_http(bd, text):
    """S18: a prefix test queued non-URLs via load_urls."""
    runners = bd.sites({"siteA": {"name": "Site A", "url_patterns": "example\\.com|httpfoo|http:|ftp"}})
    r = bd.route(text)
    assert r.status_code == 400 and r.get_json()["error"] == "no http URLs in input"
    assert runners["siteA"].loaded == []


def test_route_urls_still_takes_http_and_https_urls(bd):
    runners = bd.sites({"siteA": {"name": "Site A", "url_patterns": "example\\.com"}})
    j = bd.route("http://example.com/a\nhttps://example.com/b\nnot a url").get_json()
    assert j["total_in"] == 2
    assert runners["siteA"].loaded == ["http://example.com/a", "https://example.com/b"]


# F7 -- a bare label is not a parent domain ----------------------------------------------------------

def test_route_urls_does_not_route_a_bare_suffix_host(bd):
    """S19: http://com/anything matched a site on www.example.com as a 'subdomain'."""
    runners = bd.sites({"siteA": {"name": "Site A", "url_patterns": "", "login_url": "https://www.example.com/login"}})
    j = bd.route("http://com/anything\nhttp://localhost/x\nhttp://unrelated.org/v").get_json()
    assert j["unrouted"] == ["http://com/anything", "http://localhost/x", "http://unrelated.org/v"]
    assert runners["siteA"].loaded == []


def test_route_urls_still_routes_subdomain_and_apex_hosts(bd):
    runners = bd.sites({"siteA": {"name": "Site A", "url_patterns": "", "login_url": "https://www.example.com/login"}})
    j = bd.route("https://cdn.www.example.com/a\nhttps://example.com/b\nhttps://www.example.com/c").get_json()
    assert j["unrouted"] == []
    assert runners["siteA"].loaded == ["https://cdn.www.example.com/a", "https://example.com/b",
                                       "https://www.example.com/c"]


# F8 -- an unreadable history is not "never downloaded" ----------------------------------------------

def test_lookup_reports_an_unreadable_history(bd, monkeypatch):
    """S10: the DB error was swallowed -> known:false with no error key; the badge read 'not downloaded'."""
    bd.sites({})
    monkeypatch.setattr(bd.ext, "db_conn", _BrokenConnection)
    info = bd.lookup("https://example.com/v/1")
    assert info["known"] is None
    assert info["history_status"] == "unknown"
    assert "unreadable" in info["history_error"]


def test_lookup_measured_empty_history_is_still_not_known(bd):
    bd.sites({})
    info = bd.lookup("https://example.com/v/1")
    assert info["known"] is False and info["history_count"] == 0
    assert "history_error" not in info and "history_status" not in info
