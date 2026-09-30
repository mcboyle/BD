"""fx-dorcelclub-dl-challenge-wall (bd1 2026-09-29 23:20Z, OP-bd1): with a working
member session (the scene page is the member view over HTTP too), dorcelclub's
/dl/scene/<id>/<slug>/full/1080.mp4 answered a 302 to www.dorcelclub.com/blocked --
an hCaptcha "security check" form in the site's own chrome -- for every client on
the VM's IP. The app saved that 209,150 B page as "Captive of irresistible pleasure
[1080p].mp4" and failed it on size sanity ("likely an error page", backoff_retry,
category unknown), so the challenge never reached the take-over/relay path.

The body below is synthetic in the row's shape (site chrome first, the hCaptcha
form ~31 KB in, a "log in" link, no media); sitekey zeroed.
"""
from __future__ import annotations

import inspect
import types

import bulk_downloader.runner_challenge as rc
import bulk_downloader.runner_transport as rt

BD_GATE_SCOPE = "module"
DIAG = "FX_DORCEL_DL_CHALLENGE_SAVED_AS_MEDIA"
FILE_URL = ("https://www.dorcelclub.com/dl/scene/305502/captive-of-irresistible-pleasure"
            "/full/1080.mp4?lang=en")
SCENE = "https://www.dorcelclub.com/en/scene/305502/captive-of-irresistible-pleasure"
MP4_HEAD = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * 4096
_CHROME = ('<nav class="menu"><a href="/en/videos">Videos</a><a href="/en/actresses">Actresses</a>'
           '<a href="/en/login" class="login">Log in</a><a href="/en/subscribe">Subscribe</a></nav>')
BLOCKED = ('<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en">'
           '<head><title>Dorcel XXX videos - Porn to watch &amp; download in HD on DorcelClub</title>'
           '<script src="https://js.hcaptcha.com/1/api.js" async defer></script></head><body>'
           + _CHROME * 180 +
           '<div class="blocked"><h1>SECURITY CHECK REQUIRED</h1><form id="blocked-form" method="POST">'
           '<div class="h-captcha" data-sitekey="00000000-0000-0000-0000-000000000000"></div>'
           '<button type="submit">Continue</button></form></div></body></html>')


def _wall():
    wall = getattr(rc, "challenge_wall_in_body", None)
    assert wall is not None, (
        f"{DIAG}: runner_challenge has no challenge_wall_in_body -- a downloaded "
        f"security-check page is judged only by size sanity")
    return wall


def _rejects():
    fn = getattr(rc.ChallengeMixin, "_challenge_wall_rejects", None)
    assert fn is not None, (
        f"{DIAG}: ChallengeMixin has no _challenge_wall_rejects -- the hCaptcha page "
        f"saved as the .mp4 is failed on size sanity with a blind backoff retry")
    return fn


class _Page:
    def __init__(self, goto_error=None):
        self.visited, self.goto_error = [], goto_error

    def goto(self, url, **kw):
        self.visited.append(url)
        if self.goto_error:
            raise self.goto_error


def _fake(calls, *, browser_shows_challenge=True, config=None):
    fake = types.SimpleNamespace(config=dict(config or {}, name="dorcelclub"),
                                 site_id="8cab7bee")
    fake._update_job = lambda url, status, msg, **k: calls.setdefault("jobs", []).append(
        (status, msg, k))
    fake.log_event = lambda kind, msg, **k: calls.setdefault("events", []).append((kind, msg))
    fake._screenshot = lambda page, url: "shot.png"

    def handle(page, url):
        calls.setdefault("handled", []).append((page.visited[-1], url))
        return not browser_shows_challenge
    fake._handle_captcha_check = handle
    return fake


def test_the_row_body_is_an_hcaptcha_challenge(tmp_path):
    got = tmp_path / "Captive of irresistible pleasure [1080p].mp4"
    got.write_text(BLOCKED)
    assert BLOCKED.find("h-captcha") > 30000
    assert _wall()(got) == "hcaptcha", f"{DIAG}: the /blocked hCaptcha body was not recognised"


