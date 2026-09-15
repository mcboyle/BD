"""Row 776: the auto-teach preflight (runner.py Phase 41.5) flags the
first pending URL needs_review whenever there are no learned download
selectors and no applied template -- even when that URL is ALREADY an
accepted direct media href needing no selectors/teaching at all.

BD_GATE_SCOPE is a module-level ASSIGNMENT below -- the gate classifier
parses the assignment, not a docstring line.

No page has been fetched at this pre-flight point (that is the whole
reason the preflight exists: to avoid spawning a worker/browser only to
immediately demand teaching), so `dry_run.inspect_candidates` -- which
needs real HTML -- cannot be the check. `candidate_filter.classify(url=...)`
with no `page_host` is the only candidate signal available pre-fetch: it
classifies the URL string alone, and omitting `page_host` correctly skips
the same-site rejection branch (there is no site to compare against yet).
"""

BD_GATE_SCOPE = "module"

from bulk_downloader import runner as _runner

DIRECT_MEDIA_URL = "https://cdn.example.com/videos/scene_2160.mp4"
PLAIN_PAGE_URL = "https://members.example.com/video/watch/12345/some-scene"


def test_precondition_the_helper_exists():
    """row 776's fix is this helper. If it is missing, the row is not
    done and every assertion below is vacuously about None."""
    helper = getattr(_runner, "_pending_url_already_downloadable", None)
    assert helper is not None, (
        "row 776: bulk_downloader.runner._pending_url_already_downloadable "
        "is missing -- the auto-teach preflight has no way to see that a "
        "pending URL is already an accepted direct media href")


def test_direct_media_url_is_already_downloadable():
    assert _runner._pending_url_already_downloadable(DIRECT_MEDIA_URL) is True, (
        f"{DIRECT_MEDIA_URL!r} is a direct .mp4 href -- candidate_filter "
        f"must accept it as kind=download with no page fetched")


def test_plain_page_url_is_not_already_downloadable():
    """Negative control: prove the fixture reaches a real verdict (the
    shape is nonzero -- classify() actually ran) before trusting the
    False result, and that the helper does not just always return True."""
    from bulk_downloader import candidate_filter as _cf
    verdict = _cf.classify(url=PLAIN_PAGE_URL)
    assert verdict.reason, "precondition: classify() produced a real verdict"
    assert verdict.kind != "download", (
        "precondition: a bare page URL with no media extension must not "
        "itself classify as kind=download -- otherwise this control proves "
        "nothing about the helper")
    assert _runner._pending_url_already_downloadable(PLAIN_PAGE_URL) is False, (
        f"{PLAIN_PAGE_URL!r} is a page, not media -- the helper must not "
        f"claim it is already downloadable")


def test_auto_teach_preflight_consults_the_helper_before_flagging_needs_review():
    """The literal row acceptance: start() must not mark needs_review for
    an already-accepted media winner. start() itself is a thin retry
    wrapper around the @_run_lifecycle_serialized _start_serialized method,
    which is where the Phase 41.5 auto-teach preflight block actually
    lives. Checked at the source level (no browser/thread spawn, matching
    this repo's existing convention for Phase 41.5 -- see
    test_auto_teach_template.py) by proving the conditional that guards
    the needs_review flag actually calls the helper, not merely that the
    helper exists in isolation."""
    import inspect
    src = inspect.getsource(_runner.SiteRunner._start_serialized)
    assert "_pending_url_already_downloadable(" in src, (
        "row 776: SiteRunner._start_serialized()'s auto-teach preflight "
        "block does not call _pending_url_already_downloadable -- an "
        "accepted media winner would still be flagged needs_review")


