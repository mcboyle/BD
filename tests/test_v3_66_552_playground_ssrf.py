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

The requests branch uses a minimal injected API so all nodeids execute even when the
optional requests package is absent. pytest's monkeypatch restores both present and
absent module attributes after every test.
"""
from types import SimpleNamespace

import pytest

import bulk_downloader.selector_playground as sp

BD_GATE_SCOPE = "repo-wide"

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


def _install_requests(monkeypatch, spy=_spy_get):
    fake = SimpleNamespace(get=spy)
    monkeypatch.setattr(sp, "requests", fake, raising=False)
    monkeypatch.setattr(sp, "_HAS_REQUESTS", True)
    assert sp.requests is fake and sp._HAS_REQUESTS is True


def _assert_blocked_requests(url, monkeypatch):
    _gets.clear()
    _install_requests(monkeypatch)
    res = sp.fetch_page(url, use_playwright=False)
    assert _gets == [], f"guard must block {url} before the fetch, but fetched: {_gets}"
    assert res.get("ok") is False, f"expected ok=False for {url}, got {res!r}"
    assert "block" in (res.get("error") or "").lower(), \
        f"expected a 'blocked' error for {url}, got {res!r}"
    assert res.get("html") == "", \
        f"body must NOT be returned for a blocked target {url}, got {res!r}"


def test_fetch_page_blocks_loopback_requests(monkeypatch):
    _assert_blocked_requests(_LOOPBACK, monkeypatch)


def test_fetch_page_blocks_cgnat_requests(monkeypatch):
    _assert_blocked_requests(_CGNAT, monkeypatch)


def test_fetch_page_blocks_loopback_playwright(monkeypatch):
    # the entry guard must also cover the playwright branch, before the browser launches.
    called = []

    def _sentinel(url, **_k):
        called.append(url)
        return {"ok": True, "status": 200, "html": "SECRET-BODY",
                "final_url": url, "error": ""}

    monkeypatch.setattr(sp, "_fetch_playwright", _sentinel)
    res = sp.fetch_page(_LOOPBACK, use_playwright=True)
    assert called == [], f"_fetch_playwright must not run for a loopback target: {called}"
    assert res.get("ok") is False and "block" in (res.get("error") or "").lower(), \
        f"expected blocked (no browser) for loopback, got {res!r}"


def test_fetch_page_blocks_redirect_to_loopback_requests(monkeypatch):
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

    _install_requests(monkeypatch, _redir)
    res = sp.fetch_page(_PUBLIC, use_playwright=False)
    assert _gets == [_PUBLIC], f"only the public first hop may be fetched: {_gets}"
    assert res.get("ok") is False, f"a redirect to loopback must yield ok=False, got {res!r}"
    assert "block" in (res.get("error") or "").lower() and res.get("html") == "", \
        f"expected a 'blocked' error and no body, got {res!r}"


def test_fetch_page_allows_public_requests(monkeypatch):
    """Regression: a public target must PASS the guard and be fetched, body returned --
    proving the guard doesn't over-block. Green on both pre- and post-fix trees."""
    _gets.clear()
    _install_requests(monkeypatch)
    res = sp.fetch_page(_PUBLIC, use_playwright=False)
    assert _gets == [_PUBLIC], f"public target should be fetched once, got {_gets}"
    assert res.get("ok") is True and res.get("html") == "SECRET-BODY", \
        f"public fetch should return the body, got {res!r}"
    assert res.get("final_url") == _PUBLIC


def test_fetch_page_reports_supported_missing_requests_posture(monkeypatch):
    monkeypatch.setattr(sp, "_HAS_REQUESTS", False)
    monkeypatch.delattr(sp, "requests", raising=False)
    assert sp._HAS_REQUESTS is False and not hasattr(sp, "requests")

    res = sp.fetch_page(_PUBLIC, use_playwright=False)

    assert res == {"ok": False, "error": "requests not installed", "html": ""}


def test_requests_dependency_seams_restore_state_in_either_order(monkeypatch):
    monkeypatch.setattr(sp, "_HAS_REQUESTS", False)
    monkeypatch.delattr(sp, "requests", raising=False)
    original_flag = sp._HAS_REQUESTS
    original_present = hasattr(sp, "requests")
    original_requests = getattr(sp, "requests", None)

    def assert_restored():
        assert sp._HAS_REQUESTS is original_flag
        assert hasattr(sp, "requests") is original_present
        if original_present:
            assert sp.requests is original_requests

    def available():
        with monkeypatch.context() as scoped:
            _gets.clear()
            _install_requests(scoped)
            result = sp.fetch_page(_PUBLIC, use_playwright=False)
            assert result["ok"] is True and _gets == [_PUBLIC]
        assert_restored()

    def missing():
        with monkeypatch.context() as scoped:
            scoped.setattr(sp, "_HAS_REQUESTS", False)
            scoped.delattr(sp, "requests", raising=False)
            result = sp.fetch_page(_PUBLIC, use_playwright=False)
            assert result == {
                "ok": False, "error": "requests not installed", "html": "",
            }
        assert_restored()

    available()
    missing()
    missing()
    available()
