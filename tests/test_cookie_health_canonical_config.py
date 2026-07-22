"""Regression coverage for the canonical ``cookie_file`` site key."""


def test_check_site_uses_canonical_cookie_file(tmp_path, monkeypatch):
    from bulk_downloader import cookie_health

    monkeypatch.setattr(cookie_health, "_record", lambda *_a, **_k: None)
    result = cookie_health.check_site("s1", {
        "login_url": "https://example.invalid/login",
        "cookie_file": str(tmp_path / "missing.txt"),
    })

    assert result["status"] == "red"
    assert result["note"] == "cookies file missing or empty"


def test_check_all_sites_includes_canonical_cookie_file(monkeypatch):
    from bulk_downloader import cookie_health

    monkeypatch.setattr(cookie_health, "_ensure_table", lambda: None)
    monkeypatch.setattr(
        cookie_health,
        "check_site",
        lambda site_id, _cfg: {"site_id": site_id, "status": "green"},
    )

    result = cookie_health.check_all_sites({
        "s1": {"cookie_file": "/tmp/cookies.txt"},
    })

    assert result["checked"] == 1
    assert result["skipped"] == 0


def test_check_site_accepts_canonical_json_cookie_jar(tmp_path, monkeypatch):
    from bulk_downloader import cookie_health, cookies

    jar_path = tmp_path / "cookies.json"
    cookies.save_cookies_to_file(jar_path, [{
        "name": "fixture_session",
        "value": "valid-session-token",
        "domain": "example.test",
        "path": "/",
        "secure": False,
        "httpOnly": True,
        "sameSite": "Lax",
    }])

    class _Response:
        status_code = 200
        url = "https://example.test/members"
        content = b"<h1>Members Area</h1>"

    class _Client:
        def __init__(self, cookies=None, **_kwargs):
            assert list(cookies)[0].name == "fixture_session"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _url):
            return _Response()

    monkeypatch.setattr("httpx.Client", _Client)
    monkeypatch.setattr(cookie_health, "_record", lambda *_a, **_k: None)

    result = cookie_health.check_site("s1", {
        "auth_check_url": "https://example.test/members",
        "cookie_file": str(jar_path),
    })
    assert result["status"] == "green"
