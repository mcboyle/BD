"""fx-tiny4k-relogin-loop (O1567/O1568d, test1 10.0.70.194, harness-work/ISP-SPLIT-O1564/results/test1/tiny4k-relogin.md).

Measured on test1 (journal, 20:14-20:17Z): queue row https://tiny4k.com/ (the public homepage) yielded no download
control; _check_redirect's logged-out SHAPE (dl95-kink-1: a login offered, no logout) said "auth"; the re-login ran
and "attempt 1 settled ok"; the requeued row read the SAME shape again -> a second live login, "attempt 2 settled ok"
-> the same shape again -> dead_letter "auth-required, retries exhausted". Two live logins spent on a page that
offers a Login whatever the session is (the member scene URL downloaded 5.2 GB with that same session at 20:35Z).

Contract after the fix: the shape is evidence of a logged-out session only until a login settles OK after the shape
verdict for that URL. The same URL reading the same shape after such a login is not "auth" (no further re-login);
the row takes the no-download-control path. A FAILED re-login, another URL, and the first verdict are unchanged.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

URL = "https://tiny4k.com/"
OTHER = "https://tiny4k.com/members/video/some-scene"

# The public homepage shape: header Login + Join links, no logout control, no download control.
HOMEPAGE = """<!doctype html><html><body>
<header><a href="/">TINY4K</a><nav><a href="/tour/videos">Videos</a>
<a href="https://tiny4k.com/login">Login</a> <a href="/join">Join Now</a></nav></header>
<main><h1>Petite teens in 4K</h1><div class="thumb"><img src="/t/1.jpg"><span>Trailer</span></div></main>
</body></html>"""

MEMBER = HOMEPAGE.replace('<a href="https://tiny4k.com/login">Login</a>', '<a href="/logout">Log out</a>')
assert MEMBER != HOMEPAGE


@contextmanager
def _page(html, url=URL):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_context(java_script_enabled=False).new_page()
            pg.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html)
                     if r.request.url == url else r.abort())
            pg.goto(url, wait_until="domcontentloaded")
            yield pg
        finally:
            br.close()


def _runner():
    from bulk_downloader.runner_auth import AuthMixin

    class _R(AuthMixin):
        site_id = "fxtiny4k"

        def __init__(self):
            self.config = {"login_url": "https://tiny4k.com/login"}
            self._login_attempt_seq = 1          # the start's own login
            self._login_outcome = (1, True)

    return _R()


def _relogin(r, ok):
    """What login_async + its settle do: stamp a new attempt, then settle it (with its settle time)."""
    r._login_attempt_seq += 1
    r._login_outcome = (r._login_attempt_seq, ok)
    r._login_outcome_at = time.time()


def test_first_shape_verdict_is_still_auth():
    with _page(HOMEPAGE) as pg:
        assert _runner()._check_redirect(pg, URL, no_candidate=True) == "auth"


def test_same_shape_after_a_settled_ok_relogin_is_not_auth():
    r = _runner()
    with _page(HOMEPAGE) as pg:
        assert r._check_redirect(pg, URL, no_candidate=True) == "auth"
        _relogin(r, ok=True)
        got = r._check_redirect(pg, URL, no_candidate=True)
    assert got is None, f"FX_TINY4K_RELOGIN_LOOP_SHAPE_REPEATS_AUTH: {got!r} after login attempt 2 settled ok"


def test_the_loop_spends_one_relogin_not_two():
    """The measured sequence: verdict, re-login ok, verdict, re-login ok, verdict -> dead_letter."""
    r = _runner()
    verdicts = []
    with _page(HOMEPAGE) as pg:
        for _ in range(3):
            v = r._check_redirect(pg, URL, no_candidate=True)
            verdicts.append(v)
            if v != "auth":
                break
            _relogin(r, ok=True)
    assert verdicts.count("auth") == 1, f"FX_TINY4K_RELOGIN_LOOP_SHAPE_REPEATS_AUTH: {verdicts}"


def test_negative_control_failed_relogin_keeps_auth():
    r = _runner()
    with _page(HOMEPAGE) as pg:
        assert r._check_redirect(pg, URL, no_candidate=True) == "auth"
        _relogin(r, ok=False)
        assert r._check_redirect(pg, URL, no_candidate=True) == "auth"


def test_negative_control_another_url_is_judged_on_its_own():
    r = _runner()
    with _page(HOMEPAGE) as pg:
        assert r._check_redirect(pg, URL, no_candidate=True) == "auth"
    _relogin(r, ok=True)
    with _page(HOMEPAGE, OTHER) as pg:
        assert r._check_redirect(pg, OTHER, no_candidate=True) == "auth"


def test_negative_control_member_page_is_not_auth():
    with _page(MEMBER) as pg:
        assert _runner()._check_redirect(pg, URL, no_candidate=True) is None


def test_a_real_expiry_later_still_relogs():
    """Lens REFUTE (bd-worker-OP-bd3-B): the disproof covers only the re-check right after the re-login.
    A URL that once showed Log out, retried after the session REALLY expired (no newer login), re-logs as on base."""
    import bulk_downloader.runner_auth as ra
    window = getattr(ra, "_SHAPE_DISPROOF_WINDOW_S", 600)  # getattr: the pre-fix cut has no window -> distinctive RED
    r = _runner()
    with _page(HOMEPAGE) as pg:
        assert r._check_redirect(pg, URL, no_candidate=True) == "auth"
    _relogin(r, ok=True)
    with _page(MEMBER) as pg:
        assert r._check_redirect(pg, URL, no_candidate=True) is None
    r._login_outcome_at -= window + 1          # time passes; the session expires
    with _page(HOMEPAGE) as pg:
        got = r._check_redirect(pg, URL, no_candidate=True)
    assert got == "auth", f"FX_TINY4K_STALE_DISPROOF: {got!r} -- a login settled >{window}s ago still disproved the shape"


def test_negative_control_unknown_settle_time_keeps_auth():
    """No settle time recorded (e.g. a runner restored without one): never suppress -- base behaviour."""
    r = _runner()
    with _page(HOMEPAGE) as pg:
        assert r._check_redirect(pg, URL, no_candidate=True) == "auth"
        _relogin(r, ok=True)
        del r._login_outcome_at
        assert r._check_redirect(pg, URL, no_candidate=True) == "auth"
