"""dl95-justporn-2 (O1513 A5-A finding D2): Scrape listing refused a same-site 301.

Measured on test2: justporn.com -> "URL returned 301 redirect -- pass the final URL directly" although the target
was www.justporn.com. GREEN: /api/scrape_listing follows a redirect whose target is on the same registrable domain
and public (re-checked per hop, at most _MAX_SAME_SITE_REDIRECTS hops) and scrapes the final page (reported as "url"); a cross-site or
non-public hop is still refused with the old message, before it is fetched.

Hermetic: httpx.Client is replaced by a scripted client; no network.
"""
from __future__ import annotations

import httpx
import pytest

import bulk_downloader.app as app_mod
import bulk_downloader.app_scrape_listing as listing

BD_GATE_SCOPE = "module"

PAGE = '<a href="/video/4242/some-scene/">scene</a><a href="/category/amateur/">cat</a>'


class _Resp:
    def __init__(self, status, location=None, text=""):
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


def _client(script, fetched):
    class _Client:
        def __init__(self, *args, **kwargs):
            assert kwargs.get("follow_redirects") is False  # hops stay under our control

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            fetched.append(url)
            return script[url]

    return _Client


def _post(monkeypatch, script, url, public=lambda u: True):
    fetched = []
    monkeypatch.setattr(httpx, "Client", _client(script, fetched))
    monkeypatch.setattr(app_mod, "_is_url_public", public)
    r = app_mod.app.test_client().post("/api/scrape_listing", json={"url": url})
    return r, r.get_json(), fetched


def test_apex_to_www_301_is_followed_and_the_final_page_scraped(monkeypatch):
    script = {"https://justporn.com/": _Resp(301, "https://www.justporn.com/"),
              "https://www.justporn.com/": _Resp(200, text=PAGE)}
    r, body, fetched = _post(monkeypatch, script, "https://justporn.com/")
    assert r.status_code == 200, body
    assert fetched == ["https://justporn.com/", "https://www.justporn.com/"]
    assert body["url"] == "https://www.justporn.com/"  # the page the links came from
    assert body["found"] == ["https://www.justporn.com/video/4242/some-scene/"], body  # joined on the final URL


def test_relative_location_and_scheme_upgrade_are_followed(monkeypatch):
    script = {"http://justporn.com/latest": _Resp(302, "https://justporn.com/latest/"),
              "https://justporn.com/latest/": _Resp(308, "/latest/1/"),
              "https://justporn.com/latest/1/": _Resp(200, text=PAGE)}
    r, body, fetched = _post(monkeypatch, script, "http://justporn.com/latest")
    assert r.status_code == 200, body
    assert fetched[-1] == "https://justporn.com/latest/1/" and body["url"] == fetched[-1]


def test_no_redirect_response_is_unchanged(monkeypatch):
    r, body, fetched = _post(monkeypatch, {"https://www.justporn.com/": _Resp(200, text=PAGE)},
                             "https://www.justporn.com/")
    assert r.status_code == 200 and fetched == ["https://www.justporn.com/"]
    assert body["url"] == "https://www.justporn.com/"


@pytest.mark.parametrize("location", [
    "https://evil.example/list",             # other site
    "https://justporn.com.evil.example/",    # suffix look-alike
    "ftp://www.justporn.com/",               # not http(s)
    "",                                      # no Location
])
def test_cross_site_or_malformed_redirect_is_refused_unfetched(monkeypatch, location):
    script = {"https://justporn.com/": _Resp(301, location)}
    r, body, fetched = _post(monkeypatch, script, "https://justporn.com/")
    assert r.status_code == 400 and "redirect" in body["error"], body
    assert fetched == ["https://justporn.com/"]  # the refused target is never requested


def test_same_site_hop_to_a_non_public_host_is_refused(monkeypatch):
    script = {"https://justporn.com/": _Resp(301, "https://internal.justporn.com/")}
    public = lambda u: "internal." not in u  # noqa: E731 -- the per-hop SSRF check says no
    r, body, fetched = _post(monkeypatch, script, "https://justporn.com/", public=public)
    assert r.status_code == 400 and "redirect" in body["error"], body
    assert fetched == ["https://justporn.com/"]


def test_redirect_loop_is_bounded(monkeypatch):
    script = {"https://justporn.com/a": _Resp(302, "/b"), "https://justporn.com/b": _Resp(302, "/a")}
    r, body, fetched = _post(monkeypatch, script, "https://justporn.com/a")
    assert r.status_code == 400 and "redirect" in body["error"], body
    assert len(fetched) == listing._MAX_SAME_SITE_REDIRECTS + 1