def test_real_start_arms_the_pool_for_an_already_downloadable_url(tmp_path):
    """The source check above proves the guard calls the helper; it does
    not prove ``start()`` (the public entry point) actually reaches that
    guard. A mutant that deletes ``start()``'s call to ``_start_serialized``
    entirely still returns None (the same value a normal successful start
    returns, per StartOutcome's own docstring) and still leaves the URL
    un-flagged -- so neither of the checks above can tell "ran and passed"
    apart from "never ran". Driven through a REAL SiteRunner.start(), same
    harness shape as test_row433_download_hold_barrier.py: a fake
    ``_worker_loop`` stands in for the browser, and a counting queue proves
    the pending URL was actually queued for a worker to pick up, not just
    that the runner's state field moved.
    """
    import queue
    from bulk_downloader.runner import SiteRunner

    class _CountingQueue(queue.Queue):
        real_puts = 0

        def put(self, item, *a, **k):
            if item is not None:
                self.real_puts += 1
            return super().put(item, *a, **k)

    cfg = {
        "name": "row776-real-start",
        "auto_teach_first_run": True,
        "download_dir": str(tmp_path),
        "disk_threshold_gb": 0.0,
        "site_quota_gb": 1_000_000.0,
        "max_concurrent": 1,
    }
    runner = SiteRunner("row776-real-start", cfg)
    runner._url_queue = _CountingQueue()
    runner._worker_loop = lambda worker_idx=0, run_generation=None: None
    runner.load_urls([DIRECT_MEDIA_URL])

    try:
        runner.start()
        assert runner._state == "running", (
            "row 776: start() did not reach the running transition for an "
            "already-accepted media URL with no template/learned selectors "
            "-- either the preflight wrongly diverted to teach, or "
            "_start_serialized was never actually invoked")
        assert runner._url_queue.real_puts == 1, (
            "row 776: the running transition was reached but the pending "
            "URL was never queued for a worker -- _start_serialized's own "
            "body did not run")
        assert runner.jobs[DIRECT_MEDIA_URL].get("status") != "needs_review", (
            "row 776: an accepted media winner was still flagged "
            "needs_review by a real start()")
    finally:
        runner._stop.set()
        runner._pause.set()


# --- REFUTE fix: originating case is a PAGE url, not a bare media href ---
# The correctness lens's verdict: the fix above only exempts a pending URL
# that is ITSELF a media href. The reported defect (SITE_REPORT n=1,
# https://members.nubiles.net/video/watch/255776/show-me-how) is a PAGE
# url whose ranker-chosen candidate, once the page is fetched, is a media
# href -- that shape was still flagged needs_review on row776b's post-image.
PAGE_URL = "https://members.nubiles.net/video/watch/255776/show-me-how"
MEDIA_HREF = ("https://content4.nubiles.net/members/nubilescc/exclusive/"
              "nika/show-me-how_2160.mp4")
_ACCEPTED_MEDIA_HTML = (
    f'<html><body><a href="{MEDIA_HREF}" class="download">'
    f'Download 2160p</a></body></html>')


def test_ranker_helper_exists():
    helper = getattr(_runner, "_pending_url_ranker_accepts_media", None)
    assert helper is not None, (
        "row 776 REFUTE fix: bulk_downloader.runner."
        "_pending_url_ranker_accepts_media is missing -- the auto-teach "
        "preflight has no way to see that a PAGE url's fetched HTML ranks "
        "an accepted media winner")


class _FakeHttpxResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        return None


class _FakeHttpxClient:
    """Stands in for httpx.Client -- records the guard kwargs the helper
    passed (transport/follow_redirects/cookies) so the SSRF-guard tests
    can assert on them, and returns a canned response."""
    last_kwargs = None
    last_get_kwargs = None
    response = None
    raise_exc = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kwargs):
        type(self).last_get_kwargs = kwargs
        if type(self).raise_exc is not None:
            raise type(self).raise_exc
        return type(self).response


