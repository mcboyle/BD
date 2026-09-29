"""dl95-pegasproductions-2b (MED; download-95/B6-B/p1/pegasproductions/RESULT.md + PM RULING in
bd-review-scratch/dl95-pegasproductions-2-cx2/BLOCKED.md).

Remainder of dl95-pegasproductions-2. On test2 (1bf8eab4, auth ok) the scene game-hockey-1 closed done on the public
file the LOGGED-OUT page links. B3-B's cut refuses a trailers/teasers DIRECTORY only; a public-tier file outside one
(/videos/<slug>-1080p.mp4, a page-media <video> source) still wins on a login site and closes done as the scene.

Contract after the fix: on a login site (login_url) the pick is compared with the logged-out view of the same scene
URL (an in-page fetch with credentials omitted). A pick the logged-out page also links -- and that a cookie-less HEAD
does not refuse -- is public-tier: the best member-only alternative is taken, else the job is held needs_review
("Approve to force"). Controls: a member link the tour page merely shows (HEAD 403) is kept; a non-login site, a forced
job and an unreadable logged-out view are unchanged.

Real chromium, every request answered by page.route (no network): the scene page is served by COOKIE -- the member
page when the context's session cookie is sent, the tour page when it is not.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://www.pegasproductions.com"
SCENE = f"{ORIGIN}/game-hockey-1"
PUBLIC = f"{ORIGIN}/videos/game-hockey-1080p.mp4"              # public tier, no trailers/ directory
MEMBER = f"{ORIGIN}/members/dl/game-hockey-720p.mp4?token=abc"  # auth-scoped: only the member page has it
GATED = f"{ORIGIN}/members/dl/game-hockey-1080p.mp4"            # the tour page shows it, anonymously refused
JOIN = "https://join.billing-example.com/signup"                  # cross-origin join page, no CORS header


def _html(links):
    # A link is (href, label), or (href, label, data_url) for a JS control whose file is in data-url.
    body = "".join(f'<a class="tier" href="{h}"' + (f' data-url="{rest[0]}"' if rest else "") + f">{label}</a>"
                   for h, label, *rest in links)
    return f"<!doctype html><html><body><main><h1>The Hat Trick</h1>{body}</main></body></html>"


@contextmanager
def _scene(member_links, public_links, public_status=200, anon_member="403"):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    seen = []

    def handle(route):
        req = route.request
        cookie = req.all_headers().get("cookie", "")
        seen.append((req.method, req.url, "sid=member" in cookie))
        if req.url == SCENE:
            if "sid=member" in cookie:
                return route.fulfill(status=200, content_type="text/html", body=_html(member_links))
            return route.fulfill(status=public_status, content_type="text/html", body=_html(public_links))
        if req.url.startswith(f"{ORIGIN}/members/") and "sid=member" not in cookie:
            if anon_member == "xorigin302":  # a cookie-less HEAD then throws (CORS): no answer
                return route.fulfill(status=302, headers={"location": JOIN}, body="")
            return route.fulfill(status=403, content_type="text/html", body="<h1>Join now</h1>")
        if req.url.startswith(JOIN):
            return route.fulfill(status=200, content_type="text/html", body="<h1>Join</h1>")
        return route.fulfill(status=200, content_type="video/mp4", body=b"")

    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            ctx = br.new_context(viewport={"width": 1280, "height": 900})
            ctx.add_cookies([{"name": "sid", "value": "member", "url": ORIGIN}])
            pg = ctx.new_page()
            pg.route("**/*", handle)
            pg.goto(SCENE, wait_until="load")
            yield pg, seen
        finally:
            br.close()


class _Runner:
    site_id = "aec873ba"

    def __init__(self, **config):
        self.config = {"name": "pegasproductions", "login_url": f"{ORIGIN}/login", **config}
        self.jobs = {}
        self.updates = []

    def _update_job(self, url, status, message, **_k):
        self.updates.append((status, message))

    def _screenshot(self, _page, _url):
        return "shot.png"


def _gate(required=True):
    from bulk_downloader import runner as rmod

    gate = getattr(rmod, "_prefer_member_rendition", None)
    if gate is None and not required:
        return lambda _r, _p, _u, best: best  # BASE: the pick goes straight on (controls pin that)
    assert gate is not None, (
        "DL95_PEGAS_2B_PUBLIC_TIER_SAVED_AS_SCENE: on a login site nothing between the scorer and the download "
        f"compares the pick with the logged-out page; {PUBLIC} (linked logged-out) closes done as the scene")
    return gate


def _run(pg, runner, monkeypatch, required=True):
    from bulk_downloader import runner as rmod
    from bulk_downloader.detect import find_best_download

    logged = []
    monkeypatch.setattr(rmod, "db_log", lambda *a, **k: logged.append(a))
    best = find_best_download(pg)
    return best, _gate(required)(runner, pg, SCENE, best), logged


def _href(best):
    return best["locator"].get_attribute("href") if best else None


def test_a_lone_public_tier_file_is_held_not_saved(monkeypatch):
    r = _Runner()
    with _scene([(PUBLIC, "1080p")], [(PUBLIC, "1080p")]) as (pg, _seen):
        best, out, logged = _run(pg, r, monkeypatch)
        assert _href(best) == PUBLIC  # precondition: the scorer admits the public file
    assert out is None, f"DL95_PEGAS_2B_PUBLIC_TIER_SAVED_AS_SCENE: kept {_href(out)}"
    status, msg = r.updates[-1]
    assert status == "needs_review" and "game-hockey-1080p.mp4 is also linked on the logged-out page" in msg, msg
    assert msg.endswith("Approve to force") and logged and logged[-1][3] == "needs_review", logged


def test_the_member_rendition_wins_over_a_higher_tier_public_file(monkeypatch):
    r = _Runner(quality_preference="1080")
    with _scene([(PUBLIC, "1080p"), (MEMBER, "720p")], [(PUBLIC, "1080p")]) as (pg, _seen):
        best, out, _logged = _run(pg, r, monkeypatch)
        assert _href(best) == PUBLIC  # precondition: the public 1080p outranks the member 720p
        assert out and _href(out) == MEMBER, f"DL95_PEGAS_2B_PUBLIC_TIER_SAVED_AS_SCENE: chose {_href(out)}"
        left = [c["locator"].get_attribute("href") for c in out["_all_candidates"]]
    assert left == [MEMBER], f"a public tier stays pickable by quality_preference: {left}"
    assert not r.updates


def test_a_member_link_the_tour_page_merely_shows_is_kept(monkeypatch):
    """Control: linked logged-out but refused to a cookie-less HEAD (403) -- the member file itself."""
    r = _Runner()
    with _scene([(GATED, "1080p")], [(GATED, "1080p")]) as (pg, _seen):
        best, out, _logged = _run(pg, r, monkeypatch, required=False)
        assert out is best and _href(out) == GATED, _href(out)
    assert not r.updates


def test_member_tiers_past_the_probe_batch_are_not_public(monkeypatch):
    """Control (refute F1): ten member tiers the tour page shows, every one refused anonymously. A tier the probe
    never answered is not public; the 2160p winner stays the pick (BASE downloads it)."""
    from bulk_downloader.member_rendition import LoggedOutView

    tiers = [360, 480, 540, 576, 640, 720, 800, 900, 1080, 2160]
    links = [(f"{ORIGIN}/members/dl/s{i:02d}-{t}p.mp4", f"{t}p") for i, t in enumerate(tiers)]
    r = _Runner()
    with _scene(links, links) as (pg, _seen):
        public = LoggedOutView(pg, SCENE).public({u for u, _l in links})
        best, out, _logged = _run(pg, r, monkeypatch, required=False)
        won, kept = _href(best), _href(out)
    assert public == set(), f"DL95_PEGAS_2B_UNPROBED_TIER_JUDGED_PUBLIC: {sorted(public)}"
    assert won.endswith("-2160p.mp4") and kept == won, f"DL95_PEGAS_2B_UNPROBED_TIER_JUDGED_PUBLIC: {won} -> {kept}"
    assert not r.updates, r.updates


def test_a_member_link_redirecting_cross_origin_to_join_is_kept(monkeypatch):
    """Control (refute F2): the tour page shows the member link; anonymously it 302s to a cross-origin join page
    without CORS, so the HEAD probe throws. No answer is no verdict: the member file is downloaded, not held."""
    r = _Runner()
    with _scene([(GATED, "1080p")], [(GATED, "1080p")], anon_member="xorigin302") as (pg, _seen):
        best, out, _logged = _run(pg, r, monkeypatch, required=False)
        assert out is best and _href(out) == GATED, _href(out)
    assert not r.updates, f"DL95_PEGAS_2B_PROBE_ERROR_HELD_MEMBER_FILE: {r.updates}"


def test_a_js_control_carrying_the_public_file_in_data_url_is_held(monkeypatch):
    """Refute F3: <a href="#" data-url="/videos/...-1080p.mp4"> names its file in data-url, not in the incidental
    href="#" (which is the scene page itself)."""
    from bulk_downloader.member_rendition import element_url

    r = _Runner()
    link = ("#", "1080p", "/videos/game-hockey-1080p.mp4")
    with _scene([link], [link]) as (pg, _seen):
        best, out, logged = _run(pg, r, monkeypatch)
        assert best and best["locator"].get_attribute("data-url") == "/videos/game-hockey-1080p.mp4"  # precondition
        named = element_url(best["locator"], SCENE)
    assert named == PUBLIC, f"DL95_PEGAS_2B_HREF_HASH_HIDES_THE_FILE: element names {named!r}"
    assert out is None, "DL95_PEGAS_2B_PUBLIC_TIER_SAVED_AS_SCENE: the data-url public file was kept"
    assert r.updates[-1][0] == "needs_review" and logged and logged[-1][3] == "needs_review", r.updates


@pytest.mark.parametrize("case", ["not-login-site", "forced", "logged-out-view-unreadable"])
def test_controls_are_unchanged(monkeypatch, case):
    r = _Runner(**({"login_url": ""} if case == "not-login-site" else {}))
    if case == "forced":
        r.jobs[SCENE] = {"force_download": True}
    status = 503 if case == "logged-out-view-unreadable" else 200
    with _scene([(PUBLIC, "1080p")], [(PUBLIC, "1080p")], public_status=status) as (pg, seen):
        best, out, _logged = _run(pg, r, monkeypatch, required=False)
        assert out is best and _href(out) == PUBLIC, (case, _href(out))
    if case != "logged-out-view-unreadable":
        assert [s for s in seen if not s[2]] == [], f"{case}: the logged-out view was fetched"
    assert not r.updates


def test_the_logged_out_view_carries_no_session():
    """The comparison page is fetched without the session cookie (the logged-out view), never with it."""
    from bulk_downloader.member_rendition import LoggedOutView

    with _scene([(MEMBER, "720p")], [(PUBLIC, "1080p")]) as (pg, seen):
        view = LoggedOutView(pg, SCENE)
        assert view.readable() and PUBLIC in view.linked and MEMBER not in view.linked, view.linked
    assert ("GET", SCENE, False) in seen, seen


def test_the_gate_runs_before_quality_preference_and_min_resolution():
    """Wiring: _process_one consults it once, after the not-found refusal and before min_res / qpref."""
    import bulk_downloader.runner as rmod

    tree = ast.parse(Path(rmod.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_process_one")

    def lines(name):
        return [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
                and name in (getattr(n.func, "id", ""), getattr(n.func, "attr", ""))]
    gate, nf, qp = lines("_prefer_member_rendition"), lines("_refuse_not_found_winner"), lines("_apply_quality_preference")
    min_res = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign)
               and any(getattr(t, "id", "") == "min_res" for t in n.targets)]
    assert len(gate) == 1, f"DL95_PEGAS_2B_GATE_NOT_WIRED: {gate}"
    assert nf and qp and min_res and nf[0] < gate[0] < min(min_res) < qp[0], (nf, gate, min_res, qp)


# -- spa-api page-media pick (the same public-tier file bound to the page's <video>) --------------------------

class _SpaPage:
    def __init__(self, media, logged_out, gated=()):
        self.url, self.media, self.logged_out, self.gated = SCENE, media, logged_out, list(gated)

    def evaluate(self, js, arg=None):
        # Answered by what the script DOES (a cookie-less page fetch / HEAD probe), so the stub runs on BASE too.
        if "credentials: 'omit'" in js and "method: 'HEAD'" in js:
            return [u for u in arg if u not in self.gated]  # served anonymously as a file
        if "credentials: 'omit'" in js and "DOMParser" in js:
            return {"url": SCENE, "urls": list(self.logged_out)}
        return list(self.media)


@pytest.fixture
def spa(tmp_path, monkeypatch):
    from bulk_downloader import runner_extractors as rx

    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})

    class Stub(rx.ExtractorsMixin):
        site_id = "aec873ba"

        def __init__(self, **config):
            self.config = {"name": "pegasproductions", "login_url": f"{ORIGIN}/login",
                           "download_dir": str(tmp_path / "dl"), "min_resolution": 0, **config}
            self.jobs, self.updates, self.events, self.transfers, self.logged = {}, [], [], [], []
            self._spa_api_capture = None
            monkeypatch.setattr(rx, "db_log", lambda *a, **k: self.logged.append(a))

        def _update_job(self, url, status, message, **_k):
            self.updates.append((status, message))

        def log_event(self, kind, message, url=None, extra=None):
            self.events.append((kind, message))

        def _screenshot(self, _page, _url):
            return "shot.png"

        def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
            self.transfers.append(file_url)
            return False

    return Stub


def test_spa_page_media_public_tier_pick_is_held(spa):
    r = spa()
    pg = _SpaPage([PUBLIC], ["/videos/game-hockey-1080p.mp4"])
    handled = r._try_spa_api_media_extractor(SCENE, pg)
    assert r.transfers == [], f"DL95_PEGAS_2B_PUBLIC_TIER_SAVED_AS_SCENE: spa-api fetched {r.transfers}"
    assert handled is True
    assert r.updates[-1][0] == "needs_review" and "is also linked on the logged-out page" in r.updates[-1][1], r.updates


@pytest.mark.parametrize("case", ["member-media", "not-login-site", "gated"])
def test_spa_controls_still_transfer(spa, case):
    r = spa(**({"login_url": ""} if case == "not-login-site" else {}))
    media = MEMBER if case == "member-media" else PUBLIC
    pg = _SpaPage([media], [PUBLIC], gated=[PUBLIC] if case == "gated" else ())
    r._try_spa_api_media_extractor(SCENE, pg)
    assert r.transfers == [media], (case, r.transfers, r.updates)
