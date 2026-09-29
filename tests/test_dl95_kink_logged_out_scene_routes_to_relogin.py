"""dl95-kink-1 (O1513, harness-work/UIUX-20260928/download-95/B6-B/p1/kink/RESULT.md + app-shot-fail.png).

Measured on test2 (build 6f85714f, 01:35-01:39Z): kink c4c38143, auth_state "ok", worker page LOGGED OUT behind the
"Choose Your Option: Join / Purchase scene / Purchase channel" paywall; no login attempted; after a 4-minute search
the job failed "[page_shape] No download button found". _check_redirect's in-place wall test reads AUTH_BODY_RE over
page.content()[:20000]; the logged-out page's only login signal (the login modal's password input) sits at ~62 KB.
Widening the window is WRONG: the MEMBER page (.95 campaign/kink/1) carries the same modal and the same "log in"
wording (password input at ~68 KB). Only the member page has a logout control (a[href="/logout"]).

Contract after the fix: when a page yielded NO download control, a site that logs in (login_url) whose page offers a
login and no logout is "auth" -> _handle_auth_required (re-login + requeue); a member page, a working page, and a
public site are untouched. Fixtures are the two real pages (scripts/styles stripped).
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

FIX = Path(__file__).parent / "fixtures"
URL = "https://www.kink.com/shoot/108454"


@contextmanager
def _page(name):
    html = (FIX / name).read_text(encoding="utf-8")
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            ctx = br.new_context(java_script_enabled=False)
            pg = ctx.new_page()
            pg.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html)
                     if r.request.url == URL else r.abort())
            pg.goto(URL, wait_until="domcontentloaded")
            yield pg
        finally:
            br.close()


def _runner(login_url="https://www.kink.com/login"):
    from bulk_downloader.runner_auth import AuthMixin

    class _R(AuthMixin):
        site_id = "dl95kink"

        def __init__(self):
            self.config = {"login_url": login_url}

    return _R()


LOGGED_OUT = "dl95_kink_108454_logged_out.html"
MEMBER = "dl95_kink_member_scene.html"


def test_precondition_the_20kb_window_misses_and_both_pages_carry_a_login_form():
    from bulk_downloader.constants import AUTH_BODY_RE

    for name in (LOGGED_OUT, MEMBER):
        html = (FIX / name).read_text(encoding="utf-8")
        assert AUTH_BODY_RE.search(html), f"{name}: the login modal's password input is on the page"
    with _page(LOGGED_OUT) as pg:
        assert not AUTH_BODY_RE.search(pg.content()[:20000]), "the measured miss: no signal in the first 20 KB"


def test_logged_out_scene_without_a_candidate_is_auth():
    with _page(LOGGED_OUT) as pg:
        got = _runner()._check_redirect(pg, URL, no_candidate=True)
    assert got == "auth", f"DL95_KINK_LOGGED_OUT_NOT_DETECTED: {got!r}"


def test_member_scene_is_not_auth():
    """Negative control: the member page has the same login modal and a logout control."""
    with _page(MEMBER) as pg:
        assert _runner()._check_redirect(pg, URL, no_candidate=True) is None


def test_shape_is_not_consulted_while_the_page_still_has_a_candidate():
    with _page(LOGGED_OUT) as pg:
        assert _runner()._check_redirect(pg, URL) is None


def test_public_site_without_a_login_url_is_never_sent_to_relogin():
    with _page(LOGGED_OUT) as pg:
        assert _runner(login_url="")._check_redirect(pg, URL, no_candidate=True) is None


def test_the_no_download_branch_asks_with_no_candidate():
    """Wiring: runner.py's `if not best:` branch is the one caller that passes no_candidate=True."""
    import bulk_downloader.runner as runner_mod

    tree = ast.parse(Path(runner_mod.__file__).read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "_check_redirect"]
    flagged = [c for c in calls if any(k.arg == "no_candidate" for k in c.keywords)]
    assert len(calls) == 3 and len(flagged) == 1, (len(calls), len(flagged))
    parents = {id(ch): p for p in ast.walk(tree) for ch in ast.iter_child_nodes(p)}
    node = flagged[0]
    while node is not None and not (isinstance(node, ast.If) and ast.unparse(node.test) == "not best"):
        node = parents.get(id(node))
    assert node is not None, "no_candidate=True must be passed only under `if not best:`"
