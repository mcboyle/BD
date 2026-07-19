"""RED-first guard for v3.66.552: selector_playground.fetch_page SSRF (F-CBD12-02 /
witness F-CORE_BD12-01).

fetch_page issues requests.get(url, ..., allow_redirects=True) (and, in the playwright
branch, page.goto(url)) with NO is_global / host validation, and RETURNS the full response
body (html / headers / final_url). It is reachable via POST /api/playground/test
(app_playground -> playground -> fetch_page), so an operator can fetch
http://169.254.169.254/latest/meta-data/ or any internal service and READ the body -- a
full SSRF read primitive (more severe than the blind/HEAD siblings). allow_redirects=True
also means a public URL that 30x-redirects inward is followed.

The fix validates the target host via the canonical _common._is_safe_public_host at the
TOP of fetch_page -- before either branch -- refusing a non-global target with the module's
{"ok": False, "error": "blocked: ...", "html": ""} shape (no body), and additionally
follows redirects manually with per-hop re-validation in the requests branch (and a
per-request route guard in the playwright branch) so a redirect can't reach an internal
host.

RED on the pre-552 tree: no host guard -> a loopback/CGNAT target is fetched and its body
returned (requests.get patched to a spy so nothing hits the network; the playwright branch
proven via a sentinel so no browser is launched). GREEN once fetch_page refuses the target
before the fetch.

Convention: zero-arg fns; requests.get / _fetch_playwright patched on the module and
restored in try/finally.
"""
import bulk_downloader.selector_playground as sp

_LOOPBACK = "http://127.0.0.1:5599/secret"          # loopback (internal service)
_CGNAT = "http://100.64.0.1/x"                       # RFC 6598 shared address space
_PUBLIC = "http://93.184.216.34/page"                # public unicast literal


class _Resp:
    def __init__(self, status_code=200, text="SECRET-BODY", url="", headers=None):
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = text
        self.url = url
        self.headers = headers or {}

    def close(self):
        pass


_gets = []


def _spy_get(url, **_k):
    _gets.append(url)
    return _Resp(url=url)


def _patch_get(spy=_spy_get):
    orig = sp.requests.get
    sp.requests.get = spy
    return lambda: setattr(sp.requests, "get", orig)


def _assert_blocked_requests(url):
    _gets.clear()
    restore = _patch_get()
    try:
        res = sp.fetch_page(url, use_playwright=False)
        assert _gets == [], f"guard must block {url} before the fetch, but fetched: {_gets}"
        assert res.get("ok") is False, f"expected ok=False for {url}, got {res!r}"
        assert "block" in (res.get("error") or "").lower(), \
            f"expected a 'blocked' error for {url}, got {res!r}"
        assert res.get("html") == "", \
            f"body must NOT be returned for a blocked target {url}, got {res!r}"
    finally:
        restore()


def test_fetch_page_blocks_loopback_requests():
    _assert_blocked_requests(_LOOPBACK)


def test_fetch_page_blocks_cgnat_requests():
    _assert_blocked_requests(_CGNAT)


def test_fetch_page_blocks_loopback_playwright():
    # the entry guard must also cover the playwright branch, before the browser launches.
    called = []

    def _sentinel(url, **_k):
        called.append(url)
        return {"ok": True, "status": 200, "html": "SECRET-BODY",
                "final_url": url, "error": ""}

    orig = sp._fetch_playwright
    sp._fetch_playwright = _sentinel
    try:
        res = sp.fetch_page(_LOOPBACK, use_playwright=True)
        assert called == [], f"_fetch_playwright must not run for a loopback target: {called}"
        assert res.get("ok") is False and "block" in (res.get("error") or "").lower(), \
            f"expected blocked (no browser) for loopback, got {res!r}"
    finally:
        sp._fetch_playwright = orig


def test_fetch_page_blocks_redirect_to_loopback_requests():
    """A public URL that 302-redirects to a loopback target must NOT have the private
    hop fetched, and must yield a block -- not the internal body. RED pre-fix (the 302 is
    recorded ok; only requests' own redirect engine, absent under the spy, would chase it);
    GREEN post-fix (manual per-hop re-validation refuses the redirect target)."""
    _gets.clear()

    def _redir(url, **_k):
        _gets.append(url)
        if "93.184.216.34" in url:
            return _Resp(302, url=url, headers={"Location": "http://127.0.0.1:5599/x"})
        return _Resp(200, url=url, text="INTERNAL-BODY")

    restore = _patch_get(_redir)
    try:
        res = sp.fetch_page(_PUBLIC, use_playwright=False)
        assert all("127.0.0.1" not in u for u in _gets), \
            f"redirect to loopback must not be fetched, but fetched: {_gets}"
        assert res.get("ok") is False, f"a redirect to loopback must yield ok=False, got {res!r}"
        assert "block" in (res.get("error") or "").lower() and res.get("html") == "", \
            f"expected a 'blocked' error and no body, got {res!r}"
    finally:
        restore()


def test_fetch_page_allows_public_requests():
    """Regression: a public target must PASS the guard and be fetched, body returned --
    proving the guard doesn't over-block. Green on both pre- and post-fix trees."""
    _gets.clear()
    restore = _patch_get()
    try:
        res = sp.fetch_page(_PUBLIC, use_playwright=False)
        assert _PUBLIC in _gets, f"public target should be fetched, got {_gets}"
        assert res.get("ok") is True and res.get("html") == "SECRET-BODY", \
            f"public fetch should return the body, got {res!r}"
    finally:
        restore()