def _patch_httpx_client(monkeypatch, html=None, status_code=200, raise_exc=None):
    _FakeHttpxClient.last_kwargs = None
    _FakeHttpxClient.last_get_kwargs = None
    _FakeHttpxClient.response = (
        _FakeHttpxResponse(html, status_code) if html is not None else None)
    _FakeHttpxClient.raise_exc = raise_exc
    monkeypatch.setattr(_runner.httpx, "Client", _FakeHttpxClient)


def test_ranker_helper_true_when_fetched_page_ranks_an_accepted_media_winner(monkeypatch):
    """Precondition: prove the fixture HTML actually produces an accepted
    winner via the real ranker before trusting the helper's True result --
    otherwise a helper that always returns True would pass vacuously."""
    from bulk_downloader import dry_run as _dry_run
    verdict = _dry_run.inspect_candidates(_ACCEPTED_MEDIA_HTML, page_url=PAGE_URL)
    assert verdict.get("winner"), (
        "precondition: the fixture HTML must produce an accepted "
        "inspect_candidates winner, or this test proves nothing about "
        "the helper")

    _patch_httpx_client(monkeypatch, html=_ACCEPTED_MEDIA_HTML)
    assert _runner._pending_url_ranker_accepts_media(PAGE_URL) is True, (
        "row 776 REFUTE fix: a PAGE url whose fetched HTML ranks an "
        "accepted media winner must be reported as ranker-accepted")


def test_ranker_helper_fails_open_on_network_error(monkeypatch):
    """Negative control: a preflight check must never block start() -- a
    network error degrades silently to the pre-existing must-teach path."""
    import httpx as _httpx
    _patch_httpx_client(
        monkeypatch,
        raise_exc=_httpx.ConnectError("no route to host"),
    )
    assert _runner._pending_url_ranker_accepts_media(PAGE_URL) is False, (
        "row 776 REFUTE fix: a network error must fail open (return "
        "False), never raise or block the preflight")


def test_ranker_helper_fails_open_when_no_winner(monkeypatch):
    """Negative control: a page whose fetched HTML has no accepted
    candidate at all must still fall through to needs_review."""
    _patch_httpx_client(
        monkeypatch, html="<html><body><p>no links here</p></body></html>")
    assert _runner._pending_url_ranker_accepts_media(PAGE_URL) is False, (
        "row 776 REFUTE fix: no accepted winner must fail open, not be "
        "mistaken for an accepted media page")


def test_ranker_helper_refuses_a_link_local_url_with_no_request_made(monkeypatch):
    """AUDIT FIX (correctness REFUTE): the helper fetches a caller-
    controlled URL exactly like _scrape_listing_urls (F-RUN01-01), so it
    must apply the same _is_safe_public_host SSRF guard -- a link-local
    metadata-service URL is refused before any request is attempted."""
    _patch_httpx_client(monkeypatch, html=_ACCEPTED_MEDIA_HTML)
    ssrf_url = "http://169.254.169.254/latest/meta-data/"
    assert _runner._pending_url_ranker_accepts_media(ssrf_url) is False, (
        "row776c AUDIT FIX: a link-local URL must be refused, not fetched")
    assert _FakeHttpxClient.last_get_kwargs is None, (
        "row776c AUDIT FIX: the SSRF guard must refuse before any HTTP "
        "request is made -- the fake client's get() was called")


def test_ranker_helper_requires_a_strong_url_signal_not_a_bare_label(monkeypatch):
    """A nav page whose only positive signal is a resolution_label (a
    stray number, e.g. "/top-100") must not be mistaken for media --
    candidate_filter._STRONG_URL_SIGNALS deliberately excludes it."""
    label_only_html = (
        '<html><body><a href="https://members.nubiles.net/top-100">'
        "1080p</a></body></html>")
    from bulk_downloader import dry_run as _dry_run
    from bulk_downloader import candidate_filter as _cf
    verdict = _dry_run.inspect_candidates(label_only_html, page_url=PAGE_URL)
    winner = verdict.get("winner")
    assert winner, "precondition: fixture must still produce a winner"
    signals = set(winner.get("signals") or ())
    assert not (signals & _cf._STRONG_URL_SIGNALS), (
        "precondition: winner must have no strong URL signal, or this "
        "test proves nothing about the helper's signal-strength gate")

    _patch_httpx_client(monkeypatch, html=label_only_html)
    assert _runner._pending_url_ranker_accepts_media(PAGE_URL) is False, (
        "row776c AUDIT FIX: a label-only winner (no strong URL signal) "
        "must not be reported as ranker-accepted")


