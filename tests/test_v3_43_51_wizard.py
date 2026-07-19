"""v3.43.51: login wizard revamp tests."""
from __future__ import annotations

import json
import time
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOGIN_PY = _REPO_ROOT / "bulk_downloader" / "login.py"

def _login_impl_src():
    """Concatenated source of the decomposed login_impl/ package. login.py is now an
    ADD-only re-export shim (DECOMP-LEAF cut 3); the implementation lives in
    login_impl/*.py, so structure/path tests read the package, not the shim."""
    from pathlib import Path as _P
    import bulk_downloader.login_impl as _pkg
    _d = _P(_pkg.__file__).parent
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_d.glob("*.py")))

_RUNNER_PY = _REPO_ROOT / "bulk_downloader" / "runner.py"


def _bd_runner_src():
    """v3.66.403: runner.py decomposed into runner_*.py mixins; aggregate the
    package so moved SiteRunner method bodies stay visible to source checks."""
    from pathlib import Path as _P
    from bulk_downloader import runner as _R
    _pd = _P(_R.__file__).parent
    return "\n".join(q.read_text(encoding="utf-8")
                     for q in [_pd / "runner.py"] + sorted(_pd.glob("runner_*.py")))
_APP_JS = _REPO_ROOT / "bulk_downloader" / "static" / "app.js"


# ── login.py: _compute_cookie_expiry_days ─────────────────────────


def test_compute_cookie_expiry_finds_min_auth_cookie(clean_workdir):
    from bulk_downloader import login as lm
    cookies_dir = clean_workdir / "cookies"
    cookies_dir.mkdir()
    now = time.time()
    (cookies_dir / "wow.json").write_text(json.dumps([
        {"name": "session_id", "value": "x", "expires": now + 7 * 86400},
        {"name": "auth_token", "value": "y", "expires": now + 30 * 86400},
        {"name": "promo_seen", "value": "z", "expires": now + 1 * 86400},
        {"name": "sid", "value": "w", "expires": -1},
    ]))
    days = lm._compute_cookie_expiry_days({"site_id": "wow"})
    assert days in (6, 7)


def test_compute_cookie_expiry_returns_none_when_no_file(clean_workdir):
    from bulk_downloader import login as lm
    assert lm._compute_cookie_expiry_days({"site_id": "doesnotexist"}) is None


def test_compute_cookie_expiry_returns_none_when_only_session_cookies(clean_workdir):
    from bulk_downloader import login as lm
    cookies_dir = clean_workdir / "cookies"
    cookies_dir.mkdir()
    (cookies_dir / "wow.json").write_text(json.dumps([
        {"name": "session_id", "value": "x", "expires": -1},
        {"name": "promo_seen", "value": "y", "expires": time.time() + 86400},
    ]))
    assert lm._compute_cookie_expiry_days({"site_id": "wow"}) is None


def test_compute_cookie_expiry_handles_malformed_json(clean_workdir):
    from bulk_downloader import login as lm
    cookies_dir = clean_workdir / "cookies"
    cookies_dir.mkdir()
    (cookies_dir / "wow.json").write_text("not valid json {")
    assert lm._compute_cookie_expiry_days({"site_id": "wow"}) is None


def test_compute_cookie_expiry_filters_expired_cookies(clean_workdir):
    from bulk_downloader import login as lm
    cookies_dir = clean_workdir / "cookies"
    cookies_dir.mkdir()
    now = time.time()
    (cookies_dir / "wow.json").write_text(json.dumps([
        {"name": "session_old", "value": "x", "expires": now - 86400},
        {"name": "session_id", "value": "y", "expires": now + 5 * 86400},
    ]))
    days = lm._compute_cookie_expiry_days({"site_id": "wow"})
    assert days in (4, 5)


# ── login.py: _build_verify_result summary shaping ────────────────


def test_build_verify_result_summarizes_cookies_only_success():
    from bulk_downloader.login import _build_verify_result
    r = _build_verify_result(
        replay_ok=True, replay_ms=1200, replay_error="",
        replay_method="cookies_only",
        member_probe_ok=None, member_probe_ms=0, member_probe_error="",
        cookies_expire_in_days=14)
    assert r["replay_ok"] is True
    assert "Cookies alone" in r["summary"]
    assert "1200ms" in r["summary"]
    assert "14 days" in r["summary"]


def test_build_verify_result_summarizes_replay_failure():
    from bulk_downloader.login import _build_verify_result
    r = _build_verify_result(
        replay_ok=False, replay_ms=2000,
        replay_error="couldn't find password field",
        replay_method="",
        member_probe_ok=None, member_probe_ms=0, member_probe_error="",
        cookies_expire_in_days=7)
    assert r["replay_ok"] is False
    assert "failed" in r["summary"].lower()
    assert "password field" in r["summary"]


