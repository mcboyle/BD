"""tpl95-bang-1: a template-onboarding capture starts from the site's session (O1517).

test2 23:54Z (harness-work/UIUX-20260928/download-95/A1-A/FINDINGS-A1-A.md#T1): the onboarding capture
opened bang.com logged out; the only way in was a human typing the password over noVNC, so the
O1517 chain "log in automatically -> navigate -> find -> download" was not available to templates.

PM ruling A (harness-work/FIX/tpl95-bang-1-bd-worker-A8-A/QUESTION.md): site-scoped onboarding only.
The app gets the session in-process (stored cookies, else the worker's automatic login), writes ONLY
cookies to a 0600 jar, and capture_session reads + deletes it and seeds the context before its first
navigation. The cockpit capture tool is unchanged.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import json
import os
import stat
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent
for _p in (str(REPO), str(REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

LIVE = {"name": "sess", "value": "tok", "domain": ".example.test", "path": "/",
        "expires": time.time() + 86400, "secure": True, "httpOnly": True, "sameSite": "Lax"}
EXPIRED = dict(LIVE, expires=time.time() - 86400)


# ── capture_session: --cookies-file ─────────────────────────────────────────

def test_the_jar_is_read_normalised_and_deleted(tmp_path):
    import capture_session as cs
    jar = tmp_path / "x.cookies.json"
    login_shape = {"name": "a", "value": "1", "domain": ".example.test", "path": "/",
                   "secure": True, "httpOnly": False, "sameSite": "Lax",
                   "expirationDate": 2_000_000_000}
    jar.write_text(json.dumps([LIVE, login_shape]))
    got = cs._take_cookie_jar(str(jar))
    assert not jar.exists(), "the session jar must not outlive its read"
    assert [c["name"] for c in got] == ["sess", "a"]
    assert got[0]["expires"] == int(LIVE["expires"]) and got[1]["expires"] == 2_000_000_000
    assert all("expirationDate" not in c for c in got)


def test_negative_a_broken_jar_is_deleted_and_refused(tmp_path):
    import capture_session as cs
    jar = tmp_path / "x.cookies.json"
    jar.write_text("{not json")
    with pytest.raises(ValueError):
        cs._take_cookie_jar(str(jar))
    assert not jar.exists()
    assert cs.run(["--url", "https://example.test/", "--out", str(tmp_path / "o.wacz"),
                   "--cookies-file", str(tmp_path / "missing.json")]) == 2


def test_the_context_is_seeded_before_the_first_navigation(tmp_path, monkeypatch):
    import capture_session as cs
    from bulk_downloader import cloak
    calls = mock.MagicMock()
    browser = mock.MagicMock()
    browser.new_context.return_value = calls.ctx
    calls.ctx.new_page.return_value = calls.page
    calls.ctx.pages = [calls.page]
    calls.page.url = "https://example.test/"
    monkeypatch.setattr(cloak, "launch_browser", lambda **kw: (browser, None, "fixture"))
    jar = tmp_path / "x.cookies.json"
    jar.write_text(json.dumps([LIVE]))
    out = tmp_path / "o.wacz"
    (tmp_path / "o.FINISH").touch()
    try:
        cs.run(["--url", "https://example.test/", "--out", str(out), "--no-hud",
                "--cookies-file", str(jar), "--finish-file", str(tmp_path / "o.FINISH"),
                "--max-seconds", "1", "--url-memory-file", str(tmp_path / "m.json")])
    except Exception:
        pass   # the fake page cannot produce a WACZ; only the call order matters
    names = [c[0] for c in calls.mock_calls]
    assert "ctx.add_cookies" in names, names[:12]
    seeded = names.index("ctx.add_cookies")
    gotos = [i for i, n in enumerate(names) if n == "page.goto"]
    assert gotos and seeded < gotos[0], names[:12]
    assert calls.ctx.add_cookies.call_args[0][0][0]["name"] == "sess"
    assert not jar.exists()


# ── runner: the session a capture starts from ───────────────────────────────

def _runner(cookies, config, login_result=None):
    from bulk_downloader.runner_auth import AuthMixin
    r = AuthMixin.__new__(AuthMixin)
    r.cookies = list(cookies)
    r.config = dict(config)
    r._login_status = ""
    r.logins = []

    def _login_async(on_done=None, allow_manual=True):
        r.logins.append(allow_manual)
        ok, jar, status = login_result
        if ok:
            r.cookies = jar
        r._login_status = status
        threading.Thread(target=on_done, args=(ok,)).start()
    r.login_async = _login_async
    return r


def test_a_usable_stored_session_is_used_without_logging_in():
    r = _runner([LIVE], {"username": "u", "password": "p"})
    assert r.session_for_capture() == ([LIVE], "stored session")
    assert r.logins == []


def test_an_expired_session_runs_the_automatic_login_never_manual():
    r = _runner([EXPIRED], {"username": "u", "password": "p"},
                login_result=(True, [LIVE], "ok"))
    assert r.session_for_capture(timeout=5) == ([LIVE], "automatic login")
    assert r.logins == [False], "a capture must never open a manual takeover"


def test_negative_a_failed_login_gives_no_session_and_says_why():
    r = _runner([], {"username": "u", "password": "p"},
                login_result=(False, [], "Rejected login landing"))
    jar, how = r.session_for_capture(timeout=5)
    assert jar == [] and "Rejected login landing" in how


def test_negative_no_credentials_no_login_attempt():
    r = _runner([EXPIRED], {})
    assert r.session_for_capture() == ([], "no usable stored session and no credentials")
    assert r.logins == []


# ── the onboarding route ────────────────────────────────────────────────────

def _onboard(fresh_app, tmp_path, monkeypatch, cfg, session):
    import tools.onboard_site_template as ost
    import bulk_downloader.app as bd_app
    monkeypatch.setattr(ost, "ROOT", tmp_path)
    monkeypatch.setattr(ost, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(ost, "DRAFT_DIR", tmp_path / "drafts")
    monkeypatch.setattr(ost, "enabled_template_exists", lambda url: False)
    launched = []

    def _run(info, *, run):
        jar = info.get("cookies_file")
        launched.append((list(info["capture_cmd"]), jar,
                         Path(jar).read_text() if jar else None,
                         stat.S_IMODE(os.stat(jar).st_mode) if jar else None))
        return None
    monkeypatch.setattr(ost, "run_capture_flow", _run)
    sid = "s-bang"
    bd_app.s_cfg[sid] = dict(cfg)
    runner = SimpleNamespace(session_for_capture=lambda: session)
    monkeypatch.setitem(bd_app.runners, sid, runner)
    r = fresh_app.post(f"/api/sites/{sid}/template_onboard", json={"run": True})
    return r.get_json(), launched


def test_onboarding_hands_the_capture_the_sites_session(fresh_app, tmp_path, monkeypatch):
    body, launched = _onboard(fresh_app, tmp_path, monkeypatch,
                              {"login_url": "https://www.example.test/login",
                               "listing_url": "https://www.example.test/videos"},
                              ([LIVE], "stored session"))
    assert body["session"] == {"seeded": True, "why": "stored session"}, body
    cmd, jar, text, mode = launched[0]
    assert "--cookies-file" in cmd and cmd[cmd.index("--cookies-file") + 1].endswith(".cookies.json")
    assert json.loads(text) == [LIVE]
    assert mode == 0o600, oct(mode)
    assert "password" not in text and "username" not in text


def test_negative_a_site_without_login_launches_as_before(fresh_app, tmp_path, monkeypatch):
    body, launched = _onboard(fresh_app, tmp_path, monkeypatch,
                              {"url": "https://www.example.test/",
                               "listing_url": "https://www.example.test/videos"},
                              ([LIVE], "stored session"))
    assert body["session"] == {"seeded": False, "why": "site has no login_url"}, body
    cmd, jar, _, _ = launched[0]
    assert "--cookies-file" not in cmd and not jar


def test_negative_no_session_still_opens_the_capture_and_says_why(fresh_app, tmp_path, monkeypatch):
    body, launched = _onboard(fresh_app, tmp_path, monkeypatch,
                              {"login_url": "https://www.example.test/login",
                               "listing_url": "https://www.example.test/videos"},
                              ([], "automatic login failed: bad password"))
    assert body["session"] == {"seeded": False, "why": "automatic login failed: bad password"}
    assert "--cookies-file" not in launched[0][0]
    assert not list((tmp_path / "captures").glob("*.cookies.json"))