def test_ranker_helper_refuses_a_redirect_response_even_with_a_strong_winner(monkeypatch):
    """AUDIT FIX (shape REFUTE, row776d): follow_redirects=False stops
    httpx from ever fetching the 3xx Location target, but does NOT
    restrict the body of the 3xx response itself -- an attacker-
    controlled but SSRF-safe host could serve a 302 whose own body
    carries a crafted strong URL signal. The helper must refuse on
    the 3xx status code itself, before ever scoring the body, or a
    redirecting URL would be wrongly treated as already-downloadable."""
    _patch_httpx_client(monkeypatch, html=_ACCEPTED_MEDIA_HTML, status_code=302)
    assert _runner._pending_url_ranker_accepts_media(PAGE_URL) is False, (
        "row776d AUDIT FIX: a 3xx response must be refused by status "
        "code alone, even when its body would otherwise rank an "
        "accepted media winner")


def test_ranker_helper_installs_the_guarded_transport_with_no_redirects_and_the_given_proxy(monkeypatch):
    """AUDIT FIX (correctness REFUTE, row776d): the transport-selection
    line must be pinned, not merely present -- a mutant that flips
    follow_redirects=True (letting httpx silently follow a 3xx to a
    host the guard never re-checks) or drops proxy= from
    guarded_transport(...) (routing the fetch onto the clear interface
    instead of the required VPN tunnel) leaves every other test green."""
    import bulk_downloader.ssrf_transport as _ssrf_transport
    real_guarded_transport = _ssrf_transport.guarded_transport
    calls = []

    def _spy(policy, **kwargs):
        calls.append((policy, kwargs))
        return real_guarded_transport(policy, **kwargs)

    monkeypatch.setattr(_ssrf_transport, "guarded_transport", _spy)
    _patch_httpx_client(monkeypatch, html=_ACCEPTED_MEDIA_HTML)
    sentinel_proxy = "socks5://127.0.0.1:9050"
    assert _runner._pending_url_ranker_accepts_media(
        PAGE_URL, proxy=sentinel_proxy) is True

    assert calls, "row776d AUDIT FIX: guarded_transport was never called"
    policy, kwargs = calls[0]
    assert policy is _ssrf_transport.PUBLIC_ONLY
    assert kwargs.get("proxy") == sentinel_proxy, (
        "row776d AUDIT FIX: the caller's proxy must reach "
        "guarded_transport(), or a VPN-required site fetches on the "
        "clear interface instead of the tunnel")
    assert _FakeHttpxClient.last_kwargs.get("follow_redirects") is False, (
        "row776d AUDIT FIX: follow_redirects must stay pinned False, or "
        "httpx silently follows a 3xx to a host the guard never re-checks")


def test_ranker_helper_attaches_cookies_from_the_cookie_jar(monkeypatch, tmp_path):
    """The helper must still read a members-only page with the runner's
    own cookie jar, not anonymously -- guarding the fetch must not drop
    this pre-existing behavior."""
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".nubiles.net\tTRUE\t/\tFALSE\t0\tsession\tabc123\n")
    _patch_httpx_client(monkeypatch, html=_ACCEPTED_MEDIA_HTML)
    assert _runner._pending_url_ranker_accepts_media(
        PAGE_URL, cookie_file=str(cookie_file)) is True
    cookies = _FakeHttpxClient.last_get_kwargs.get("cookies")
    assert isinstance(cookies, _runner.httpx.Cookies), (
        "row776c AUDIT FIX: the guarded fetch must still forward the "
        "site's cookie jar")
    assert cookies.get("session", domain=".nubiles.net", path="/") == "abc123"