def test_build_verify_result_member_probe_failure():
    from bulk_downloader.login import _build_verify_result
    r = _build_verify_result(
        replay_ok=True, replay_ms=1500, replay_error="",
        replay_method="fresh_login",
        member_probe_ok=False, member_probe_ms=800,
        member_probe_error="bounced to login form",
        cookies_expire_in_days=None)
    assert r["replay_ok"] is True
    assert r["member_probe_ok"] is False
    assert "bounced to login" in r["summary"]


def test_build_verify_result_warns_short_expiry():
    from bulk_downloader.login import _build_verify_result
    r = _build_verify_result(
        replay_ok=True, replay_ms=1500, replay_error="",
        replay_method="cookies_only",
        member_probe_ok=None, member_probe_ms=0, member_probe_error="",
        cookies_expire_in_days=1)
    assert "<24h" in r["summary"]


# ── verify_login_replay: short-circuit ────────────────────────────


def test_verify_login_replay_no_login_url_short_circuits(clean_workdir):
    from bulk_downloader.login import verify_login_replay
    result = verify_login_replay(
        {"name": "test"}, profile_dir=str(clean_workdir / "fake_profile"))
    assert result["replay_ok"] is False
    assert "login_url" in result["replay_error"].lower()


# ── Endpoints ─────────────────────────────────────────────────────


def _setup_stub_runner():
    """Insert a stub runner. Returns the stub for assertions."""
    from bulk_downloader import app as _app
    class StubRunner:
        def __init__(self):
            self._verify_calls = []
            self._last_verify_result = None
        def verify_login_after_wizard(self, member_url=""):
            self._verify_calls.append({"member_url": member_url})
            self._last_verify_result = {
                "replay_ok": True, "replay_ms": 1500,
                "replay_error": "", "replay_method": "cookies_only",
                "member_probe_ok": True if member_url else None,
                "member_probe_ms": 500 if member_url else 0,
                "member_probe_error": "",
                "cookies_expire_in_days": 14,
                "summary": "stub success",
            }
            return self._last_verify_result
        def get_last_verify_result(self):
            return self._last_verify_result
    _app.s_cfg["test_sid"] = {"name": "test",
                                  "login_url": "https://x.com/login"}
    stub = StubRunner()
    _app.runners["test_sid"] = stub
    return stub


def test_endpoint_login_verify_calls_runner(fresh_app):
    stub = _setup_stub_runner()
    r = fresh_app.post("/api/sites/test_sid/login_verify",
                          json={"member_url": ""})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["ok"] is True
    assert body["replay_ok"] is True
    assert stub._verify_calls == [{"member_url": ""}]


def test_endpoint_login_verify_passes_member_url(fresh_app):
    stub = _setup_stub_runner()
    r = fresh_app.post("/api/sites/test_sid/login_verify",
                          json={"member_url": "https://x.com/dashboard"})
    body = r.get_json()
    assert body["member_probe_ok"] is True
    assert stub._verify_calls[0]["member_url"] == "https://x.com/dashboard"


def test_endpoint_login_verify_404_when_site_missing(fresh_app):
    r = fresh_app.post("/api/sites/nonexistent/login_verify", json={})
    assert r.status_code == 404


def test_endpoint_login_verify_status(fresh_app):
    _setup_stub_runner()
    r = fresh_app.get("/api/sites/test_sid/login_verify_status")
    assert r.status_code == 404
    fresh_app.post("/api/sites/test_sid/login_verify", json={})
    r = fresh_app.get("/api/sites/test_sid/login_verify_status")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["summary"] == "stub success"


def test_endpoint_login_verify_status_404_when_site_missing(fresh_app):
    r = fresh_app.get("/api/sites/nonexistent/login_verify_status")
    assert r.status_code == 404


# ── Wizard UI source structure ────────────────────────────────────


# ── runner.SiteRunner ─────────────────────────────────────────────


def test_runner_has_verify_methods():
    src = _bd_runner_src()
    assert "def verify_login_after_wizard" in src
    assert "def get_last_verify_result" in src


def test_runner_verify_stores_last_result():
    src = _bd_runner_src()
    pos = src.find("def verify_login_after_wizard")
    body = src[pos:pos + 1500]
    assert "self._last_verify_result" in body


# ── login.py: structure ────────────────────────────────────────────


def test_login_has_verify_primitives():
    src = _login_impl_src()
    for fn in ("verify_login_replay", "_build_verify_result",
                  "_compute_cookie_expiry_days",
                  "_attempt_headless_fill_submit"):
        assert f"def {fn}" in src, f"missing {fn}"
