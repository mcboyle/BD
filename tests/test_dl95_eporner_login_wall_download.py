"""dl95-eporner-1 (test2 2026-09-29, B6-B p1/eporner): eporner's 1080p /dload/ link
answered its "Login & create account" page (44,572 B saved as 17592192-1080p.mp4).
The app never logged in; the body was failed only by size sanity ("likely an
error page"), so the held credentials were never used.

The fixture is that body verbatim, csrfToken value zeroed. Its form is rendered
by JS (no password <input>), so AUTH_BODY_RE alone cannot see it; the page names
itself via <link rel=canonical href=".../login/<return-path>">.
"""
from __future__ import annotations

import inspect
import types
from pathlib import Path

import bulk_downloader.runner_auth as ra
import bulk_downloader.runner_transport as rt

BD_GATE_SCOPE = "module"
FIXTURE = Path(__file__).parent / "fixtures" / "dl95_eporner" / "dload_1080_login_wall.html"
MP4_HEAD = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * 4096


def _fake(config, calls):
    fake = types.SimpleNamespace(
        config=config, site_id="0c546602", jobs={}, _lock=types.SimpleNamespace(
            __enter__=lambda *a: None, __exit__=lambda *a: None),
        _session_ok=types.SimpleNamespace(clear=lambda: None, set=lambda: None),
        _login_thread=None, cookies={}, _cookies_updated_at=0,
        _url_queue=types.SimpleNamespace(put=lambda u: calls.setdefault("requeued", []).append(u)),
    )
    fake._lock = _Lock()
    fake._update_job = lambda url, status, msg, **k: calls.setdefault("jobs", []).append((status, msg))
    fake.login_async = lambda *a, **k: calls.__setitem__("login", calls.get("login", 0) + 1)
    fake._handle_auth_required = lambda url, why="Session expired": ra.AuthMixin._handle_auth_required(fake, url, why=why)
    return fake


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_the_row_body_is_recognised_as_the_login_page():
    why = ra.login_wall_in_body(FIXTURE)
    assert why and "/login/" in why, f"eporner login wall not recognised: {why!r}"


def test_media_and_a_non_login_html_page_are_not_login_walls(tmp_path):
    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(MP4_HEAD)
    page = tmp_path / "b.mp4"
    page.write_text('<!DOCTYPE html><html><head><title>Not found</title>'
                    '<link rel="canonical" href="https://x.test/video/123/"></head></html>')
    assert ra.login_wall_in_body(mp4) is None
    assert ra.login_wall_in_body(page) is None
    assert ra.login_wall_in_body(tmp_path / "missing.mp4") is None


def test_the_row_download_logs_in_and_requeues_instead_of_failing(tmp_path, monkeypatch):
    monkeypatch.setattr(ra, "db_log", lambda *a, **k: None)
    got = tmp_path / "17592192-1080p.mp4"
    got.write_bytes(FIXTURE.read_bytes())
    calls = {}
    fake = _fake({"username": "u", "password": "p", "max_retries": 2}, calls)
    assert ra.AuthMixin._login_wall_rejects(fake, "https://www.eporner.com/video-x/", got) is True
    assert not got.exists() and (tmp_path / "_failed" / got.name).exists()
    assert calls.get("login") == 1, calls
    assert calls["jobs"][0][0] == "pending" and "Download requires login" in calls["jobs"][0][1], calls


def test_without_credentials_it_reports_download_requires_login(tmp_path, monkeypatch):
    monkeypatch.setattr(ra, "db_log", lambda *a, **k: None)
    got = tmp_path / "v.mp4"
    got.write_bytes(FIXTURE.read_bytes())
    calls = {}
    fake = _fake({"max_retries": 2}, calls)
    assert ra.AuthMixin._login_wall_rejects(fake, "https://www.eporner.com/video-x/", got) is True
    assert "login" not in calls
    status, msg = calls["jobs"][-1]
    assert status == "failed" and msg.startswith("Download requires login"), msg


def test_a_real_media_file_is_left_alone(tmp_path):
    got = tmp_path / "v.mp4"
    got.write_bytes(MP4_HEAD)
    calls = {}
    assert ra.AuthMixin._login_wall_rejects(_fake({}, calls), "u", got) is False
    assert got.exists() and calls == {}


def test_the_download_path_checks_the_login_wall_before_size_sanity():
    src = inspect.getsource(rt)
    wall = src.find("login_wall_rejects(page_url, final_path)")
    sanity = src.find("Phase 17.20: Size sanity check")
    assert 0 <= wall < sanity, "login-wall check is not wired ahead of size sanity"