def test_media_a_plain_error_page_and_a_missing_file_are_not_challenges(tmp_path):
    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(MP4_HEAD)
    err = tmp_path / "b.mp4"
    err.write_text('<!DOCTYPE html><html><head><title>Not found</title></head>'
                   '<body>404 ' + _CHROME + '</body></html>')
    wall = _wall()
    assert wall(mp4) is None
    assert wall(err) is None
    assert wall(tmp_path / "missing.mp4") is None


def test_the_row_download_takes_the_challenge_path_not_size_sanity(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "db_log", lambda *a, **k: None)
    got = tmp_path / "Captive of irresistible pleasure [1080p].mp4"
    got.write_text(BLOCKED)
    calls, page = {}, _Page()
    assert _rejects()(_fake(calls), SCENE, got, page=page, file_url=FILE_URL) is True, (
        f"{DIAG}: the challenge body was left for size sanity")
    assert not got.exists() and (tmp_path / "_failed" / got.name).exists()
    assert page.visited == [FILE_URL], "the worker browser must open the challenged file URL"
    assert calls["handled"] == [(FILE_URL, SCENE)], calls
    kind, msg = calls["events"][0]
    assert kind == "captcha" and "hcaptcha" in msg and "www.dorcelclub.com/dl/scene/305502" in msg
    assert "lang=en" not in msg, "the query string is not echoed into the event log"


def test_the_relay_is_told_when_enabled(tmp_path, monkeypatch):
    import bulk_downloader.captcha_relay as relay
    monkeypatch.setattr(rc, "db_log", lambda *a, **k: None)
    seen = []
    monkeypatch.setattr(relay, "check_and_handle",
                        lambda page, sid, url, worker_idx=None: seen.append((sid, url)))
    got = tmp_path / "v.mp4"
    got.write_text(BLOCKED)
    assert _rejects()(_fake({}, config={"use_captcha_relay": True}), SCENE, got,
                      page=_Page(), file_url=FILE_URL) is True
    assert seen == [("8cab7bee", SCENE)]


def test_a_challenge_the_browser_does_not_show_is_needs_review_with_the_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "db_log", lambda *a, **k: None)
    got = tmp_path / "v.mp4"
    got.write_text(BLOCKED)
    calls = {}
    assert _rejects()(_fake(calls, browser_shows_challenge=False), SCENE, got,
                      page=_Page(), file_url=FILE_URL) is True
    status, msg, extra = calls["jobs"][-1]
    assert status == "needs_review" and extra.get("captcha_type") == "hcaptcha", calls
    assert "challenge" in msg and "failed" != status


def test_a_browser_that_cannot_open_the_url_still_reports_the_challenge(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "db_log", lambda *a, **k: None)
    got = tmp_path / "v.mp4"
    got.write_text(BLOCKED)
    calls = {}
    assert _rejects()(_fake(calls), SCENE, got, page=_Page(RuntimeError("net::ERR_ABORTED")),
                      file_url=FILE_URL) is True
    assert "handled" not in calls
    status, _msg, extra = calls["jobs"][-1]
    assert status == "needs_review" and extra.get("screenshot") == ""


def test_a_real_media_file_is_left_alone(tmp_path):
    got = tmp_path / "v.mp4"
    got.write_bytes(MP4_HEAD)
    calls, page = {}, _Page()
    assert _rejects()(_fake(calls), SCENE, got, page=page, file_url=FILE_URL) is False
    assert got.exists() and calls == {} and page.visited == []


def test_the_download_path_checks_the_challenge_wall_before_size_sanity():
    src = inspect.getsource(rt)
    wall = src.find("challenge_wall_rejects(")
    sanity = src.find("Phase 17.20: Size sanity check")
    assert 0 <= wall < sanity, f"{DIAG}: challenge-wall check is not wired ahead of size sanity"