def _wire_cookie_headers(monkeypatch, tmp_path, urls):
    """Run the helper against a real httpx.Client over a MockTransport and
    return {url: Cookie header actually sent} -- the wire, not a kwarg."""
    import httpx
    jar = tmp_path / "scoped-cookies.txt"
    jar.write_text(
        "# Netscape HTTP Cookie File\n"
        ".auth.example\tTRUE\t/private\tTRUE\t2147483647\tsession\t"
        "cookie-fixture-not-a-secret\n")
    seen = {}

    def handle(request):
        seen[str(request.url)] = request.headers.get("cookie", "")
        return httpx.Response(200, text="<html><body>no download</body></html>")

    monkeypatch.setattr(
        "bulk_downloader.provider_resolve_impl._common._is_safe_public_host",
        lambda host: (True, "public fixture"))
    monkeypatch.setattr(
        "bulk_downloader.ssrf_transport.guarded_transport",
        lambda *a, **k: httpx.MockTransport(handle))
    for url in urls:
        _runner._pending_url_ranker_accepts_media(url, cookie_file=str(jar))
    assert set(seen) == set(urls), seen
    return seen


def test_ranker_helper_sends_a_jar_cookie_only_to_the_url_it_is_scoped_to(monkeypatch, tmp_path):
    """Row 776rp2 (correctness REFUTE, A2-A): a cookie scoped
    `.auth.example /private secure` must reach ONLY the in-scope https url.
    Flattening the jar to {name: value} sent it to every preflight target --
    a different host, and the same host over plain http on another path."""
    in_scope = "https://auth.example/private/page"
    wrong_host = "https://unrelated.example/page"
    wrong_path_insecure = "http://auth.example/public/page"
    seen = _wire_cookie_headers(monkeypatch, tmp_path,
                                [in_scope, wrong_host, wrong_path_insecure])
    assert "session=cookie-fixture-not-a-secret" in seen[in_scope], seen
    leaked = [u for u in (wrong_host, wrong_path_insecure) if "session=" in seen[u]]
    assert leaked == [], (
        "jar cookie sent outside its domain/path/secure scope to: "
        f"{leaked} -- the jar was flattened instead of consulted per url")


def test_negative_control_the_in_scope_url_still_carries_the_jar_cookie(monkeypatch, tmp_path):
    """The other answer: scoping must not become 'never send cookies' --
    the members-only page keeps the runner's own session."""
    seen = _wire_cookie_headers(monkeypatch, tmp_path,
                                ["https://auth.example/private/deeper/page"])
    assert seen == {"https://auth.example/private/deeper/page":
                    "session=cookie-fixture-not-a-secret"}, seen


def test_auto_teach_preflight_consults_the_ranker_helper_before_flagging_needs_review():
    """Same source-level proof pattern as
    test_auto_teach_preflight_consults_the_helper_before_flagging_needs_review
    above, for the new ranker-consulting helper."""
    import inspect
    src = inspect.getsource(_runner.SiteRunner._start_serialized)
    assert "_pending_url_ranker_accepts_media(" in src, (
        "row 776 REFUTE fix: SiteRunner._start_serialized()'s auto-teach "
        "preflight block does not call _pending_url_ranker_accepts_media "
        "-- a PAGE url whose fetched HTML ranks an accepted media winner "
        "would still be flagged needs_review")


