"""O1567 fx-auto-approve-loginfree (HARDEN-T165, operator 2026-09-30 08:5xZ via bd-pm-A).

Row: bd3 spankbang — the approve-to-force gate held a scene with
"Only a public-tier file found ... Approve to force" even though spankbang has
no login (sites_config.json: login_url set, auth_required=False). Operator
ruling: the gate must not hold scenes on sites with no login.

Contract after the fix: the public-tier hold (member_rendition.HELD_MESSAGE) is
skipped on a login-free site -- member_rendition.is_login_site, the gate's own
predicate, is False when the site declares auth_required=False (the explicit
bool wins, as in scene_crawler._site_is_public), whatever its login_url.
The pick downloads unchanged. Real login sites
(auth_required unset or True, http login_url) still hold a lone public-tier
file needs_review, and forced jobs / unreadable views stay unchanged.

Real chromium, every request answered by page.route (no network), same harness
as tests/test_dl95_pegasproductions_2b_member_rendition.py.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://spankbang.example"
SCENE = f"{ORIGIN}/a58rp/video/scene"
PUBLIC = f"{ORIGIN}/17040805-1080p.mp4"  # the only file; the page is the same logged in or out


def _html(links):
    body = "".join(f'<a class="dl" href="{h}">{label}</a>' for h, label in links)
    return f"<!doctype html><html><body><main><h1>Scene</h1>{body}</main></body></html>"


@contextmanager
def _scene(links, public_status=200):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    seen = []

    def handle(route):
        req = route.request
        seen.append((req.method, req.url))
        if req.url == SCENE:
            return route.fulfill(status=200, content_type="text/html", body=_html(links))
        return route.fulfill(status=public_status, content_type="video/mp4", body=b"")

    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            ctx = br.new_context(viewport={"width": 1280, "height": 900})
            pg = ctx.new_page()
            pg.route("**/*", handle)
            pg.goto(SCENE, wait_until="load")
            yield pg, seen
        finally:
            br.close()


class _Runner:
    site_id = "7ccb5156"

    def __init__(self, **config):
        # spankbang-on-bd3 shape: login_url present (is_login_site True) but the
        # site declares auth_required False -- a login-free site.
        self.config = {"name": "spankbang",
                       "login_url": f"{ORIGIN}/",
                       "auth_required": False, **config}
        self.jobs = {}
        self.updates = []

    def _update_job(self, url, status, message, **_k):
        self.updates.append((status, message))

    def _screenshot(self, _page, _url):
        return "shot.png"


def _gate():
    from bulk_downloader import runner as rmod

    gate = getattr(rmod, "_prefer_member_rendition", None)
    assert gate is not None, "fx-auto-approve-loginfree: _prefer_member_rendition missing"
    return gate


def _run(pg, runner, monkeypatch):
    from bulk_downloader import runner as rmod
    from bulk_downloader.detect import find_best_download

    logged = []
    monkeypatch.setattr(rmod, "db_log", lambda *a, **k: logged.append(a))
    best = find_best_download(pg)
    return best, _gate()(runner, pg, SCENE, best), logged


def _href(best):
    return best["locator"].get_attribute("href") if best else None


def test_public_declared_site_downloads_its_public_file(monkeypatch):
    """Defect (spankbang T165): a login-free site (auth_required=False) whose
    only file is public-tier must not be held needs_review; the file is the
    scene and downloads."""
    r = _Runner()
    with _scene([(PUBLIC, "1080p")]) as (pg, _seen):
        best, out, logged = _run(pg, r, monkeypatch)
        assert _href(best) == PUBLIC  # precondition: the scorer admits the only file
        won = _href(out)
    assert out is best and won == PUBLIC, (
        f"FX_AUTO_APPROVE_LOGINFREE: login-free site held: {r.updates}")
    assert not r.updates, r.updates
    assert not logged, logged


@pytest.mark.parametrize("auth", ["unset", "true"])
def test_login_site_lone_public_tier_file_is_still_held(monkeypatch, auth):
    """Negative control (vip4k/eporner shape): login_url http and auth_required
    unset or True -- a lone public-tier file still holds needs_review."""
    cfg = {} if auth == "unset" else {"auth_required": True}
    r = _Runner(**cfg)
    if auth == "unset":
        r.config.pop("auth_required")
    with _scene([(PUBLIC, "1080p")]) as (pg, _seen):
        best, out, logged = _run(pg, r, monkeypatch)
        assert _href(best) == PUBLIC
    assert out is None, f"FX_AUTO_APPROVE_LOGINFREE_BROADENED: {auth} login site no longer holds"
    status, msg = r.updates[-1]
    assert status == "needs_review" and msg.endswith("Approve to force"), msg
    assert logged and logged[-1][3] == "needs_review", logged


def test_forced_job_on_public_site_still_downloads(monkeypatch):
    """Control: force_download on a public site downloads (gate bypass already
    existed for forced jobs and must survive)."""
    r = _Runner()
    r.jobs[SCENE] = {"force_download": True}
    with _scene([(PUBLIC, "1080p")]) as (pg, _seen):
        best, out, _logged = _run(pg, r, monkeypatch)
        won = _href(out)
    assert out is best and won == PUBLIC
    assert not r.updates, r.updates


def test_spa_logged_out_view_skips_public_sites():
    """The extractor-side gate (_spa_logged_out_view) must also stand down on a
    login-free site, and keep judging real login sites."""
    from bulk_downloader import runner_extractors as rx

    host_public = _Runner()
    assert rx._spa_logged_out_view(host_public, SCENE, page=None, page_url=SCENE) is None

    host_login = _Runner()
    host_login.config["auth_required"] = ""  # unset: login site (login_url http)
    view = rx._spa_logged_out_view(host_login, SCENE, page=None, page_url=SCENE)
    from bulk_downloader.member_rendition import LoggedOutView
    assert isinstance(view, LoggedOutView)


def test_is_login_site_predicate():
    """The gate's own predicate: an explicit auth_required=False wins over an
    http login_url (spankbang); unset or True keeps the login_url rule."""
    from bulk_downloader import member_rendition as mr

    assert mr.is_login_site({"login_url": "https://x/", "auth_required": False}) is False
    assert mr.is_login_site({"login_url": "https://x/", "auth_required": True}) is True
    assert mr.is_login_site({"login_url": "https://x/"}) is True  # unset: members area
    assert mr.is_login_site({"auth_required": True}) is False  # no login_url: nothing to compare
    assert mr.is_login_site({}) is False
