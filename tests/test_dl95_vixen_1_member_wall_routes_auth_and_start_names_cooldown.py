"""dl95-vixen-1: vixen's members-only wall was taken for a RATE LIMIT.

MEASURED on test2 2026-09-29 02:33Z (download-95/A9-A/p1/vixen/RESULT.md D3,
journal-566a98cc-0230.txt line 18):

    [566a98cc][rate_limit] https://www.vixen.com/videos/fit-babe-needs-cum:
      Rate limit at ... (page text: ...ORMERS LIVE SEX SEARCH LOGIN GET ACCESS
      ACCESS DENIED  You must be a member to watch this vid...)

The site went ``rate_limited`` (24h cooldown, no re-login) and every later
Start answered ``ok`` and ran nothing.

Product fix: dl95-blacked-3 (44dd21852, same sentence on blacked) made
``_check_redirect`` answer "auth" when the bare denial phrase is a page's ONLY
rate-limit evidence and the page says the content is for members. This file
pins the vixen capture, the correctness-lens repro that refuted the first
vixen cut (bd-cx-worker-1 R1: a membership wall that ALSO says "Too many
requests" must keep the cooldown), and the second half of the row: a Start
refused by an active cooldown must NAME it (``blocked_by: rate_limited``),
never a bare ``ok``.
"""
from __future__ import annotations

import time

import pytest

from bulk_downloader.runner import SiteRunner

BD_GATE_SCOPE = "module"

_SID = "dl95vix1"
_SCENE = "https://www.vixen.com/videos/fit-babe-needs-cum"

# The captured page text (journal 02:33:11Z + RESULT.md D3), nav included.
VIXEN_WALL = ("VIDEOS MODELS PERFORMERS LIVE SEX SEARCH LOGIN GET ACCESS "
              "ACCESS DENIED  You must be a member to watch this video")

# bd-cx-worker-1 R1 repro (actual Chromium body in its lens run).
CX1_THROTTLE_WITH_MEMBER_TEXT = (
    "Access denied. You must be a member to watch this video. "
    "Too many requests.")


class _Loc:
    def __init__(self, text):
        self._t = text

    def inner_text(self, timeout=None):
        return self._t


class _Page:
    def __init__(self, text, url=_SCENE):
        self.url = url
        self._t = text

    def locator(self, sel):
        return _Loc(self._t)

    def content(self):
        return "<html><body>" + self._t + "</body></html>"


def _check(text):
    r = object.__new__(SiteRunner)
    return SiteRunner._check_redirect(r, _Page(text), _SCENE)


def test_vixen_members_only_wall_routes_auth_not_rate_limit():
    got = _check(VIXEN_WALL)
    assert got == "auth", (
        f"DL95-VIXEN-1: vixen 'ACCESS DENIED  You must be a member to watch "
        f"this video' wall classified {got!r}; it must be 'auth' (re-login), "
        f"not a site-wide 24h rate-limit cooldown")


@pytest.mark.parametrize("text", [
    CX1_THROTTLE_WITH_MEMBER_TEXT,
    ("ACCESS DENIED  You must be a member to watch this video. "
     "Rate limit exceeded, try again later."),
    "HTTP 429 Too Many Requests -- you must be a member to download",
])
def test_member_wall_beside_explicit_throttle_keeps_the_cooldown(text):
    got = _check(text)
    assert got == "rl", (
        f"CX1_THROTTLE_LOST: a page with an explicit throttle phrase AND "
        f"membership text classified {got!r}; the throttle must keep 'rl'")


@pytest.mark.parametrize("text", [
    "ACCESS DENIED",
    "Access Denied\nYour IP address has been blocked.",
])
def test_control_bare_denial_without_membership_stays_rate_limit(text):
    assert _check(text) == "rl", text


def test_control_normal_scene_page_is_none():
    assert _check("VIDEOS MODELS  Fit Babe Needs Cum  Download 4K  "
                  "Become a member today!") is None


def _start(monkeypatch, runner):
    from bulk_downloader import app as app_mod

    monkeypatch.setitem(app_mod.runners, _SID, runner)
    monkeypatch.setattr(app_mod, "_rate_check", lambda _action: True)
    with app_mod.app.test_request_context(
            f"/api/sites/{_SID}/start", method="POST"):
        resp = app_mod._do_action(_SID, "start")
    status = 200
    if isinstance(resp, tuple):
        resp, status = resp
    return status, resp.get_json()


def _runner(tmp_path):
    runner = SiteRunner(_SID, {"name": "vixen-fixture",
                               "download_dir": str(tmp_path)})
    runner.jobs[_SCENE] = {"status": "pending", "retry_after": 0}
    runner.urls.append(_SCENE)
    runner.log_event = lambda *a, **k: None
    return runner


def test_start_refused_by_cooldown_names_rate_limited(monkeypatch, tmp_path):
    runner = _runner(tmp_path)
    runner._rl_until = time.time() + 3600
    status, body = _start(monkeypatch, runner)
    assert runner.state() != "running", "cooldown must refuse the start"
    assert status == 200 and body.get("blocked_by") == "rate_limited", (
        f"DL95-VIXEN-1-SILENT-START: Start on a rate_limited site answered "
        f"{body!r}; a refused Start must name the cooldown, not a bare ok")


def test_control_no_cooldown_is_not_reported(monkeypatch, tmp_path):
    runner = _runner(tmp_path)
    runner._rl_until = 0
    assert runner.is_rate_limited() is False
    runner.start = lambda *a, **k: None
    _status, body = _start(monkeypatch, runner)
    assert body.get("blocked_by") != "rate_limited", body