def test_real_start_arms_the_pool_for_a_page_url_whose_ranker_accepts_media(tmp_path, monkeypatch):
    """The literal row776 REFUTE acceptance probe: PAGE url + fixture HTML
    whose winner is an accepted media_extension candidate; assert
    status != needs_review and the URL is queued (real_puts == 1), driven
    through a REAL SiteRunner.start() exactly as
    test_real_start_arms_the_pool_for_an_already_downloadable_url above."""
    import queue
    from bulk_downloader.runner import SiteRunner

    _patch_httpx_client(monkeypatch, html=_ACCEPTED_MEDIA_HTML)
    monkeypatch.setattr(
        SiteRunner, "_download_proxy_url", lambda self: None)

    class _CountingQueue(queue.Queue):
        real_puts = 0

        def put(self, item, *a, **k):
            if item is not None:
                self.real_puts += 1
            return super().put(item, *a, **k)

    cfg = {
        "name": "row776-refute-real-start",
        "auto_teach_first_run": True,
        "download_dir": str(tmp_path),
        "disk_threshold_gb": 0.0,
        "site_quota_gb": 1_000_000.0,
        "max_concurrent": 1,
    }
    runner = SiteRunner("row776-refute-real-start", cfg)
    runner._url_queue = _CountingQueue()
    runner._worker_loop = lambda worker_idx=0, run_generation=None: None
    runner.load_urls([PAGE_URL])

    try:
        runner.start()
        assert runner._state == "running", (
            "row 776 REFUTE fix: start() did not reach the running "
            "transition for a PAGE url whose fetched HTML ranks an "
            "accepted media winner -- the preflight wrongly diverted to "
            "teach")
        assert runner._url_queue.real_puts == 1, (
            "row 776 REFUTE fix: the running transition was reached but "
            "the pending URL was never queued for a worker")
        assert runner.jobs[PAGE_URL].get("status") != "needs_review", (
            "row 776 REFUTE fix: a PAGE url whose ranker winner is an "
            "accepted media href was still flagged needs_review by a "
            "real start() -- this is the originating defect shape "
            "(SITE_REPORT n=1, nubiles.net) still reproducing")
    finally:
        runner._stop.set()
        runner._pause.set()


def test_real_start_fails_closed_and_never_fetches_when_the_proxy_tunnel_is_down(tmp_path, monkeypatch):
    """AUDIT FIX (correctness REFUTE, row776d): `_ranker_accepts`'s own
    `except Exception: return False` around `_download_proxy_url()` must
    be pinned -- a mutant that instead falls through with `proxy = None`
    on a VPN-required-but-down tunnel would route the ranker's SSRF-
    capable fetch onto the clear interface instead of degrading to the
    must-teach path, and no other test in this band would catch it."""
    import queue
    from bulk_downloader.runner import SiteRunner

    _patch_httpx_client(monkeypatch, html=_ACCEPTED_MEDIA_HTML)

    def _tunnel_down(self):
        raise RuntimeError("vpn tunnel is down")

    monkeypatch.setattr(SiteRunner, "_download_proxy_url", _tunnel_down)

    cfg = {
        "name": "row776d-refute-proxy-down",
        "auto_teach_first_run": True,
        "download_dir": str(tmp_path),
        "disk_threshold_gb": 0.0,
        "site_quota_gb": 1_000_000.0,
        "max_concurrent": 1,
    }
    runner = SiteRunner("row776d-refute-proxy-down", cfg)
    runner._url_queue = queue.Queue()
    runner._worker_loop = lambda worker_idx=0, run_generation=None: None
    runner.load_urls([PAGE_URL])

    try:
        runner.start()
        assert runner.jobs[PAGE_URL].get("status") == "needs_review", (
            "row776d AUDIT FIX: a down proxy tunnel must degrade to the "
            "must-teach path, not silently arm the pool")
        assert _FakeHttpxClient.last_get_kwargs is None, (
            "row776d AUDIT FIX: _download_proxy_url() raising must stop "
            "the ranker helper before any HTTP request is made -- "
            "falling through with proxy=None would fetch on the clear "
            "interface instead of the required tunnel")
    finally:
        runner._stop.set()
        runner._pause.set()
